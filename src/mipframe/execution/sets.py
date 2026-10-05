"""Exact set relations over sorted membership arrays and inverted indexes."""
import numpy as np
from mipframe.frame.schema import DType,SchemaError
from mipframe.frame.array_source import read_columns,frame_from_columns
from mipframe.data.batch import scalar
from mipframe.execution.graph import key_column



def fingerprint(value):return hash(value)


class ExactKey:
    def __init__(self,value):self.value=value
    def __hash__(self):return fingerprint(self.value)
    def __eq__(self,other):return isinstance(other,ExactKey) and self.value==other.value


def checked(ctx,frame,fields,*,unique=(),nonnull=()):
    if not set(fields)<=set(frame.schema):raise SchemaError('Missing input columns')
    from mipframe.data.relations import Relation,build
    b=read_columns(frame,fields,ctx);table=Relation(b.columns,b.size,ctx.work)
    schema={n:frame.schema[n] for n in fields}
    nullable=tuple(n for n in nonnull if not isinstance(b.columns[n],np.ndarray) or b.columns[n].dtype.hasobject)
    nulls=build(ctx.work,schema,(table.row(i) for i in range(b.size) if any(b.columns[n][i] is None for n in nullable))) if nullable else ()
    if len(nulls):ctx.invalid('Null identity',schema,nulls.records())
    if unique:
        schema={**{n:schema[n] for n in unique},'count':DType('int')}
        order=table.order(unique)
        try:duplicate=build(ctx.work,schema,(dict(zip(unique,k),count=len(ids)) for k,ids in table.duplicate_groups(unique,order)))
        finally:ctx.work.release(order)
        if len(duplicate):ctx.invalid('Duplicate identity',schema,duplicate.records())
    return b


class Domain:
    def __init__(self,ctx,typ,frame,observed,name):
        from mipframe.data.relations import build,Relation,ColumnBuilder
        from mipframe.data.csr_graph import ScalarLookup
        self.ctx,self.typ,self.name=ctx,typ,name
        if frame is not None:
            key=key_column(frame)
            if not typ.compatible(frame.schema[key]):raise SchemaError('Domain identity differs')
            values=checked(ctx,frame,(key,),nonnull=(key,)).columns[key]
        else:values=observed
        from mipframe.data.relations import unique_values
        self.values=unique_values(ctx.work,values,typ.kind)
        self.lookup=ScalarLookup(self.values)
        if frame is not None:
            positions=self.lookup.locate(observed,ctx.work)
            absent=build(ctx.work,{name:typ},({name:scalar(observed[i])} for i in range(len(positions)) if positions[i]<0))
            ctx.work.release(positions)
            if len(absent):ctx.invalid('Identity outside declared domain',{name:typ},absent.records())
    def __len__(self):return len(self.values)
    def frame(self):return self.ctx.publish({self.name:self.typ},({self.name:scalar(v)} for v in self.values),key=(self.name,),limited=False)
    def selection(self,frame):
        from mipframe.data.ids import IDSet
        from mipframe.data.relations import build
        if frame is None:return range(len(self))
        key=key_column(frame)
        if not self.typ.compatible(frame.schema[key]):raise SchemaError('Selection identity differs')
        values=checked(self.ctx,frame,(key,),nonnull=(key,)).columns[key]
        missing=build(self.ctx.work,{key:self.typ},({key:scalar(v)} for v in values if scalar(v) not in self.lookup))
        if len(missing):self.ctx.invalid('Selection outside domain',{key:self.typ},missing.records())
        return IDSet((self.lookup[scalar(v)] for v in values),work=self.ctx.work).array


class Membership:
    def __init__(self,ctx,frame,set_field,member,sets=None,vertices=None,value=None):
        from mipframe.data.sorting import lexorder
        from mipframe.data.relations import ColumnView
        fields=(set_field,member) if value is None else (set_field,member,value)
        b=checked(ctx,frame,fields,unique=(set_field,member),nonnull=(set_field,member))
        self.ctx=ctx;self.name=set_field;self.member=member
        self.sets=Domain(ctx,frame.schema[set_field],sets if sets is not None else frame.domains.get(set_field),b.columns[set_field],set_field)
        self.members=Domain(ctx,frame.schema[member],vertices if vertices is not None else frame.domains.get(member),b.columns[member],member)
        ss=self.sets.lookup.locate(b.columns[set_field],ctx.work);mm=self.members.lookup.locate(b.columns[member],ctx.work)
        order=lexorder(ctx.work,(ss,mm));self.ids=ctx.work.array(b.size,'<i8')
        self.ptr=ctx.work.array(len(self.sets)+1,'<i8',zero=True);self.rptr=ctx.work.array(len(self.members)+1,'<i8',zero=True)
        block=max(1,min(ctx.work.memory//256,ctx.work.available//128))
        with ctx.work.reserve(block*64):
            for start in range(0,b.size,block):
                ids=order[start:start+block];s=ss[ids];m=mm[ids]
                self.ids[start:start+len(ids)]=m;np.add.at(self.ptr,s+1,1);np.add.at(self.rptr,m+1,1)
        np.cumsum(self.ptr,out=self.ptr);np.cumsum(self.rptr,out=self.rptr)
        self.values=None if value is None else ColumnView(b.columns[value],order)
        reverse=lexorder(ctx.work,(mm,ss));self.reverse=ctx.work.array(b.size,'<i8')
        with ctx.work.reserve(block*64):
            for start in range(0,b.size,block):self.reverse[start:start+block]=ss[reverse[start:start+block]]
        for a in (ss,mm,reverse):ctx.work.release(a)
        if value is None:ctx.work.release(order)
    def support(self,i):return self.ids[int(self.ptr[i]):int(self.ptr[i+1])]
    def inverted(self,i):return self.reverse[int(self.rptr[i]):int(self.rptr[i+1])]
    def candidates(self,frame):
        from mipframe.data.relations import build
        b=checked(self.ctx,frame,('left','right'),unique=('left','right'),nonnull=('left','right'))
        for field in ('left','right'):
            if not self.sets.typ.compatible(frame.schema[field]):raise SchemaError('Pair endpoint identity differs')
            absent=build(self.ctx.work,{field:self.sets.typ},({field:scalar(v)} for v in b.columns[field] if scalar(v) not in self.sets.lookup))
            if len(absent):self.ctx.invalid('Pair outside domain',{field:self.sets.typ},absent.records())
        table=build(self.ctx.work,{'a':'int','b':'int'},(dict(a=self.sets.lookup[scalar(a)],b=self.sets.lookup[scalar(b)]) for a,b in zip(b.columns['left'],b.columns['right'])))
        order=table.order(('a','b'))
        return ((r['a'],r['b']) for r in table.records(ids=order))
    def intersection(self,a,b):return self.combined(a,b,'intersection')
    def intersection_count(self,a,b):
        x,y=self.support(a),self.support(b)
        if len(x)>len(y):x,y=y,x
        if not len(x):return 0
        block=max(1,min(len(x),self.ctx.work.memory//256,self.ctx.work.available//128))
        total=0
        with self.ctx.work.reserve(block*64):
            for start in range(0,len(x),block):
                values=x[start:start+block];positions=np.searchsorted(y,values)
                total+=int(np.count_nonzero((positions<len(y))&(y[np.minimum(positions,len(y)-1)]==values)))
        return total
    def combined(self,a,b,op):
        if op not in ('intersection','union','left_only','right_only'):raise ValueError('Unknown member operation')
        x,y=self.support(a),self.support(b);i=j=0
        while i<len(x) or j<len(y):
            if j==len(y) or i<len(x) and x[i]<y[j]:
                if op in ('union','left_only'):yield int(x[i])
                i+=1
            elif i==len(x) or y[j]<x[i]:
                if op in ('union','right_only'):yield int(y[j])
                j+=1
            else:
                if op in ('union','intersection'):yield int(x[i])
                i+=1;j+=1


def packed_key(values):
    import json,struct
    from mipframe.frame.schema import encode
    for value in values:
        raw=json.dumps(value,default=encode,sort_keys=True,separators=(',',':')).encode('utf8')
        yield struct.pack('<Q',len(raw));yield raw


def membership_labels(s):
    from mipframe.data.intern import BytePool
    from mipframe.data.buffers import Buffer
    pool=BytePool(s.ctx.work);representatives=Buffer(s.ctx.work,'<u8');labels=s.ctx.work.array(len(s.sets),'<u8')
    for i in range(len(s.sets)):
        lo,hi=map(int,s.ptr[i:i+2])
        payload=(int(v) for v in s.ids[lo:hi]) if s.values is None else ((int(s.ids[j]),scalar(s.values[j])) for j in range(lo,hi))
        label=pool.intern(packed_key(payload))
        if label==len(representatives):representatives.append(i)
        labels[i]=representatives[label]
    return labels


def run(ctx,name,**p):
    field=p.pop('set','set');member=p.pop('member','member')
    frame=p.pop('S',p.pop('membership',None))
    s=Membership(ctx,frame,field,member,p.pop('sets',None),p.pop('vertices',None),p.get('value') if name=='equal_groups' else None)
    sv=lambda i:scalar(s.sets.values[i]);mv=lambda i:scalar(s.members.values[i])
    domain=s.sets.frame();domains={'left':domain,'right':domain}
    if name=='equal_groups':
        if field=='class':raise SchemaError('Rename the group key')
        classes=membership_labels(s)
        return ctx.publish({field:s.sets.typ,'class':s.sets.typ},({field:sv(i),'class':sv(c)} for i,c in enumerate(classes)),key=(field,),domains={field:domain,'class':domain})
    if name=='components':
        if member=='component':raise SchemaError('Rename member to avoid component')
        from mipframe.ops._kernels.union_find import DSU
        dsu=DSU(len(s.members),ctx)
        for i in range(len(s.sets)):
            ids=s.support(i)
            for v in ids[1:]:dsu.union(int(ids[0]),int(v))
        d=s.members.frame()
        return ctx.publish({member:s.members.typ,'component':s.members.typ},({member:mv(i),'component':mv(dsu.representative(i))} for i in range(len(s.members))),key=(member,),domains={member:d,'component':d})
    if name=='pairs':
        relation=p['relation'];ordered=p['ordered'];include=p['include_self']
        if relation not in ('equal','subset','strict_subset','superset','strict_superset','overlap','disjoint'):raise ValueError('Unknown set relation')
        if type(include) is not bool or ordered is not None and type(ordered) is not bool:raise TypeError('ordered/include_self must be boolean')
        if p['candidates'] is not None:
            if p['left'] is not None or p['right'] is not None or ordered is False:raise ValueError('Explicit candidate direction conflicts with selection')
            pairs=s.candidates(p['candidates'])
        else:
            left=s.sets.selection(p['left']);right=s.sets.selection(p['right'])
            symmetric=relation in ('equal','overlap','disjoint')
            same=np.array_equal(left,right)
            ordered=not(symmetric and same) if ordered is None else ordered
            if not ordered and (not symmetric or not same):raise ValueError('Unordered pairs require symmetric relation and identical domains')
            if relation=='equal':
                from mipframe.data.ids import IDSet
                from mipframe.data.relations import build
                labels=membership_labels(s);selected_right=IDSet(right,work=ctx.work)
                from itertools import chain
                counts=build(ctx.work,{'color':'int','side':'int'},chain(
                    (dict(color=int(labels[i]),side=0) for i in left),(dict(color=int(labels[i]),side=1) for i in right),
                    (dict(color=int(labels[i]),side=2) for i in left if i in selected_right)))
                total=0
                for _,ids in counts.groups(('color',)):
                    n=sum(counts.columns['side'][i]==0 for i in ids);r=sum(counts.columns['side'][i]==1 for i in ids);shared=len(ids)-n-r
                    total+=n*r-(0 if include else shared) if ordered else n*(n-1)//2+(n if include else 0)
                    ctx.enforce(int(total))
            reverse=relation in ('superset','strict_superset')
            small,large=(right,left) if reverse else (left,right)
            from mipframe.data.ids import IDSet
            large_set=IDSet(large,work=ctx.work)
            def candidates():
                for a in small:
                    x=s.support(a)
                    if len(x) and relation in ('subset','strict_subset','superset','strict_superset','equal'):
                        rare=min(x,key=lambda v:int(s.rptr[v+1]-s.rptr[v]))
                        options=(int(v) for v in s.inverted(int(rare)) if v in large_set)
                    elif relation=='overlap':
                        options=IDSet((int(v) for m in x for v in s.inverted(int(m)) if v in large_set),work=ctx.work)
                    else:options=large
                    for b in options:
                        i,j=(b,a) if reverse else (a,b)
                        if (include or i!=j) and (ordered or i<=j):yield i,j
            pairs=candidates()
        schema={'left':s.sets.typ,'right':s.sets.typ,**{n:DType('int') for n in ('left_n','right_n','intersection','left_only','right_only','union_n')}}
        def batches():
            from mipframe.data.buffers import Buffer
            from mipframe.data.relations import Relation
            from mipframe.data.batch import Columns
            # Internal IDs and cardinalities are already validated integers.
            # Append directly to bounded typed columns instead of constructing
            # and measuring a Python record for every pair.
            columns={n:Buffer(ctx.work,'<i8') for n in ('a','b','an','bn','k')}
            count=0
            for a,b in pairs:
                if not include and a==b:continue
                an,bn=len(s.support(a)),len(s.support(b))
                if relation in ('subset','strict_subset') and (an>bn or relation=='strict_subset' and an==bn):continue
                if relation in ('superset','strict_superset') and (an<bn or relation=='strict_superset' and an==bn):continue
                if relation=='equal' and an!=bn:continue
                k=s.intersection_count(a,b)
                yes={'equal':k==an==bn,'subset':k==an,'strict_subset':k==an and an<bn,'superset':k==bn,'strict_superset':k==bn and bn<an,'overlap':k>0,'disjoint':k==0}[relation]
                if yes:
                    count+=1;ctx.enforce(count)
                    for name,value in zip(columns,(a,b,an,bn,k)):columns[name].append(value)
            table=Relation({n:b.finish() for n,b in columns.items()},count,ctx.work);order=table.order(('a','b'))
            block=max(1,min(8192,ctx.work.memory//2048,ctx.work.available//1024))
            from mipframe.data.relations import ColumnView
            for start in range(0,len(order),block):
                with ctx.work.reserve(block*256):
                    ids=order[start:start+block]
                    a,b,an,bn,k=(table.columns[n][ids] for n in ('a','b','an','bn','k'))
                    left=s.sets.values[a] if isinstance(s.sets.values,np.ndarray) else ColumnView(s.sets.values,a)
                    right=s.sets.values[b] if isinstance(s.sets.values,np.ndarray) else ColumnView(s.sets.values,b)
                    yield Columns(dict(left=left,right=right,left_n=an,right_n=bn,intersection=k,left_only=an-k,right_only=bn-k,union_n=an+(bn-k)),len(ids))
        return ctx.publish_batches(schema,batches(),key=('left','right'),domains=domains)
    if name=='members':
        op=p['op']
        if op not in ('union','intersection','left_only','right_only'):raise ValueError('Unknown member operation')
        pairs=s.candidates(p['pairs'])
        return ctx.publish({'left':s.sets.typ,'right':s.sets.typ,'member':s.members.typ},
            (dict(left=sv(a),right=sv(b),member=mv(m)) for a,b in pairs for m in s.combined(a,b,op)),
            key=('left','right','member'),domains={**domains,'member':s.members.frame()})
    if name in ('aggregate','pair_aggregate'):
        if p['agg']!='sum':raise ValueError('Only exact sum is supported')
        by=(p['by'],) if isinstance(p['by'],str) else tuple(p['by'])
        if len(set(by))!=len(by):raise SchemaError('Duplicate group keys')
        values=p['values'];value=p['value'];fields=(member,*by,value)
        if (field in (*by,'value') if name=='aggregate' else any(n in ('left','right','value') for n in by)) or 'value' in by:raise SchemaError('Rename colliding output columns')
        b=checked(ctx,values,fields,unique=(member,*by),nonnull=(member,))
        if not s.members.typ.compatible(values.schema[member]):raise SchemaError('Attribute member identity differs')
        if values.schema[value].kind not in ('int','number'):raise SchemaError('Attribute values must be exact numbers')
        from mipframe.data.relations import build,search_range
        invalid=build(ctx.work,{n:values.schema[n] for n in fields},
            (r for r in b.records(fields) if r[value] is None or r[value] in (-float('inf'),float('inf'))))
        if len(invalid):ctx.invalid('Attribute values must be finite and nonmissing',{n:values.schema[n] for n in fields},invalid.records())
        weights=build(ctx.work,{'_member':'int',**{n:values.schema[n] for n in by},'_value':'number'},
            ({'_member':s.members.lookup[r[member]],'_value':r[value],**{n:r[n] for n in by}}
             for r in b.records(fields) if r[member] in s.members.lookup))
        index=weights.order(('_member',))
        pairs=s.candidates(p['pairs']) if name=='pair_aggregate' else ((i,None) for i in range(len(s.sets)))
        schema=({'left':s.sets.typ,'right':s.sets.typ} if name=='pair_aggregate' else {field:s.sets.typ})|{n:values.schema[n] for n in by}|{'value':DType('number')}
        def records():
            for a,b in pairs:
                def contributions():
                    for m in (s.support(a) if b is None else s.combined(a,b,p['op'])):
                        lo,hi=search_range(weights,index,('_member',),(int(m),))
                        yield from weights.records(ids=index[lo:hi])
                with ctx.work.fork() as work:
                    table=build(work,{'_member':'int',**{n:values.schema[n] for n in by},'_value':'number'},contributions())
                    prefix={field:sv(a)} if b is None else dict(left=sv(a),right=sv(b))
                    groups=table.groups(by) if by else (((),range(len(table))),)
                    for key,ids in groups:
                        total=0
                        for i in ids:total+=table.columns['_value'][int(i)];ctx.check_size(total)
                        yield {**prefix,**dict(zip(by,key)),'value':total}
        key=(field,*by) if name=='aggregate' else ('left','right',*by)
        return ctx.publish(schema,records(),key=key,domains={field:domain} if name=='aggregate' else domains)
    raise NotImplementedError(name)


def equal_groups(ctx,F,*,by,rows,groups=None):
    rows=(rows,) if isinstance(rows,str) else tuple(rows)
    if len(set(rows))!=len(rows) or by=='class':raise SchemaError('Invalid group payload columns')
    fields=tuple(dict.fromkeys((by,*rows)))
    data=checked(ctx,F,fields,nonnull=(by,))
    domain=Domain(ctx,F.schema[by],groups if groups is not None else F.domains.get(by),data.columns[by],by)
    from mipframe.data.relations import Relation,build
    from mipframe.data.intern import BytePool
    from mipframe.data.buffers import Buffer
    table=Relation(data.columns,data.size,ctx.work)
    order=table.order((by,*rows));pool=BytePool(ctx.work);representatives=Buffer(ctx.work,'<u8')
    def records():
        from mipframe.data.relations import search_range
        for i,value in enumerate(domain.values):
            # Prefix lookup uses the same primary group ordering.
            lo,hi=search_range(table,order,(by,),(scalar(value),))
            ident=pool.intern(packed_key(table.key(int(j),rows) for j in order[lo:hi]))
            if ident==len(representatives):representatives.append(i)
            yield {by:scalar(value),'class':scalar(domain.values[int(representatives[ident])])}
    d=domain.frame()
    return ctx.publish({by:domain.typ,'class':domain.typ},records(),key=(by,),domains={by:d,'class':d})
