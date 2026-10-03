"""Entity-set operations and Frame-valued sparse counts."""

from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from mipframe.frame.entity import _table


class DegreeDescription(dict):
    def __repr__(self):
        keys = tuple(self)
        return _table(
            "<DegreeDescription>",
            ("value",),
            keys,
            lambda i: {
                "value": f"{self[keys[i]]:,}"
                if type(self[keys[i]]) is int
                else self[keys[i]]
            },
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


def incidence_counts(frame):
    from mipframe.frame.adapters import sparse_counts
    from mipframe.frame import col

    axis = "var" if isinstance(frame, RowFrame) else "row"
    options = {"rows": frame} if axis == "var" else {"vars": frame}
    return sparse_counts(frame._owner, axis=axis, **options).where(col("nnz") > 0)


def neighbors(frame):
    return frame.terms.refs("var" if isinstance(frame, RowFrame) else "row")


def set_operation(left, right, operation):
    from mipframe.frame import Frame
    from mipframe.frame import SchemaError
    from mipframe.frame.base import PlanIDs
    from mipframe.frame.base import merge_models

    if not isinstance(right, type(left)) or left._entity_axis != right._entity_axis:
        raise TypeError("Set operations require the same entity axis")
    if (left._owner.uid, left._owner.version) != (
        right._owner.uid,
        right._owner.version,
    ):
        raise SchemaError("Set operations require the same snapshot")
    axis = left._entity_axis

    def scan(fields, ctx):
        a, _ = ctx.stage(left, (axis,))
        if operation in ('intersection', 'difference'):
            from mipframe.store.incidence import Target
            from itertools import islice
            import numpy as np

            selected = Target(left._owner, axis, right)
            if selected.colors is not None or isinstance(selected.ids, range):
                # A global family already has a bounded integer membership
                # lookup. Do not stage all 190,000 members to test 200 IDs.
                cursor = iter(ctx.execute(f'SELECT c0 FROM {a} ORDER BY c0'))
                block = max(1, ctx.memory // 256)
                while batch := list(islice(cursor, block)):
                    ids = np.array([i for (i,) in batch], dtype=np.int64)
                    mask = selected.mask(ids)
                    if operation == 'difference':
                        mask = ~mask
                    for ident in ids[mask]:
                        yield {axis: int(ident)} if fields else {}
                return
        b, _ = ctx.stage(right, (axis,))
        op = {"union": "UNION", "intersection": "INTERSECT", "difference": "EXCEPT"}[
            operation
        ]
        for (ident,) in ctx.execute(
            f"SELECT c0 FROM {a} {op} SELECT c0 FROM {b} ORDER BY c0"
        ):
            yield {axis: ident} if fields else {}

    result = Frame(
        {axis: left.schema[axis]},
        key=(axis,),
        models=merge_models(left, right),
        source=scan,
    )
    result._source_spec = {
        "kind": "entity_set",
        "left": left,
        "right": right,
        "operation": operation,
    }
    return type(left)(left._owner, PlanIDs(result, axis))


def jaccard(left, right):
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 1.0
