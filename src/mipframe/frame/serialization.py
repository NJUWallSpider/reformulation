"""Versioned query specifications and atomic, exact streaming exports."""

from pathlib import Path
from tempfile import NamedTemporaryFile
import json
import os
import uuid
from mipframe.frame.schema import DType
from mipframe.frame.schema import Ref
from mipframe.frame.schema import SchemaError
from mipframe.frame.schema import encode
from mipframe.frame.schema import decode
from mipframe.frame.schema import normalize
from mipframe.frame.expr import QueryExpr
from mipframe.frame.expr import col
from mipframe.frame.expr import literal
from mipframe.frame.expr import Aggregate
from mipframe.frame.base import Frame
from mipframe.frame.executor import Context
from mipframe.frame.materialized import stored_frame


def from_records(records, *, schema, key=None, models=(), memory_mb=None):
    """
    MIPFRAME HELP BEGIN
    Frame.from_records [api.Frame.from_records]

    Create a typed relation from explicit records.

    Frame.from_records(records, *, schema, key=None, models=(), memory_mb=None)

    When to use: Create a typed relation from explicit records.

    Inputs: Records plus declared schema; optional key/models and memory_mb.

    Returns: Ordinary Frame. Ref identity must be valid; this does not create a Model.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.Frame.schema, concept.frame
    MIPFRAME HELP END
    """
    if isinstance(models, dict):
        models = tuple(models.values())
    schema = {n: t if isinstance(t, DType) else DType(t) for n, t in schema.items()}
    ctx = Context(models, memory_mb)
    try:
        table = ctx.table(schema)

        def rows():
            for record in records:
                if set(record) != set(schema):
                    raise SchemaError("Record columns must match the declared schema")
                row = {n: normalize(record[n], t) for n, t in schema.items()}
                for n, t in schema.items():
                    if t.kind == "ref" and row[n] is not None:
                        for model in models:
                            if model.uid == t.uid:
                                if model.version != t.version:
                                    raise SchemaError("Reference version mismatch")
                                total = len(
                                    model.model.rows
                                    if t.axis == "row"
                                    else model.model.variables
                                )
                                if row[n] >= total:
                                    raise SchemaError(
                                        "Reference ID outside the declared model axis"
                                    )
                yield row

        ctx.append(table, schema, rows())
        if key is not None:
            key = (key,) if isinstance(key, str) else tuple(key)
            if not set(key) <= set(schema):
                raise SchemaError("Unknown key column")
            columns = ",".join("c" + str(tuple(schema).index(n)) for n in key)
            query = (
                f"SELECT 1 FROM {table} GROUP BY {columns} HAVING count(*)>1 LIMIT 1"
                if columns
                else f"SELECT 1 FROM {table} LIMIT 1 OFFSET 1"
            )
            if ctx.execute(query).fetchone():
                raise SchemaError("Declared key is not unique")
        result = stored_frame(ctx, table, schema, key=key)
        result._known_count = ctx.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        result._source_spec = {"kind": "relation", "relation": str(uuid.uuid4())}
        return result
    except BaseException:
        ctx.close()
        raise


def to_spec(frame):
    frame._guard()

    def node(f):
        if f._op == "source":
            source = dict(getattr(f, "_source_spec", {}))
            if not source:
                raise SchemaError(
                    "This analysis relation must be supplied as an explicit relation binding"
                )
            kind = source["kind"]
            if kind == "algorithm_relation":
                source["source"] = node(source["source"])
            if kind == "algorithm":
                from mipframe.ops._runtime import recipe_arguments

                source["arguments"] = recipe_arguments(source["arguments"], node)
            if kind in ("row", "var"):
                ids = f._index
                from mipframe.frame.base import PlanIDs

                if isinstance(ids, PlanIDs):
                    return node(ids.frame)
                source["ids"] = (
                    {"range": [ids.start, ids.stop, ids.step]}
                    if isinstance(ids, range)
                    else list(map(int, ids))
                )
            if kind == "terms":
                source["rows"] = None if f._rows is None else node(f._rows)
                source["vars"] = None if f._vars is None else node(f._vars)
            if kind == "relation":
                source["schema"] = {n: t.to_spec() for n, t in f.schema.items()}
            if kind == "entity_set":
                source["left"] = node(source["left"])
                source["right"] = node(source["right"])
            if kind == "sparse_counts":
                source["rows"] = node(source["rows"])
                source["vars"] = node(source["vars"])
            if kind in ("membership", "entity_refs"):
                source["source"] = node(source["source"])
            if kind == "project":
                source["source"] = node(source["source"])
                source["bindings"] = {n: node(v) for n, v in source["bindings"].items()}
            return {"op": "scan", **source}
        result = {"op": f._op, "inputs": [node(p) for p in f._parents]}
        p = f._params
        if f._op == "filter":
            result["predicates"] = [e.to_spec() for e in p["predicates"]]
        elif f._op == "select":
            result["expressions"] = {
                n: e.to_spec() for n, e in p["expressions"].items()
            }
        elif f._op == "group":
            result.update(
                keys=p["keys"],
                aggregates={
                    n: {
                        "op": a.op,
                        "expr": None if a.expr is None else a.expr.to_spec(),
                    }
                    for n, a in p["aggregates"].items()
                },
            )
        elif f._op == "slice":
            result["slice"] = [p["slice"].start, p["slice"].stop, p["slice"].step]
        else:
            result.update(p)
        return result

    return {"version": 1, "plan": node(frame)}


def frame_from_spec(spec, *, models, relations=None):
    """
    MIPFRAME HELP BEGIN
    frame_from_spec [api.frame_from_spec]

    Restore an exact relation with explicit snapshot/dependency bindings.

    frame_from_spec(spec, *, models, relations=None)

    When to use: Restore an exact relation with explicit snapshot/dependency bindings.

    Returns: Frame; externally claimed uniqueness is not trusted.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity, api.Frame.write
    MIPFRAME HELP END
    """
    if (
        not isinstance(spec, dict)
        or set(spec) != {"version", "plan"}
        or spec["version"] != 1
    ):
        raise SchemaError("Unsupported Frame query specification")
    models = dict(models)
    relations = relations or {}

    def expression(e, depth):
        if depth > 64:
            raise SchemaError("Query specification is too deep")
        op = e["op"]
        if op == "col":
            return col(e["name"])
        if op == "literal":
            return literal(decode(e["value"]))
        if op == "isin_frame":
            return expression(e["input"], depth + 1).isin(
                frame_from_spec(e["frame"], models=models, relations=relations)
            )
        if op == "isin":
            return QueryExpr(
                op,
                (
                    expression(e["input"], depth + 1),
                    tuple(expression(v, depth + 1) for v in e["values"]),
                ),
            )
        if op not in (
            "eq",
            "ne",
            "lt",
            "le",
            "gt",
            "ge",
            "add",
            "sub",
            "mul",
            "div",
            "and",
            "or",
            "not",
            "neg",
            "abs",
            "is_null",
        ):
            raise SchemaError("Unknown query expression")
        return QueryExpr(op, tuple(expression(a, depth + 1) for a in e["args"]))

    def build(n, depth=0):
        if depth > 64:
            raise SchemaError("Query specification is too deep")
        op = n["op"]
        if op == "scan":
            kind = n["kind"]
            if kind == "algorithm_relation":
                return build(n["source"], depth + 1).relations[n["name"]]
            if kind == "algorithm":
                from mipframe.ops._runtime import restore

                return restore(n, lambda child: build(child, depth + 1))
            if kind == "relation":
                if n["relation"] not in relations:
                    raise SchemaError("Missing explicit relation binding")
                relation = relations[n["relation"]]
                if dict(relation.schema) != {
                    k: DType(**v) for k, v in n["schema"].items()
                }:
                    raise SchemaError("Relation schema or snapshot binding mismatch")
                return relation
            if kind == "entity_refs":
                return build(n["source"], depth + 1).refs(n["column"])
            if kind == "entity_set":
                from mipframe.ops.support import set_operation

                return set_operation(
                    build(n["left"], depth + 1),
                    build(n["right"], depth + 1),
                    n["operation"],
                )
            if kind == "sparse_counts":
                from mipframe.frame.adapters import sparse_counts

                m = models[n["uid"]]
                if m.version != n["version"]:
                    raise SchemaError("Model version mismatch")
                return sparse_counts(
                    m,
                    build(n["rows"], depth + 1),
                    build(n["vars"], depth + 1),
                    n["axis"],
                )
            if kind == "project":
                return build(n["source"], depth + 1).project(
                    **{k: build(v, depth + 1) for k, v in n["bindings"].items()}
                )
            if n.get("uid") not in models:
                raise SchemaError("Missing model binding")
            m = models[n["uid"]]
            if (m.uid, m.version) != (n["uid"], n["version"]):
                raise SchemaError("Model binding identity/version mismatch")
            if kind in ("row", "var"):
                ids = n["ids"]
                ids = range(*ids["range"]) if isinstance(ids, dict) else ids
                total = m.shape[0 if kind == "row" else 1]
                if any(type(i) is not int or i < 0 or i >= total for i in ids):
                    raise SchemaError("Invalid model IDs")
                return (m.rows if kind == "row" else m.vars)._select(ids)
            if kind == "membership":
                from mipframe.frame.adapters import membership
                from fractions import Fraction

                config = n["config"]
                args = {
                    k: config[k] for k in ("mode", "coefficient", "rhs", "objective")
                }
                if config.get("multiplicity_ratio") is not None:
                    args["multiplicity_ratio"] = Fraction(config["multiplicity_ratio"])
                sig = m.structure.signatures(depth=n["depth"], **args)
                if sig.config != config:
                    raise SchemaError("Incompatible signature configuration")
                return membership(build(n["source"], depth + 1), n["depth"], sig)
            if kind == "terms":
                return m.terms(
                    rows=None if n["rows"] is None else build(n["rows"], depth + 1),
                    vars=None if n["vars"] is None else build(n["vars"], depth + 1),
                )
            raise SchemaError("Unknown scan source")
        inputs = [build(p, depth + 1) for p in n["inputs"]]
        f = inputs[0]
        if op == "filter":
            return f.where(*(expression(e, depth + 1) for e in n["predicates"]))
        if op == "select":
            return f.select(
                **{k: expression(e, depth + 1) for k, e in n["expressions"].items()}
            )
        if op == "join":
            return f.join(
                inputs[1],
                on=n["on"],
                how=n["how"],
                cardinality=n["cardinality"],
                require=n["require"],
            )
        if op == "group":
            aggregates = {}
            for k, a in n["aggregates"].items():
                if a["op"] not in ("count", "sum", "min", "max", "nunique"):
                    raise SchemaError("Unknown aggregate")
                aggregates[k] = Aggregate(
                    a["op"],
                    None if a["expr"] is None else expression(a["expr"], depth + 1),
                )
            return f.group_by(*n["keys"]).agg(**aggregates)
        if op == "distinct":
            return f.distinct()
        if op == "sort":
            return f.sort_by(*n["by"], descending=n["descending"])
        if op == "sample":
            return f.sample(n["n"], random_state=n["seed"])
        if op == "slice":
            return f.iloc[slice(*n["slice"])]
        if op == "limit":
            return f.iloc[slice(n["start"], n["stop"], n["step"])]
        raise SchemaError("Unknown query operation")

    return build(spec["plan"])


def write(frame, path, *, format=None):
    path = Path(path)
    fmt = format or path.suffix.lstrip(".")
    if fmt not in ("jsonl", "parquet"):
        raise ValueError("Use jsonl or parquet")
    if path.exists():
        raise FileExistsError(path)
    metadata = {
        "version": 1,
        "schema": {n: t.to_spec() for n, t in frame.schema.items()},
        "key": frame.key,
        "search_provenance": frame._search_runs(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = NamedTemporaryFile(
        prefix="." + path.name + ".", dir=path.parent, delete=False
    )
    temporary = Path(temp.name)
    temp.close()
    try:
        if fmt == "jsonl":
            with temporary.open("w") as out:
                out.write(json.dumps({"mipframe": metadata}) + "\n")
                for batch in frame.iter_batches():
                    for row in batch.to_records():
                        out.write(
                            json.dumps({n: encode(v) for n, v in row.items()}) + "\n"
                        )
        else:
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError as e:
                raise ImportError("Parquet export requires mipframe[parquet]") from e
            fields = []
            for n, t in frame.schema.items():
                typ = (
                    pa.struct(
                        [
                            ("numerator", pa.string()),
                            ("denominator", pa.string()),
                            ("infinity", pa.int8()),
                        ]
                    )
                    if t.kind == "number"
                    else (
                        pa.string()
                        if t.kind in ("str", "int", "enum")
                        else pa.bool_() if t.kind == "bool" else pa.int64()
                    )
                )
                fields.append(pa.field(n, typ, nullable=t.nullable))
            schema = pa.schema(
                fields, metadata={b"mipframe": json.dumps(metadata).encode()}
            )
            with pq.ParquetWriter(temporary, schema) as writer:
                for batch in frame.iter_batches():
                    values = []
                    for raw in batch.to_records():
                        row = {}
                        for n, v in raw.items():
                            t = frame.schema[n]
                            if v is None:
                                row[n] = None
                            elif t.kind == "ref":
                                row[n] = v.id
                            elif t.kind == "int":
                                row[n] = str(v)
                            elif t.kind == "number":
                                e = encode(v)
                                row[n] = (
                                    {
                                        "numerator": None,
                                        "denominator": None,
                                        "infinity": e["infinity"],
                                    }
                                    if "infinity" in e
                                    else dict(
                                        numerator=e["rational"][0],
                                        denominator=e["rational"][1],
                                        infinity=None,
                                    )
                                )
                            else:
                                row[n] = v
                        values.append(row)
                    writer.write_table(pa.Table.from_pylist(values, schema=schema))
        os.link(temporary, path)  # Do not overwrite a destination created concurrently.
        return path
    finally:
        temporary.unlink(missing_ok=True)


def read_frame(path, *, models=()):
    """
    MIPFRAME HELP BEGIN
    read_frame [api.read_frame]

    Restore an exact relation with explicit snapshot/dependency bindings.

    read_frame(path, *, models=())

    When to use: Restore an exact relation with explicit snapshot/dependency bindings.

    Returns: Frame; externally claimed uniqueness is not trusted.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity, api.Frame.write
    MIPFRAME HELP END
    """
    path = Path(path)
    if path.suffix != ".jsonl":
        raise ValueError("read_frame currently supports exact JSONL")
    with path.open() as stream:
        header = json.loads(next(stream))["mipframe"]
        if header["version"] != 1:
            raise SchemaError("Unsupported Frame file version")
        schema = {n: DType(**t) for n, t in header["schema"].items()}
        result = from_records(
            ({n: decode(v) for n, v in json.loads(line).items()} for line in stream),
            schema=schema,
            key=header["key"],
            models=models,
        )

        # Imported provenance describes the producing run; it grants no key or proof facts.
        result._search_provenance = tuple(header.get("search_provenance", ()))
        return result
