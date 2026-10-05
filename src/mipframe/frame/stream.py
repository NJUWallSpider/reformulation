"""Bounded column scans for the shared relational executor.

These are physical scan/filter/projection operators. Exact expressions outside
primitive comparisons still use QueryExpr.evaluate; blocking validation stays
in the relational executor and is never bypassed by this stream.
"""
import operator
import numpy as np
from mipframe.data.batch import Columns
from mipframe.data.relations import ColumnView

_COMPARE={name:getattr(operator,name) for name in ('eq','ne','lt','le','gt','ge')}


def batches(frame,fields,ctx):
    """Return a bounded typed stream, or None for a non-streaming source."""
    op=frame._op
    if op=='join' and frame._params['cardinality'] in ('1:1','m:1'):
        from mipframe.frame.executor import join
        return join(frame,fields,ctx,as_batches=True)
    if op=='filter':
        predicates=frame._params['predicates']
        if not all(_safe_predicate(e) for e in predicates):return None
        needed=tuple(dict.fromkeys((*fields,*(n for e in predicates for n in e.dependencies))))
        parent=batches(frame._parents[0],needed,ctx)
        return None if parent is None else _filter(parent,fields,predicates,ctx)
    if op=='select':
        expressions=frame._params['expressions']
        if not all(expressions[n].op=='col' for n in fields):return None
        names={n:expressions[n].args[0] for n in fields}
        parent=batches(frame._parents[0],tuple(dict.fromkeys(names.values())),ctx)
        return None if parent is None else _select(parent,names)
    if op!='source':return None
    if hasattr(frame,'_column_scan'):return frame._column_scan(fields,ctx)
    if hasattr(frame,'_typed_relation'):
        t=frame._typed_relation
        return _stored(frame,t.columns,len(t),fields,ctx)
    if hasattr(frame,'_array_columns'):
        t=frame._array_columns
        return _stored(frame,t.columns,t.size,fields,ctx)
    if hasattr(frame,'_entity_axis') and set(fields)<={frame._entity_axis,'nnz','lb','ub','lhs','rhs','obj'}:
        if hasattr(frame._owner.model,'f'):return None
        return _entities(frame,fields,ctx)
    if getattr(frame,'_term_frame',False):
        m=frame._owner.model
        if hasattr(m,'f') or ('coef' in fields and not m.data.numbers.integral):return None
        if not all(isinstance(a,np.ndarray) for a in (m.row_ptr,m.col_idx,m.coeff_id)):return None
        selected=getattr(frame,'_vars',None)
        index=getattr(selected,'_index',None)
        # Keep the sparse CSC scan for explicitly small column selections.
        if isinstance(index,range) and len(index)<m.capacity('var')//8:return None
        return _terms(frame,fields,ctx)
    return None


def _safe_predicate(expr):
    if expr.op in _COMPARE:return all(a.op in ('col','literal') for a in expr.args)
    if expr.op=='isin':return expr.args[0].op=='col' and all(a.op=='literal' for a in expr.args[1])
    if expr.op in ('and','or','not'):return all(_safe_predicate(a) for a in expr.args)
    return False


def _block(ctx,fields):
    return max(1,min(8192,ctx.work.memory//max(1024,128*len(fields)),ctx.work.available//max(1024,128*len(fields))))


def _stored(frame,columns,count,fields,ctx):
    block=_block(ctx,fields)
    for start in range(0,count,block):
        frame._guard();stop=min(count,start+block)
        yield Columns({n:columns[n][start:stop] for n in fields},stop-start)


def _entities(frame,fields,ctx):
    from itertools import islice
    from mipframe.frame.base import PlanIDs
    frame._guard();m=frame._owner.model;axis=frame._entity_axis
    if isinstance(frame._index,PlanIDs):
        index=frame._index
        ids=(r[index.column] for r in index.frame._raw((index.column,),ctx))
    else:ids=iter(frame._index)
    block=_block(ctx,fields);ptr=m.row_ptr if axis=='row' else m.col_ptr
    while True:
        frame._guard()
        with ctx.work.reserve(block*128):
            selected=np.fromiter(islice(ids,block),dtype='<i8')
            if not len(selected):return
            columns={}
            for name in fields:
                # A degree cannot exceed the addressable sparse-array length,
                # so signed int64 is exact even when pointers use uint64.
                if name==axis:columns[name]=selected
                elif name=='nnz':columns[name]=(ptr[selected+1]-ptr[selected]).astype('<i8')
                else:
                    from mipframe.data.relations import DictionaryColumn
                    if name=='obj':columns[name]=DictionaryColumn(m.data.numbers,m.data.arrays['var_obj_id'],selected)
                    else:
                        side='lb' if name in ('lb','lhs') else 'ub'
                        columns[name]=DictionaryColumn(m.data.numbers,m.data.arrays[axis+'_'+side+'_id'],selected,m.data.arrays[axis+'_'+side+'_finite'],-float('inf') if side=='lb' else float('inf'))
            yield Columns(columns,len(selected))


def _terms(frame,fields,ctx):
    frame._guard();m=frame._owner.model;block=_block(ctx,fields)
    variables=frame._vars;allowed=var_order=None
    if variables is not None:
        table,_=ctx.stage(variables,('var',));allowed=table.columns['var'];var_order=table.order(('var',))
        if len(allowed)<m.capacity('var')//8:
            # A lazy selection can also be sparse. Resolve its size before
            # choosing CSR batches versus the existing selective CSC scan.
            yield from _record_batches(frame._scan(fields,ctx),frame,fields,ctx)
            return
    selected=frame._rows
    if selected is None:
        spans=iter(((0,len(m.col_idx)),))
    else:
        table,_=ctx.stage(selected,('row',));order=table.order(('row',))
        def ranges():
            begin=end=None
            try:
                for i in order:
                    row=int(table.columns['row'][i]);lo,hi=int(m.row_ptr[row]),int(m.row_ptr[row+1])
                    if lo==hi:continue
                    if end==lo:end=hi
                    else:
                        if begin is not None:yield begin,end
                        begin,end=lo,hi
                if begin is not None:yield begin,end
            finally:
                if not ctx.work.closed:ctx.work.release(order)
        spans=ranges()
    for begin,end in spans:
        for start in range(begin,end,block):
            frame._guard();stop=min(end,start+block)
            with ctx.work.reserve((stop-start)*128):
                keep=None
                if allowed is not None:
                    if not len(allowed):continue
                    positions=np.searchsorted(allowed,m.col_idx[start:stop],sorter=var_order.view(np.int64))
                    keep=(positions<len(allowed))&(allowed[var_order[np.minimum(positions,len(allowed)-1)]]==m.col_idx[start:stop])
                    if not np.any(keep):continue
                columns={}
                for name in fields:
                    if name=='row':columns[name]=np.searchsorted(m.row_ptr[1:],np.arange(start,stop,dtype='<u8'),side='right')
                    elif name=='var':columns[name]=m.col_idx[start:stop]
                    else:columns[name]=m.data.numbers.numerator[m.coeff_id[start:stop]]
                if keep is not None:columns={n:c[keep] for n,c in columns.items()}
                yield Columns(columns,stop-start if keep is None else int(np.count_nonzero(keep)))


def _primitive(expr,batch,ctx):
    if expr.op=='col':
        result=batch.columns[expr.args[0]]
        return result if isinstance(result,np.ndarray) and result.dtype.kind in 'iub' else NotImplemented
    if expr.op=='literal':
        value=expr.evaluate({},ctx)
        # NumPy integer comparisons with bigint, rational or float may coerce
        # through float64. Delegate these to the scalar exact evaluator.
        return value if isinstance(value,(int,bool)) and -(2**63)<=value<2**63 else NotImplemented
    if expr.op=='isin':
        values=_primitive(expr.args[0],batch,ctx)
        if not isinstance(values,np.ndarray) or values.dtype.kind not in 'ib':return NotImplemented
        if id(expr) not in ctx.literal_members:ctx.contains_literals(expr,int(values[0]))
        table,order,has_null,_=ctx.literal_members[id(expr)];column=table.columns['value']
        if has_null or not isinstance(column,np.ndarray) or column.dtype.kind not in 'ib':return NotImplemented
        if not len(column):return np.zeros(len(values),dtype=bool)
        positions=np.searchsorted(column,values,sorter=order.view(np.int64))
        return (positions<len(column))&(column[order[np.minimum(positions,len(column)-1)]]==values)
    if expr.op in _COMPARE:
        left=_primitive(expr.args[0],batch,ctx);right=_primitive(expr.args[1],batch,ctx)
        if left is NotImplemented or right is NotImplemented:return NotImplemented
        # Unsigned source identity arrays are at most uint32. A uint64 column
        # can contain values beyond the signed range; keep exact scalar logic.
        if any(isinstance(v,np.ndarray) and v.dtype==np.dtype('uint64') for v in (left,right)):return NotImplemented
        return _COMPARE[expr.op](left,right)
    if expr.op in ('and','or','not'):
        values=[_primitive(a,batch,ctx) for a in expr.args]
        if any(v is NotImplemented for v in values):return NotImplemented
        return np.logical_not(values[0]) if expr.op=='not' else (np.logical_and if expr.op=='and' else np.logical_or)(*values)
    return NotImplemented


def _filter(parent,fields,predicates,ctx):
    for batch in parent:
        with ctx.work.reserve(max(128,batch.size*64)):
            keep=np.ones(batch.size,dtype=bool)
            for expr in predicates:
                value=_primitive(expr,batch,ctx)
                if value is NotImplemented:
                    for i,row in enumerate(batch.records(tuple(batch.columns))):
                        # Evaluate all rows, as the scalar stream does.
                        keep[i]=keep[i] and expr.evaluate(row,ctx) is True
                else:keep &= value
            ids=np.flatnonzero(keep)
            if len(ids):
                columns={n:(batch.columns[n][ids] if isinstance(batch.columns[n],np.ndarray) else ColumnView(batch.columns[n],ids)) for n in fields}
                yield Columns(columns,len(ids))


def _select(parent,names):
    for batch in parent:yield Columns({n:batch.columns[source] for n,source in names.items()},batch.size)


def _record_batches(records,frame,fields,ctx):
    from mipframe.data.relations import RelationBuilder
    iterator=iter(records);block=_block(ctx,fields)
    while True:
        with ctx.work.fork() as scratch:
            builder=RelationBuilder(scratch,{n:frame.schema[n] for n in fields})
            for _ in range(block):
                row=next(iterator,None)
                if row is None:break
                builder.append(row)
            if not builder.count:return
            table=builder.finish()
            yield Columns(table.columns,len(table))
