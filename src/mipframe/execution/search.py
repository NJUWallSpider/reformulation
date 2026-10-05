"""Nonrecursive integer-array graph search kernels."""
import numpy as np
from mipframe.store.search_store import Objects,SearchStopped,nonnegative
from mipframe.alg._graph import SearchGraph
from mipframe.execution.graph import labels


def graph_search(ctx,name,E,**p):
    from mipframe.alg.graph import minimum
    if name in ('clique_candidates','odd_cycle_candidates'):nonnegative(p['top'],'top')
    if name in ('clique_candidates','maximal_cliques'):minimum(p['min_size'],'min_size')
    directed=p.get('directed',False);chordless=p.get('chordless',False)
    if type(directed) is not bool or type(chordless) is not bool:raise TypeError('directed and chordless must be boolean')
    if directed and chordless:raise ValueError('chordless=True is only supported for undirected graphs')
    lengths=p.get('lengths')
    if name=='odd_cycle_candidates':
        if not isinstance(lengths,(tuple,list)) or not lengths or any(type(n) is not int or n<3 or n%2==0 for n in lengths):raise ValueError('lengths must contain odd integers >= 3')
        lengths=tuple(sorted(set(lengths)));low,high=min(lengths),max(lengths)
    elif name=='simple_cycles':
        low=p['min_length'] if p['min_length'] is not None else 1 if directed else 3
        high=p['max_length'];minimum(low,'min_length')
        if not directed and low<3:raise ValueError('Undirected cycles have length >= 3')
        if high is not None:
            minimum(high,'max_length')
            if high<low:raise ValueError('max_length must be >= min_length')
    g=SearchGraph(ctx,E,p['u'],p['v'],p['vertices'],p['scope'],directed=directed,score=p.get('score'),value=p.get('value','value'))
    kind='clique' if name in ('clique_candidates','maximal_cliques') else 'cycle'
    objects=Objects(ctx,g,kind,p.get('top'))
    ctx.ready()
    try:
        if name=='clique_candidates':
            for root in g.priority:
                ctx.tick();current=[root];available=g.neighbors(root).copy()
                while len(available):
                    ctx.tick();x=int(available[np.argmin(g.rank[available])]);current.append(x)
                    available=np.intersect1d(available,g.neighbors(x),assume_unique=True)
                if len(current)>=p['min_size']:
                    current.sort();g.verify_clique(current);objects.add(current)
        elif name=='maximal_cliques':
            # Each frame owns P/X and a remaining branch cursor. The current path
            # is undone on frame exit, without SQL savepoints or recursive calls.
            stack=[(g.active,np.empty(0,dtype=np.int64),None,0)];current=[]
            while stack:
                P,X,branches,index=stack[-1]
                if branches is None:
                    ctx.tick()
                    if not len(P) and not len(X):
                        if len(current)>=p['min_size']:
                            seq=sorted(current);g.verify_clique(seq);objects.add(seq)
                        stack.pop()
                        if stack:current.pop()
                        continue
                    union=np.union1d(P,X)
                    pivot=min(map(int,union),key=lambda v:(-len(np.intersect1d(P,g.neighbors(v),assume_unique=True)),v))
                    branches=np.setdiff1d(P,g.neighbors(pivot),assume_unique=True)
                    stack[-1]=(P,X,branches,0)
                if index>=len(branches):
                    stack.pop()
                    if stack:current.pop()
                    continue
                x=int(branches[index]);neighbors=g.neighbors(x)
                childP=np.intersect1d(P,neighbors,assume_unique=True)
                childX=np.intersect1d(X,neighbors,assume_unique=True)
                stack[-1]=(P[P!=x],np.union1d(X,[x]),branches,index+1)
                current.append(x);stack.append((childP,childX,None,0))
        else:
            component=labels(g);sizes=np.bincount(component,minlength=g.n)
            visited=ctx.work.array(g.n,np.bool_,zero=True)
            for root in g.priority:
                if sizes[component[root]]<low:continue
                ctx.tick();path=[root];visited[root]=True
                def neighbors(v):
                    ids=g.neighbors(v);ids=ids[(ids>=root)&(component[ids]==component[root])]
                    return iter(ids[np.argsort(g.rank[ids],kind='stable')])
                stack=[neighbors(root)]
                while stack:
                    raw=next(stack[-1],None)
                    if raw is None:
                        stack.pop();visited[path.pop()]=False;continue
                    x=int(raw);ctx.tick();n=len(path)
                    if x==root:
                        if n<low or high is not None and n>high or lengths is not None and n not in lengths:continue
                        if not directed and path[1]>path[-1]:continue
                        if g.verify_cycle(path,chordless):objects.add(path)
                    elif not visited[x] and (high is None or n<high):
                        visited[x]=True;path.append(x);stack.append(neighbors(x))
    except SearchStopped:
        if not ctx.candidate:raise
    return objects.publish(f'{name}; verified {kind} objects; ignored self-loops={g.ignored_loops}')
