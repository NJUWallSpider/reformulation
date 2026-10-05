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
    f=facts(owner)
    if len(parents)!=len(positions):raise InvalidCertificateError('Invalid parent positions')
    for p,i in zip(parents,positions):
        if p not in _issued or p._facts is not f or not 0<=i<len(p._status) or p._status[i]!=1:raise InvalidCertificateError('Invalid parent certificate')
    assumptions=tuple(dict.fromkeys((*assumptions,*(a for p in parents for a in p._assumptions))))
    if domain not in ('auto','integer','relaxation'):raise ValueError('Invalid proof domain')
    integer=domain=='integer' or any(p._domain=='integer' for p in parents)
    if integer and domain=='relaxation':raise InvalidCertificateError('Integer proof cannot become an LP proof')
    from collections.abc import Sequence
    class Premises(Sequence):
        def __len__(self):return len(parents)
        def __getitem__(self,i):return parents[i]._targets.claim(f,positions[i])
    premises=Premises()
    ib,contradiction=intervals(f,assumptions)
    try:status,deps=linear(f,t,c,residual,premises=premises,assumptions=assumptions,intervals=ib,memory_mb=memory_mb,disk_mb=disk_mb)
    except (ValueError,IndexError) as e:raise InvalidCertificateError(str(e)) from e
    if contradiction:
        from mipframe.data.columns import ConstantStatus
        status=ConstantStatus(1,len(t.ids))
    return BatchProof(_KEY,owner,t,status,rule=rule,certificate=c,bounds=deps,parents=parents,
                      assumptions=assumptions,domain='integer' if integer else 'relaxation',
                      recipe=dict(residual=residual,parent_indices=positions,assumptions=assumptions))


from mipframe.data.runtime import workspace_method


class LinearBatch:
    """
    Incremental compiler. Executes one compact batch without per-target Reports.

    MIPFRAME HELP BEGIN
    Incremental linear certificate builder obtained through m.check.linear_batch().

    Full contract: mf.help("api.LinearBatch")
    MIPFRAME HELP END
    """
    def __init__(self,checker,*,residual='exact',domain='auto',**options):
        self.checker=checker;self.residual=residual;self.domain=domain;self.closed=False
        self.options=options
        from mipframe.data.runtime import WorkContext
        from mipframe.data.buffers import Buffer
        from mipframe.data.columns import ExactBuilder,EnumBuilder
        from mipframe.check.keys import Keys
        self.work=WorkContext(options.get('memory_mb',128),options.get('disk_mb'))
        self.keys=Keys(self.work);self.assumptions=None
        self.offsets=Buffer(self.work,'<u8');self.offsets.append(0);self.variables=Buffer(self.work,'<u8');self.coefficients=ExactBuilder(self.work)
        self.rhs=ExactBuilder(self.work);self.sides=EnumBuilder(self.work,('lb','ub','eq'))
        self.source_offsets=Buffer(self.work,'<u8');self.source_offsets.append(0);self.source_ids=Buffer(self.work,'<u8');self.weights=ExactBuilder(self.work)
        self.source_sides=EnumBuilder(self.work,('lb','ub','eq'));self.source_kinds=EnumBuilder(self.work,('row','var','proof','assumption'))
        self.parents=[];self.positions=Buffer(self.work,'<u8')
    @workspace_method
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
        owner=self.checker._owner
        terms,rhs,relation=claim_data(owner,target)
        assumptions=assumptions_data(owner,assume)
        if self.assumptions is not None and assumptions!=self.assumptions:
            raise ValueError('Batch targets must share assumptions; use explicit case proofs')
        certificate,parents,positions=compile_sources(owner,using,assumptions)
        self.keys.append(key);self.assumptions=assumptions
        for variable,value in terms:self.variables.append(variable);self.coefficients.append(value)
        self.offsets.append(len(self.variables));self.rhs.append(rhs)
        self.sides.append({'<=':'ub','>=':'lb','==':'eq'}[relation])
        for ident,kind in zip(certificate.sources,certificate.kinds):
            self.source_ids.append(int(ident)+len(self.parents) if kind=='proof' else int(ident))
        self.weights.extend(certificate.multipliers)
        self.source_sides.extend(certificate.sides);self.source_kinds.extend(certificate.kinds)
        self.source_offsets.append(len(self.source_ids))
        self.parents.extend(parents);self.positions.extend(positions)
        return self
    @workspace_method
    def run(self):
        """
        MIPFRAME HELP BEGIN
        Verify every staged linear target and seal the builder.

        Full contract: mf.help("api.LinearBatch.run")
        MIPFRAME HELP END
        """
        if self.closed:raise ValueError('Batch closed')
        owner=self.checker._owner
        t=Targets(range(len(self.keys)),'affine',self.sides.freeze(),self.rhs.freeze(),
            self.offsets.finish(),self.variables.finish(),self.coefficients.freeze(),**self.options)
        certificate=CertificateBatch(self.source_offsets.finish(),self.source_ids.finish(),
            self.weights.freeze(),self.source_sides.freeze(),self.source_kinds.freeze(),**self.options)
        result=execute(owner,t,certificate,residual=self.residual,parents=self.parents,
            positions=self.positions.finish(),assumptions=self.assumptions or (),domain=self.domain,**self.options)
        object.__setattr__(result,'_external_keys',self.keys);self.close()
        return result
    def close(self):
        """
        MIPFRAME HELP BEGIN
        Release unpublished batch work and close the builder.

        Full contract: mf.help("api.LinearBatch.close")
        MIPFRAME HELP END
        """
        self.closed=True
        self.work.close()
        for name in ('keys','_keyset','variables','coefficients','rhs','sides','source_ids','weights',
                     'source_sides','source_kinds','parents','positions','offsets','source_offsets'):
            if hasattr(self,name):delattr(self,name)
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


class Checker:
    """
    MIPFRAME HELP BEGIN
    Exact certificate verification through m.check.

    Full contract: mf.help("api.Checker")
    MIPFRAME HELP END
    """
    def __init__(self,owner):self._owner=owner
    def enumerate(self,target,*,memory_mb=128,disk_mb=None,**kwargs):
        """
        MIPFRAME HELP BEGIN
        Verify a claim on a finite local integer domain.

        Full contract: mf.help("api.Checker.enumerate")
        MIPFRAME HELP END
        """
        from mipframe.check.finite import enumerate_claim
        return enumerate_claim(self._owner,target,memory_mb=memory_mb,disk_mb=disk_mb,**kwargs)
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
        from mipframe.check.compact import linear_rows
        return linear_rows(self._owner,**kwargs)
    def bound(self,*,vars,values,side='ub'):
        """
        MIPFRAME HELP BEGIN
        Check direct variable-bound implications in a batch.

        Full contract: mf.help("api.Checker.bound")
        MIPFRAME HELP END
        """
        from mipframe.check.compact import check_bounds
        return check_bounds(self._owner,Targets(vars,'var',side,values))
    def affine_integrality(self,*,vars,rows):
        """
        MIPFRAME HELP BEGIN
        Check integer affine definitions independently of the target integer declaration.

        Full contract: mf.help("api.Checker.affine_integrality")
        MIPFRAME HELP END
        """
        from mipframe.check.compact import affine_integrality
        return affine_integrality(self._owner,vars,rows)
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
        options=dict(memory_mb=memory_mb,disk_mb=disk_mb)
        if isinstance(using,dict):
            if target.relation!='==' or set(using)!={'le','ge'}:raise InvalidCertificateError('Equality needs le/ge certificates')
            from mipframe.linear.claim import Claim
            ps=[self.linear(Claim(target.expr,r),using=using[k],residual=residual,assume=assume,domain=domain,**options)
                for k,r in (('le','<='),('ge','>='))]
            t=target_data(self._owner,target)
            b=BatchProof(_KEY,self._owner,t,[all(p.ok for p in ps)],rule='equality',parents=[p.batch for p in ps],
                         assumptions=tuple(dict.fromkeys(a for p in ps for a in p.batch._assumptions)),
                         domain='integer' if any(p.batch._domain=='integer' for p in ps) else 'relaxation')
            return b.for_target(t.ids[0])
        a=assumptions_data(self._owner,assume);c,parents,positions=compile_sources(self._owner,using,a)
        t=target_data(self._owner,target)
        b=execute(self._owner,t,c,residual=residual,parents=parents,positions=positions,assumptions=a,domain=domain,**options)
        return b.for_target(t.ids[0])
    def identity(self,left,right=0,*,using=(),assume=(),memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Check an exact affine identity or explicit equality combination.

        Full contract: mf.help("api.Checker.identity")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import coerce
        target=(coerce(self._owner,left)-right).eq(0)
        p=self.linear(target,using=using,assume=assume,memory_mb=memory_mb,disk_mb=disk_mb)
        if not p.ok and not using and not assume:
            b=BatchProof(_KEY,self._owner,p.batch._targets,[2],rule='identity',
                         certificate=p.batch._certificate,recipe=p.batch._recipe)
            return b.for_target(b.target_ids[0])
        return p
    @resource_scope
    def round(self,target,*,from_,domain='auto',memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Round a proved bound on a model-integer-valued expression.

        Full contract: mf.help("api.Checker.round")
        MIPFRAME HELP END
        """
        from mipframe.data.runtime import WorkContext
        if domain not in ('auto','integer'):raise InvalidCertificateError('Rounding is integer-only')
        if not isinstance(from_,ProofRef) or from_.batch not in _issued or not from_.ok:raise InvalidCertificateError('Expected proved bound')
        t=target_data(self._owner,target);f=facts(self._owner)
        if from_.batch._facts is not f:raise InvalidCertificateError('Foreign proof')
        ts,b,rel=t.claim(f,0);ss,sb,srel=from_.batch._targets.claim(f,from_.index)
        if rel not in ('<=','>=') or rel!=srel or ts!=ss:raise InvalidCertificateError('Rounding requires identical coefficients and direction')
        for v,a in ts:
            if Fraction(a).denominator!=1 or f.numeric_var(v)[2]!='I':raise InvalidCertificateError('Expression is not integer-valued')
        value=(floor if rel=='<=' else ceil)(sb)
        ok=value<=b if rel=='<=' else value>=b
        p=BatchProof(_KEY,self._owner,t,[ok],rule='round_expression',parents=[from_.batch],dependencies=(('integer',v,'kind') for v,_ in ts),
                     assumptions=from_.batch._assumptions,domain='integer',recipe=dict(parent_indices=[from_.index]))
        return p.for_target(t.ids[0])
    def infeasible(self,*,using=(),assume=(),residual='exact',memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Verify an explicit contradiction certificate.

        Full contract: mf.help("api.Checker.infeasible")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import LinExpr
        return self.linear(LinExpr(self._owner,1)<=0,using=using,assume=assume,residual=residual,memory_mb=memory_mb,disk_mb=disk_mb)

    @resource_scope
    def cases(self,target,*,var,cases,domain='auto',memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Discharge an exhaustive finite integer split over one variable.

        Full contract: mf.help("api.Checker.cases")
        MIPFRAME HELP END
        """
        from mipframe.data.runtime import WorkContext
        if domain not in ('auto','integer'):raise InvalidCertificateError('Cases are integer-only')
        f=facts(self._owner)
        from mipframe.frame.schema import SchemaError
        if getattr(var,'uid',getattr(getattr(var,'_owner',None),'uid',f.uid))!=f.uid:raise SchemaError('Foreign case variable')
        if not isinstance(cases,dict):raise InvalidCertificateError('Cases must be a value-to-proof mapping')
        v=var.id if hasattr(var,'id') else int(var)
        rec=f.numeric_var(v);lo,hi=rec[3:5]
        if rec[2]!='I' or lo==-inf or hi==inf:raise InvalidCertificateError('Finite integer variable required')
        try:keys=[exact(k) for k in cases]
        except (ValueError,TypeError) as e:raise InvalidCertificateError('Invalid case value') from e
        if not keys or len(keys)!=floor(hi)-ceil(lo)+1 or len(set(keys))!=len(keys) or any(Fraction(k).denominator!=1 or not ceil(lo)<=k<=floor(hi) for k in keys):raise InvalidCertificateError('Cases must cover a nonempty entire integer domain')
        data=claim_data(self._owner,target);parents=[];remaining=[]
        for k,p in cases.items():
            if not isinstance(p,ProofRef) or p.batch not in _issued or not p.ok or p.batch._facts is not f:raise InvalidCertificateError('Invalid branch proof')
            if p.batch._targets.claim(f,p.index)!=data:raise InvalidCertificateError('Branch conclusion differs')
            matched=False
            for ts,b,rel in p.batch._assumptions:
                if rel=='==' and len(ts)==1 and ts[0][0]==v and Fraction(b,ts[0][1])==k:matched=True
                else:remaining.append((ts,b,rel))
            if not matched:raise InvalidCertificateError('Missing matching case assumption')
            parents.append(p.batch)
        t=target_data(self._owner,target)
        p=BatchProof(_KEY,self._owner,t,[True],rule='cases',parents=parents,domain='integer',
                     dependencies=(('integer',v,'kind'),('var',v,'lb'),('var',v,'ub')),
                     assumptions=tuple(dict.fromkeys(remaining)),recipe=dict(var=v,values=keys,parent_indices=[p.index for p in cases.values()]))
        return p.for_target(t.ids[0])
