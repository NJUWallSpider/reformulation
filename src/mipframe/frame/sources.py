"""Column-pruned scans of canonical entity metadata and sparse positions."""

from fractions import Fraction
from math import inf
from math import ceil
from math import floor
from itertools import islice
from mipframe.frame.schema import DType
from mipframe.frame.schema import ref_type


def binary(kind, lo, hi):
    return (
        kind == "I"
        and lo != -inf
        and hi != inf
        and ceil(lo) <= floor(hi)
        and ceil(lo) >= 0
        and floor(hi) <= 1
    )


def entity_schema(owner, axis):
    schema = {axis: ref_type(owner, axis), "name": DType("str"), "source_line": DType("int", True)}
    if axis == "var":
        schema["kind"] = DType("enum", values=("C", "I"))
    schema.update(lb=DType("number"), ub=DType("number"))
    if axis == "var":
        schema.update(
            obj=DType("number"),
            type=DType("enum", values=("B", "I", "C")),
            is_binary=DType("bool"),
            nnz=DType("int"),
        )
    else:
        schema.update(
            nnz=DType("int"),
            relation=DType("str"),
            sense=DType("str"),
            lhs=DType("number"),
            rhs=DType("number"),
            coef_min=DType("number", True),
            coef_max=DType("number", True),
        )
    return schema


def entity_scan(frame, fields, ctx):
    m = frame._owner.model
    axis = frame._entity_axis
    from mipframe.frame.base import PlanIDs

    if isinstance(frame._index, PlanIDs):
        selected = frame._index
        ids = (r[selected.column] for r in selected.frame._raw((selected.column,), ctx))
    else:
        ids = iter(frame._index)
    requested = set(fields)
    metadata = {"source_line": "line",
        "name": "name",
        "lb": "lo",
        "ub": "hi",
        "lhs": "lo",
        "rhs": "hi",
        "obj": "obj",
        "kind": "kind",
    }
    needed = {metadata[n] for n in fields if n in metadata}
    if requested & {"type", "is_binary"}:
        needed.update(("kind", "lo", "hi"))
    if requested & {"relation", "sense"}:
        needed.update(("lo", "hi"))
    columns = tuple(sorted(needed))
    table = "rows" if axis == "row" else "variables"
    ptr = m.row_ptr if axis == "row" else m.col_ptr
    block = max(1, min(500, ctx.memory // 2048))
    while True:
        batch = list(islice(ids, block))
        if not batch:
            return
        rows = {}
        if columns:
            rows = m.gather(axis, columns, batch)
        for ident in batch:
            frame._owner.model
            ident = int(ident)
            raw = rows.get(ident, {})
            lo = -inf if raw.get("lo") is None else m.num(raw["lo"])
            hi = inf if raw.get("hi") is None else m.num(raw["hi"])
            record = {}
            for n in fields:
                if n == axis:
                    value = ident
                elif n == "source_line":
                    value = raw["line"] or None
                elif n == "name":
                    value = raw["name"]
                elif n == "kind":
                    value = raw["kind"]
                elif n in ("lb", "lhs"):
                    value = lo
                elif n in ("ub", "rhs"):
                    value = hi
                elif n == "obj":
                    value = m.num(raw["obj"])
                elif n == "nnz":
                    value = int(ptr[ident + 1] - ptr[ident])
                elif n in ("type", "is_binary"):
                    b = binary(raw["kind"], lo, hi)
                    value = b if n == "is_binary" else "B" if b else raw["kind"]
                elif n in ("relation", "sense"):
                    value = (
                        "free"
                        if lo == -inf and hi == inf
                        else "<="
                        if lo == -inf
                        else ">="
                        if hi == inf
                        else "="
                        if lo == hi
                        else "range"
                    )
                elif n in ("coef_min", "coef_max"):
                    value = (min if n == "coef_min" else max)(
                        (c for _, c in m.iter_terms(ident)), default=None
                    )
                else:
                    raise KeyError(n)
                record[n] = value
            yield record
