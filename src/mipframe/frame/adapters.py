"""Analysis and sparse-kernel sources use the common Frame execution interface."""

from fractions import Fraction
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType
from mipframe.frame.schema import ref_type


def sql_frame(store, query, schema, *, key=None, domains=None):
    def scan(fields, ctx):
        store.guard()
        select = ",".join('"' + n.replace('"', '""') + '"' for n in fields) or "1"
        for values in store.execute(f"SELECT {select} FROM ({query})"):
            row = {}
            for name, value in zip(fields, values):
                t = schema[name]
                row[name] = (
                    Fraction(value)
                    if t.kind == "number" and value is not None
                    else bool(value)
                    if t.kind == "bool" and value is not None
                    else value
                )
            yield row

    result = Frame(schema, key=key, models=(store.owner,), source=scan, domains=domains)
    result._resource = store
    result._source_spec = {
        "kind": "relation",
        "relation": f"analysis:{id(store)}:{query}",
    }
    return result


def sparse_counts(owner, rows=None, vars=None, axis="row"):
    if axis not in ("row", "var"):
        raise ValueError("axis must be row or var")
    from mipframe.ops.matrix import _axis
    from mipframe.frame.entity import RowFrame
    from mipframe.frame.entity import VarFrame

    rows = _axis(owner, rows, RowFrame)
    variables = _axis(owner, vars, VarFrame)
    sources, target = (rows, variables) if axis == "row" else (variables, rows)
    schema = {axis: ref_type(owner, axis), "nnz": DType("int")}

    def scan(fields, ctx):
        import numpy as np
        from mipframe.store.incidence import Target

        m = owner.model
        all_target = isinstance(target.ids, range) and target.ids == range(
            len(m.variables if axis == "row" else m.rows)
        )
        ptr = m.row_ptr if axis == "row" else m.col_ptr
        opposite = 'var' if axis == 'row' else 'row'
        block = max(1, ctx.memory // 128)
        source_ids, target_ids = sources.ids, target.ids
        if 'nnz' in fields and not all_target:
            from mipframe.frame.base import PlanIDs
            from mipframe.store.query_store import SQLIndex

            def prepared_ids(frame, name, ids):
                if isinstance(ids, PlanIDs):
                    table, _ = ctx.stage(frame, (name,))
                    return SQLIndex(ctx, f'SELECT c0 AS id FROM {table} ORDER BY ordinal',
                                    unique=True)
                return ids

            source_ids = prepared_ids(sources, axis, source_ids)
            target_ids = prepared_ids(target, opposite, target_ids)
            # A small opposite selection (e.g. 200 columns with four entries
            # each) can be much cheaper than scanning 950 full 200-term rows.
            # Keep the selected counters bounded, and compare actual degrees.
            ns = len(source_ids)
            if ns <= ctx.memory // 256 and len(target_ids) < ns:
                counts = dict.fromkeys(source_ids, 0)
                opposite_ptr = m.col_ptr if axis == 'row' else m.row_ptr
                source_cost = sum(int(ptr[i + 1]) - int(ptr[i]) for i in counts)
                target_cost = sum(int(opposite_ptr[i + 1]) - int(opposite_ptr[i])
                                  for i in target_ids)
                if target_cost < source_cost:
                    for ident in target_ids:
                        for ids, _ in m.sparse.blocks(opposite, ident, block):
                            for i in ids:
                                i = int(i)
                                if i in counts:
                                    counts[i] += 1
                    for ident, total in counts.items():
                        yield {n: ident if n == axis else total for n in fields}
                    return
        selected = Target(owner, opposite, target) if not all_target else None
        fast_mask = selected is not None and (selected.colors is not None
                                             or isinstance(selected.ids, range))
        for source in source_ids:
            total = 0
            if "nnz" in fields:
                start, end = int(ptr[source]), int(ptr[source + 1])
                if all_target:
                    total = end - start
                else:
                    for begin in range(start, end, block):
                        stop = min(end, begin + block)
                        ids = (
                            m.col_idx[begin:stop]
                            if axis == "row"
                            else np.searchsorted(
                                m.row_ptr, m.col_positions[begin:stop], side="right"
                            )
                            - 1
                        )
                        total += (int(np.count_nonzero(selected.mask(ids))) if fast_mask
                                  else sum(ctx.contains(target, int(i)) for i in ids))
            yield {n: source if n == axis else total for n in fields}

    result = Frame(
        schema, key=(axis,), models=(owner,), source=scan, domains={axis: sources}
    )
    result._source_spec = {
        "kind": "sparse_counts",
        "uid": owner.uid,
        "version": owner.version,
        "rows": rows,
        "vars": variables,
        "axis": axis,
    }
    return result


def pattern_counts(source, *, depth=None, memory_mb=128):
    from mipframe.store.incidence import IncidenceCollection
    from mipframe.check.report import family_counts
    from types import MappingProxyType

    old = family_counts(IncidenceCollection(source), depth, memory_mb, patterns=True)
    store = old._store
    directory = sql_frame(
        store,
        f"SELECT node AS pattern,count FROM ({old._query})",
        dict(pattern=DType("int"), count=DType("int")),
        key=("pattern",),
    )
    members = sql_frame(
        store,
        f"SELECT id AS {source._entity_axis},node AS pattern FROM {old._members} ORDER BY ordinal",
        {
            source._entity_axis: ref_type(source._owner, source._entity_axis),
            "pattern": DType("int"),
        },
        key=(source._entity_axis,),
    )
    prefix = "F" if source._entity_axis == "var" else "V"
    items = sql_frame(
        store,
        f"""WITH RECURSIVE chain(pattern,node,parent,color,n) AS (
        SELECT DISTINCT m.node,n.node,n.parent,n.color,n.n FROM {old._members} m JOIN {old._nodes} n ON n.node=m.node
        UNION ALL SELECT c.pattern,n.node,n.parent,n.color,n.n FROM chain c JOIN {old._nodes} n ON n.node=c.parent
    ) SELECT pattern,'{prefix}'||color||'@{old.depth}' AS family,n AS count FROM chain ORDER BY pattern,color""",
        dict(pattern=DType("int"), family=DType("str"), count=DType("int")),
        key=("pattern", "family"),
    )
    directory._relations = MappingProxyType(dict(members=members, items=items))
    return directory


def membership(source, depth=None, signatures=None):
    from mipframe.store.incidence import default_depth

    owner = source._owner
    axis = source._entity_axis
    result = owner.ir._result if signatures is None else signatures
    if result._store.owner is not owner:
        raise ValueError("Signatures belong to another live model")
    depth = default_depth(owner, axis) if depth is None else depth
    result._check_depth(depth)
    colors = result._store.array(result._config, axis, depth)
    families = (
        result._row_families(depth) if axis == "row" else result._var_families(depth)
    )
    schema = {axis: ref_type(owner, axis), "family": DType("str")}

    def scan(fields, ctx):
        from functools import lru_cache

        @lru_cache(maxsize=512)
        def label(color):
            ordinal = (
                families._store.db.execute(
                    "SELECT position FROM families WHERE color=?", (color,)
                ).fetchone()[0]
                if axis == "row"
                else color
            )
            return f"{'F' if axis == 'row' else 'V'}{ordinal}@{depth}"

        for ident in source.ids:
            yield {n: ident if n == axis else label(int(colors[ident])) for n in fields}

    frame = Frame(schema, key=(axis,), models=(owner,), source=scan)
    frame._source_spec = {
        "kind": "membership",
        "uid": owner.uid,
        "version": owner.version,
        "source": source,
        "config": result.config,
        "depth": depth,
    }
    return frame


def family_directory(catalog):
    """Scalar family directory; membership is a separate normalized relation."""
    from types import MappingProxyType

    owner = catalog._owner
    axis = getattr(catalog._store, "kind", "row")
    schema = dict(
        id=DType("str"),
        count=DType("int"),
        depth=DType("int", True),
        mode=DType("str", True),
        nnz_min=DType("int"),
        nnz_max=DType("int"),
    )

    def family(position):
        return catalog.loc[f"F{position}"]

    def scan(fields, ctx):
        for position in catalog.ids:
            f = family(position)
            sig = f.signature
            nnz = f.nnz if hasattr(f, "nnz") else sig.nnz
            lo, hi = nnz if isinstance(nnz, tuple) else (nnz, nnz)
            raw = dict(
                id=f.id,
                count=f.count,
                depth=getattr(sig, "depth", None),
                mode=getattr(sig, "mode", None),
                nnz_min=lo,
                nnz_max=hi,
            )
            yield {n: raw[n] for n in fields}

    directory = Frame(
        schema, key=("id",), models=(owner,), source=scan, known_count=len(catalog)
    )

    def members(fields, ctx):
        for position in catalog.ids:
            f = family(position)
            for ident in (f.rows if axis == "row" else f.vars).ids:
                yield {n: ident if n == axis else f.id for n in fields}

    member_frame = Frame(
        {axis: ref_type(owner, axis), "family": DType("str")},
        key=(axis,),
        models=(owner,),
        source=members,
    )
    directory._relations = MappingProxyType({"members": member_frame})
    directory._source_spec = {
        "kind": "relation",
        "relation": f"family:{id(catalog._store)}",
    }
    member_frame._source_spec = {
        "kind": "relation",
        "relation": f"family-members:{id(catalog._store)}",
    }
    return directory


def color_frame(result, axis):
    owner = result._store.owner
    schema = {
        axis: ref_type(owner, axis),
        "name": DType("str"),
        **{
            f"{'R' if axis == 'row' else 'V'}{d}": DType("int")
            for d in range(result.depth + 1)
        },
    }

    def scan(fields, ctx):
        from mipframe.frame.sources import entity_scan

        source = owner.rows if axis == "row" else owner.vars
        basics = tuple(n for n in fields if n in (axis, "name"))
        color_fields = {
            n: result._store.array(result._config, axis, int(n[1:]))
            for n in fields
            if n not in (axis, "name")
        }
        for ident, raw in enumerate(entity_scan(source, basics, ctx)):
            yield {
                n: raw[n] if n in basics else int(color_fields[n][ident])
                for n in fields
            }

    frame = Frame(schema, key=(axis,), models=(owner,), source=scan)
    frame._source_spec = {
        "kind": "relation",
        "relation": f"colors:{owner.uid}:{result._key}:{axis}:{result.depth}",
    }
    return frame


def family_parameters(family):
    """Canonical signed-role observations, with normalized exact histograms."""
    from types import MappingProxyType

    owner = family._store.owner
    parameters = family._parameters
    schema = dict(
        role=DType("str"),
        basis=DType("str"),
        minimum=DType("number", True),
        maximum=DType("number", True),
        distinct=DType("int"),
        varying=DType("bool"),
        multiplicity_min=DType("number", True),
        multiplicity_max=DType("number", True),
    )

    def distributions():
        for role in parameters.profile.roles:
            yield parameters._key(role), role.coefficients, role.multiplicity
        yield "lb", parameters.profile.lhs, None
        yield "ub", parameters.profile.rhs, None

    def scan(fields, ctx):
        for label, dist, mult in distributions():
            values = {"role": label, "basis": "canonical"}
            for n in fields:
                if n == "minimum":
                    values[n] = dist.min
                elif n == "maximum":
                    values[n] = dist.max
                elif n == "distinct":
                    values[n] = dist.distinct
                elif n == "varying":
                    values[n] = dist.varying
                elif n == "multiplicity_min":
                    values[n] = None if mult is None else mult.min
                elif n == "multiplicity_max":
                    values[n] = None if mult is None else mult.max
            yield {n: values[n] for n in fields}

    frame = Frame(schema, key=("role",), models=(owner,), source=scan)
    hs = dict(role=DType("str"), value=DType("number"), count=DType("int"))

    def histogram(fields, ctx):
        for label, dist, _ in distributions():
            for value, n in dist.items():
                raw = dict(role=label, value=value, count=n)
                yield {k: raw[k] for k in fields}

    h = Frame(hs, key=("role", "value"), models=(owner,), source=histogram)
    frame._relations = MappingProxyType({"histogram": h})
    for f, name in ((frame, "parameters"), (h, "histogram")):
        f._source_spec = {
            "kind": "relation",
            "relation": f"{name}:{owner.uid}:{family._store.result._key}:{family.depth}:{family.id}",
        }
    return frame


def signature_frame(rows, signature):
    from mipframe.ir.algebraic import signature_method
    from types import MappingProxyType

    owner = rows._owner
    method = signature_method(signature)
    schema = dict(
        row=ref_type(owner, "row"),
        name=DType("str"),
        nnz=DType("int"),
        relation=DType("str"),
        lb=DType("number"),
        ub=DType("number"),
        scale=DType("number"),
    )

    def scan(fields, ctx):
        for ident in rows.ids:
            raw = {"row": ident}
            if "name" in fields:
                raw["name"] = owner.model.rows[ident][1]
            if set(fields) - {"row", "name"}:
                sig = method(owner, ident)
                raw.update(
                    nnz=sig.nnz,
                    relation=sig.sense,
                    lb=-float("inf") if sig.lhs is None else sig.lhs,
                    ub=float("inf") if sig.rhs is None else sig.rhs,
                    scale=sig.scale,
                )
            yield {n: raw[n] for n in fields}

    frame = Frame(schema, key=("row",), models=rows._models, source=scan)
    ps = dict(
        row=ref_type(owner, "row"),
        coef=DType("number"),
        kind=DType("str", True),
        bounds=DType("str", True),
        count=DType("int"),
    )

    def pattern(fields, ctx):
        for ident in rows.ids:
            sig = method(owner, ident)
            for token, n in sig.pattern:
                coef, kind, bounds = (
                    (token, None, None) if isinstance(token, Fraction) else token
                )
                raw = dict(row=ident, coef=coef, kind=kind, bounds=bounds, count=n)
                yield {k: raw[k] for k in fields}

    detail = Frame(
        ps, key=("row", "coef", "kind", "bounds"), models=rows._models, source=pattern
    )
    frame._relations = MappingProxyType({"pattern": detail})
    for f, part in ((frame, "summary"), (detail, "pattern")):
        f._source_spec = {
            "kind": "relation",
            "relation": f"signature:{owner.uid}:{signature}:{id(rows)}:{part}",
        }
    return frame
