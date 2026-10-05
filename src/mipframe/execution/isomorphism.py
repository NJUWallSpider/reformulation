"""Exact joint refinement followed by label-preserving bijection search."""
from collections import Counter,defaultdict
import numpy as np
from mipframe.execution.sets import checked,Domain
from mipframe.data.batch import scalar
from mipframe.frame.schema import DType,SchemaError


class Instance:
    def __init__(self,ctx,vertices,colors,edges):
        self.vertices=np.asarray(vertices,dtype=np.int64)
        self.lookup={v:i for i,v in enumerate(vertices)}
        self.colors=np.asarray(colors,dtype=np.int64)
        ordered=sorted((self.lookup[u],self.lookup[v],label) for (u,v),label in edges.items())
        self.u=np.asarray([u for u,v,l in ordered],dtype=np.int64)
        self.v=np.asarray([v for u,v,l in ordered],dtype=np.int64)
        self.labels=np.asarray([l for u,v,l in ordered],dtype=np.int64)
        self.ptr=ctx.work.array(len(vertices)+1,np.uint64,zero=True)
        np.cumsum(np.bincount(self.u,minlength=len(vertices)),out=self.ptr[1:])
    def label(self,u,v):
        start,stop=map(int,self.ptr[u:u+2]);p=int(np.searchsorted(self.v[start:stop],v))+start
        return int(self.labels[p]) if p<stop and self.v[p]==v else None


def refine(ctx,instances):
    previous=len({int(c) for g in instances for c in g.colors})
    while True:
        keys={};results=[]
        for g in instances:
            tokens=[Counter() for _ in g.colors]
            for u,v,label in zip(g.u,g.v,g.labels):
                tokens[u][0,int(g.colors[v]),int(label)]+=1
                tokens[v][1,int(g.colors[u]),int(label)]+=1
            result=np.empty(len(g.colors),dtype=np.int64)
            for v,color in enumerate(g.colors):
                ctx.tick();key=(int(color),tuple(sorted(tokens[v].items())))
                result[v]=keys.setdefault(key,len(keys))
            results.append(result)
        for g,result in zip(instances,results):g.colors=result
        if len(keys)==previous:return
        previous=len(keys)


def match(ctx,g,r):
    if Counter(g.colors)!=Counter(r.colors):return None
    size=len(g.colors);hist=Counter(g.colors)
    order=sorted(range(size),key=lambda v:(hist[g.colors[v]],int(g.vertices[v])))
    candidates=[np.flatnonzero(r.colors==g.colors[v]) for v in order]
    mapping=np.full(size,-1,dtype=np.int64);used=np.zeros(size,dtype=bool)
    cursors=np.zeros(size,dtype=np.int64);depth=0
    while depth>=0:
        if depth==size:
            if any(g.label(u,v)!=r.label(int(mapping[u]),int(mapping[v])) for u in range(size) for v in range(size)):
                raise RuntimeError('Internal isomorphism verification failed')
            return mapping
        u=order[depth];choices=candidates[depth]
        if cursors[depth]>=len(choices):
            cursors[depth]=0;depth-=1
            if depth>=0:
                old=order[depth];used[mapping[old]]=False;mapping[old]=-1
            continue
        v=int(choices[cursors[depth]]);cursors[depth]+=1
        if used[v]:continue
        ctx.tick();good=g.label(u,u)==r.label(v,v)
        if good:
            for previous in order[:depth]:
                target=int(mapping[previous])
                if g.label(u,previous)!=r.label(v,target) or g.label(previous,u)!=r.label(target,v):good=False;break
        if good:mapping[u]=v;used[v]=True;depth+=1
    return None


def classify(ctx,E,*,vertices,graph='graph',u='u',v='v',graphs=None,directed=False,vertex_labels=(),edge_labels=(),**_):
    from mipframe.alg._isomorphism import labels_tuple
    if graph in ('class','vertex','representative_vertex') or u==v or graph in (u,v):raise SchemaError('Graph and endpoint columns must be distinct and not reserved')
    if type(directed) is not bool:raise TypeError('directed must be boolean')
    vertex_labels,edge_labels=labels_tuple(vertex_labels),labels_tuple(edge_labels)
    vf=(graph,'vertex',*vertex_labels);ef=(graph,u,v,*edge_labels)
    vc=checked(ctx,vertices,vf,unique=(graph,'vertex'),nonnull=(graph,'vertex'))
    ec=checked(ctx,E,ef,nonnull=(graph,u,v))
    if not vertices.schema[graph].compatible(E.schema[graph]) or not vertices.schema['vertex'].compatible(E.schema[u]) or not vertices.schema['vertex'].compatible(E.schema[v]):raise SchemaError('Graph and vertex identities must have compatible types')
    groups=Domain(ctx,vertices.schema[graph],graphs if graphs is not None else vertices.domains.get(graph),
        list(vc.columns[graph])+list(ec.columns[graph]),graph)
    keys=Domain(ctx,vertices.schema['vertex'],None,vc.columns['vertex'],'vertex')
    node_data=[{} for _ in groups.values];edge_data=[{} for _ in groups.values]
    label_ids={};edge_label_ids={}
    for i in range(vc.size):
        g=groups.lookup[scalar(vc.columns[graph][i])];node=keys.lookup[scalar(vc.columns['vertex'][i])]
        label=tuple(scalar(vc.columns[n][i]) for n in vertex_labels)
        node_data[g][node]=label_ids.setdefault(label,len(label_ids))
    bad=[];conflicts=defaultdict(set)
    for i in range(ec.size):
        name=scalar(ec.columns[graph][i]);g=groups.lookup[name]
        a,b=scalar(ec.columns[u][i]),scalar(ec.columns[v][i])
        missing=False
        for x in (a,b):
            if x not in keys.lookup or keys.lookup[x] not in node_data[g]:bad.append({graph:name,'vertex':x});missing=True
        if missing:continue
        a,b=keys.lookup[a],keys.lookup[b]
        label=tuple(scalar(ec.columns[n][i]) for n in edge_labels)
        label=edge_label_ids.setdefault(label,len(edge_label_ids))
        for left,right in ((a,b),) if directed or a==b else ((a,b),(b,a)):
            conflicts[g,left,right].add(label);edge_data[g][left,right]=label
    if bad:ctx.invalid('Edge endpoint absent from its declared graph',{graph:groups.typ,'vertex':keys.typ},bad)
    count_name='label_count'
    while count_name in (graph,u,v):count_name='_'+count_name
    bad=[{graph:scalar(groups.values[g]),u:scalar(keys.values[a]),v:scalar(keys.values[b]),count_name:len(labels)} for (g,a,b),labels in sorted(conflicts.items()) if len(labels)>1]
    if bad:ctx.invalid('Parallel edges have conflicting labels',{graph:groups.typ,u:keys.typ,v:keys.typ,count_name:DType('int')},bad)
    instances=[Instance(ctx,sorted(nodes),[nodes[v] for v in sorted(nodes)],edges) for nodes,edges in zip(node_data,edge_data)]
    ctx.scope_info=f'{len(groups)} declared graph instances; directed={directed}'
    ctx.paths.add('array color refinement and bijection undo stack');ctx.ready();refine(ctx,instances)
    representatives=[];classes=[];mappings=[];count=0
    for i,g in enumerate(instances):
        representative=None
        for r in representatives:
            mapping=match(ctx,g,instances[r])
            if mapping is not None:representative=r;break
        if representative is None:
            representative=i;representatives.append(i);mapping=np.arange(len(g.vertices))
        classes.append(representative);mappings.append(mapping);count+=len(mapping)+1;ctx.enforce(count)
    status=ctx.finish(found=len(groups),retained=len(groups),details='exact label-preserving bijections; not a MIP symmetry proof')
    maps=ctx.publish({graph:groups.typ,'vertex':keys.typ,'representative_vertex':keys.typ},
        ({graph:scalar(groups.values[i]),'vertex':scalar(keys.values[int(g.vertices[v])]),
          'representative_vertex':scalar(keys.values[int(instances[classes[i]].vertices[target])])}
         for i,g in enumerate(instances) for v,target in enumerate(mappings[i])),
        key=(graph,'vertex'),relations={'search':status})
    return ctx.publish({graph:groups.typ,'class':groups.typ},
        ({graph:scalar(groups.values[i]),'class':scalar(groups.values[r])} for i,r in enumerate(classes)),key=(graph,),
        domains={graph:groups.frame(),'class':groups.frame()},relations={'mapping':maps,'search':status})
