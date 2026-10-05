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


def from_records(records, *, schema, key=None, models=(), memory_mb=None, disk_mb=None):
    """
    Create an exact typed relation, spilling only to array artifacts.

    MIPFRAME HELP BEGIN
    Create a typed relation from explicit records.

    Full contract: mf.help("api.Frame.from_records")
    MIPFRAME HELP END
    """
    models=tuple(models.values()) if isinstance(models,dict) else tuple(models)
    schema={n:t if isinstance(t,DType) else DType(t) for n,t in schema.items()}
    key=(key,) if isinstance(key,str) else tuple(key) if key is not None else None
    if key is not None and not set(key)<=set(schema):raise SchemaError('Unknown key column')
    ctx=Context(models,memory_mb,disk_mb)
    def rows():
        for record in records:
            if set(record)!=set(schema):raise SchemaError('Record columns must match the declared schema')
            row={n:normalize(record[n],t) for n,t in schema.items()}
            for n,t in schema.items():
                if t.kind=='ref' and row[n] is not None:
                    for model in models:
                        if model.uid==t.uid:
                            if model.version!=t.version:raise SchemaError('Reference version mismatch')
                            if row[n] not in model.model.entity_ids(t.axis):raise SchemaError('Reference ID outside the declared model axis')
            yield row
    try:
        from mipframe.data.relations import build
        table=build(ctx.work,schema,rows())
        if key is not None:
            for _,ids in table.groups(key):
                if len(ids)>1:raise SchemaError('Declared key is not unique')
        return stored_frame(ctx,table,schema,key=key)
    except BaseException:ctx.close();raise


def expression_spec(expr, frame_spec):
    """Encode expression leaves using the containing plan's Frame namespace."""
    op, args = expr.op, expr.args
    if op == "literal":
        return {"op": op, "value": encode(args[0])}
    if op == "col":
        return {"op": op, "name": args[0]}
    if op == "isin_frame":
        return {"op": op, "input": expression_spec(args[0], frame_spec),
                "frame": frame_spec(args[1])}
    if op == "isin":
        return {"op": op, "input": expression_spec(args[0], frame_spec),
                "values": [expression_spec(v, frame_spec) for v in args[1]]}
    return {"op": op, "args": [expression_spec(a, frame_spec) for a in args]}


def to_spec(frame):
    frame._guard()
    from mipframe.frame.base import PlanIDs
    seen = {}
    active = set()

    def expression(expr):
        return expression_spec(expr, lambda f: {"version": 1, "plan": node(f)})

    def node(f):
        # Entity selection wrappers serialize as their underlying relational plan.
        index = getattr(f, "_index", None)
        if f._op == "source" and f._query.spec.get("kind") in ("row", "var") and isinstance(index, PlanIDs):
            return node(index.frame)
        ident = id(f)
        if ident in active:
            raise SchemaError("Cyclic Frame query plan")
        if ident in seen:
            number, result = seen[ident]
            result["$id"] = number
            return {"op": "ref", "id": number}
        active.add(ident)
        number = len(seen)
        result = {}
        seen[ident] = (number, result)
        if f._op == "source":
            from mipframe.frame.source_codec import encode_source
            result.update(encode_source(f, node))
        else:
            result.update(op=f._op, inputs=[node(p) for p in f._parents])
            p = f._params
            if f._op == "filter":
                result["predicates"] = [expression(e) for e in p["predicates"]]
            elif f._op == "select":
                result["expressions"] = {n: expression(e) for n, e in p["expressions"].items()}
            elif f._op == "group":
                result.update(
                    keys=p["keys"],
                    aggregates={n: {"op": a.op, "expr": None if a.expr is None else expression(a.expr)}
                                for n, a in p["aggregates"].items()},
                )
            elif f._op == "slice":
                result["slice"] = [p["slice"].start, p["slice"].stop, p["slice"].step]
            else:
                result.update(p)
        active.remove(ident)
        return result

    return {"version": 1, "plan": node(frame)}


def frame_from_spec(spec, *, models, relations=None):
    """
    MIPFRAME HELP BEGIN
    Restore an exact relation with explicit snapshot/dependency bindings.

    Full contract: mf.help("api.frame_from_spec")
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
            member = e["frame"]
            if set(member) != {"version", "plan"} or member["version"] != 1:
                raise SchemaError("Unsupported Frame query specification")
            return expression(e["input"], depth + 1).isin(build(member["plan"], depth + 1))
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

    references = {}
    declared = set()

    def build(n, depth=0):
        if depth > 64:
            raise SchemaError("Query specification is too deep")
        if not isinstance(n, dict):
            raise SchemaError("Invalid query node")
        if n.get("op") == "ref":
            ident = n.get("id")
            if set(n) != {"op", "id"} or type(ident) is not int or ident not in references:
                raise SchemaError("Unknown or cyclic query reference")
            return references[ident]
        ident = n.get("$id")
        if "$id" in n:
            if type(ident) is not int or ident < 0 or ident in declared:
                raise SchemaError("Invalid or duplicate query node ID")
            declared.add(ident)
        result = operation(n, depth)
        if "$id" in n:
            references[ident] = result
        return result

    def operation(n, depth):
        op = n["op"]
        if op == "scan":
            from mipframe.frame.source_codec import decode_source
            return decode_source(n, lambda child: build(child, depth+1), models, relations)
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


def write(frame, path, *, format=None, memory_mb=None, disk_mb=None):
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
                for batch in frame.iter_batches(memory_mb=memory_mb,disk_mb=disk_mb):
                    for row in batch._records():
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
                for batch in frame.iter_batches(memory_mb=memory_mb,disk_mb=disk_mb):
                    values = []
                    for raw in batch._records():
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
    Restore an exact relation with explicit snapshot/dependency bindings.

    Full contract: mf.help("api.read_frame")
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
