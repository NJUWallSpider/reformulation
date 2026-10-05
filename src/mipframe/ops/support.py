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

    def scan_batches(fields,ctx):
        import numpy as np
        from mipframe.data.batch import Columns
        from mipframe.frame.array_source import read_columns
        from mipframe.data.ids import set_ids,bounded_ids
        from mipframe.store.incidence import Target
        a=read_columns(left,(axis,),ctx).columns[axis]
        selected=Target(left._owner,axis,right)
        if operation in ('intersection','difference') and (selected.colors is not None or isinstance(selected.ids,range)):
            ids=bounded_ids(a,ctx.work);block=max(1,min(8192,ctx.memory//256,ctx.work.available//128))
            for start in range(0,len(ids),block):
                with ctx.work.reserve(block*64):
                    values=ids[start:start+block]
                    if selected.colors is not None:mask=selected.colors[values]==selected.color
                    else:
                        index=selected.ids
                        mask=np.zeros(len(values),dtype=bool) if not len(index) else (values>=min(index[0],index[-1]))&(values<=max(index[0],index[-1]))&((values-min(index[0],index[-1]))%abs(index.step)==0)
                    values=values[mask if operation=='intersection' else ~mask]
                    if len(values):yield Columns({axis:values} if axis in fields else {},len(values))
            return
        b=read_columns(right,(axis,),ctx).columns[axis]
        ids=set_ids(a,b,operation,ctx.work)
        block=max(1,min(8192,ctx.memory//128))
        for start in range(0,len(ids),block):
            selected=ids[start:start+block]
            yield Columns({axis:selected} if axis in fields else {},len(selected))
    def scan(fields,ctx):
        for batch in scan_batches(fields,ctx):yield from batch.records(fields)

    result = Frame(
        {axis: left.schema[axis]},
        key=(axis,),
        models=merge_models(left, right),
        source=scan,
    )
    result._column_scan=scan_batches
    result._query.spec = {
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
