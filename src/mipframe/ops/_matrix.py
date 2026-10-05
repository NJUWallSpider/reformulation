"""Exact sparse Ax and A-transpose-y with explicit coverage semantics."""

from fractions import Fraction
from mipframe.frame.schema import DType, SchemaError, ref_type
from mipframe.ops._runtime import operation


def compute(ctx, selection, values, value, missing, transpose):
    if missing not in ("error", "zero"):
        raise ValueError("missing must be 'error' or 'zero'")
    axis = "var" if transpose else "row"
    input_axis = "row" if transpose else "var"
    if getattr(selection, "_entity_axis", None) != axis:
        raise SchemaError("Wrong output axis")
    owner = selection._owner
    from mipframe.frame.array_source import read_columns
    from mipframe.data.batch import scalar
    if not {input_axis,value}<=set(values.schema):
        raise SchemaError('Missing vector axis or value column')
    schema={n:values.schema[n] for n in (input_axis,value)}
    if not ref_type(owner,input_axis).compatible(schema[input_axis]):
        raise SchemaError('Input vector uses the wrong axis or snapshot')
    if schema[value].kind not in ('int','number'):
        raise SchemaError('Vector values must be exact numbers')
    columns=read_columns(values,(input_axis,value),ctx).columns
    from mipframe.data.ids import IDSet
    from mipframe.data.relations import Relation,build,search_range
    m=owner.model;live=IDSet(m.entity_ids(input_axis),work=ctx.work)
    vector=Relation(columns,len(columns[input_axis]),ctx.work);order=vector.order((input_axis,))
    def invalid():
        for key,group in vector.groups((input_axis,)):
            ident=key[0]
            for at,i in enumerate(group):
                number=scalar(columns[value][int(i)])
                if ident is None or ident not in live or at>0 or number is None or number in (-float('inf'),float('inf')):
                    yield {input_axis:ident,value:number}
    bad=build(ctx.work,schema,invalid())
    if len(bad):ctx.invalid('Invalid or duplicate vector ID, missing value, or nonfinite value',schema,bad.records())
    def lookup(ident,default=0):
        lo,hi=search_range(vector,order,(input_axis,),(ident,))
        return default if lo==hi else scalar(columns[value][int(order[lo])])
    ids=read_columns(selection,(axis,),ctx).columns[axis]
    adjacent=m.iter_column if transpose else m.iter_terms
    if missing=='error':
        absent=IDSet((target for source in ids for target,_ in adjacent(int(source)) if lookup(target,None) is None),work=ctx.work)
        if len(absent):ctx.invalid('Missing required vector values; use missing="zero" for a sparse vector',
            {input_axis:ref_type(owner,input_axis)},({input_axis:i} for i in absent))
    output='value' if transpose else 'activity'
    def records():
        for source in ids:
            total=0
            for target,coefficient in adjacent(int(source)):
                total += coefficient*lookup(target)
                ctx.check_size(total)
            yield {axis:int(source),output:total}
    ctx.paths.add('CSC exact multiply-accumulate' if transpose else 'CSR exact multiply-accumulate')
    return ctx.publish({axis:ref_type(owner,axis),output:DType('number')},records(),
                       key=(axis,),domains={axis:selection})


@operation("matrix.activity")
def activity(ctx, selection, values, *, value="value", missing="error"):
    return compute(ctx, selection, values, value, missing, False)


@operation("matrix.transpose_activity")
def transpose_activity(ctx, selection, values, *, value="value", missing="error"):
    return compute(ctx, selection, values, value, missing, True)
