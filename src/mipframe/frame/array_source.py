"""Completed typed columns with model-aware lifetime, without relation tables."""
import uuid
import numpy as np
from mipframe.data.batch import Columns


def frame_from_columns(columns, schema, *, size=None, models=(), key=None, domains=None, relations=None):
    from mipframe.frame.base import Frame
    if any(not hasattr(v,'dtype') for v in columns.values()):
        from mipframe.frame.executor import Context
        from mipframe.frame.materialized import stored_frame
        from mipframe.data.relations import build
        ctx=Context(models)
        try:
            count=size if size is not None else len(next(iter(columns.values()),()))
            table=build(ctx.work,schema,({n:v[i] for n,v in columns.items()} for i in range(count)))
            return stored_frame(ctx,table,schema,key=key,domains=domains,relations=relations)
        except BaseException:ctx.close();raise
    arrays=dict(columns)
    n=size if size is not None else len(next(iter(arrays.values()),()))
    if any(len(a)!=n for a in arrays.values()):
        raise ValueError('Column lengths differ')
    for a in arrays.values():
        if isinstance(a,np.ndarray):a.flags.writeable=False
    data=Columns(arrays,n)
    def scan(fields,ctx):
        yield from data.records(fields)
    result=Frame(schema,key=key,models=models,source=scan,known_count=n,domains=domains,relations=relations)
    result._array_columns=data
    result._query.spec={'kind':'relation','relation':str(uuid.uuid4())}
    return result


def read_columns(frame,fields,ctx):
    frame._guard()
    if frame._op=='source':
        if hasattr(frame,'_typed_relation'):
            t=frame._typed_relation
            return Columns({n:t.columns[n] for n in fields},len(t))
        if hasattr(frame,'_array_columns'):
            b=frame._array_columns
            return Columns({n:b.columns[n] for n in fields},b.size)
    table,_=ctx.stage(frame,fields)
    return Columns(table.columns,len(table))
