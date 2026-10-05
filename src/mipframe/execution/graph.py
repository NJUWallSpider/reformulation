"""Deterministic CSR graph kernels over compact integer vertex IDs."""
import heapq
import numpy as np
from mipframe.frame.array_source import read_columns, frame_from_columns
from mipframe.frame.schema import DType, SchemaError, normalize
from mipframe.data.batch import scalar


def key_column(frame):
    if frame.key is not None and len(frame.key)==1:return frame.key[0]
    if len(frame.schema)==1:return next(iter(frame.schema))
    raise SchemaError('Expected a single-key domain')


from mipframe.data.csr_graph import CSRGraph


class Graph(CSRGraph):
    def __init__(self,ctx,E,tail,head,vertices=None):
        self.ctx,self.source,self.typ=ctx,E,E.schema[tail]
        if not self.typ.compatible(E.schema[head]):raise SchemaError('Graph endpoints must have compatible scalar identities')
        columns=read_columns(E,tuple(dict.fromkeys((tail,head))),ctx).columns
        aa,bb=columns[tail],columns[head]
        if any(v is None for c in (aa,bb) for v in c):raise SchemaError('Graph endpoints cannot be null')
        domains=[vertices] if vertices is not None else [E.domains.get(tail),E.domains.get(head)]
        values=[]
        if vertices is None:values.extend(c for c,d in zip((aa,bb),domains) if d is None)
        for d in domains:
            if d is None:continue
            k=key_column(d)
            if not self.typ.compatible(d.schema[k]):raise SchemaError('Vertex domain type differs')
            values.append(read_columns(d,(k,),ctx).columns[k])
        try:super().__init__(ctx.work,aa,bb,values,self.typ.kind)
        except KeyError:
            ctx.invalid('Endpoint outside declared domain',{'column':DType('str'),'value':self.typ},
                (dict(column=n,value=scalar(v)) for n,c in ((tail,aa),(head,bb)) for v in c if v not in self.lookup))
    def domain(self):
        # Output owns a lease independent of the graph scratch.
        return self.ctx.publish({'vertex':self.typ},({'vertex':scalar(v)} for v in self.values),key=('vertex',),limited=False)
    def selection(self,frame):
        if frame is None:return range(self.n)
        key=key_column(frame)
        if not self.typ.compatible(frame.schema[key]):raise SchemaError('Selection type differs')
        values=read_columns(frame,(key,),self.ctx).columns[key]
        missing=(dict(value=scalar(v)) for v in values if v not in self.lookup)
        from mipframe.data.relations import build
        t=build(self.ctx.work,{'value':self.typ},missing)
        if len(t):self.ctx.invalid('Selection outside domain',{'value':self.typ},t.records())
        from mipframe.data.ids import IDSet
        return IDSet((self.lookup[v] for v in values),work=self.ctx.work).array


def bfs(g,source,target=None):
    if not hasattr(g,"_bfs_workspace"):
        g._bfs_workspace=tuple(g.ctx.work.array(g.n,np.int64) for _ in range(3))
    pred,depth,queue=g._bfs_workspace
    pred[:]=-1;depth[:]=-1
    queue[0]=source;head=0;end=1;pred[source]=source;depth[source]=0
    while head<end:
        v=int(queue[head]);head+=1
        if v==target:break
        for raw in g.neighbors(v):
            n=int(raw)
            if pred[n]<0:
                pred[n]=v;depth[n]=depth[v]+1;queue[end]=n;end+=1
    return pred,depth


def labels(g,undirected=False):
    from mipframe.data.graph_kernels import component_labels
    return component_labels(g,g.ctx.work,undirected)


def cycle_error(g,error):
    from mipframe.execution.dag_diagnostics import fail_cycles
    fail_cycles(g,error)


def topological(g,error):
    from mipframe.execution.reduction import topology
    order=topology(g,error);level=g.ctx.work.array(g.n,'<i8',zero=True)
    for raw in order:
        v=int(raw)
        for other in g.neighbors(v):
            n=int(other);level[n]=max(level[n],level[v]+1)
    return order,level


def run(ctx,name,E,**p):
    from mipframe.core.errors import GraphCycleError,NotDAGError
    if name=='transitive_reduction':
        from mipframe.execution.reduction import reduction
        return reduction(ctx,E,**p)
    tail=p.pop('tail',p.pop('u','u'));head=p.pop('head',p.pop('v','v'))
    g=Graph(ctx,E,tail,head,p.pop('vertices',None));domain=g.domain()
    value=lambda i:scalar(g.values[i])
    if name in ('components','scc'):
        field='component' if name=='components' else 'scc';lab=labels(g,name=='components')
        return ctx.publish({'vertex':g.typ,field:g.typ},(dict(vertex=value(i),**{field:value(lab[i])}) for i in range(g.n)),key=('vertex',),domains={'vertex':domain,field:domain})
    if name=='topological_order':
        order,level=topological(g,GraphCycleError)
        return ctx.publish({'vertex':g.typ,'order':DType('int'),'level':DType('int')},(dict(vertex=value(v),order=i,level=int(level[v])) for i,v in enumerate(order)),key=('vertex',),domains={'vertex':domain})
    if name=='path':
        source,target=normalize(p['source'],g.typ),normalize(p['target'],g.typ)
        missing=[dict(column=n,value=v) for n,v in (('source',source),('target',target)) if v not in g.lookup]
        if missing:ctx.invalid('Path endpoints outside domain',{'column':DType('str'),'value':g.typ},missing)
        a,b=g.lookup[source],g.lookup[target]
        pred,depth=bfs(g,a,b);edges=g.ctx.work.array(g.n,'<i8');count=0;v=b
        if pred[b]>=0:
            while v!=a:edges[count]=v;count+=1;v=int(pred[v])
        status=ctx.publish({'source':g.typ,'target':g.typ,'reachable':DType('bool'),'length':DType('int',True)},[dict(source=value(a),target=value(b),reachable=bool(pred[b]>=0),length=None if pred[b]<0 else int(depth[b]))],key=('source','target'),limited=False)
        return ctx.publish({'step':DType('int'),'u':g.typ,'v':g.typ},(dict(step=i,u=value(int(pred[int(v)])),v=value(int(v))) for i,v in enumerate(reversed(edges[:count]))),key=('step',),relations={'status':status},domains={'u':domain,'v':domain})
    if name=='reachability':
        if type(p['include_self']) is not bool:raise TypeError('include_self must be boolean')
        sources=g.selection(p['sources']);targets=g.selection(p['targets'])
        def records():
            for u in sources:
                pred,depth=bfs(g,u)
                cyclic=any(pred[int(v)]>=0 for v in g.neighbors(u,True))
                for v in targets:
                    if pred[v]>=0 and (u!=v or p['include_self']):yield dict(source=value(u),target=value(v))
        return ctx.publish({'source':g.typ,'target':g.typ},records(),key=('source','target'),domains={'source':domain,'target':domain})
    raise NotImplementedError(name)
