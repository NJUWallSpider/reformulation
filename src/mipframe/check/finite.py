"""Explicit finite enumeration and point checking, outside the linear kernel."""
from fractions import Fraction
from math import inf,floor,ceil,isfinite
from itertools import product
import time
from mipframe.check.kernel import facts,exact
from mipframe.check.compact import BatchProof,ProofRef,_KEY
from mipframe.check.compiler import claim_data,target_data,compile_sources,assumptions_data,intervals
from mipframe.check.certificate import InvalidCertificateError,UnsupportedEnumerationDomain
from mipframe.data.runtime import resource_scope


def satisfied(data,point):
    ts,rhs,rel=data;value=sum(c*point[v] for v,c in ts)
    return value<=rhs if rel=='<=' else value>=rhs if rel=='>=' else value==rhs


@resource_scope
def enumerate_claim(owner,target,*,vars,using=(),assume=(),max_states=100000,time_limit=None,domain='auto',memory_mb=128,disk_mb=None):
    from mipframe.core.resources import memory_bytes
    from mipframe.frame.executor import QueryMemoryError,size
    budget=memory_bytes(memory_mb)
    if domain not in ('auto','integer'):raise InvalidCertificateError('Enumeration proves integer claims only')
    if max_states is not None and (isinstance(max_states,bool) or not isinstance(max_states,int) or max_states<0):raise ValueError('Invalid state limit')
    if time_limit is not None and (isinstance(time_limit,bool) or not isfinite(time_limit) or time_limit<0):raise ValueError('Invalid time limit')
    f=facts(owner);selected=set()
    for v in vars:
        if getattr(v,'uid',getattr(getattr(v,'_owner',None),'uid',f.uid))!=f.uid:raise InvalidCertificateError('Foreign enumeration variable')
        ident=v.id if hasattr(v,'id') else v
        from numbers import Integral
        if isinstance(ident,bool) or not isinstance(ident,Integral):raise InvalidCertificateError('Expected a variable ID')
        f.validate('var',ident);selected.add(int(ident))
        if len(selected)*256>budget:raise QueryMemoryError('Enumeration state exceeds the working-memory budget')
    selected=sorted(selected)
    a=assumptions_data(owner,assume);c,parents,positions=compile_sources(owner,using,a)
    a=tuple(dict.fromkeys((*a,*(x for p in parents for x in p._assumptions))))
    ib,contradiction=intervals(f,a);premises=[];deps=[]
    for i,(kind,ident,side) in enumerate(zip(c.kinds,c.sources,c.sides)):
        ident=int(ident)
        if kind=='proof':premises.append(parents[ident]._targets.claim(f,positions[ident]))
        elif kind=='assumption':premises.append(a[ident])
        else:premises.append(f.side(kind,ident,side));deps.append((kind,ident,side))
    premises.extend(a);data=claim_data(owner,target)
    fixed={v:lo for v,(lo,hi) in ib.items() if lo==hi}
    used={v for ts,_,_ in (*premises,data) for v,c in ts}
    for v in used-set(selected)-set(fixed):
        lo,hi=f.var_bounds(v)
        if lo==hi and lo not in (-inf,inf):
            fixed[v]=lo;deps.extend((('var',v,'lb'),('var',v,'ub')))
            if f.var_kind(v)=='I':
                deps.append(('integer',v,'kind'));contradiction |= Fraction(lo).denominator!=1
    if len(used)*256+sum(size(c) for ts,_,_ in (*premises,data) for _,c in ts)>budget:
        raise QueryMemoryError('Enumeration premise state exceeds the working-memory budget')
    if not used<=set(selected)|set(fixed):raise UnsupportedEnumerationDomain('Variables remain outside the finite selection')
    ranges=[]
    for v in selected:
        rec=f.numeric_var(v);lo,hi=ib.get(v,rec[3:5])
        if rec[2]!='I' or lo==-inf or hi==inf:raise UnsupportedEnumerationDomain('Finite integer variables are required')
        ranges.append(range(ceil(lo),floor(hi)+1));deps.extend((('integer',v,'kind'),('var',v,'lb'),('var',v,'ub')))
    # itertools.product materializes each range. The odometer retains only one
    # assignment even when a domain has billions of values and max_states=1.
    def assignments():
        if any(r.start>=r.stop for r in ranges):return
        values=[r.start for r in ranges]
        while True:
            yield values
            for i in range(len(values)-1,-1,-1):
                values[i]+=1
                if values[i]<ranges[i].stop:break
                values[i]=ranges[i].start
            else:return
    start=time.monotonic();states=feasible=0;minimum=maximum=None;counterexample={};status=1;exhaustive=True;stop_reason=None
    if not contradiction:
        for values in assignments():
            if max_states is not None and states>=max_states or time_limit is not None and time.monotonic()-start>=time_limit:
                status=0;exhaustive=False;stop_reason='max_states' if max_states is not None and states>=max_states else 'time_limit';break
            states+=1;point=dict(fixed);point.update(zip(selected,values))
            if not all(satisfied(p,point) for p in premises):continue
            feasible+=1;value=sum(c*point[v] for v,c in data[0])-data[1]
            minimum=value if minimum is None else min(minimum,value);maximum=value if maximum is None else max(maximum,value)
            if not satisfied(data,point):counterexample=point;status=2;exhaustive=False;break
    t=target_data(owner,target)
    p=BatchProof(_KEY,owner,t,[status],rule='enumerate',parents=parents,dependencies=deps,
                 assumptions=a,domain='integer',certificate=c,
                 recipe=dict(vars=selected,parent_indices=positions,assumptions=a,max_states=max_states,time_limit=time_limit))
    object.__setattr__(p,'_details',dict(states=states,feasible_states_observed=feasible,minimum=minimum,maximum=maximum,
                                      exhaustive=exhaustive,vacuous=exhaustive and feasible==0,stop_reason=stop_reason,
                                      extrema='exact' if exhaustive else 'observed only',counterexample_is_model_solution=False))
    object.__setattr__(p,'_counterexample',counterexample)
    return ProofRef(p,0)


class PointReport:
    """
    MIPFRAME HELP BEGIN
    Exact point-feasibility result returned by m.check.solution().

    Full contract: mf.help("api.PointReport")
    MIPFRAME HELP END
    """
    def __init__(self,owner,values,violations,summary):
        self._owner,self._values,self._violations=owner,values,violations
        self.summary=summary;self.ok=not violations;self.status='PROVED' if self.ok else 'REFUTED'
        self.scope=dict(kind='solution',domain='integer',arithmetic='exact',assumptions=0,complete=True)
        self.method='solution'
    def __bool__(self):raise TypeError('Inspect .ok')
    def require(self):
        """
        MIPFRAME HELP BEGIN
        Require exact integer-model point feasibility.

        Full contract: mf.help("api.PointReport.require")
        MIPFRAME HELP END
        """
        if not self.ok:
            from mipframe.check.report import ValidationError
            raise ValidationError(self)
        return self
    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        Inspect every violated row side, variable bound and integrality condition.

        Full contract: mf.help("api.PointReport.evidence")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        from mipframe.frame.schema import DType,ref_type,Ref
        result={}
        for axis in ('row','var'):
            records=({axis:Ref(self._owner.uid,self._owner.version,axis,r['ident']), 'side':r['side'],'value':r['value'],'bound':r['bound']}
                     for r in self._violations.records() if r['axis']==axis)
            with self._violations.work.fork():
                result['violating_'+('rows' if axis=='row' else 'vars')]=Frame.from_records(records,
                    schema={axis:ref_type(self._owner,axis),'side':'str','value':'number','bound':'number'},models=(self._owner,))
        return result
    def to_spec(self,*,materialize_mb=None):
        """
        MIPFRAME HELP BEGIN
        Materialize the exact point for later validation.

        Full contract: mf.help("api.PointReport.to_spec")
        MIPFRAME HELP END
        """
        from collections.abc import Mapping
        from mipframe.check.artifact import materialize
        point=self._values
        class Values(Mapping):
            def __len__(self):return point.facts.nvars
            def __iter__(self):return (str(v) for v in point.facts.var_ids())
            def __getitem__(self,key):return str(point[int(key)])
        return materialize(dict(schema='mipframe-point-1',snapshot=self._owner.uid,values=Values()),materialize_mb)
    @resource_scope
    def write(self,path,*,memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Write the exact point as a portable artifact.

        Full contract: mf.help("api.PointReport.write")
        MIPFRAME HELP END
        """
        from mipframe.check.artifact import write_bundle
        from mipframe.check.kernel import frozen_ints
        from mipframe.data.columns import ExactColumn
        ids=frozen_ints(self._values.facts.var_ids())
        values=ExactColumn((self._values[int(i)] for i in ids),len(ids))
        return write_bundle(dict(schema='mipframe-point-2',snapshot=self._owner.uid,ids=ids,values=values),path)
    def close(self):
        """
        MIPFRAME HELP BEGIN
        Release retained point and violation storage.

        Full contract: mf.help("api.PointReport.close")
        MIPFRAME HELP END
        """
        if hasattr(self._values,'close'):self._values.close()
        self._violations.work.close()
    def __repr__(self):return f'<PointReport {self.status}: {self.summary}>'


@resource_scope
def solution(owner,values,*,value='value',missing='error',domain='auto',memory_mb=128,disk_mb=None):
    from mipframe.frame.base import Frame
    from mipframe.frame.schema import SchemaError
    if domain not in ('auto','integer'):
        from mipframe._help.diagnostics import hint
        raise hint(ValueError('Solution checks verify the integer model; use matrix.activity for fractional points'),'limitation.fractional-point')
    if missing not in ('error','zero'):raise ValueError('Invalid missing policy')
    from mipframe.data.exact_sparse import ExactSparse,ExactPoint
    f=facts(owner)
    def entries():
        records=((r['var'],r[value]) for r in values) if isinstance(values,Frame) else values.items()
        for key,x in records:
            if hasattr(key,'uid') and key.uid!=owner.uid or hasattr(key,'_owner') and key._owner.uid!=owner.uid:
                raise SchemaError('Foreign variable')
            v=key.id if hasattr(key,'id') else key
            from numbers import Integral
            if isinstance(v,bool) or not isinstance(v,Integral):raise SchemaError('Expected an integer variable ID')
            f.validate('var',v)
            yield v,exact(x)
    storage=ExactSparse(memory_mb,disk_mb)
    try:
        try:storage.build(entries(),duplicates=True)
        except ValueError as e:
            if 'Duplicate vector variable' in str(e):raise SchemaError('Duplicate point variable') from e
            raise
        if missing=='error' and len(storage.ids)!=f.nvars:raise SchemaError('Point must cover every variable')
        point=ExactPoint(storage,f)
    except BaseException:storage.close();raise
    from mipframe.data.relations import RelationBuilder
    from mipframe.data.runtime import WorkContext
    builder=RelationBuilder(WorkContext(),dict(axis='str',ident='int',side='str',value='number',bound='number'))
    def violation_record(axis,ident,side,value,bound):builder.append(dict(axis=axis,ident=ident,side=side,value=value,bound=bound))
    max_integer=0
    for v in f.var_ids():
        _,_,kind,lo,hi,_=f.numeric_var(v);x=point[v]
        if x<lo:violation_record('var',v,'lb',x,lo)
        if x>hi:violation_record('var',v,'ub',x,hi)
        if kind=='I' and Fraction(x).denominator!=1:
            violation=min(x-floor(x),ceil(x)-x);max_integer=max(max_integer,violation)
            violation_record('var',v,'integer',x,floor(x))
    for r in f.row_ids():
        x=sum(c*point[v] for v,c in f.terms(r));lo,hi=f.row_bounds(r)
        if x<lo:violation_record('row',r,'lb',x,lo)
        if x>hi:violation_record('row',r,'ub',x,hi)
    objective=f.constant+sum(f.numeric_var(v)[5]*point[v] for v in f.var_ids())
    violations=builder.finish()
    return PointReport(owner,point,violations,dict(objective=objective,objective_sense=f.sense,optimality_proved=False,
                                                  max_integrality_violation=max_integer,violations=len(violations)))
