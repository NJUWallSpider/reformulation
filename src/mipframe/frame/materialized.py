"""Frames borrowing completed typed relations with explicit result ownership."""
from mipframe.frame.base import Frame
import uuid


def stored_frame(ctx,table,schema,*,key=None,domains=None,relations=None):
    def source(fields,execution):
        ctx.guard()
        yield from table.records(fields)
    result=Frame(schema,key=key,models=ctx.models,source=source,known_count=len(table),domains=domains,relations=relations)
    result._query.resource=ctx
    result._typed_relation=table
    ctx.retained=True
    result._query.spec={'kind':'relation','relation':str(uuid.uuid4())}
    return result
