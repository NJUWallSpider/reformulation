"""One exact relational executor over bounded typed storage."""
from fractions import Fraction
from mipframe.core.errors import MIPFrameError, QueryMemoryError, MaterializationLimitError
from mipframe.core.resources import memory_bytes, size
from mipframe.data.runtime import WorkContext
from mipframe.data.relations import build, search_range, match_ranges, scalar


def numeric(value):
    return None if value is None else float(value) if value in ('inf','-inf') else Fraction(value)

def pack(value,typ):
    if value is None:return None
    return str(value) if typ.kind in ('number','int') else int(value) if typ.kind=='bool' else value

def unpack(value,typ):
    if value is None:return None
    return numeric(value) if typ.kind=='number' else int(value) if typ.kind=='int' else bool(value) if typ.kind=='bool' else value


class Context:
    @property
    def query_context(self):return self
    def __init__(self,models=(),memory_mb=None,disk_mb=None,*,work=None):
        from mipframe.options import options
        self.work=work.fork() if work is not None else WorkContext(options.query.memory_mb if memory_mb is None else memory_mb,options.query.disk_mb if disk_mb is None else disk_mb)
        self.memory=self.work.memory;self.models=tuple(models)
        self._closed=False;self.retained=False;self.staged={};self.members={};self.literal_members={};self._staged_frames={}
        from mipframe.store.lifecycle import register
        for m in self.models:register(m,self)
    def guard(self):
        from mipframe.check.budget import current_budget
        budget=current_budget()
        if budget is not None:budget.poll()
        for model in self.models:model.model
        if self._closed:raise MIPFrameError('Query storage is closed')
    def check_size(self,row):
        if size(row)>self.memory//4:raise QueryMemoryError('A query value or record exceeds the working-memory budget')
    def stage(self,frame,fields=None):
        self.guard();fields=tuple(frame.schema if fields is None else fields);key=(id(frame),fields)
        if key not in self.staged:
            from mipframe.data.relations import Relation
            frame._guard()
            self._staged_frames[id(frame)]=frame
            schema={n:frame.schema[n] for n in fields}
            if frame._op=='source' and hasattr(frame,'_typed_relation'):
                source=frame._typed_relation
                table=Relation({n:source.columns[n] for n in fields},len(source),self.work,source.max_record)
            elif frame._op=='source' and hasattr(frame,'_array_columns'):
                source=frame._array_columns
                table=Relation({n:source.columns[n] for n in fields},source.size,self.work)
            elif frame._op=='select' and all(frame._params['expressions'][n].op=='col' for n in fields):
                expressions=frame._params['expressions'];needed=tuple(dict.fromkeys(expressions[n].args[0] for n in fields))
                source,_=self.stage(frame._parents[0],needed)
                table=Relation({n:source.columns[expressions[n].args[0]] for n in fields},len(source),self.work,source.max_record)
            else:
                from mipframe.frame.stream import batches
                from mipframe.data.relations import RelationBuilder
                stream=batches(frame,fields,self)
                if stream is None:table=build(self.work,schema,frame._raw(fields,self))
                else:
                    builder=RelationBuilder(self.work,schema)
                    for batch in stream:builder.extend(batch)
                    table=builder.finish()
            self.staged[key]=(table,schema)
        return self.staged[key]
    def contains(self,frame,ident):
        key=frame.key[0]
        if id(frame) not in self.members:
            table,_=self.stage(frame,(key,));self.members[id(frame)]=(table,table.order((key,)))
        table,ids=self.members[id(frame)]
        lo,hi=search_range(table,ids,(key,),(ident,))
        return lo<hi
    def contains_literals(self,expression,value):
        from mipframe.frame.schema import dtype
        import numpy as np
        key=id(expression)
        if key not in self.literal_members:
            table=build(self.work,{'value':dtype(value)},({'value':item.evaluate({},self)} for item in expression.args[1]))
            order=table.order(('value',));column=table.columns['value']
            has_null=False if isinstance(column,np.ndarray) else any(x is None for x in column)
            self.literal_members[key]=(table,order,has_null,expression)
        table,order,has_null,_=self.literal_members[key]
        column=table.columns['value']
        if isinstance(column,np.ndarray) and column.dtype.kind in 'iub':
            # Avoid Python binary-search row dictionaries for primitive keys.
            # Equality stays exact, including a rational or bigint probe value.
            try:integer=int(value)
            except (ValueError,OverflowError,TypeError):return False
            if integer!=value or not -(2**63)<=integer<2**63:return False
            pos=int(np.searchsorted(column,integer,sorter=order.view(np.int64)))
            return pos<len(order) and int(column[int(order[pos])])==integer
        lo,hi=search_range(table,order,('value',),(value,))
        return True if lo<hi else None if has_null else False
    def clear_caches(self):
        self.staged.clear();self.members.clear();self.literal_members.clear();self._staged_frames.clear()
    def close(self):
        if self._closed:return
        self.clear_caches();self.work.close();self._closed=True
    def __del__(self):
        if hasattr(self,'_closed'):self.close()
    def __enter__(self):return self
    def __exit__(self,*_):
        if not self.retained:self.close()


def blocking(frame,fields,ctx):
    op,p=frame._op,frame._params;parent=frame._parents[0]
    if op=='join':yield from join(frame,fields,ctx);return
    if op=='group':yield from group(frame,fields,ctx);return
    needed=tuple(parent.schema) if op=='distinct' else tuple(dict.fromkeys((*fields,*p.get('by',()))))
    table,_=ctx.stage(parent,needed)
    if op=='sort':ids=table.order(p['by'],p['descending'])
    elif op=='slice':ids=range(len(table))[p['slice']]
    elif op=='distinct':
        from mipframe.data.buffers import Buffer
        from mipframe.data.sorting import lexorder
        b=Buffer(ctx.work,'<u8')
        for _,positions in table.groups(needed):b.append(int(positions[0]))
        chosen=b.finish();order=lexorder(ctx.work,(chosen,));ids=(int(chosen[i]) for i in order)
    elif op=='sample':
        import random
        if p['n']>len(table):raise ValueError('Sample larger than population')
        rng=random.Random(p['seed'])
        ranks=build(ctx.work,{'rank':'int'},({'rank':rng.getrandbits(128)} for _ in range(len(table))))
        rank=ranks.order(('rank',));selected=rank[:p['n']]
        from mipframe.data.sorting import lexorder
        order=lexorder(ctx.work,(selected,));ids=(int(selected[i]) for i in order)
    else:raise NotImplementedError(op)
    yield from table.records(fields,ids)


def group(frame,fields,ctx):
    p=frame._params;parent=frame._parents[0];keys=p['keys']
    needed=tuple(dict.fromkeys((*keys,*(n for a in p['aggregates'].values() if a.expr is not None for n in a.expr.dependencies))))
    table,_=ctx.stage(parent,needed)
    groups=table.groups(keys) if keys else (((),range(len(table))),)
    for key,ids in groups:
        row={}
        for n in fields:
            if n in keys:row[n]=key[keys.index(n)];continue
            a=p['aggregates'][n]
            def values():
                for i in ids:
                    value=1 if a.expr is None else a.expr.evaluate(table.row(int(i)),ctx)
                    if value is not None:yield value
            if a.op=='nunique':
                with ctx.work.fork() as scratch:
                    t=build(scratch,{'v':frame.schema[n] if a.expr is None else a.expr.type(parent.schema)},({'v':v} for v in values()))
                    value=sum(1 for _ in t.groups(('v',)))
            elif a.op=='count':value=sum(1 for _ in values())
            elif a.op=='sum':
                from mipframe.store.numbers import exact_binary
                value=0
                for v in values():value=exact_binary('add',value,v);ctx.check_size(value)
            else:value=(min if a.op=='min' else max)(values(),default=None)
            row[n]=value
        yield row


def join(frame,fields,ctx,*,as_batches=False):
    import json
    from mipframe.frame.schema import DType
    from mipframe.check.frame import report
    from mipframe.check.report import CardinalityError
    left,right=frame._parents;p=frame._params;pairs=p['on'];ln,rn=tuple(pairs),tuple(pairs.values())
    lf=tuple(dict.fromkeys((*ln,*(n for n in fields if n in left.schema))))
    rf=tuple(dict.fromkeys((*rn,*(n for n in fields if n in right.schema and n not in left.schema))))
    l,ls=ctx.stage(left,lf);r,rs=ctx.stage(right,rf)
    li,ri=l.order(ln),r.order(rn);card,req=p['cardinality'],p['require']
    lower,upper,right_matched=match_ranges(l,r,ln,rn,li,ri)
    def violations():
        for side,t,order,keys,types,unique in (
            ('left',l,li,ln,ls,card in ('1:1','1:m')),
            ('right',r,ri,rn,rs,card in ('1:1','m:1'))):
            def text(k):return json.dumps([pack(v,types[n]) for n,v in zip(keys,k)])
            if unique:
                for key,ids in t.duplicate_groups(keys,order):
                    yield dict(side=side,reason='duplicate key',key=text(key),count=len(ids))
            if req in (side,'both'):
                for i in range(len(t)):
                    matched=lower[i]!=upper[i] if side=='left' else right_matched[i]
                    if not matched:yield dict(side=side,reason='unmatched',key=text(t.key(i,keys)),count=1)
    schema={'side':DType('str'),'reason':DType('str'),'key':DType('str'),'count':DType('int')}
    retained=ctx.retained
    checked=report(ctx,violations(),schema,dict(operation='join',scope='full declared inputs',cardinality=card,require=req),relations={'left':left,'right':right})
    if not checked.ok:
        checked._recipe=dict(kind='execution',frame_object=frame)
        raise CardinalityError('Join cardinality or match requirement failed',checked)
    ctx.retained=retained
    left_columns=tuple((n,l.columns[n]) for n in fields if n in l.columns)
    right_columns=tuple((n,r.columns[n]) for n in fields if n not in l.columns)
    if as_batches:
        from mipframe.data.relations import unique_join_batches
        yield from unique_join_batches(ctx.work,dict(left_columns),dict(right_columns),lower,upper,ri,outer=p['how']=='left')
        return
    for i in range(len(l)):
        lo,hi=int(lower[i]),int(upper[i])
        matches=ri[lo:hi] if lo<hi else (-1,) if p['how']=='left' else ()
        for j in matches:
            row={n:scalar(c[i]) for n,c in left_columns}
            row.update((n,None if j<0 else scalar(c[int(j)])) for n,c in right_columns)
            yield row
