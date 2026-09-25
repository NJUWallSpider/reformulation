"""Name-independent discovery; recognized families are overlays, not reductions."""
from collections import Counter,defaultdict,deque
from fractions import Fraction as F
import hashlib,json
import numpy as np
from .store import dump
from .numbers import decimal

def digest(x):
    return hashlib.blake2b(repr(x).encode(),digest_size=12).hexdigest()

def sense(m,r):
    lo,hi=m.rows[r][2:4]
    return '=' if lo is not None and lo==hi else '>=' if hi is None else '<=' if lo is None else 'range'

def signature(m,r):
    items=m.row_ids(r);values=[m.num(c) for _,c in items]
    return (sense(m,r),len(items),tuple(sorted(Counter(m.variables[v][2] for v,_ in items).items())),sum(c>0 for c in values),sum(c<0 for c in values),tuple(sorted(Counter(values).values())))

def refine(m):
    vc=[digest((v[2],v[3] is not None,v[4] is not None)) for v in m.variables]
    rc=[digest(signature(m,r)) for r in range(len(m.rows))]
    # Exact signatures remain available independently; structural labels deliberately omit magnitudes.
    exact_v=[digest((v[2],m.num(v[3]),m.num(v[4]),m.num(v[5]))) for v in m.variables]
    exact_r=[digest((sense(m,r),m.num(m.rows[r][2]),m.num(m.rows[r][3]),tuple(sorted(m.num(c) for _,c in m.row_ids(r))))) for r in range(len(m.rows))]
    history=[]
    for _ in range(3):
        vn=[[] for _ in vc];newrc=[]
        for r in range(len(m.rows)):
            rs=[]
            for v,cid in m.row_ids(r):
                c=m.num(cid);role=(1 if c>0 else -1,abs(c)==1)
                rs.append((role,vc[v]));vn[v].append((role,rc[r]))
            newrc.append(digest((rc[r],tuple(sorted(rs)))))
        vc=[digest((vc[v],tuple(sorted(adj)))) for v,adj in enumerate(vn)];rc=newrc
        history.append({'variable_classes':len(set(vc)),'row_classes':len(set(rc))})
    np.save(m.path/'variable_roles.npy',np.asarray(vc,dtype='S24'))
    np.save(m.path/'row_roles.npy',np.asarray(rc,dtype='S24'))
    np.save(m.path/'exact_variable_labels.npy',np.asarray(exact_v,dtype='S24'))
    np.save(m.path/'exact_row_labels.npy',np.asarray(exact_r,dtype='S24'))
    return history

def assignments(m):
    candidate=[];byvar=defaultdict(list)
    for r,meta in enumerate(m.rows):
        a=m.row_ids(r)
        if m.num(meta[2])==m.num(meta[3])==1 and a and all(m.variables[v][2]=='B' and m.num(c)==1 for v,c in a):
            candidate.append(r)
            for v,c in a:byvar[v].append(r)
    adj={r:[] for r in candidate};bad=set()
    for v,rr in byvar.items():
        if len(rr)!=2:
            bad.update(rr);continue
        a,b=rr;adj[a].append((b,v));adj[b].append((a,v))
    visited=set();blocks=[];rejected=[]
    for root in candidate:
        if root in visited:continue
        color={root:0};queue=deque([root]);component=[];bipartite=True
        while queue:
            r=queue.popleft();component.append(r);visited.add(r)
            for s,v in adj[r]:
                if s not in color:color[s]=1-color[r];queue.append(s)
                elif color[s]==color[r]:bipartite=False
        if not bipartite or bad.intersection(component):
            rejected.append({'rows':component,'reason':'non-bipartite or variable not incident to exactly two candidate rows'})
            continue
        left=sorted(r for r in component if color[r]==0);right=sorted(r for r in component if color[r]==1)
        li={r:i for i,r in enumerate(left)};ri={r:i for i,r in enumerate(right)};bindings=[];pairs=Counter()
        for r in left:
            for s,v in adj[r]:bindings.append([v,li[r],ri[s]]);pairs[li[r],ri[s]]+=1
        blocks.append({'id':f'assignment_{len(blocks)}','left_rows':left,'right_rows':right,'shape':[len(left),len(right)],'bindings':bindings,'complete':len(pairs)==len(left)*len(right),'missing_pairs':len(left)*len(right)-len(pairs),'duplicate_pairs':[[i,j,k] for (i,j),k in pairs.items() if k>1],'status':'exact_incidence'})
    return blocks,rejected

def temporal(m,blocks):
    good=[];exceptions=[];source_switch=defaultdict(set)
    for r,meta in enumerate(m.rows):
        a=m.row_ids(r)
        if len(a)!=4:continue
        cont=[(v,c) for v,c in a if m.variables[v][2]=='C'];binary=[(v,c) for v,c in a if m.variables[v][2]=='B']
        if len(cont)!=2 or len(binary)!=2:continue
        negative=[v for v,c in cont if m.num(c)==-1];positive=[v for v,c in cont if m.num(c)==1]
        qb=[(v,c) for v,c in binary if m.num(c)>0];mb=[(v,c) for v,c in binary if m.num(c)<0]
        if len(negative)!=1 or len(positive)!=1 or len(qb)!=1 or len(mb)!=1:continue
        source,target=negative[0],positive[0];sw,q=qb[0];arc,negm=mb[0]
        if meta[3] is not None or m.num(meta[2])!=m.num(q)+m.num(negm):
            exceptions.append({'row':r,'reason':'RHS is not exactly q-M, or sense differs'});continue
        rec={'row':r,'source':source,'target':target,'switch':sw,'arc':arc,'q':q,'negative_M':negm}
        good.append(rec);source_switch[source].add(sw)
    unique={v:next(iter(ss)) for v,ss in source_switch.items() if len(ss)==1}
    for rec in good:
        rec['assignment']=None
    for block in blocks:
        binding={v:(i,j) for v,i,j in block['bindings']}
        related=[r for r in good if r['arc'] in binding and r['switch'] in binding]
        options=[]
        for transpose in (False,True):
            matched=[]
            for r in related:
                s=unique.get(r['source']);t=unique.get(r['target'])
                if s!=r['switch'] or s not in binding or t not in binding:continue
                i,j=binding[s];k,l=binding[t]
                if binding[r['arc']]==((k,j) if transpose else (i,l)):
                    matched.append(r)
            options.append((len(matched),transpose,matched))
        options.sort(key=lambda z:z[0],reverse=True)
        if options[0][0] and options[0][0]>options[1][0]:
            for r in options[0][2]:r['assignment']={'block':block['id'],'transposed':options[0][1]}
    bounds={v:[m.num(meta[3]),m.num(meta[4])] for v,meta in enumerate(m.variables) if meta[2]=='C'}
    for r,meta in enumerate(m.rows):
        a=m.row(r)
        if len(a)!=1:continue
        v,c=next(iter(a.items()))
        if v not in bounds:continue
        lo,hi=m.num(meta[2]),m.num(meta[3])
        if c<0:lo,hi=(hi/c if hi is not None else None),(lo/c if lo is not None else None)
        else:lo,hi=(lo/c if lo is not None else None),(hi/c if hi is not None else None)
        if lo is not None:bounds[v][0]=max(bounds[v][0],lo) if bounds[v][0] is not None else lo
        if hi is not None:bounds[v][1]=min(bounds[v][1],hi) if bounds[v][1] is not None else hi
    return good,exceptions,{str(v):[decimal(a) if a is not None else None for a in pair] for v,pair in bounds.items()},unique

def field(values):
    if all(x==values[0] for x in values):return {'constant':values[0]},None
    return {'parameter':True},values

def generic_candidate(m,rows):
    # Restricted anti-unification: canonical slots by type/sign/unit role, then coefficient and ID.
    records=[]
    for r in rows:
        terms=sorted(m.row_ids(r),key=lambda vc:(m.variables[vc[0]][2],m.num(vc[1])>0,abs(m.num(vc[1]))==1,m.num(vc[1]),vc[0]))
        records.append({'row':r,'variables':[v for v,c in terms],'coefficients':[c for v,c in terms],'lower':m.rows[r][2],'upper':m.rows[r][3]})
    coeff=[]
    for k in range(len(records[0]['variables'])):
        desc,_=field([rec['coefficients'][k] for rec in records]);coeff.append(desc)
    lo,_=field([rec['lower'] for rec in records]);hi,_=field([rec['upper'] for rec in records])
    instances=[]
    for rec in records:
        inst={'row':rec['row'],'variables':rec['variables'],'parameters':[c for k,c in enumerate(rec['coefficients']) if 'parameter' in coeff[k]]}
        if 'parameter' in lo:inst['lower']=rec['lower']
        if 'parameter' in hi:inst['upper']=rec['upper']
        instances.append(inst)
    # Explicit accounting in fixed-width ID units, including bindings, header and parameters.
    raw=sum((4+2*len(rec['variables']))*8 for rec in records)
    encoded=96+8*len(coeff)+sum((1+len(rec['variables'])+len(rec['parameters'])+('lower' in rec)+('upper' in rec))*8 for rec in instances)
    return {'kind':'linear_slots','formula':'lower <= sum(k, coefficient[k] * variable[k]) <= upper','coefficients':coeff,'lower':lo,'upper':hi,'instances':instances,'exceptions':[],'encoding_estimate':{'raw_bytes':raw,'encoded_bytes':encoded,'saving':raw-encoded}}

def discover(m):
    history=refine(m);blocks,rejected=assignments(m)
    times,exceptions,bounds,switches=temporal(m,blocks)
    candidates=[]
    for block in blocks:
        for side in ('left_rows','right_rows'):
            candidate=generic_candidate(m,block[side]);candidate['formula']='sum(j, y[i,j]) = 1' if side=='left_rows' else 'sum(i, y[i,j]) = 1'
            if block['duplicate_pairs']:candidate['formula']='sum(e incident to index, x[e]) = 1; parallel edges retained'
            candidate['structure']=block['id'];candidates.append(candidate)
    if times:
        qdesc,_=field([r['q'] for r in times]);mdesc,_=field([r['negative_M'] for r in times])
        instances=[]
        for rec in times:
            inst={'row':rec['row'],'variables':[rec['source'],rec['target'],rec['switch'],rec['arc']]}
            if 'parameter' in qdesc:inst['q']=rec['q']
            if 'parameter' in mdesc:inst['negative_M']=rec['negative_M']
            instances.append(inst)
        raw=len(times)*96;encoded=128+sum(40+8*('q' in r)+8*('negative_M' in r) for r in instances)
        candidates.append({'kind':'temporal','formula':'t[j]-t[i] >= q[i,j]*(1-switch[i]) - M[i,j]*(1-arc[i,j])','q':qdesc,'negative_M':mdesc,'instances':instances,'exceptions':exceptions,'encoding_estimate':{'raw_bytes':raw,'encoded_bytes':encoded,'saving':raw-encoded}})
    buckets=defaultdict(list)
    for r in range(len(m.rows)):buckets[signature(m,r)].append(r)
    for rows in buckets.values():
        if len(rows)>=2:candidates.append(generic_candidate(m,rows))
    # Greedy MDL-style covering. More specific templates win equal savings.
    candidates.sort(key=lambda c:c['encoding_estimate']['saving'],reverse=True)
    covered=set();families=[]
    for candidate in candidates:
        rr={i['row'] for i in candidate['instances']}
        if candidate['encoding_estimate']['saving']<=0 or rr.intersection(covered):continue
        candidate['id']=f'family_{len(families)}';families.append(candidate);covered.update(rr)
    residual=[]
    for r in range(len(m.rows)):
        if r not in covered:residual.append({'row':r,'terms':m.row_ids(r),'lower':m.rows[r][2],'upper':m.rows[r][3]})
    compressed={'schema_version':1,'model_version':m.manifest['model_version'],'sense':m.manifest['sense'],'objective_constant':m.manifest['objective_constant'],'variables':[list(v) for v in m.variables],'row_metadata':[[r[0],r[1],r[4]] for r in m.rows],'families':families,'residual':residual}
    structures={'wl_rounds':history,'warning':'Role colors are not a symmetry certificate. Structure overlays do not change the model.','assignments':blocks,'assignment_rejections':rejected,'temporal':{'rows':times,'exceptions':exceptions,'time_bounds':bounds,'switches':{str(k):v for k,v in switches.items()}},'row_fingerprints':[{'signature':repr(sig),'count':len(rows),'rows':rows} for sig,rows in buckets.items()]}
    dump(m.path/'compressed.json',compressed);dump(m.path/'structures.json',structures)
