"""Exact support discovery on integer arrays, with control-only result cards."""
from collections import Counter, defaultdict, deque
from itertools import combinations
import numpy as np
from mipframe.ir.indexing.store import Table, ident, js


def evidence(s, run, reason, **fields):
    rec = dict(reason=reason, **fields)
    key = ident('e_', rec)
    s.put(run, 'evidence', key, dict(id=key, **rec))


def candidate(s, run, rg, vg, origin):
    if not s.size(rg) or not s.size(vg):
        return None
    if not hasattr(s, '_candidate_index'):
        s._candidate_index = {(c['run'],c['rg'],c['vg']):g for g,c in s.candidates.items()}
    key = run,rg,vg
    g = s._candidate_index.get(key)
    if g is None:
        g = len(s.candidates)+1
        s._candidate_index[key] = g
        s.candidates[g] = dict(run=run,rg=rg,vg=vg,origins=[])
    record=s.candidates[g]
    origins = record['origins']
    if origin not in origins:
        origins.append(origin)
        s.candidates[g]=record
    return g


def direction(lo, hi):
    return 'eq' if lo == hi else 'le' if lo is None else 'ge' if hi is None else 'range'


def prepare(s, run, rows, variables, result):
    m = s.owner.model
    rg,vg = s.domain('row',rows),s.domain('var',variables)
    rows,variables = s.domains[rg],s.domains[vg]
    selected = set(map(int,variables))
    edges = s.make_table(('r','v'),((int(r),v) for r in rows for v in map(int,m.support(int(r))) if v in selected))
    rowgroups,vargroups = {},{}
    def add_group(groups,g,origin):
        origins = groups.setdefault(g,[])
        if origin not in origins:origins.append(origin)
    for axis,ids,groups in (('row',rows,rowgroups),('var',variables,vargroups)):
        for depth in range(result.depth+1):
            colors = result._store.array(result._config,axis,depth)[ids]
            order = np.argsort(colors,kind='stable')
            cuts = np.r_[0,np.flatnonzero(np.diff(colors[order]))+1,len(ids)]
            for start,stop in zip(cuts[:-1],cuts[1:]):
                if start == stop:continue
                c = int(colors[order[start]])
                g = s.domain(axis,ids[order[start:stop]])
                add_group(groups,g,dict(kind='signature',axis=axis,depth=depth,color=c))
    kinds = {int(v):m.variable_kind(int(v)) for v in variables}
    directions,types,units = defaultdict(list),defaultdict(list),defaultdict(list)
    for raw in rows:
        r = int(raw);lo,hi = m.row_bounds(r)
        sense = direction(lo,hi);directions[sense].append(r)
        terms = m.support(r);inside = [int(v) for v in terms if int(v) in selected]
        distinct = {kinds[v] for v in inside}
        typ = next(iter(distinct)) if len(distinct)==1 else None
        if typ:types[typ,sense].append(r)
        first = m.uniform_coefficient(r) if typ and inside and len(inside)==len(terms) else None
        if first is not None:
            low,high = (lo,hi) if first>0 else (hi,lo)
            units[typ,direction(low,high),'unit' if low==high==first else 'other'].append(r)
    for sense,ids in sorted(directions.items()):
        add_group(rowgroups,s.domain('row',ids),dict(kind='coarse_direction',sense=sense))
    rm,vm = defaultdict(list),defaultdict(list)
    for groups,index in ((rowgroups,rm),(vargroups,vm)):
        for g in groups:
            for i in s.domains[g]:index[int(i)].append(g)
    pairs = set()
    for r,v in edges.records():
        pairs.update((a,b) for a in rm[r] for b in vm[v])
    for a,b in sorted(pairs):
        candidate(s,run,a,b,dict(kind='roles',rows=rowgroups[a],vars=vargroups[b]))
    er,ev = edges.columns['r'],edges.columns['v']
    for groups,kind in ((types,'homogeneous_type'),(units,'unit_sum')):
        for key,ids in sorted(groups.items()):
            d = s.domain('row',ids)
            u = s.domain('var',ev[np.isin(er,s.domains[d])])
            origin = dict(kind=kind,type=key[0],sense=key[1]) if kind=='homogeneous_type' else dict(kind=kind,seed=list(key))
            candidate(s,run,d,u,origin)
    return rg,vg,edges


def partition(s, run, g, rg, vg):
    n,nr = s.size(vg),s.size(rg)
    members = s.members[g]
    distinct,total = len(np.unique(members.columns['v'])),len(members)
    nonempty = len(np.unique(members.columns['r']))
    ok = bool(n and nr and total==distinct==n and nonempty==nr)
    if ok:
        key = ident('partition_',[rg,vg])
        s.put(run,'partitions',key,dict(id=key,g=g,rows_domain=rg,vars_domain=vg,
            sets=nr,variables=n,origins=s.candidates[g]['origins'],verification='verified'))
    else:
        evidence(s,run,'not a partition',candidate=g,empty_sets=nr-nonempty,
                 uncovered_variables=n-distinct,extra_memberships=total-distinct)
    return ok,distinct,total


def split_two(s, run, g, rg, vg):
    members = s.members[g];rs,vs = members.columns['r'],members.columns['v']
    variables,counts = np.unique(vs,return_counts=True)
    if len(variables)!=s.size(vg) or np.any(counts!=2):return
    order = np.argsort(vs,kind='stable');ends = rs[order].reshape(-1,2)
    graph = defaultdict(list)
    for a,b in ends:
        graph[int(a)].append(int(b));graph[int(b)].append(int(a))
    colors = {}
    for raw in s.domains[rg]:
        root = int(raw)
        if root in colors or root not in graph:continue
        colors[root] = 0;queue = deque([root]);component = [];valid = True
        while queue:
            r = queue.popleft();component.append(r)
            for other in graph[r]:
                if other not in colors:
                    colors[other] = 1-colors[r];queue.append(other)
                elif colors[other]==colors[r]:
                    if valid:evidence(s,run,'non-bipartite candidate',candidate=g,edge=[r,other])
                    valid = False
        if not valid:continue
        uv = s.domain('var',variables[np.isin(ends[:,0],component)])
        for side in (0,1):
            rr = s.domain('row',(r for r in component if colors[r]==side))
            new = candidate(s,run,rr,uv,dict(kind='bipartite_component',parent=g,root=root,side=side))
            mask = np.isin(rs,s.domains[rr])
            s.members[new] = s.make_table(('r','v'),zip(rs[mask],vs[mask]))
            partition(s,run,new,rr,uv)


def discover(s, run, edges):
    er,ev = edges.columns['r'],edges.columns['v']
    for g,c in list(s.candidates.items()):
        if c['run']!=run:continue
        rg,vg = c['rg'],c['vg']
        mask = np.isin(er,s.domains[rg]) & np.isin(ev,s.domains[vg])
        s.members[g] = s.make_table(('r','v'),zip(er[mask],ev[mask]))
        if not partition(s,run,g,rg,vg)[0]:split_two(s,run,g,rg,vg)
    parts = [p for p in s.records(run,'partitions') if p['sets']>=2]
    for pa,pb in combinations(parts,2):
        if pa['vars_domain']!=pb['vars_domain']:continue
        left = {v:r for r,v in s.members[pa['g']].records()}
        right = {v:r for r,v in s.members[pb['g']].records()}
        counts = Counter((left[v],right[v]) for v in left)
        collisions = sorted((a,b,n) for (a,b),n in counts.items() if n>1)
        if collisions:
            evidence(s,run,'multiple variables per cell',duplicate_coordinates=len(collisions),
                extra_variables=sum(n-1 for _,_,n in collisions),partitions=[pa['id'],pb['id']],counterexample=list(collisions[0]))
            continue
        def preference(p):
            return (any(o.get('seed',[None])[-1]=='unit' for o in p['origins']),p['sets'],-p['rows_domain'])
        if preference(pa)<preference(pb):pa,pb,left,right = pb,pa,right,left
        ar,br,vg = pa['rows_domain'],pb['rows_domain'],pa['vars_domain']
        t = ident('tensor_',[run,ar,br,vg]);na,nb = pa['sets'],pb['sets'];nv = s.size(vg)
        am = {int(r):i for i,r in enumerate(s.domains[ar])};bm = {int(r):i for i,r in enumerate(s.domains[br])}
        s.coordinates[t] = s.make_table(('v','i','j','a','b'),((v,left[v],right[v],am[left[v]],bm[right[v]]) for v in sorted(left)))
        s.put(run,'tensors',t,dict(id=t,shape=[na,nb],variables=nv,rows_domains=[ar,br],vars_domain=vg,
            partitions=[pa['id'],pb['id']],complete_product=nv==na*nb,missing_cells=na*nb-nv,
            verification='verified',meaning='incidence coordinates; not a symmetry certificate'))
