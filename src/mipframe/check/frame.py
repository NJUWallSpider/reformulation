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
    from mipframe.check.report import Report
    from mipframe.data.relations import build
    table=build(ctx.work,schema,rows)
    violations=stored_frame(ctx,table,schema,relations=relations)
    return Report(ctx,len(table)==0,dict(summary,violations=len(table)),violations)


def project(source,bindings,*,memory_mb=128,disk_mb=None):
    from mipframe.core.resources import resource_limits
    resource_limits(memory_mb,disk_mb)
    from mipframe.store.incidence import Target
    from mipframe.check.report import CardinalityError,Report
    from mipframe.data.relations import RelationBuilder
    from mipframe.data.ids import IDSet
    if not bindings:raise ValueError('Specify at least one projection column')
    axis=source._entity_axis;opposite='var' if axis=='row' else 'row'
    if any(not n.isidentifier() or n.startswith('_') or n in {'source','source_id',axis,'to_record'} for n in bindings):raise SchemaError('Projection columns must be distinct public names without identity/method conflicts')
    targets={n:Target(source._owner,opposite,value).frame for n,value in bindings.items()}
    schema={axis:ref_type(source._owner,axis),**{n:ref_type(source._owner,opposite) for n in targets}}
    models=merge_models(source,*targets.values());state={}
    def prepare():
        if 'error' in state:raise state['error']
        if 'data' in state:return state['data']
        ctx=Context(models,memory_mb,disk_mb);state['ctx']=ctx
        try:
            import numpy as np
            m=source._owner.model
            selections={n:IDSet(ctx.stage(f,(opposite,))[0].columns[opposite],work=ctx.work) for n,f in targets.items()}
            vs={'source':ref_type(source._owner,axis),'column':DType('str'),'count':DType('int')}
            bad=RelationBuilder(ctx.work,vs)
            block=max(1,ctx.memory//max(256,64*len(targets)))
            ptr=m.row_ptr if axis=='row' else m.col_ptr
            neighbors=m.col_idx if axis=='row' else m.csc_row_ids
            if not hasattr(m,'f') and isinstance(ptr,np.ndarray) and isinstance(neighbors,np.ndarray):
                from mipframe.data.projection import sparse_projection
                from mipframe.data.relations import Relation
                sources=ctx.stage(source,(axis,))[0].columns[axis]
                columns={axis:sources};counts={}
                for name,selected in selections.items():
                    counts[name],columns[name]=sparse_projection(ctx.work,ptr,neighbors,sources,selected.array)
                block=max(1,min(8192,ctx.memory//256,ctx.work.available//128))
                with ctx.work.reserve(block*64):
                    for begin in range(0,len(sources),block):
                        stop=min(len(sources),begin+block);invalid=np.zeros(stop-begin,dtype=bool)
                        for count in counts.values():invalid |= count[begin:stop]!=1
                        for offset in np.flatnonzero(invalid):
                            i=begin+int(offset)
                            for name,count in counts.items():
                                if count[i]!=1:bad.append(dict(source=int(sources[i]),column=name,count=int(count[i])))
                for count in counts.values():ctx.work.release(count)
                data=Relation(columns,len(sources),ctx.work)
            else:
                output=RelationBuilder(ctx.work,schema)
                for rec in source._raw((axis,),ctx):
                    ident=rec[axis];counts=dict.fromkeys(targets,0);values=dict.fromkeys(targets)
                    for ids,_ in m.sparse.blocks(axis,int(ident),block):
                        for raw in ids:
                            neighbor=int(raw)
                            for name,selected in selections.items():
                                if neighbor in selected:counts[name]+=1;values[name]=neighbor
                    for name in targets:
                        if counts[name]!=1:bad.append(dict(source=ident,column=name,count=counts[name]))
                    output.append({axis:ident,**values})
                data=output.finish()
            bad=bad.finish()
            if len(bad):
                ms={'source':ref_type(source._owner,axis),'column':DType('str'),'row':ref_type(source._owner,'row'),'var':ref_type(source._owner,'var'),'coef':DType('number')}
                def matches(fields,execution):
                    for rec in bad.records():
                        ident,name=rec['source'],rec['column']
                        for neighbors,_ in source._owner.model.sparse.blocks(axis,ident,block):
                            for raw in neighbors:
                                other=int(raw)
                                if other not in selections[name]:continue
                                row=ident if axis=='row' else other;var=other if axis=='row' else ident
                                values=dict(source=ident,column=name,row=row,var=var)
                                if 'coef' in fields:values['coef']=source._owner.model.coefficient(row,var)
                                yield {n:values[n] for n in fields}
                match_frame=Frame(ms,models=models,source=matches)
                violations=stored_frame(ctx,bad,vs,key=('source','column'),relations={'matches':match_frame,'sources':source})
                checked=Report(ctx,False,dict(operation='exactly_one',scope='full declared source selection',checked_sources=len(data),violations=len(bad)),violations)
                checked._recipe=dict(kind='execution',frame_object=result)
                error=CardinalityError(f'{len(bad)} projection cardinality violations; inspect exception.report.violations',checked)
                from mipframe._help.diagnostics import hint
                hint(error,'recipe.coordinates');state['error']=error;raise error
            state['data']=data;ctx.retained=True;result._query.resource=ctx
            return data
        except BaseException:
            if not ctx.retained:ctx.close()
            raise
    def scan(fields,ctx):yield from prepare().records(fields)
    result=Frame(schema,key=(axis,),models=models,source=scan,domains=targets,relations={'sources':source})
    result._query.spec=dict(kind='project',source=source,bindings=targets);result._params={'barrier':True}
    return result


def check(frame, kind, columns=None, *, key=None, value=None, memory_mb=None, disk_mb=None, _context=None):
    if kind not in ('unique','product','function'):raise ValueError('Unknown check; use unique, product or function')
    def names(v):
        ns=(v,) if isinstance(v,str) else tuple(v or ())
        if not ns or len(ns)!=len(set(ns)) or not set(ns)<=set(frame.schema):raise SchemaError('Check requires distinct existing columns')
        return ns
    left=names(key) if kind=='function' else names(columns)
    right=names(value) if kind=='function' else ()
    selected=tuple(dict.fromkeys((*left,*right)))
    if kind=='product' and any(n not in frame.domains for n in left):raise SchemaError('Product checks require declared target domains')
    ctx=_context if _context is not None else Context(frame._models,memory_mb,disk_mb)
    try:
        table,schema=ctx.stage(frame,selected);count=len(table)
        summary=dict(operation=kind,scope='full current Frame selection',checked_records=count,columns=left)
        if kind=='function':
            summary.update(key=left,value=right,uncovered_targets={n:len(frame.domains[n])-sum(1 for k,_ in table.groups((n,)) if k[0] is not None) for n in selected if n in frame.domains})
        def violations():
            unique=covered=foreign=0
            for k,ids in table.groups(left):
                unique+=1;amount=len(ids)
                if kind=='function':
                    from mipframe.data.relations import build
                    with ctx.work.fork() as scratch:
                        values=build(scratch,{n:schema[n] for n in right},table.records(right,ids))
                        amount=sum(1 for _ in values.groups(right))
                if amount>1:yield dict(reason='function violation' if kind=='function' else 'duplicate coordinate',key=json.dumps([pack(v,schema[n]) for n,v in zip(left,k)]),count=amount)
                if kind=='product':
                    if any(v is None or not ctx.contains(frame.domains[n],v) for n,v in zip(left,k)):foreign+=len(ids)
                    else:covered+=1
            if kind!='function':summary.update(unique_coordinates=unique,duplicate_records=count-unique)
            if kind=='product':
                expected=prod(len(frame.domains[n]) for n in left);missing=expected-covered
                summary.update(expected_coordinates=expected,missing_coordinates=missing,outside_domain_records=foreign)
                if foreign:yield dict(reason='outside declared domain',key='',count=foreign)
                if missing:yield dict(reason='missing coordinates',key='',count=missing)
        result=report(ctx,violations(),{'reason':DType('str'),'key':DType('str'),'count':DType('int')},summary,relations={'records':frame})
        result._recipe=dict(kind='frame',check=kind,columns=columns,key=key,value=value,frame_object=frame)
        if result.ok and kind in ('unique','product'):frame._verified_unique.add(left)
        return result
    except BaseException:
        if not ctx.retained:ctx.close()
        raise


def check_group(owner, requests, *, budget, memory_mb=128, disk_mb=None):
    """Share one relational workspace, including staging and sort caches."""
    from mipframe.frame.base import merge_models
    models=tuple(dict.fromkeys(m for frame,_,_ in requests for m in frame._models))
    if any((m.uid,m.version)!=(owner.uid,owner.version) for m in models):
        raise SchemaError('Frame check belongs to another snapshot')
    ctx=Context(models,memory_mb,disk_mb)
    reports=[]
    try:
        for frame,kind,options in requests:
            budget.charge(phase='frame')
            reports.append(check(frame,kind,_context=ctx,**options))
        budget.poll()
        return reports
    except BaseException:
        ctx.close()
        raise
