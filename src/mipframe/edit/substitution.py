"""Compile fixed substitution using selected CSC incidence and exact scratch.

The compiled object is a numerical artifact, not evidence of fixedness. Its
extension map and the edit's bound-proof bindings carry that separate obligation.
"""
from dataclasses import dataclass
from math import inf
import numpy as np
from mipframe.data.ids import IDSet
from mipframe.data.overrides import Overrides, overrides_from_storage, overrides_from_columns
from mipframe.data.exact_sparse import ExactSparse
from mipframe.data.columns import ExactColumn


@dataclass(frozen=True)
class FixedSubstitutionDelta:
    fixed: object
    shifts: object
    degrees: object
    trivial_rows: object
    infeasible_rows: object
    objective_constant_delta: object
    removed_nnz: int
    resource: object = None
    drop_policy: object = None


class SubstitutionInfeasibleError(ValueError):
    """
    MIPFRAME HELP BEGIN
    An affected empty row excludes activity zero.

    Full contract: mf.help("api.SubstitutionInfeasibleError")
    MIPFRAME HELP END
    """
    def __init__(self, compiled, parent, row_bounds):
        self.rows = compiled.infeasible_rows
        self.compiled, self.parent, self.row_bounds = compiled, parent, row_bounds
        super().__init__('Infeasible after fixed substitution: empty row excludes zero')
    @property
    def violations(self):
        from mipframe.frame.base import Frame
        def scan(fields,ctx):
            for r in self.rows:
                lo,hi=self.row_bounds.get(r,self.parent.row_bounds(r));shift=self.compiled.shifts.get(r,0)
                values=dict(row=r,lb=lo-shift,ub=hi-shift,activity=0)
                yield {n:values[n] for n in fields}
        return Frame(dict(row='int',lb='number',ub='number',activity='int'),source=scan,known_count=len(self.rows))


def incidence(f, v):
    """Yield ID/value-ID slices without decoding coefficients on the zero path."""
    base = getattr(f, '_base', f)
    a = base.data.arrays
    if v < base.var_capacity:
        lo,hi = map(int,a['col_ptr'][v:v+2])
        rows = a['csc_row_ids'][lo:hi]
        values = a['csc_value_ids'][lo:hi]
        yield rows,values,base.numbers
    # Added numerical blocks have their own compact column index.
    if hasattr(f,'added_column'):
        yield from f.added_column(v)


def live_row(f,r):
    return not hasattr(f,'_row_live') or (bool(f._row_live[r]) if r<len(f._row_live) else r in f._added_rows)


def trivial_policy(parent,row,policy):
    if isinstance(policy,bool):return policy
    base=getattr(parent,'_base',parent)
    if row<base.row_capacity:
        lo,hi=map(int,base.row_ptr[row:row+2]);variables=base.col_idx[lo:hi]
    else:variables=np.fromiter((v for v,c in parent._added_rows[row][3]),dtype='<u8')
    for ids,drop in reversed(policy):
        positions=np.searchsorted(ids,variables);valid=positions<len(ids)
        if len(ids) and np.any(valid&(ids[np.minimum(positions,len(ids)-1)]==variables)):return drop
    return False


def compile_fixed(parent, fixed, drop_rows, row_bounds, objective, drop_trivial, work):
    if not fixed:
        return FixedSubstitutionDelta(fixed,Overrides(),Overrides(),IDSet(),IDSet(),0,0,work.fork())
    if not getattr(parent,'_added_rows',None) and not any(c for _,c in fixed.items()):
        result=compile_zero_dense(parent,fixed,drop_rows,row_bounds,drop_trivial,work)
        if result is not None:return result
    scratch = work.fork()
    try:
        # Capacity depends on selected incidence, not on all model rows.
        upper = 0
        for v in fixed:
            for rows,_,_ in incidence(parent,v): upper += len(rows)
        count = min(parent.nrows,upper)
        capacity = 1 << max(1,(max(1,count)*2-1).bit_length())
        if capacity*25+count*8>work.available//2:
            return compile_spilled(parent,fixed,drop_rows,row_bounds,objective,drop_trivial,work)
        keys = scratch.array(capacity,'<i8');keys[:]=-1
        removed = scratch.array(capacity,'<u8',zero=True)
        sums = scratch.array(capacity,'<i8',zero=True)
        overflow = scratch.array(capacity,'?',zero=True)
        occupied = scratch.array(count,'<u8')
        used = 0
        def slot(r):
            nonlocal used
            p = (r*11400714819323198485)&(capacity-1)
            while keys[p]!=-1 and keys[p]!=r:p=(p+1)&(capacity-1)
            if keys[p]==-1:
                keys[p]=r;occupied[used]=p;used+=1
            return p
        nonzero = False
        for v,c in fixed.items():
            nonzero |= bool(c)
            for rows,_,_ in incidence(parent,v):
                for raw in rows:
                    r=int(raw)
                    if r not in drop_rows and live_row(parent,r):removed[slot(r)]+=1
        def contributions():
            for v,c in fixed.items():
                if not c:continue
                for rows,values,numbers in incidence(parent,v):
                    for raw,value_id in zip(rows,values):
                        r=int(raw)
                        if r in drop_rows or not live_row(parent,r):continue
                        p=slot(r);value=numbers[int(value_id)]*c
                        if overflow[p]:yield r,value;continue
                        total=int(sums[p])+value
                        if type(total) is int and -(1<<63)<=total<1<<63:sums[p]=total
                        else:
                            overflow[p]=True
                            if sums[p]:yield r,int(sums[p])
                            sums[p]=0
                            yield r,value
            for p in occupied[:used]:
                if sums[p]:yield int(keys[p]),int(sums[p])
        shifts = Overrides()
        if nonzero:
            storage = ExactSparse(work=work).build(contributions())
            shifts = overrides_from_storage(storage)
        # Sort only affected row IDs. Counts never enter rational arithmetic.
        from mipframe.data.sorting import lexorder
        row_ids = scratch.array(used,'<u8')
        for i,p in enumerate(occupied[:used]): row_ids[i]=keys[p]
        order = lexorder(scratch,(row_ids,))
        artifact = work.fork()
        ids = artifact.array(used,'<u8')
        degrees = artifact.array(used,'<u8')
        trivial = artifact.array(used,'<u8')
        bad = artifact.array(used,'<u8')
        nt=nb=removed_nnz=0
        for i,position in enumerate(order):
            r=int(row_ids[position]);p=slot(r)
            degree=parent.row_degree(r)-int(removed[p]);removed_nnz+=int(removed[p])
            ids[i]=r;degrees[i]=degree
            if degree==0:
                lo,hi=row_bounds.get(r,parent.row_bounds(r));shift=shifts.get(r,0)
                if lo-shift<=0<=hi-shift:
                    if trivial_policy(parent,r,drop_trivial):trivial[nt]=r;nt+=1
                else:bad[nb]=r;nb+=1
        ids,degrees,trivial,bad=(artifact.seal(a) for a in (ids,degrees,trivial,bad))
        degree_map=overrides_from_columns(ids,(degrees,))
        delta=0
        for v,c in fixed.items():
            if c:delta+=(parent.numeric_var(v)[5] if objective is None else objective[0].get(v,0))*c
        result=FixedSubstitutionDelta(fixed,shifts,degree_map,IDSet(trivial[:nt],work=artifact),IDSet(bad[:nb],work=artifact),delta,removed_nnz,artifact,drop_trivial)
        return result
    finally:scratch.close()


def compile_zero_dense(parent,fixed,drop_rows,row_bounds,drop_trivial,work):
    """Vectorize incidence counts when touched density justifies dense row scratch."""
    base=getattr(parent,'_base',parent);ptr=base.data.arrays['col_ptr']
    if parent.row_capacity*8>work.available//2:return None
    selected=fixed.ids[:int(np.searchsorted(fixed.ids,base.var_capacity))]
    block=max(1,work.memory//128);upper=0
    for start in range(0,len(selected),block):
        chunk=selected[start:start+block];upper+=int((ptr[chunk+1]-ptr[chunk]).sum())
    if parent.row_capacity>max(4096,upper*4):return None
    with work.fork() as scratch:
        counts=scratch.array(parent.row_capacity,'<u8',zero=True)
        limit=max(1,work.memory//256)
        # Build bounded adjacent-column runs; each run gathers only selected incidence.
        start=0
        while start<len(selected):
            end=min(len(selected),start+max(1,limit//16));vs=selected[start:end]
            lengths=(ptr[vs+1]-ptr[vs]).astype(np.int64)
            while len(vs)>1 and int(lengths.sum())>limit:
                end=start+max(1,len(vs)//2);vs=selected[start:end];lengths=(ptr[vs+1]-ptr[vs]).astype(np.int64)
            size=int(lengths.sum())
            if len(vs)==1 and size>limit:
                begin,finish=map(int,ptr[int(vs[0]):int(vs[0])+2]);pieces=(base.data.arrays['csc_row_ids'][i:min(finish,i+limit)] for i in range(begin,finish,limit))
            else:
                offsets=np.cumsum(lengths)-lengths
                positions=np.repeat(ptr[vs],lengths)+np.arange(size,dtype='<u8')-np.repeat(offsets,lengths).astype('<u8')
                pieces=(base.data.arrays['csc_row_ids'][positions],)
            for rows in pieces:
                live=getattr(parent,'_row_live',None)
                if live is not None:rows=rows[live[rows]]
                if drop_rows:
                    mask=getattr(drop_rows,'mask',None)
                    if mask is not None:rows=rows[~mask[rows]]
                    else:rows=rows[np.fromiter((int(r) not in drop_rows for r in rows),dtype=bool,count=len(rows))]
                np.add.at(counts,rows,1)
            start=end
        touched=np.flatnonzero(counts);resource=work.fork();n=len(touched)
        ids=resource.array(n,'<u4' if parent.row_capacity<2**32 else '<u8');ids[:]=touched
        degrees=resource.array(n,'<u8');trivial=resource.array(n,ids.dtype);bad=resource.array(n,ids.dtype)
        nt=nb=removed_nnz=0
        for i,raw in enumerate(ids):
            r=int(raw);degree=parent.row_degree(r)-int(counts[r]);degrees[i]=degree;removed_nnz+=int(counts[r])
            if degree==0:
                lo,hi=row_bounds.get(r,parent.row_bounds(r))
                if lo<=0<=hi:
                    if trivial_policy(parent,r,drop_trivial):trivial[nt]=r;nt+=1
                else:bad[nb]=r;nb+=1
        ids,degrees,trivial,bad=(resource.seal(a) for a in (ids,degrees,trivial,bad))
        return FixedSubstitutionDelta(fixed,Overrides(),overrides_from_columns(ids,(degrees,)),
            IDSet(trivial[:nt],work=resource),IDSet(bad[:nb],work=resource),0,removed_nnz,resource,drop_trivial)


def compile_spilled(parent,fixed,drop_rows,row_bounds,objective,drop_trivial,work):
    """Bounded sorted-run route instead of a random-access oversized hash table."""
    counts=ExactSparse(work=work)
    def records(contributions=False):
        for v,c in fixed.items():
            if contributions and not c:continue
            for rows,values,numbers in incidence(parent,v):
                for r,i in zip(rows,values):
                    r=int(r)
                    if r in drop_rows or not live_row(parent,r):continue
                    yield r,numbers[int(i)]*c if contributions else 1
    try:
        counts.build(records())
        shifts=Overrides()
        if any(c for _,c in fixed.items()):shifts=overrides_from_storage(ExactSparse(work=work).build(records(True)))
        artifact=work.fork();n=len(counts.ids)
        ids=artifact.array(n,'<u8');degrees=artifact.array(n,'<u8')
        trivial=artifact.array(n,'<u8');bad=artifact.array(n,'<u8');nt=nb=removed_nnz=0
        for i,(r,removed) in enumerate(counts.items()):
            removed=int(removed);degree=parent.row_degree(r)-removed;removed_nnz+=removed
            ids[i]=r;degrees[i]=degree
            if degree==0:
                lo,hi=row_bounds.get(r,parent.row_bounds(r));shift=shifts.get(r,0)
                if lo-shift<=0<=hi-shift:
                    if trivial_policy(parent,r,drop_trivial):trivial[nt]=r;nt+=1
                else:bad[nb]=r;nb+=1
        ids,degrees,trivial,bad=(artifact.seal(a) for a in (ids,degrees,trivial,bad))
        delta=sum((parent.numeric_var(v)[5] if objective is None else objective[0].get(v,0))*c for v,c in fixed.items() if c)
        return FixedSubstitutionDelta(fixed,shifts,overrides_from_columns(ids,(degrees,)),
            IDSet(trivial[:nt],work=artifact),IDSet(bad[:nb],work=artifact),delta,removed_nnz,artifact,drop_trivial)
    finally:counts.close()


class ShiftedBoundColumn:
    def __init__(self,parent,bounds,shifts,side):
        self.parent,self.bounds,self.shifts,self.side=parent,bounds,shifts,side
    def __len__(self):return len(self.shifts)
    def __getitem__(self,i):
        r=int(self.shifts.ids[i])
        value=self.bounds.get(r,self.parent.row_bounds(r))[self.side]
        return value if value in (-inf,inf) else value-self.shifts.columns[0][i]


def shifted_bounds(parent,bounds,shifts):
    return overrides_from_columns(shifts.ids,
        (ShiftedBoundColumn(parent,bounds,shifts,0),ShiftedBoundColumn(parent,bounds,shifts,1)),kind='bounds')


def compiled_spec(compiled):
    if compiled is None:return None
    return dict(drop_policy=compiled.drop_policy,shifts=compiled.shifts,degree_ids=compiled.degrees.ids,
                degree_values=compiled.degrees.columns[0] if compiled.degrees else np.empty(0,dtype='<u8'),
                trivial_rows=compiled.trivial_rows.array,infeasible_rows=compiled.infeasible_rows.array,
                objective_constant_delta=compiled.objective_constant_delta,removed_nnz=compiled.removed_nnz)


def restore_compiled(record,fixed):
    if record is None:return None
    ids=record['degree_ids'];values=record['degree_values']
    if len(ids)!=len(values) or np.any(ids[1:]<=ids[:-1]) or np.any(values<0):raise ValueError('Invalid compiled row degrees')
    return FixedSubstitutionDelta(fixed,record['shifts'],overrides_from_columns(ids,(values,)),
        IDSet(record['trivial_rows']),IDSet(record['infeasible_rows']),
        record['objective_constant_delta'],int(record['removed_nnz']),drop_policy=record.get('drop_policy'))
