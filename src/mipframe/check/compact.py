"""Proof-carrying certificate API and lazy presentation adapters."""
import numpy as np
from mipframe.data.runtime import resource_scope
from mipframe.check.budget import check_operation
from fractions import Fraction
from math import floor, ceil, inf
from types import MappingProxyType
import json
import weakref
from mipframe.check.kernel import (facts, exact, frozen_ints, CertificateBatch,
                                  Targets, linear, Facts)

_issued = weakref.WeakSet()
_KEY = object()


class BatchProof:
    """
    Checked immutable batch. Reports/claims are materialized only on request.

    MIPFRAME HELP BEGIN
    Immutable checked batch with lazy target presentation.

    Full contract: mf.help("api.BatchProof")
    MIPFRAME HELP END
    """
    def __init__(self, key, owner, targets, status, *, rule, certificate=None,
                 bounds=(), parents=(), dependencies=(), per_target_dependencies=(), recipe=None, domain='relaxation', assumptions=()):
        if key is not _KEY:
            raise TypeError('Proofs can only be issued by a checker')
        self._owner, self._facts = owner, facts(owner)
        from mipframe.data.columns import ConstantStatus,EnumColumn
        self._targets,self._status=targets,status if isinstance(status,ConstantStatus) else EnumColumn(status,(0,1,2),len(targets.ids))
        self._failures = len(self._status)-self._status.count(1)
        self._ok = not self._failures
        self._rule, self._certificate = rule, certificate
        from mipframe.data.columns import DependencyColumn,RaggedDependencies
        self._bounds, self._parents = RaggedDependencies(bounds,bounds=True), tuple(parents)
        self._dependencies = DependencyColumn(dependencies)
        self._target_dependencies = RaggedDependencies(per_target_dependencies)
        self._recipe = recipe or {}
        self._domain = domain
        self._assumptions = tuple(assumptions)
        self._lookup = None
        _issued.add(self)
        self._sealed = True

    def __setattr__(self, name, value):
        if getattr(self, '_sealed', False):
            raise AttributeError('Proofs are immutable')
        object.__setattr__(self, name, value)

    @property
    def target_ids(self):
        """
        MIPFRAME HELP BEGIN
        Read compact integer target IDs in batch order.

        Full contract: mf.help("api.BatchProof.target_ids")
        MIPFRAME HELP END
        """
        return self._targets.ids

    @property
    def ok(self):
        """
        MIPFRAME HELP BEGIN
        Test whether every target is PROVED.

        Full contract: mf.help("api.BatchProof.ok")
        MIPFRAME HELP END
        """
        return self._ok

    @property
    def status(self):
        """
        MIPFRAME HELP BEGIN
        Read the aggregate proof status.

        Full contract: mf.help("api.BatchProof.status")
        MIPFRAME HELP END
        """
        return 'PROVED' if self.ok else 'REFUTED' if 2 in self._status else 'INCONCLUSIVE'

    @property
    def summary(self):
        """
        MIPFRAME HELP BEGIN
        Read batch operation, target count and failure count.

        Full contract: mf.help("api.BatchProof.summary")
        MIPFRAME HELP END
        """
        return dict(operation=self._rule, targets=len(self._status), failures=self._failures,
                    **getattr(self,'_details',{}))

    @property
    def scope(self):
        """
        MIPFRAME HELP BEGIN
        Read the snapshot, exact arithmetic, domain and assumption count.

        Full contract: mf.help("api.BatchProof.scope")
        MIPFRAME HELP END
        """
        return dict(snapshot=dict(uid=self._facts.uid), arithmetic='exact',
                    domain=self._domain, assumptions=len(self._assumptions), complete=True)

    def require(self):
        """
        MIPFRAME HELP BEGIN
        Require every target to be proved.

        Full contract: mf.help("api.BatchProof.require")
        MIPFRAME HELP END
        """
        if not self.ok:
            from mipframe.check.report import ValidationError
            raise ValidationError(self)
        return self

    def __bool__(self): raise TypeError('Proof has no implicit truth value; use .ok')

    def for_target(self, key):
        """
        MIPFRAME HELP BEGIN
        Select one conclusion by its target key.

        Full contract: mf.help("api.BatchProof.for_target")
        MIPFRAME HELP END
        """
        keys=getattr(self,'_external_keys',None)
        if hasattr(keys,'lookup'):return ProofRef(self,keys.lookup(key))
        if self._lookup is None:
            from mipframe.data.index import PositionIndex
            object.__setattr__(self,'_lookup',PositionIndex(self.target_ids))
        return ProofRef(self,self._lookup[int(key)])

    def select(self, ids):
        """
        MIPFRAME HELP BEGIN
        Select several conclusions by their target keys.

        Full contract: mf.help("api.BatchProof.select")
        MIPFRAME HELP END
        """
        return ProofSelection(self,(self.for_target(key).index for key in ids))

    def round_many(self, *, vars=None, ub=None, lb=None, memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Round proved variable bounds using model integrality.

        Full contract: mf.help("api.BatchProof.round_many")
        MIPFRAME HELP END
        """
        if self._targets.kind != 'var' or (ub is None) == (lb is None):
            raise ValueError('Rounding requires variable bounds and exactly one side')
        side, requested = ('ub',ub) if ub is not None else ('lb',lb)
        from mipframe.check.graph import CheckBatch
        from mipframe.data.columns import ExactColumn
        from mipframe.data.index import PositionIndex
        chosen=tuple(map(int,self.target_ids if vars is None else vars))
        if len(set(chosen))!=len(chosen):raise ValueError('Duplicate target IDs')
        values=ExactColumn(requested,len(chosen))
        lookup=PositionIndex(self.target_ids)
        with CheckBatch(self._owner.check,memory_mb=memory_mb,disk_mb=disk_mb) as plan:
            handles=[]
            for v,value in zip(chosen,values):
                i=lookup[v]
                if self._targets.sides[i]!=side:raise ValueError('Rounding requires matching sides')
                target=(plan.pool.terms(((v,1),)),value,'<=' if side=='ub' else '>=')
                h=plan._stage('round',target,(plan._reference(ProofRef(self,i)),),config=(v,side))
                handles.append((v,h._index))
            plan._many(handles,'rounded')
            return plan.run()['rounded']

    def assumptions(self, index=None):
        return self._assumptions

    def domain_at(self, index):
        return self._domain

    def dependencies(self, index=None):
        """Iterate actual premise identities without building Reports."""
        yield from self._dependencies
        if len(self._target_dependencies.values):
            for deps in (self._target_dependencies if index is None else self._target_dependencies[index:index+1]):
                yield from deps
        if self._certificate is not None:
            c = self._certificate
            spans = ((0,len(c.sources)),) if index is None else ((int(c.offsets[index]),int(c.offsets[index+1])),)
            for lo,hi in spans:
                for ident,weight,kind,side in zip(c.sources[lo:hi],c.multipliers[lo:hi],c.kinds[lo:hi],c.sides[lo:hi]):
                    if weight and kind in ('row','var'):
                        yield kind,int(ident),side
        if len(self._bounds.values):
            for deps in (self._bounds if index is None else self._bounds[index:index+1]):
                for v, side in deps:
                    yield 'var',v,side
        if self._certificate is not None:
            c = self._certificate
            lo, hi = (0, len(c.sources)) if index is None else map(int, c.offsets[index:index+2])
            code=c.kinds.labels.index('proof')
            for p in np.flatnonzero(c.kinds.codes[lo:hi]==code):
                p=int(p)+lo
                if c.multipliers[p]:
                    j=int(c.sources[p])
                    yield from self._parents[j].dependencies(self._recipe['parent_indices'][j])
        elif self._rule == 'round':
            for i in (range(len(self._status)) if index is None else (index,)):
                yield from self._parents[0].dependencies(self._recipe['positions'][i])
        else:
            for j, parent in enumerate(self._parents):
                positions = self._recipe.get('parent_indices')
                yield from parent.dependencies(None if positions is None else positions[j])

    @property
    def evidence(self):
        from mipframe.frame.base import Frame
        keys=getattr(self,'_external_keys',None)
        records=(dict(target=str(keys[i] if keys is not None else int(v)),status=('INCONCLUSIVE','PROVED','REFUTED')[self._status[i]])
                 for i,v in enumerate(self.target_ids))
        return dict(results=Frame.from_records(records,schema=dict(target='str',status='str'),key=('target',)))

    def to_spec(self,*,materialize_mb=None):
        """
        MIPFRAME HELP BEGIN
        Materialize an exact certificate graph for replay.

        Full contract: mf.help("api.BatchProof.to_spec")
        MIPFRAME HELP END
        """
        from mipframe.check.artifact import native_graph,materialize
        return materialize(native_graph(self),materialize_mb)

    def _native_spec(self):
        from mipframe.check.artifact import native_graph
        return native_graph(self)

    def write(self,path,*,memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Export an exact certificate graph to an artifact directory.

        Full contract: mf.help("api.BatchProof.write")
        MIPFRAME HELP END
        """
        from mipframe.check.artifact import native_graph,write_bundle
        from mipframe.data.runtime import WorkContext
        with WorkContext(memory_mb,disk_mb):return write_bundle(native_graph(self),path)

    @classmethod
    @check_operation
    def replay(cls, spec, *, model, memory_mb=128, disk_mb=None, _ancestors=frozenset(), _internal=False):
        """
        MIPFRAME HELP BEGIN
        Bind a saved batch certificate to a model and rerun its checks.

        Full contract: mf.help("api.BatchProof.replay")
        MIPFRAME HELP END
        """
        from pathlib import Path
        if isinstance(spec,(str,bytes,Path)):
            from mipframe.check.artifact import read_bundle
            path=Path(spec)
            if path.is_dir():
                value,tree=read_bundle(path)
                try:return cls.replay(value,model=model,memory_mb=memory_mb,disk_mb=disk_mb)
                finally:
                    for array in tree.maps:array._mmap.close()
            spec=json.loads(path.read_text())
        if isinstance(spec,BatchProof):
            if not _internal or spec not in _issued or spec._facts is not facts(model):raise ValueError('Untrusted dependency')
            return spec
        if spec.get('schema') in ('mipframe-certificate-2','mipframe-certificate-3'):
            if set(spec)-{'schema','snapshot','revision','root','nodes','status'}:raise ValueError('Cyclic or invalid certificate graph fields')
            if (spec.get('snapshot'),spec.get('revision'))!=(facts(model).uid,facts(model).revision):raise ValueError('Certificate snapshot mismatch')
            if not spec['nodes'] or any(node.get('rule')!='check_graph' for node in spec['nodes']):
                from mipframe.check.legacy import replay_legacy
                return replay_legacy(model,spec,memory_mb=memory_mb,disk_mb=disk_mb)
            results=[]
            for node in spec['nodes']:
                parents=node.get('parents',())
                if any(type(i) is not int or not 0<=i<len(results) for i in parents):raise ValueError('Cyclic or invalid certificate dependency')
                record=dict(node,parents=[results[i] for i in parents])
                results.append(cls.replay(record,model=model,memory_mb=memory_mb,disk_mb=disk_mb,_internal=True))
            root=spec['root']
            if type(root) is not int or not 0<=root<len(results):raise ValueError('Invalid certificate root')
            return results[root]
        if not _internal:raise ValueError('Unsupported certificate format; regenerate the proof')
        if id(spec) in _ancestors:raise ValueError('Cyclic certificate dependency')
        _ancestors=_ancestors|{id(spec)}
        if len(_ancestors)>256:raise ValueError('Certificate dependency depth exceeds 256')
        def parent(spec):return cls.replay(spec,model=model,memory_mb=memory_mb,disk_mb=disk_mb,_ancestors=_ancestors,_internal=True)
        if spec.get('schema') != 'mipframe-certificate-node-2' or spec.get('snapshot') != facts(model).uid or spec.get('revision') != facts(model).revision:
            raise ValueError('Certificate version or snapshot mismatch')
        def decode(v):
            if isinstance(v,dict):
                if set(v)=={'q'}: return exact(v['q'])
                return {k:decode(x) for k,x in v.items()}
            if isinstance(v,list): return [decode(x) for x in v]
            return v
        rule=spec['rule']; recipe=decode(spec['recipe'])
        if rule=='check_graph':
            from mipframe.check.graph import replay_graph
            result=replay_graph(model, recipe, [parent(p) for p in spec.get('parents',[])], memory_mb=memory_mb,disk_mb=disk_mb)
            if spec.get('keys') is not None:
                if len(spec['keys'])!=len(result.target_ids):raise ValueError('Invalid external keys')
                from mipframe.check.keys import Keys
                from mipframe.data.runtime import WorkContext
                keys=Keys(WorkContext(memory_mb,disk_mb))
                for key in spec['keys']:keys.append(key)
                object.__setattr__(result,'_external_keys',keys)
            return result
        raise ValueError('Unsupported certificate rule')

    def __repr__(self):
        return f'<BatchProof {self.status}: {self.summary}>'


class ProofRef:
    """
    A lazy reference into a batch; it is not a separately executed proof.

    MIPFRAME HELP BEGIN
    A checked result position in a BatchProof.

    Full contract: mf.help("api.ProofRef")
    MIPFRAME HELP END
    """
    __slots__ = ('batch','index','__weakref__')
    def __init__(self,batch,index):
        if isinstance(index,bool) or not isinstance(index,(int,np.integer)) or batch not in _issued or not 0 <= index < len(batch._status):
            raise ValueError('Invalid proof reference')
        object.__setattr__(self,'batch',batch)
        object.__setattr__(self,'index',index)
    def __setattr__(self,*_): raise AttributeError('Proof references are immutable')
    @property
    def ok(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: ok.

        Full contract: mf.help("api.ProofRef.ok")
        MIPFRAME HELP END
        """
        return self.batch._status[self.index]==1
    @property
    def status(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: status.

        Full contract: mf.help("api.ProofRef.status")
        MIPFRAME HELP END
        """
        return ('INCONCLUSIVE','PROVED','REFUTED')[self.batch._status[self.index]]
    @property
    def scope(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: scope.

        Full contract: mf.help("api.ProofRef.scope")
        MIPFRAME HELP END
        """
        return dict(self.batch.scope, assumptions=len(self.batch.assumptions(self.index)),
                    domain=self.batch.domain_at(self.index))
    @property
    def conclusion(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: conclusion.

        Full contract: mf.help("api.ProofRef.conclusion")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import LinExpr
        from mipframe.linear.claim import Claim
        ts,rhs,rel=self.batch._targets.claim(self.batch._facts,self.index)
        return Claim(LinExpr(self.batch._owner,-rhs,items=lambda:iter(ts)),rel)
    def require(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: require.

        Full contract: mf.help("api.ProofRef.require")
        MIPFRAME HELP END
        """
        if not self.ok:
            from mipframe.check.report import ValidationError
            raise ValidationError(self)
        return self
    def __bool__(self): raise TypeError('Proof has no implicit truth value; use .ok')
    @property
    def method(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: method.

        Full contract: mf.help("api.ProofRef.method")
        MIPFRAME HELP END
        """
        if self.batch._rule=='check_graph':
            return self.batch._graph.tasks.rule_at(self.batch._roots[self.index])
        return self.batch._rule
    @property
    def summary(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: summary.

        Full contract: mf.help("api.ProofRef.summary")
        MIPFRAME HELP END
        """
        if self.batch._rule=='check_graph':
            graph=self.batch._graph;root=self.batch._roots[self.index]
            return dict(operation=self.method,targets=1,failures=int(not self.ok),**graph.details.get(root,{}))
        return self.batch.summary
    def to_spec(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: to_spec.

        Full contract: mf.help("api.ProofRef.to_spec")
        MIPFRAME HELP END
        """
        return dict(schema='mipframe-proof-ref-2',snapshot=self.batch._facts.uid,index=self.index,batch=self.batch.to_spec())
    def write(self,path,*,memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: write.

        Full contract: mf.help("api.ProofRef.write")
        MIPFRAME HELP END
        """
        from mipframe.check.artifact import write_bundle
        from mipframe.data.runtime import WorkContext
        with WorkContext(memory_mb,disk_mb):
            return write_bundle(dict(schema='mipframe-proof-ref-2',batch=self.batch._native_spec(),index=self.index),path)

    @property
    def dependencies(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: dependencies.

        Full contract: mf.help("api.ProofRef.dependencies")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        from mipframe.frame.schema import Ref,ref_type
        owner=self.batch._owner;records=[]
        for kind,i,side in dict.fromkeys(self.batch.dependencies(self.index)):
            axis='var' if kind=='integer' else kind
            records.append(dict(kind=kind,row=Ref(owner.uid,owner.version,'row',i) if axis=='row' else None,
                                var=Ref(owner.uid,owner.version,'var',i) if axis=='var' else None,side=side))
        return Frame.from_records(records,schema=dict(kind='str',row=ref_type(owner,'row').optional(),
                                                      var=ref_type(owner,'var').optional(),side='str'),models=(owner,))
    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: evidence.

        Full contract: mf.help("api.ProofRef.evidence")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        from mipframe.frame.schema import Ref,ref_type
        owner=self.batch._owner;records=[]
        deps=((v,side) for kind,v,side in self.batch.dependencies(self.index) if kind=='var') if self.batch._rule=='check_graph' else self.batch._bounds[self.index] if self.batch._bounds else ()
        for v,side in deps:
            value=self.batch._facts.numeric_var(v)[3 if side=='lb' else 4]
            records.append(dict(var=Ref(owner.uid,owner.version,'var',v),side=side,bound=value))
        result=dict(bounds=Frame.from_records(records,schema=dict(var=ref_type(owner,'var'),side='str',bound='number'),models=(owner,)))
        if self.batch._targets.kind!='integrality':
            ts,_,_=self.batch._targets.claim(self.batch._facts,self.index)
            result['terms']=Frame.from_records([dict(var=Ref(owner.uid,owner.version,'var',v),coef=c) for v,c in ts],
                schema=dict(var=ref_type(owner,'var'),coef='number'),models=(owner,))
        certificate=self.batch._certificate
        if certificate is not None:
            lo,hi=map(int,certificate.offsets[self.index:self.index+2])
            result['sources']=Frame.from_records(
                (dict(kind=certificate.kinds[i],source=int(certificate.sources[i]),side=certificate.sides[i],multiplier=certificate.multipliers[i]) for i in range(lo,hi)),
                schema=dict(kind='str',source='int',side='str',multiplier='number'))
        if self.batch._rule=='check_graph' and self.method in ('linear','identity','enumerate'):
            graph=self.batch._graph;root=self.batch._roots[self.index]
            sources=graph.tasks[root][2]
            def rows():
                for source in sources:
                    kind=source[0]
                    if kind=='model':yield dict(kind=source[1],source=source[2],side=source[3],multiplier=source[-1])
                    elif kind=='task':yield dict(kind='proof',source=source[1],side='ub',multiplier=source[-1])
                    elif kind=='external':yield dict(kind='proof',source=source[2],side='ub',multiplier=source[-1])
                    else:yield dict(kind='assumption',source=source[1][0],side={'<=':'ub','>=':'lb','==':'eq'}[source[1][2]],multiplier=source[-1])
            result['sources']=Frame.from_records(rows(),schema=dict(kind='str',source='int',side='str',multiplier='number'))
        if self.batch._rule=='check_graph' and self.method=='cases':
            graph=self.batch._graph;root=self.batch._roots[self.index]
            _,_,sources,_,(v,values)=graph.tasks[root]
            branches=[graph.source(source) for source in sources]
            result['cases']=Frame.from_records([dict(value=value,status='PROVED') for value in values],schema=dict(value='number',status='str'))
            discharged=[]
            for value,(_,conditions,_) in zip(values,branches):
                for expression,rhs,relation in conditions:
                    terms=graph.terms(expression)
                    if relation=='==' and len(terms)==1 and terms[0][0]==v and Fraction(rhs,terms[0][1])==value:
                        discharged.append(dict(value=value,var=Ref(owner.uid,owner.version,'var',v)))
            result['discharged']=Frame.from_records(discharged,schema=dict(value='number',var=ref_type(owner,'var')),models=(owner,))
        if self.batch._rule=='cases':
            recipe=self.batch._recipe;values=recipe['values'];v=recipe['var']
            result['cases']=Frame.from_records([dict(value=x,status=p.status) for x,p in zip(values,self.batch._parents)],schema=dict(value='number',status='str'))
            discharged=[]
            for value,p in zip(values,self.batch._parents):
                for ts,rhs,rel in p._assumptions:
                    if rel=='==' and len(ts)==1 and ts[0][0]==v and Fraction(rhs,ts[0][1])==value:
                        discharged.append(dict(value=value,var=Ref(owner.uid,owner.version,'var',v)))
            result['discharged']=Frame.from_records(discharged,schema=dict(value='number',var=ref_type(owner,'var')),models=(owner,))
        return result
    @property
    def counterexample(self):
        """
        MIPFRAME HELP BEGIN
        Read a selected checked result: counterexample.

        Full contract: mf.help("api.ProofRef.counterexample")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        from mipframe.frame.schema import Ref,ref_type
        owner=self.batch._owner
        point=self.batch._graph.counterexamples.get(self.batch._roots[self.index],{}) if self.batch._rule=='check_graph' else getattr(self.batch,'_counterexample',{})
        records=[dict(var=Ref(owner.uid,owner.version,'var',v),value=x) for v,x in point.items()]
        return Frame.from_records(records,schema=dict(var=ref_type(owner,'var'),value='number'),models=(owner,))
    def __repr__(self):
        scope = ' local premises' if self.method == 'enumerate' else ''
        if self.batch._assumptions: scope += f' assume: {len(self.batch._assumptions)}'
        return f'<ProofRef {self.status}: {int(self.batch.target_ids[self.index])}{scope}>'


class ProofSelection:
    """
    Immutable positions in a checked batch; no per-target reports.

    MIPFRAME HELP BEGIN
    Immutable selected positions in a BatchProof.

    Full contract: mf.help("api.ProofSelection")
    MIPFRAME HELP END
    """
    __slots__ = ('batch', 'positions')
    def __init__(self, batch, positions):
        if batch not in _issued: raise ValueError('Unverified batch')
        positions = frozen_ints(positions)
        if any(i < 0 or i >= len(batch._status) for i in positions):
            raise ValueError('Invalid proof selection')
        object.__setattr__(self, 'batch', batch)
        object.__setattr__(self, 'positions', positions)
    def __setattr__(self, *args): raise AttributeError('Proof selections are immutable')
    @property
    def ok(self): return all(self.batch._status[i] == 1 for i in self.positions)
    @property
    def scope(self):
        return dict(self.batch.scope, assumptions=len(set(a for i in self.positions for a in self.batch.assumptions(i))),
                    domain='integer' if any(self.batch.domain_at(i)=='integer' for i in self.positions) else 'relaxation')


def proof_parts(proof):
    if isinstance(proof, ProofRef): return proof.batch, (proof.index,)
    if isinstance(proof, ProofSelection): return proof.batch, proof.positions
    return proof, range(len(proof._status))


def trusted(proof, f):
    from mipframe.check.certificate import InvalidCertificateError
    if not isinstance(proof, (BatchProof, ProofRef, ProofSelection)):
        raise InvalidCertificateError('Expected a verified certificate of this snapshot')
    batch, positions = proof_parts(proof)
    if batch not in _issued or batch._facts is not f:
        raise InvalidCertificateError('Expected a verified certificate of this snapshot')
    if not proof.ok:
        raise InvalidCertificateError('Inconclusive proof cannot authorize an edit')
    if any(batch.assumptions(i) for i in positions):
        from mipframe.edit.plan import UndischargedAssumptionError
        raise UndischargedAssumptionError('Conditional certificates cannot authorize an edit')
    return batch


def check_linear(owner,targets,certificate,residual='exact',**options):
    from mipframe.check.compiler import execute
    return execute(owner,targets,certificate,residual=residual,**options)


@resource_scope
def linear_rows(owner,*,targets,witnesses,side='ub',residual='exact',**options):
    if hasattr(targets,'ids'): targets=targets.ids
    c=witnesses if isinstance(witnesses,CertificateBatch) else CertificateBatch.paths(witnesses,**options)
    return check_linear(owner,Targets(targets,sides=side,**options),c,residual,**options)


def check_bounds(owner,targets):
    if targets.kind != 'var':raise ValueError('Bound rule requires variable targets')
    from mipframe.check.graph import CheckBatch
    with CheckBatch(owner.check) as plan:
        handles=[]
        for v,side,value in zip(targets.ids,targets.sides,targets.rhs):
            v=int(v)
            h=plan._stage('bound',(plan.pool.terms(((v,1),)),value,{'lb':'>=','ub':'<=','eq':'=='}[side]),config=(v,side))
            handles.append((v,h._index))
        plan._many(handles,'bounds')
        return plan.run()['bounds']


def affine_integrality(owner,variables,rows):
    from mipframe.check.graph import CheckBatch
    with CheckBatch(owner.check) as plan:
        plan.affine_integrality(vars=variables,rows=rows,result_key='integrality')
        return plan.run()['integrality']


class OneHotAffine:
    """
    MIPFRAME HELP BEGIN
    Validated shared one-hot affine structure returned by Checker.one_hot_affine().

    Full contract: mf.help("api.OneHotAffine")
    MIPFRAME HELP END
    """
    def __delattr__(self,name):raise AttributeError("Checked shared structure is immutable")
    def __setattr__(self,name,value):
        if getattr(self,"_sealed",False):raise AttributeError("Checked shared structure is immutable")
        object.__setattr__(self,name,value)
    def __init__(self,owner,groups):
        self.owner,self.f=owner,facts(owner)
        self.groups=tuple((int(r),tuple((int(a),int(v)) for a,v in ds)) for r,ds in groups)
        self.data=[];self.deps=[];self.active=[]; seen=set()
        for r,definitions in self.groups:
            ts,rhs,_=self.f.side('row',r,'eq')
            options={v for v,c in ts}
            if rhs!=1 or not options or any(c!=1 for v,c in ts) or seen & options:
                raise ValueError('Expected disjoint complete one-hot equalities')
            seen.update(options); self.deps.append(('row',r,'eq'))
            if type(self.f) is Facts and self.f.numbers.integral:
                ids=np.fromiter(sorted(options),dtype='<u8');a=self.f.data.arrays
                lower=self.f.numbers.numerator[a['var_lb_id'][ids]]
                upper=self.f.numbers.numerator[a['var_ub_id'][ids]]
                if not np.all((a['var_type'][ids]==1)&(a['var_lb_finite'][ids]==1)&
                    (a['var_ub_finite'][ids]==1)&(lower>=0)&(upper<=1)&(lower<=upper)):
                    raise ValueError('One-hot options must be binary or fixed binary')
                active=ids[upper!=0].tolist()
            else:
                active=[]
                for v in sorted(options):
                    _,_,k,lo,hi,_=self.f.numeric_var(v)
                    if k!='I' or lo<0 or hi>1 or lo>hi:
                        raise ValueError('One-hot options must be binary or fixed binary')
                    if hi:active.append(v)
            self.active.append(active)
            for v in options:
                self.deps.extend((('integer',v,'kind'),('var',v,'lb'),('var',v,'ub')))
            affine=[]
            for dr,v in definitions:
                dts,b,_=self.f.side('row',dr,'eq');d=dict(dts);a=d.pop(v,0)
                if not a or v in options or not set(d)<=options:
                    raise ValueError('Affine definition contains unexplained terms')
                co={x:-c*a for x,c in d.items()} if a in (-1,1) else {x:exact(-Fraction(c,a)) for x,c in d.items()}
                constant=b*a if a in (-1,1) else exact(Fraction(b,a))
                _,_,_,lo,hi,_=self.f.numeric_var(v)
                affine.append((v,co,constant,lo,hi))
                self.deps.extend((('row',dr,'eq'),('var',v,'lb'),('var',v,'ub')))
            self.data.append((options,affine))
        from mipframe.data.columns import DependencyColumn
        self.deps=DependencyColumn(self.deps)
        self.active=tuple(tuple(x) for x in self.active)
        self.data=tuple((frozenset(options),tuple((v,MappingProxyType(co),c,lo,hi) for v,co,c,lo,hi in ds)) for options,ds in self.data)
        self._sealed=True

    def bound_exclusions(self):
        """
        MIPFRAME HELP BEGIN
        Prove that options violating a defined variable bound are zero.

        Full contract: mf.help("api.OneHotAffine.bound_exclusions")
        MIPFRAME HELP END
        """
        self.owner.model
        if hasattr(self,'_exclusions'):return self._exclusions
        excluded=[]
        for active,(options,definitions) in zip(self.active,self.data):
            for v in active:
                if any((value:=co.get(v,0)+c)<lo or value>hi for _,co,c,lo,hi in definitions):
                    excluded.append(v)
        from mipframe.check.graph import CheckBatch
        with CheckBatch(self.owner.check) as plan:
            handles=[]
            for v in excluded:
                h=plan._stage('one_hot_exclusion',(plan.pool.terms(((v,1),)),0,'<='),config=(v,self.groups))
                handles.append((v,h._index))
            plan._many(handles,'exclusions')
            result=plan.run()['exclusions']
        object.__setattr__(self,'_exclusions',result)
        return result

    def expand_exclusions(self):
        """
        Reference expansion into linear combination, bounds and rounding.

        Deliberately explicit and slower; used to cross-check the shared rule.

        MIPFRAME HELP BEGIN
        Expand exclusions into explicit linear certificates and rounding.

        Full contract: mf.help("api.OneHotAffine.expand_exclusions")
        MIPFRAME HELP END
        """
        ids=[];rhs=[];off=[0];sources=[];weights=[];sides=[];kinds=[]
        for (onehot,defs),(options,affine) in zip(self.groups,self.data):
            for v in sorted(options):
                if self.f.numeric_var(v)[4]==0:continue
                for (dr,y),(_,co,constant,lo,hi) in zip(defs,affine):
                    value=co.get(v,0)+constant
                    if lo<=value<=hi:continue
                    a=dict(self.f.terms(dr))[y]
                    if value>hi:
                        low=min(co.get(x,0) for x in options);d=co.get(v,0)-low
                        if d==0:raise ValueError('Inconsistent affine range requires a contradiction certificate')
                        bound_value=Fraction(hi-constant-low,d)
                        ws=(-Fraction(1,a*d),-Fraction(low,d),Fraction(1,d));side='ub'
                    else:
                        high=max(co.get(x,0) for x in options);d=high-co.get(v,0)
                        if d==0:raise ValueError('Inconsistent affine range requires a contradiction certificate')
                        bound_value=Fraction(high+constant-lo,d)
                        ws=(Fraction(1,a*d),Fraction(high,d),Fraction(1,d));side='lb'
                    if d==0:raise ValueError('Degenerate inconsistent affine range; use a contradiction certificate')
                    ids.append(v);rhs.append(bound_value);sources.extend((dr,onehot,y));weights.extend(ws)
                    sides.extend(('eq','eq',side));kinds.extend(('row','row','var'));off.append(len(sources));break
        p=check_linear(self.owner,Targets(ids,'var','ub',rhs),CertificateBatch(off,sources,weights,sides,kinds),'bounds')
        return p.round_many(ub=0)


def one_hot_affine(owner,groups):
    owner.model
    key=tuple((int(r),tuple((int(a),int(v)) for a,v in ds)) for r,ds in groups)
    from collections import OrderedDict
    cache=getattr(owner,'_one_hot_affine_cache',None)
    if cache is None:cache=owner._one_hot_affine_cache=OrderedDict()
    if key not in cache:
        cache[key]=OneHotAffine(owner,key)
        if len(cache)>4:cache.popitem(last=False)
    cache.move_to_end(key)
    return cache[key]
