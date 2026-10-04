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
    result._query.resource = store
    result._query.spec = {
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
    result._query.spec = {
        "kind": "sparse_counts",
        "uid": owner.uid,
        "version": owner.version,
        "rows": rows,
        "vars": variables,
        "axis": axis,
    }
    return result


def pattern_counts(source, *, depth=None, memory_mb=128):
    from mipframe.ir.patterns import build_patterns
    from types import MappingProxyType

    old = build_patterns(source, depth, memory_mb)
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
    from mipframe.ir.signatures import refinement
    from mipframe.analysis import _Families
    owner, axis = source._owner, source._entity_axis
    depth = 2 if depth is None else depth
    result = refinement(owner,depth=depth) if signatures is None else signatures
    if result._store.owner is not owner: raise ValueError('Refinement belongs to another model')
    method = 'template' if result.mode == 'template' else 'exact_structural'
    provider = _Families(source,method,depth,result)
    directory = provider.directory()
    members = directory.related('members')
    def scan(fields, ctx):
        for row in members._raw((axis,'family'),ctx):
            raw = {axis:row[axis],'family':f'{row["family"]}@{depth}'}
            yield {n:raw[n] for n in fields}
    frame = Frame({axis:ref_type(owner,axis),'family':DType('str')},key=(axis,),models=(owner,),source=scan)
    frame._query.spec = dict(kind='membership',uid=owner.uid,version=owner.version,
        source=source,config=result.config,depth=depth)
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
        signature_id=DType("str"),
        method=DType("str"),
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
                    signature_id=__import__("hashlib").sha256(sig.key().encode()).hexdigest(),
                    method="algebraic" if signature=="algebratic" else signature,
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
        f._query.spec = {
            "kind": "relation",
            "relation": f"signature:{owner.uid}:{signature}:{id(rows)}:{part}",
        }
    return frame
