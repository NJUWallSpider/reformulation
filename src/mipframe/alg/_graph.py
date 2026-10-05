"""Typed induced CSR search graphs with exact weights and independent checks."""
from fractions import Fraction
import numpy as np
from mipframe.execution.graph import Graph
from mipframe.frame.array_source import read_columns,frame_from_columns
from mipframe.data.batch import scalar
from mipframe.frame.schema import DType,SchemaError


def checked(ctx,frame,fields,unique=(),nonnull=()):
    columns=read_columns(frame,fields,ctx)
    from mipframe.data.relations import Relation,build
    table=Relation(columns.columns,columns.size,ctx.work)
    def failures():
        if unique:
            for _,ids in table.groups(unique):
                if len(ids)>1:
                    for i in ids[1:]:yield table.row(int(i),fields)
        for i in range(columns.size):
            if any(columns.columns[n][i] is None for n in nonnull):yield table.row(i,fields)
    bad=build(ctx.work,{n:frame.schema[n] for n in fields},failures())
    if len(bad):ctx.invalid('Duplicate keys or missing values',{n:frame.schema[n] for n in fields},bad.records())
    return columns


class SearchGraph(Graph):
    def __init__(self,ctx,E,u,v,vertices,scope,*,directed,score=None,value='value'):
        super().__init__(ctx,E,u,v,vertices)
        from mipframe.data.buffers import Buffer
        from mipframe.data.relations import build,stable_order
        from mipframe.data.sorting import lexorder
        from mipframe.data.columns import ExactBuilder
        ids=Buffer(ctx.work,'<i8')
        for i in self.selection(scope):ids.append(int(i))
        self.active=ids.finish();self.directed=directed;self.size=len(self.active)
        active=ctx.work.array(self.n,np.bool_,zero=True)
        for v in self.active:active[int(v)]=True
        self.ignored_loops=0
        def pairs():
            for u,v in zip(self.u,self.v):
                u,v=int(u),int(v)
                if not active[u] or not active[v]:continue
                if not directed and u==v:self.ignored_loops+=1;continue
                yield dict(u=u,v=v)
                if not directed:yield dict(u=v,v=u)
        with ctx.work.fork() as scratch:
            table=build(scratch,{'u':'int','v':'int'},pairs())
            uu,vv=Buffer(ctx.work,'<i8'),Buffer(ctx.work,'<i8')
            for (u,v),_ in table.groups(('u','v')):uu.append(u);vv.append(v)
        self.u,self.v=uu.finish(),vv.finish()
        self.ptr[:]=0;self.rptr[:]=0
        for u,v in zip(self.u,self.v):self.ptr[int(u)+1]+=1;self.rptr[int(v)+1]+=1
        np.cumsum(self.ptr,out=self.ptr);np.cumsum(self.rptr,out=self.rptr)
        rev=lexorder(ctx.work,(self.v,self.u));self.reverse=ctx.work.array(len(rev),'<i8')
        for i,j in enumerate(rev):self.reverse[i]=self.u[int(j)]
        ctx.work.release(rev)
        self.weight_ids=None;self.weights=None
        if score is not None:
            if not self.typ.compatible(score.schema['vertex']):raise SchemaError('Score vertex type or snapshot differs')
            if score.schema[value].kind not in ('number','int'):raise SchemaError('Scores must be finite exact numbers')
            columns=checked(ctx,score,('vertex',value),unique=('vertex',),nonnull=('vertex',))
            builder=ExactBuilder(ctx.work);self.weight_ids=ctx.work.array(self.n,'<i8');self.weight_ids[:]=-1
            def invalid():
                for vertex,w in zip(columns.columns['vertex'],columns.columns[value]):
                    vertex,w=scalar(vertex),scalar(w)
                    if vertex not in self.lookup or w is None or w in (float('inf'),-float('inf')):yield dict(vertex=vertex,**{value:w})
                    else:self.weight_ids[self.lookup[vertex]]=len(builder);builder.append(w)
            bad=build(ctx.work,{n:score.schema[n] for n in ('vertex',value)},invalid())
            if len(bad):ctx.invalid('Scores must be finite and in the domain',{n:score.schema[n] for n in ('vertex',value)},bad.records())
            bad=build(ctx.work,{'vertex':self.typ},(dict(vertex=self.value(v)) for v in self.active if self.weight_ids[int(v)]<0))
            if len(bad):ctx.invalid('Scores must cover the search scope',{'vertex':self.typ},bad.records())
            self.weights=builder.freeze()
        def compare(i,j):
            a,b=int(self.active[i]),int(self.active[j]);wa,wb=self.weight(a),self.weight(b)
            if wa!=wb:return -1 if wa>wb else 1
            da,db=len(self.neighbors(a)),len(self.neighbors(b))
            return -1 if da>db else 1 if da<db else (a>b)-(a<b)
        order=stable_order(ctx.work,len(self.active),compare)
        self.priority=ctx.work.array(len(order),'<i8')
        for i,j in enumerate(order):self.priority[i]=self.active[int(j)]
        ctx.work.release(order)
        self.rank=ctx.work.array(self.n,'<i8');self.rank[:]=-1
        for i,v in enumerate(self.priority):self.rank[int(v)]=i
        ctx.scope_info=f'{"induced selection" if scope is not None else "full declared domain"}: {self.size} of {self.n} vertices'
        ctx.paths.add('CSR search with explicit undo stack')

    def weight(self,v):return 1 if self.weights is None else self.weights[int(self.weight_ids[int(v)])]
    def value(self,v):return scalar(self.values[int(v)])
    def domain(self):return self.ctx.publish({'vertex':self.typ},({'vertex':self.value(v)} for v in self.active),key=('vertex',),limited=False)
    def adjacent(self,u,v):
        neighbors=self.neighbors(u);p=int(np.searchsorted(neighbors,v))
        return p<len(neighbors) and int(neighbors[p])==v
    def verify_clique(self,sequence):
        if any(not self.adjacent(u,v) for i,u in enumerate(sequence) for v in sequence[i+1:]):raise RuntimeError('Internal clique verification failed')
        if any(v not in sequence and all(self.adjacent(int(v),u) for u in sequence) for v in self.active):raise RuntimeError('Internal maximality verification failed')
    def verify_cycle(self,path,chordless):
        n=len(path)
        if any(not self.adjacent(path[i],path[(i+1)%n]) for i in range(n)):raise RuntimeError('Internal cycle verification failed')
        return not chordless or not any(self.adjacent(path[i],path[j]) for i in range(n) for j in range(i+2,n) if (i,j)!=(0,n-1))
