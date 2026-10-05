"""Exact finite enumeration using a nonrecursive assignment/undo workspace."""
from math import ceil,floor,inf
import numpy as np
from mipframe.execution.sets import checked,Domain
from mipframe.frame.schema import DType,SchemaError
from mipframe.data.batch import scalar


def enumerate_system(ctx,variables,terms,rows,**_):
    vc=checked(ctx,variables,('var','lb','ub'),unique=('var',),nonnull=('var',))
    rc=checked(ctx,rows,('row','lb','ub'),unique=('row',),nonnull=('row',))
    tc=checked(ctx,terms,('row','var','coef'),unique=('row','var'),nonnull=('row','var'))
    if not variables.schema['var'].compatible(terms.schema['var']) or not rows.schema['row'].compatible(terms.schema['row']):raise SchemaError('Term endpoints must match declared identities')
    for frame,b,fields,finite in ((variables,vc,('lb','ub'),True),(rows,rc,('lb','ub'),False),(terms,tc,('coef',),True)):
        for n in fields:
            if frame.schema[n].kind not in ('number','int'):raise SchemaError('Expected exact numeric columns')
        bad=[i for i in range(b.size) if any(b.columns[n][i] is None or finite and b.columns[n][i] in (-inf,inf) for n in fields)]
        if bad:ctx.invalid('Expected nonmissing finite exact values' if finite else 'Expected nonmissing exact values',frame.schema,b.take(np.asarray(bad)).records(tuple(frame.schema)))
    vs=Domain(ctx,variables.schema['var'],variables.select('var'),tc.columns['var'],'var')
    rs=Domain(ctx,rows.schema['row'],rows.select('row'),tc.columns['row'],'row')
    vb={scalar(v):(ceil(l),floor(h)) for v,l,h in zip(vc.columns['var'],vc.columns['lb'],vc.columns['ub'])}
    bounds=[vb[scalar(v)] for v in vs.values]
    rb={scalar(r):(scalar(l),scalar(h)) for r,l,h in zip(rc.columns['row'],rc.columns['lb'],rc.columns['ub'])}
    limits=[rb[scalar(r)] for r in rs.values]
    rids=np.fromiter((rs.lookup[scalar(r)] for r in tc.columns['row']),dtype=np.int64)
    vids=np.fromiter((vs.lookup[scalar(v)] for v in tc.columns['var']),dtype=np.int64)
    order=np.lexsort((vids,rids));vids=vids[order];coefficients=tc.columns['coef'][order]
    ptr=ctx.work.array(len(rs)+1,np.uint64,zero=True)
    np.cumsum(np.bincount(rids,minlength=len(rs)),out=ptr[1:])
    assignment=[None]*len(vs);cursor=[lo for lo,hi in bounds];found=[]
    ctx.scope_info=f'finite integer system: {len(vs)} variables, {len(rs)} rows'
    ctx.paths.add('array integer DFS with exact interval pruning')
    def feasible():
        for r,(lower,upper) in enumerate(limits):
            lo=hi=0
            for p in range(int(ptr[r]),int(ptr[r+1])):
                v=int(vids[p]);c=scalar(coefficients[p]);x=assignment[v]
                a,b=bounds[v] if x is None else (x,x)
                ca,cb=c*a,c*b;lo+=min(ca,cb);hi+=max(ca,cb)
            if lower>upper or hi<lower or lo>upper:return False
        return True
    ctx.ready();ctx.tick()
    if all(lo<=hi for lo,hi in bounds) and feasible():
        depth=0
        while depth>=0:
            if depth==len(bounds):
                ctx.enforce((len(found)+1)*(len(bounds)+1));found.append(tuple(assignment))
                depth-=1
                if depth>=0:assignment[depth]=None
                continue
            x=cursor[depth];lo,hi=bounds[depth]
            if x>hi:
                cursor[depth]=lo;depth-=1
                if depth>=0:assignment[depth]=None
                continue
            ctx.tick();cursor[depth]=x+1;assignment[depth]=x
            if feasible():depth+=1
            else:assignment[depth]=None
    status=ctx.finish(found=len(found),retained=len(found),details='all feasible integer assignments; exact leaf checks')
    summary=ctx.publish({'assignment':DType('int')},({'assignment':i} for i in range(1,len(found)+1)),key=('assignment',),relations={'search':status})
    return ctx.publish({'assignment':DType('int'),'var':vs.typ,'value':DType('int')},
        ({'assignment':i,'var':scalar(vs.values[v]),'value':value} for i,values in enumerate(found,1) for v,value in enumerate(values)),
        key=('assignment','var'),domains={'var':vs.frame()},relations={'summary':summary,'search':status})
