"""Complete relational checks, declared domains and strict sparse projection."""

from math import prod
import json
from mipframe.frame.base import Frame
from mipframe.frame.base import merge_models
from mipframe.frame.schema import DType
from mipframe.frame.schema import SchemaError
from mipframe.frame.schema import ref_type
from mipframe.frame.executor import Context
from mipframe.frame.executor import pack
from mipframe.frame.expr import col


from mipframe.frame.materialized import stored_frame


def report(ctx, rows, schema, summary, *, relations=None):
    from mipframe.check.report import ValidationReport

    table = ctx.table(schema)
    ctx.append(table, schema, rows)
    total = ctx.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    violations = stored_frame(ctx, table, schema, relations=relations)
    return ValidationReport(
        ctx, total == 0, dict(summary, violations=total), violations
    )


def join_failure(frame, ctx, lt, ls, rt, rs, pairs, card, require, match):
    from mipframe.check.report import CardinalityError

    schema = {
        "side": DType("str"),
        "reason": DType("str"),
        "key": DType("str"),
        "count": DType("int"),
    }

    def rows():
        for side, table, s, keys, unique in [
            ("left", lt, ls, tuple(pairs), card in ("1:1", "1:m")),
            ("right", rt, rs, tuple(pairs.values()), card in ("1:1", "m:1")),
        ]:
            names = tuple(s)
            columns = ",".join(f"c{names.index(n)}" for n in keys)
            if unique:
                for values in ctx.execute(
                    f"SELECT {columns},count(*) FROM {table} GROUP BY {columns} HAVING count(*)>1"
                ):
                    yield dict(
                        side=side,
                        reason="duplicate key",
                        key=json.dumps(values[:-1]),
                        count=values[-1],
                    )
            if require in (side, "both"):
                alias, other, oa = ("l", rt, "r") if side == "left" else ("r", lt, "l")
                selected = ",".join(f"{alias}.c{names.index(n)}" for n in keys)
                for values in ctx.execute(
                    f"SELECT {selected} FROM {table} {alias} WHERE NOT EXISTS(SELECT 1 FROM {other} {oa} WHERE {match}) ORDER BY {alias}.ordinal"
                ):
                    yield dict(
                        side=side, reason="unmatched", key=json.dumps(values), count=1
                    )

    checked = report(
        ctx,
        rows(),
        schema,
        dict(
            operation="join",
            scope="full declared inputs",
            cardinality=card,
            require=require,
        ),
        relations={"left": frame._parents[0], "right": frame._parents[1]},
    )
    checked._recipe = dict(kind="execution", frame_object=frame)
    return CardinalityError("Join cardinality or match requirement failed", checked)


def project(source, bindings, *, exactly_one=True, memory_mb=128):
    from mipframe.store.incidence import Target
    from mipframe.store.incidence import IncidenceCollection
    from mipframe.store.query_store import QueryStore
    from mipframe.store.query_store import memory_bytes
    from mipframe.check.report import CardinalityError
    from mipframe.check.report import ValidationReport
    from mipframe.frame.adapters import sql_frame

    memory_bytes(memory_mb)
    if exactly_one is not True:
        raise ValueError("Only exactly_one=True is supported")
    if not bindings:
        raise ValueError("Specify at least one projection column")
    axis = source._entity_axis
    opposite = "var" if axis == "row" else "row"
    reserved = {"source", "source_id", axis, "to_record"}
    if any(
        not n.isidentifier() or n.startswith("_") or n in reserved for n in bindings
    ):
        raise SchemaError(
            "Projection columns must be distinct public names without identity/method conflicts"
        )
    targets = {
        name: Target(source._owner, opposite, value).frame
        for name, value in bindings.items()
    }
    schema = {
        axis: ref_type(source._owner, axis),
        **{n: ref_type(source._owner, opposite) for n in targets},
    }
    state = {}

    def prepare():
        if "error" in state:
            raise state["error"]
        if "store" in state:
            return
        store = QueryStore(source._owner, memory_mb)
        try:
            prepared = {
                n: Target(source._owner, opposite, f).prepared(store)
                for n, f in targets.items()
            }
            width = len(prepared)
            if width * 64 > store.memory // 2:
                raise SchemaError("Projection width exceeds working-memory budget")
            table = store.table(
                "ordinal INTEGER PRIMARY KEY, source_id INTEGER UNIQUE,"
                + ",".join(f"c{i} INTEGER" for i in range(width))
            )
            bad = store.table(
                "ordinal INTEGER PRIMARY KEY, source_id INTEGER, column_name TEXT, count INTEGER"
            )
            relation = IncidenceCollection(source)
            block = max(1, store.memory // max(256, 64 * width))
            source_count = 0
            for ident in source.ids:
                source_count += 1
                counts, values = [0] * width, [None] * width
                for ids, _ in relation._blocks(ident, block):
                    for i, target in enumerate(prepared.values()):
                        matched = ids[target.mask(ids)]
                        counts[i] += len(matched)
                        if len(matched):
                            values[i] = int(matched[0])
                for n, count in zip(prepared, counts):
                    if count != 1:
                        store.execute(
                            f"INSERT INTO {bad}(source_id,column_name,count) VALUES(?,?,?)",
                            (int(ident), n, count),
                        )
                store.execute(
                    f"INSERT INTO {table} VALUES({','.join('?' for _ in range(width + 2))})",
                    (None, int(ident), *values),
                )
            number = store.execute(f"SELECT count(*) FROM {bad}").fetchone()[0]
            if number:
                violation_schema = {
                    "source": ref_type(source._owner, axis),
                    "column": DType("str"),
                    "count": DType("int"),
                }
                violations = sql_frame(
                    store,
                    f"SELECT source_id AS source,column_name AS column,count FROM {bad} ORDER BY ordinal",
                    violation_schema,
                    key=("source", "column"),
                )
                match_schema = {
                    "source": ref_type(source._owner, axis),
                    "column": DType("str"),
                    "row": ref_type(source._owner, "row"),
                    "var": ref_type(source._owner, "var"),
                    "coef": DType("number"),
                }

                def matches(fields, ctx):
                    m = source._owner.model
                    for ident, name in store.execute(
                        f"SELECT source_id,column_name FROM {bad} ORDER BY ordinal"
                    ):
                        target = prepared[name]
                        for ids, positions in relation._blocks(ident, block):
                            keep = target.mask(ids)
                            for other, pos in zip(ids[keep], positions[keep]):
                                values = {
                                    "source": ident,
                                    "column": name,
                                    "row": ident if axis == "row" else int(other),
                                    "var": int(other) if axis == "row" else ident,
                                }
                                if "coef" in fields:
                                    values["coef"] = m.num(int(m.coeff_id[pos]))
                                yield {n: values[n] for n in fields}

                match_frame = Frame(
                    match_schema,
                    models=merge_models(source, *targets.values()),
                    source=matches,
                )
                match_frame._resource = store
                violations._relations = __import__("types").MappingProxyType(
                    {"matches": match_frame, "sources": source}
                )
                checked = ValidationReport(
                    store,
                    False,
                    dict(
                        operation="exactly_one",
                        scope="full declared source selection",
                        checked_sources=source_count,
                        violations=number,
                    ),
                    violations,
                )
                checked._recipe = dict(kind="execution", frame_object=result)
                error = CardinalityError(
                    f"{number} projection cardinality violations; inspect exception.report.violations",
                    checked,
                )
                from mipframe._help.diagnostics import hint
                hint(error, "recipe.coordinates")
                state["error"] = error
                raise error
            state.update(store=store, table=table)
        except CardinalityError:
            raise
        except BaseException:
            store.close()
            raise

    def scan(fields, ctx):
        prepare()
        store = state["store"]
        table = state["table"]
        expressions = {
            axis: "source_id",
            **{n: "c" + str(i) for i, n in enumerate(targets)},
        }
        query = f"SELECT {','.join(expressions[n] for n in fields) or '1'} FROM {table} ORDER BY ordinal"
        for row in store.execute(query):
            yield dict(zip(fields, row))

    result = Frame(
        schema,
        key=(axis,),
        models=merge_models(source, *targets.values()),
        source=scan,
        domains=targets,
        relations={"sources": source},
    )
    result._source_spec = {"kind": "project", "source": source, "bindings": targets}
    result._params = {"barrier": True}
    return result


def check(frame, kind, columns=None, *, key=None, value=None, memory_mb=None):
    if kind not in ("unique", "product", "function"):
        raise ValueError("Unknown check; use unique, product or function")

    def names(v):
        ns = (v,) if isinstance(v, str) else tuple(v or ())
        if not ns or len(ns) != len(set(ns)) or not set(ns) <= set(frame.schema):
            raise SchemaError("Check requires distinct existing columns")
        return ns

    left = names(key) if kind == "function" else names(columns)
    right = names(value) if kind == "function" else ()
    selected = tuple(dict.fromkeys((*left, *right)))
    if kind == "product" and any(n not in frame.domains for n in left):
        raise SchemaError("Product checks require declared target domains")
    ctx = Context(frame._models, memory_mb)
    try:
        table, schema = ctx.stage(frame, selected)
        allnames = tuple(schema)
        cols = lambda ns: ",".join(f"c{allnames.index(n)}" for n in ns)
        count = ctx.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        lcols = cols(left)
        unique = ctx.execute(
            f"SELECT count(*) FROM (SELECT {lcols} FROM {table} GROUP BY {lcols})"
        ).fetchone()[0]
        summary = dict(
            operation=kind,
            scope="full current Frame selection",
            checked_records=count,
            columns=left,
        )
        violation_schema = {
            "reason": DType("str"),
            "key": DType("str"),
            "count": DType("int"),
        }

        def violations():
            if kind == "function":
                both = cols(selected)
                inner = f"SELECT {both} FROM {table} GROUP BY {both}"
                q = f"SELECT {lcols},count(*) FROM ({inner}) GROUP BY {lcols} HAVING count(*)>1"
            else:
                q = f"SELECT {lcols},count(*) FROM {table} GROUP BY {lcols} HAVING count(*)>1"
            for row in ctx.execute(q):
                yield {
                    "reason": "function violation"
                    if kind == "function"
                    else "duplicate coordinate",
                    "key": json.dumps(row[:-1]),
                    "count": row[-1],
                }
            if kind == "product":
                expected = prod(len(frame.domains[n]) for n in left)
                # Ref domains remain authoritative after filtering and renaming.
                foreign = 0
                valid_schema = {n: schema[n] for n in left}
                valid = ctx.table(valid_schema)
                for row in ctx.rows(
                    f"SELECT {lcols} FROM {table}", {n: schema[n] for n in left}
                ):
                    if any(
                        row[n] is None or not ctx.contains(frame.domains[n], row[n])
                        for n in left
                    ):
                        foreign += 1
                    else:
                        ctx.append(valid, valid_schema, (row,))
                keys = ",".join("c" + str(i) for i in range(len(left)))
                covered = ctx.execute(
                    f"SELECT count(*) FROM (SELECT {keys} FROM {valid} GROUP BY {keys})"
                ).fetchone()[0]
                missing = expected - covered
                summary.update(
                    expected_coordinates=expected,
                    missing_coordinates=missing,
                    outside_domain_records=foreign,
                )
                if foreign:
                    yield dict(reason="outside declared domain", key="", count=foreign)
                if missing:
                    yield dict(reason="missing coordinates", key="", count=missing)

        if kind != "function":
            summary.update(unique_coordinates=unique, duplicate_records=count - unique)
        else:
            summary.update(
                key=left,
                value=right,
                uncovered_targets={
                    n: len(frame.domains[n])
                    - ctx.execute(
                        f"SELECT count(DISTINCT c{allnames.index(n)}) FROM {table}"
                    ).fetchone()[0]
                    for n in selected
                    if n in frame.domains
                },
            )
        result = report(
            ctx, violations(), violation_schema, summary, relations={"records": frame}
        )
        result._recipe = {"kind": "frame", "check": kind, "columns": columns, "key": key, "value": value, "frame_object": frame}
        if result.ok and kind in ("unique", "product"):
            frame._verified_unique.add(left)
        return result
    except BaseException:
        if not ctx.retained:
            ctx.close()
        raise
