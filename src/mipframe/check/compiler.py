"""Public expression/Frame adapters compile once into the exact array kernel."""
from fractions import Fraction
from mipframe.data.runtime import resource_scope
from math import inf,ceil,floor
from mipframe.check.kernel import facts,exact,Targets,CertificateBatch,linear
from mipframe.check.compact import BatchProof,ProofRef,_issued,_KEY
from mipframe.check.certificate import InvalidCertificateError


def claim_data(owner,claim):
    from mipframe.linear.claim import Claim
    if not isinstance(claim,Claim) or any(m.uid!=owner.uid or m.version!=owner.version for m in claim.expr._models):
        raise InvalidCertificateError('Expected a claim of this snapshot')
    d={}
    for v,c in claim.expr._items():d[v]=d.get(v,0)+exact(c)
    return tuple(sorted((v,c) for v,c in d.items() if c)),exact(-claim.expr.constant),claim.relation


def target_data(owner,claim,key=0):
    ts,rhs,rel=claim_data(owner,claim);side={'<=':'ub','>=':'lb','==':'eq'}[rel]
    if claim._source is not None:
        src=claim._source
        expected=facts(owner).side(src['axis'],src['id'],src['side'])
        if expected!=(ts,rhs,rel):raise InvalidCertificateError('Forged source claim')
        if src['axis']=='row':return Targets([src['id']],sides=side)
    if len(ts)==1 and ts[0][1]==1:return Targets([ts[0][0]],'var',side,[rhs])
    return Targets([key],'affine',side,[rhs],[0,len(ts)],(v for v,c in ts),(c for v,c in ts))


def assumptions_data(owner,assume):
    return tuple(dict.fromkeys(claim_data(owner,a) for a in assume))


def intervals(f,assumptions):
    result={};contradiction=False
    for terms,rhs,rel in assumptions:
        if not terms:
            contradiction |= not (0<=rhs if rel=='<=' else 0>=rhs if rel=='>=' else rhs==0)
        if len(terms)!=1:continue
        v,a=terms[0];value=Fraction(rhs,a)
        if a<0:rel={'<=':'>=','>=':'<=','==':'=='}[rel]
        lo,hi=result.get(v,f.numeric_var(v)[3:5])
        if rel in ('>=','=='):lo=max(lo,value)
        if rel in ('<=','=='):hi=min(hi,value)
        result[v]=(lo,hi);contradiction |= lo>hi
    return result,contradiction


def source_records(owner,using,reports=None):
    from mipframe.frame.base import Frame
    from mipframe.linear.claim import source_claim
    if isinstance(using,Frame):
        for rec in using:
            keys=[k for k in ('row','var','proof') if k in using.schema and rec[k] is not None]
            if len(keys)!=1:raise InvalidCertificateError('Exactly one certificate source is required')
            axis=keys[0];item=rec[axis]
            if axis=='proof':item=reports[item]
            else:item=source_claim(item.bind(owner) if hasattr(item,'bind') else item,rec['side'])
            yield rec['multiplier'],item
    else:
        for value in using:
            if isinstance(value,Frame):yield from source_records(owner,value,reports)
            elif isinstance(value,tuple) and len(value)==2:yield value
            else:yield 1,value


def compile_sources(owner,using,assumptions=(),reports=None):
    from mipframe.data.runtime import WorkContext,leased_array
    from mipframe.data.buffers import Buffer
    from mipframe.data.columns import ExactBuilder,EnumBuilder
    from mipframe.data.intern import BytePool
    work=WorkContext();f=facts(owner);ids=Buffer(work,'<u8');positions=Buffer(work,'<u8')
    sides=EnumBuilder(work,('lb','ub','eq'));kinds=EnumBuilder(work,('row','var','proof','assumption'));weights=ExactBuilder(work)
    parents=[];parent_keys=BytePool(work)
    for a,src in source_records(owner,using,reports):
        a=exact(a)
        if isinstance(src,ProofRef):
            b=src.batch
            if b not in _issued or b._facts is not f or not src.ok:raise InvalidCertificateError('Expected a checked source proof')
            import struct
            kind,ident,side='proof',parent_keys.intern((struct.pack('<QQ',id(b),src.index),)),'ub'
            if ident==len(parents):parents.append(b);positions.append(src.index)
        else:
            data=claim_data(owner,src)
            if src._source is None:
                if data not in assumptions:raise InvalidCertificateError('Unproved claim is not a source')
                kind,ident,side='assumption',assumptions.index(data),'ub'
            else:
                s=src._source;kind,ident,side=s['axis'],s['id'],s['side']
                if f.side(kind,ident,side)!=data:raise InvalidCertificateError('Forged source provenance')
        ids.append(ident);sides.append(side);kinds.append(kind);weights.append(a)
    n=len(ids)
    result=CertificateBatch([0,n],leased_array(ids.finish(),work),weights.freeze(),sides.freeze(),kinds.freeze())
    return result,parents,leased_array(positions.finish(),work)


@resource_scope
def execute(owner,t,c,*,residual='exact',parents=(),positions=(),assumptions=(),domain='auto',rule='linear',memory_mb=128,disk_mb=None):
    from mipframe.check.graph import execute_graph
    return execute_graph(owner,t,c,residual=residual,parents=parents,positions=positions,
                         assumptions=assumptions,domain=domain,rule=rule,memory_mb=memory_mb,disk_mb=disk_mb)


from mipframe.data.runtime import workspace_method


class LinearBatch:
    """
    Compatibility input adapter for the unified check graph.

    MIPFRAME HELP BEGIN
    Incremental linear certificate builder obtained through m.check.linear_batch().

    Full contract: mf.help("api.LinearBatch")
    MIPFRAME HELP END
    """
    def __init__(self,checker,*,residual='exact',domain='auto',**options):
        from mipframe.check.graph import CheckBatch
        from mipframe.check.keys import Keys
        from mipframe.data.runtime import WorkContext
        self.checker=checker;self.residual=residual;self.domain=domain
        self.plan=CheckBatch(checker,**options)
        self.work=WorkContext(options.get('memory_mb',128),options.get('disk_mb'))
        self.keys=Keys(self.work);self.roots=[];self.closed=False

    def add(self,*,key,target,using=(),assume=()):
        """
        MIPFRAME HELP BEGIN
        Stage one keyed target with the same using and assume arguments as check.linear.

        Full contract: mf.help("api.LinearBatch.add")
        MIPFRAME HELP END
        """
        if self.closed:raise ValueError('Batch closed')
        try:self.keys.lookup(key)
        except KeyError:pass
        else:raise ValueError('Duplicate target key')
        handle=self.plan.linear(target,using=using,assume=assume,residual=self.residual,domain=self.domain)
        self.keys.append(key);self.roots.append(handle._index)
        return self

    def run(self):
        """
        MIPFRAME HELP BEGIN
        Verify every staged linear target and seal the builder.

        Full contract: mf.help("api.LinearBatch.run")
        MIPFRAME HELP END
        """
        if self.closed:raise ValueError('Batch closed')
        from mipframe.check.graph import GraphExecution,GraphProof
        self.plan._guard();self.plan.closed=True
        state=GraphExecution(self.checker._owner,self.plan.pool,self.plan.tasks,tuple(self.plan.parents),self.plan.budget)
        try:
            state.execute(self.roots)
            result=GraphProof(self.checker._owner,state,self.roots)
            object.__setattr__(result,'_external_keys',self.keys)
            return result
        finally:self.close()

    def close(self):
        """
        MIPFRAME HELP BEGIN
        Release unpublished batch work and close the builder.

        Full contract: mf.help("api.LinearBatch.close")
        MIPFRAME HELP END
        """
        if self.closed:return
        self.closed=True;self.plan.close();self.work.close()
        self.keys=None;self.roots=[]

    def __enter__(self):
        self.plan.__enter__()
        return self

    def __exit__(self,*args):
        self.plan.__exit__(*args)
        self.close()


class Checker:
    """
    MIPFRAME HELP BEGIN
    Exact certificate verification through m.check.

    Full contract: mf.help("api.Checker")
    MIPFRAME HELP END
    """
    def __init__(self,owner):self._owner=owner
    def budget(self, **options):
        """
        Explicitly raise finite shared limits for nested check operations.

        MIPFRAME HELP BEGIN
        Share explicit finite limits across nested check operations.

        Full contract: mf.help("api.Checker.budget")
        MIPFRAME HELP END
        """
        from mipframe.check.budget import CheckBudget
        return CheckBudget(**options)

    def batch(self, **options):
        """
        Register checks in one deferred, exact execution graph.

        MIPFRAME HELP BEGIN
        Register related checks once and execute their shared dependency graph.

        Full contract: mf.help("api.Checker.batch")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import CheckBatch
        return CheckBatch(self, **options)
    def enumerate(self,target,*,memory_mb=128,disk_mb=None,**kwargs):
        """
        MIPFRAME HELP BEGIN
        Verify a claim on a finite local integer domain.

        Full contract: mf.help("api.Checker.enumerate")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'enumerate',target,memory_mb=memory_mb,disk_mb=disk_mb,**kwargs)
    def solution(self,values,*,memory_mb=128,disk_mb=None,**kwargs):
        """
        MIPFRAME HELP BEGIN
        Validate a complete exact point against the mathematical integer model, including integrality, original bounds and both row sides.

        Full contract: mf.help("api.Checker.solution")
        MIPFRAME HELP END
        """
        from mipframe.check.finite import solution
        return solution(self._owner,values,memory_mb=memory_mb,disk_mb=disk_mb,**kwargs)
    def linear_rows(self,**kwargs):
        """
        MIPFRAME HELP BEGIN
        Check existing row IDs directly from ragged source witnesses.

        Full contract: mf.help("api.Checker.linear_rows")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import linear_rows_graph
        return linear_rows_graph(self._owner,**kwargs)
    def bound(self,*,vars,values,side='ub'):
        """
        MIPFRAME HELP BEGIN
        Check direct variable-bound implications in a batch.

        Full contract: mf.help("api.Checker.bound")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'bound',vars=vars,values=values,side=side)
    def affine_integrality(self,*,vars,rows):
        """
        MIPFRAME HELP BEGIN
        Check integer affine definitions independently of the target integer declaration.

        Full contract: mf.help("api.Checker.affine_integrality")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'affine_integrality',vars=vars,rows=rows)
    def one_hot_affine(self,*,groups):
        """
        MIPFRAME HELP BEGIN
        Validate shared one-hot affine structure once, then check bound exclusions.

        Full contract: mf.help("api.Checker.one_hot_affine")
        MIPFRAME HELP END
        """
        from mipframe.check.compact import one_hot_affine
        return one_hot_affine(self._owner,groups)
    def linear_batch(self,**kwargs):
        """
        MIPFRAME HELP BEGIN
        Compile scalar Claim inputs into one compact batch.

        Full contract: mf.help("api.Checker.linear_batch")
        MIPFRAME HELP END
        """
        return LinearBatch(self,**kwargs)
    @resource_scope
    def linear_many(self,certificate=None,*,targets=None,witnesses=None,terms=None,using=(),reports=None,residual='exact',domain='auto',**options):
        """
        MIPFRAME HELP BEGIN
        Check compact custom targets and ragged explicit witnesses.

        Full contract: mf.help("api.Checker.linear_many")
        MIPFRAME HELP END
        """
        if certificate is not None:
            from mipframe.ops.reduction import LinearCertificate
            if not isinstance(certificate,LinearCertificate):raise TypeError('Expected LinearCertificate')
            if targets is not None or witnesses is not None or terms is not None or using:raise ValueError('Do not mix a certificate with separate inputs')
            f=facts(self._owner)
            if (certificate.uid,certificate.revision)!=(f.uid,f.revision):raise InvalidCertificateError('Certificate belongs to another snapshot')
            if certificate._resource is not None:certificate._resource.guard()
            return execute(self._owner,certificate.targets,certificate.witnesses,residual=residual,domain=domain,**options)
        if isinstance(targets,Targets):
            return execute(self._owner,targets,witnesses or using,residual=residual,domain=domain,**options)
        from mipframe.frame.executor import Context
        from mipframe.data.buffers import Buffer
        from mipframe.data.columns import ExactBuilder
        from mipframe.data.relations import build,search_range
        from mipframe.data.exact_sparse import ExactSparse
        from mipframe.check.keys import Keys
        owner=self._owner;f=facts(owner)
        if reports is not None and any(not isinstance(k,str) for k in reports):raise ValueError('Proof mapping requires string keys')
        memory_mb=options.get('memory_mb',128);disk_mb=options.get('disk_mb')
        with Context((owner,),memory_mb,disk_mb) as ctx, ctx.work.fork() as work:
            external=Keys(work);rhs=ExactBuilder(work);sides=[]
            from mipframe.data.columns import EnumBuilder
            target_sides=EnumBuilder(work,('lb','ub','eq'))
            for record in targets:
                external.append(record['target']);rhs.append(exact(record['rhs']))
                target_sides.append({'<=':'ub','>=':'lb','==':'eq'}[record['relation']])
            n=len(external);capacity=f.var_capacity
            if n*capacity>2**64:raise OverflowError('Affine target coordinate capacity exceeds uint64')
            def reference(ref,kind):
                from mipframe.entity import Entity
                identity=(ref._owner.uid,ref._owner.version,'row' if type(ref).__name__=='Row' else 'var') if isinstance(ref,Entity) else (ref.uid,ref.version,ref.kind)
                if identity!=(owner.uid,owner.version,kind):raise InvalidCertificateError('Reference belongs to another snapshot or axis')
                f.validate(kind,ref.id);return ref.id
            offsets=Buffer(work,'<u8');offsets.append(0);variables=Buffer(work,'<u8');values=ExactBuilder(work)
            from contextlib import closing
            with closing(ExactSparse(work=work)) as storage:
                storage.build((external.lookup(term['target'])*capacity+reference(term['var'],'var'),exact(term['coef'])) for term in terms)
                last=0
                for code,value in storage.items():
                    target,var=divmod(code,capacity)
                    while last<target:offsets.append(len(variables));last+=1
                    variables.append(var);values.append(value)
                while len(offsets)<=n:offsets.append(len(variables))
                compiled=Targets(range(n),'affine',target_sides.freeze(),rhs.freeze(),offsets.finish(),variables.finish(),values.freeze())
            parents=[];positions=Buffer(work,'<u8')
            blocks=(using,) if hasattr(using,'schema') else using
            def witnesses():
                for block in blocks:
                    for record in block:
                        if record.get('direction','main')!='main':raise InvalidCertificateError('Use scalar directional equality certificates')
                        chosen=[kind for kind in ('row','var','proof') if record.get(kind) is not None]
                        if len(chosen)!=1:raise InvalidCertificateError('Expected one source')
                        kind=chosen[0]
                        if kind=='proof':
                            proof=reports[record[kind]]
                            if not isinstance(proof,ProofRef) or proof.batch not in _issued or proof.batch._facts is not f or not proof.ok:raise InvalidCertificateError('Expected checked source proof')
                            ident=len(parents);side='ub';parents.append(proof.batch);positions.append(proof.index)
                        else:
                            ident=reference(record[kind],kind);side=record['side'];f.side(kind,ident,side)
                        yield dict(target=external.lookup(record['target']),source=ident,multiplier=exact(record['multiplier']),side=side,kind=kind)
            table=build(work,dict(target='int',source='int',multiplier='number',side='str',kind='str'),witnesses())
            order=table.order(('target',));offsets=Buffer(work,'<u8');offsets.append(0);at=0
            for target in range(n):
                while at<len(order) and int(table.columns['target'][order[at]])==target:at+=1
                offsets.append(at)
            certificate=CertificateBatch(offsets.finish(),(table.columns['source'][i] for i in order),
                (table.columns['multiplier'][i] for i in order),(table.columns['side'][i] for i in order),(table.columns['kind'][i] for i in order))
            result=execute(owner,compiled,certificate,residual=residual,domain=domain,parents=parents,positions=positions.finish(),**options)
            object.__setattr__(result,'_external_keys',external)
            return result
    @resource_scope
    def linear(self,target,*,using=(),residual='exact',assume=(),domain='auto',memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Verify that supplied sources imply the target inequality or equality.

        Full contract: mf.help("api.Checker.linear")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'linear',target,using=using,residual=residual,assume=assume,domain=domain,memory_mb=memory_mb,disk_mb=disk_mb)
    def identity(self,left,right=0,*,using=(),assume=(),memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Check an exact affine identity or explicit equality combination.

        Full contract: mf.help("api.Checker.identity")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'identity',left,right,using=using,assume=assume,memory_mb=memory_mb,disk_mb=disk_mb)
    @resource_scope
    def round(self,target,*,from_,domain='auto',memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Round a proved bound on a model-integer-valued expression.

        Full contract: mf.help("api.Checker.round")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'round',target,from_=from_,domain=domain,memory_mb=memory_mb,disk_mb=disk_mb)
    def infeasible(self,*,using=(),assume=(),residual='exact',memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Verify an explicit contradiction certificate.

        Full contract: mf.help("api.Checker.infeasible")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'infeasible',using=using,assume=assume,residual=residual,memory_mb=memory_mb,disk_mb=disk_mb)

    @resource_scope
    def cases(self,target,*,var,cases,domain='auto',memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Discharge an exhaustive finite integer split over one variable.

        Full contract: mf.help("api.Checker.cases")
        MIPFRAME HELP END
        """
        from mipframe.check.graph import immediate
        return immediate(self,'cases',target,var=var,cases=cases,domain=domain,memory_mb=memory_mb,disk_mb=disk_mb)
