"""Source-relative edits with explicit certificate coverage and no semantic SQL."""
from dataclasses import dataclass, field
from fractions import Fraction
from math import inf
from types import MappingProxyType
from mipframe.check.kernel import facts, exact, bound, OverlayFacts
from mipframe.check.compact import BatchProof, ProofRef, ProofSelection, proof_parts, trusted


@dataclass(frozen=True)
class Delta:
    drop_rows: frozenset = frozenset()
    drop_vars: frozenset = frozenset()
    fixed: object = field(default_factory=dict)
    var_bounds: object = field(default_factory=dict)
    row_bounds: object = field(default_factory=dict)
    kinds: object = field(default_factory=dict)
    add_rows: object = field(default_factory=dict)
    add_vars: object = field(default_factory=dict)
    objective: object = None
    sense: object = None
    operations: tuple = ()
    substitution: object = None

    def __post_init__(self):
        from mipframe.data.overrides import Overrides
        for name in ('fixed','var_bounds','row_bounds','kinds'):
            object.__setattr__(self,name,Overrides(getattr(self,name),kind='bounds' if name.endswith('bounds') else 'kind' if name=='kinds' else 'exact'))
        for name in ('add_rows','add_vars'):
            object.__setattr__(self,name,MappingProxyType(dict(getattr(self,name))))
        from mipframe.data.ids import IDSet
        object.__setattr__(self,'drop_rows',IDSet(self.drop_rows,work=getattr(self.substitution,'resource',None)))
        object.__setattr__(self,'drop_vars',IDSet(self.drop_vars,work=getattr(self.substitution,'resource',None)))


class DeltaEdit:
    """
    MIPFRAME HELP BEGIN
    Source-relative simultaneous modifications producing a new independent snapshot.

    Full contract: mf.help("api.Edit")
    MIPFRAME HELP END
    """
    def __init__(self,owner,*,memory_mb=128,disk_mb=None):
        self._owner,self._facts=owner,facts(owner)
        self.memory_mb,self.disk_mb=memory_mb,disk_mb
        from mipframe.data.runtime import WorkContext
        from mipframe.data.ids import MutableSelection
        self._work=WorkContext(memory_mb,disk_mb)
        self._drop_rows=MutableSelection(self._facts.row_capacity,self._work)
        self._drop_vars=MutableSelection(self._facts.var_capacity,self._work)
        from mipframe.data.overrides import Overrides
        self._fixed=Overrides();self._drop_trivial=True;self._substitution_options=[];self._compiled_cache=None
        from mipframe.data.overrides import StagedOverrides
        self._var_bounds=StagedOverrides('bounds',self._work);self._row_bounds=StagedOverrides('bounds',self._work)
        self._kinds=StagedOverrides('kind',self._work);self._bound_fields={'var':None,'row':None}
        self._add_rows={};self._add_vars={};self._objective=None;self._sense=None
        self._operations=[];self._sealed=False;self._closed=False
        import uuid
        from mipframe.edit.draft import EDITS
        self.uid=str(uuid.uuid4());EDITS[self.uid]=self

    def _guard(self):
        if self._sealed or self._closed: raise ValueError('Edit is sealed or closed')
        self._owner.model

    def _ids(self,selection,axis):
        self._guard()
        if hasattr(selection,'_owner') and selection._owner.uid!=self._owner.uid:
            raise ValueError('Foreign snapshot selection')
        values=selection.ids if hasattr(selection,'ids') else [selection.id] if hasattr(selection,'id') else [selection] if isinstance(selection,int) else selection
        import numpy as np
        if isinstance(values,np.ndarray) and values.dtype.kind in 'iu':
            f=self._facts;capacity=f.var_capacity if axis=='var' else f.row_capacity
            block=max(1,self._work.memory//128)
            for start in range(0,len(values),block):
                chunk=values[start:start+block]
                if np.any(chunk<0) or np.any(chunk>=capacity):raise ValueError('Invalid entity ID')
                live=getattr(f,'_'+axis+'_live',None)
                if live is not None:
                    base=chunk<len(live)
                    if not np.all(live[chunk[base]]):raise ValueError('Removed entity ID')
                    for ident in chunk[~base]:f.validate(axis,int(ident))
            from mipframe.data.ids import IDSet
            return IDSet(values,work=self._work).array
        def checked():
            for v in values:
                if hasattr(v,'uid') and (v.uid!=self._owner.uid or getattr(v,'version',self._owner.version)!=self._owner.version or getattr(v,'kind',axis)!=axis):
                    raise ValueError('Foreign reference or entity axis')
                v=v.id if hasattr(v,'id') else v
                from numbers import Integral
                if isinstance(v,bool) or not isinstance(v,Integral):raise ValueError('Expected integer IDs or entity selection')
                v=int(v);self._facts.validate(axis,v);yield v
        from mipframe.data.ids import IDSet
        return IDSet(checked(),work=self._work).array

    def _proof(self,proof):
        if proof is None: return None
        if isinstance(proof,(list,tuple)):
            if not proof:
                from mipframe.check.certificate import InvalidCertificateError
                raise InvalidCertificateError('because requires at least one proof')
            for p in proof: trusted(p,self._facts)
            return tuple(proof)
        trusted(proof,self._facts)
        return proof

    def drop_rows(self,rows,*,because=None):
        """
        MIPFRAME HELP BEGIN
        Stage deletion of source rows.

        Full contract: mf.help("api.Edit.drop_rows")
        MIPFRAME HELP END
        """
        ids=self._ids(rows,'row');because=self._proof(because)
        if any(r in self._row_bounds for r in ids): raise ValueError('Cannot drop and modify a row')
        from mipframe.data.ids import IDSet
        if self._drop_rows:ids=IDSet((r for r in ids if r not in self._drop_rows),work=self._work).array
        from mipframe.check.kernel import frozen_ints
        self._drop_rows.update(ids);self._operations.append(('drop',frozen_ints(ids),because))
        return self

    def _set_bounds(self,values,axis,lb,ub,because):
        import numpy as np
        ids=self._ids(values,axis);because=self._proof(because)
        dest=self._var_bounds if axis=='var' else self._row_bounds
        def aligned(value):
            from mipframe.data.overrides import BoundSide
            if isinstance(value,BoundSide):return value
            if not hasattr(value,'to_records'):return None
            from mipframe.data.overrides import BoundVector
            from mipframe.execution.algorithm import ArrayAlgorithmStore
            if axis not in value.schema or 'value' not in value.schema:raise ValueError('Bound value Frame needs identity and value columns')
            typ=value.schema[axis]
            if typ.kind=='ref' and (typ.uid,typ.version,typ.axis)!=(self._facts.uid,self._facts.revision,axis):raise ValueError('Foreign bound identity')
            with ArrayAlgorithmStore(value._models,work=self._work) as context:
                def records():
                    for row in value._raw((axis,'value'),context):
                        ident=int(row[axis]);self._facts.validate(axis,ident)
                        yield ident,bound(row['value'])
                vector=BoundVector(records(),self._work)
            if len(vector)!=len(ids) or any(not np.array_equal(vector.ids[start:start+4096],ids[start:start+4096]) for start in range(0,len(ids),4096)):
                raise ValueError('Bound keys must match the selection')
            return vector
        lower,upper=aligned(lb),aligned(ub)
        from mipframe.data.overrides import streamed_bounds
        fields=self._bound_fields[axis]
        def records():
            for v in ids:
                if v in (self._drop_vars if axis=='var' else self._drop_rows) or axis=='var' and v in self._fixed:raise ValueError('Cannot modify a removed entity')
                old=dest.get(v,(self._facts.var_bounds if axis=='var' else self._facts.row_bounds)(v))
                new=(old[0] if lb is None else lower[v] if lower is not None else bound(lb),old[1] if ub is None else upper[v] if upper is not None else bound(ub))
                prior=0 if fields is None else int(fields[v])
                if any(prior&(1<<i) and new[i]!=old[i] for i,val in enumerate((lb,ub)) if val is not None):raise ValueError('Conflicting bound edits')
                if new[0]>new[1]:raise ValueError('Inconsistent bounds')
                yield new
        staged=streamed_bounds(ids,records,self._work)
        if fields is None:
            fields=self._work.array(self._facts.var_capacity if axis=='var' else self._facts.row_capacity,'u1',zero=True)
            self._bound_fields[axis]=fields
        fields[ids]|=(1 if lb is not None else 0)|(2 if ub is not None else 0)
        dest.update(staged)
        self._operations.append((axis+'_bounds',ids,because))
        return self

    def set_var_bounds(self,vars,*,lb=None,ub=None,because=None):
        """
        MIPFRAME HELP BEGIN
        Change source variable bounds.

        Full contract: mf.help("api.Edit.set_var_bounds")
        MIPFRAME HELP END
        """
        return self._set_bounds(vars,'var',lb,ub,because)
    def set_row_bounds(self,rows,*,lb=None,ub=None,because=None):
        """
        MIPFRAME HELP BEGIN
        Change source row bounds without changing its expression.

        Full contract: mf.help("api.Edit.set_row_bounds")
        MIPFRAME HELP END
        """
        return self._set_bounds(rows,'row',lb,ub,because)
    def fix(self,vars,value,*,because=None):
        """
        MIPFRAME HELP BEGIN
        Set both source variable bounds to one exact value.

        Full contract: mf.help("api.Edit.fix")
        MIPFRAME HELP END
        """
        return self.set_var_bounds(vars,lb=value,ub=value,because=because)

    def substitute_fixed(self,vars=None,*,because=None,drop_trivial_rows=True):
        """
        Substitute selected finite fixed source variables and retain extension data.

        MIPFRAME HELP BEGIN
        Substitute finite fixed values and retain an exact lifting map.

        Full contract: mf.help("api.Edit.substitute_fixed")
        MIPFRAME HELP END
        """
        self._guard()
        if type(drop_trivial_rows) is not bool:raise TypeError('drop_trivial_rows must be boolean')
        if vars is None:
            def candidates():
                for v in self._facts.var_ids():
                    if v in self._drop_vars or v in self._fixed:continue
                    lo,hi=self._var_bounds.get(v,self._facts.var_bounds(v))
                    if lo==hi and lo not in (-inf,inf):yield v
            vars=candidates()
        ids=self._ids(vars,'var');because=self._proof(because)
        from mipframe.data.exact_sparse import ExactSparse
        from mipframe.data.overrides import overrides_from_storage
        from mipframe.data.ids import IDSet
        # Validate the complete selection and both declaration orders before mutation.
        selected=IDSet(ids)
        for v in ids:
            if v in self._drop_vars:raise ValueError('Cannot substitute a removed variable')
            lo,hi=self._var_bounds.get(v,self._facts.var_bounds(v))
            if lo!=hi or lo in (-inf,inf):raise ValueError('Substitution requires a finite fixed value')
            if self._kinds.get(v,self._facts.var_kind(v))=='I' and lo.denominator!=1:
                raise ValueError('Noninteger fixed integer variable')
        if any(v in selected for _,_,_,terms in self._add_rows.values() for v,c in terms):
            raise ValueError('New row references substituted variable; use an intermediate snapshot')
        new_ids=IDSet((v for v in ids if v not in self._fixed),work=self._work).array
        if not len(new_ids):return self
        storage=ExactSparse(work=self._work)
        def records():
            yield from self._fixed.items()
            for v in new_ids:yield int(v),self._var_bounds.get(v,self._facts.var_bounds(v))[0]
        try:storage.build(records(),duplicates=True)
        except BaseException:storage.close();raise
        self._fixed=overrides_from_storage(storage)
        self._drop_trivial=drop_trivial_rows
        self._substitution_options.append((new_ids,drop_trivial_rows))
        self._operations.append(('substitute',new_ids,because))
        self._compiled_cache=None
        return self

    def _compiled(self):
        signature=(len(self._operations),id(self._objective),len(self._add_rows))
        if self._compiled_cache is None or self._compiled_cache[0]!=signature:
            from mipframe.edit.substitution import compile_fixed
            result=compile_fixed(self._facts,self._fixed,self._drop_rows,self._row_bounds,
                                 self._objective,(self._drop_trivial if all(flag==self._drop_trivial for _,flag in self._substitution_options) else tuple(self._substitution_options)),self._work)
            self._compiled_cache=(signature,result)
        return self._compiled_cache[1]

    def drop_vars(self,vars):
        """
        MIPFRAME HELP BEGIN
        Remove only finally unreferenced variables.

        Full contract: mf.help("api.Edit.drop_vars")
        MIPFRAME HELP END
        """
        ids=self._ids(vars,'var')
        for v in ids:
            if self._facts.numeric_var(v)[5]:raise ValueError('Variable has nonzero objective; use substitute_fixed')
        selected=set(ids)
        for r in self._facts.row_ids():
            if r not in self._drop_rows and any(v in selected for v,c in self._facts.terms(r)):
                raise ValueError('Variable is not isolated; use substitute_fixed')
        self._drop_vars.update(ids);self._operations.append(('drop_vars',ids,None))
        return self

    def set_domains(self,vars,*,kind,because=None):
        """
        MIPFRAME HELP BEGIN
        Set the canonical variable domain.

        Full contract: mf.help("api.Edit.set_domains")
        MIPFRAME HELP END
        """
        ids=self._ids(vars,'var');because=self._proof(because)
        if kind not in ('I','C'):raise ValueError('Domain must be I or C')
        if any(v in self._fixed or v in self._drop_vars for v in ids):raise ValueError('Cannot modify a removed variable')
        if any(v in self._kinds and self._kinds[v]!=kind for v in ids):raise ValueError('Conflicting domains')
        from mipframe.data.overrides import overrides_from_columns
        from mipframe.data.columns import EnumColumn
        self._kinds.update(overrides_from_columns(ids,(EnumColumn(kind,('C','I'),len(ids)),),kind='kind'))
        self._operations.append(('domain',ids,because))
        return self

    def _name(self,name,axis,ident):
        if name is None:return f'_{axis}_{ident}'
        if not isinstance(name,str) or not name:raise ValueError('Expected a nonempty name')
        added=self._add_rows if axis=='row' else self._add_vars
        original=self._owner.model.row_names if axis=='row' else self._owner.model.var_names
        if (name in original and name not in getattr(self,'_replacement_names',())) or any(r[0]==name for r in added.values()):raise ValueError('Duplicate entity name')
        return name

    def _expr(self,expr):
        from mipframe.edit.draft import DraftVar,DraftExpr
        from mipframe.linear.expr import coerce
        if isinstance(expr,DraftVar):expr=expr.expr
        if isinstance(expr,DraftExpr):
            if expr._edit is not self:raise ValueError('Foreign draft expression')
            return tuple((v,exact(c)) for (_,v),c in expr._items()),exact(expr.constant)
        expr=coerce(self._owner,expr)
        if any(m.uid!=self._owner.uid for m in expr._models):raise ValueError('Foreign expression')
        return tuple((v,exact(c)) for v,c in expr._items()),exact(expr.constant)

    def add_vars(self,specs):
        """
        MIPFRAME HELP BEGIN
        Stage new variables and return Edit-local handles.

        Full contract: mf.help("api.Edit.add_vars")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        from mipframe.edit.draft import DraftRef,draft_type
        self._guard();staged={};records=[];labels=set();names=set()
        for rec in specs.to_records() if hasattr(specs,'to_records') else specs:
            label=rec['label'];kind=rec.get('kind','C')
            if label in labels or kind not in ('C','I'):raise ValueError('Invalid variable spec')
            labels.add(label);v=self._facts.var_capacity+len(self._add_vars)+len(staged)
            name=self._name(rec.get('name'),'var',v)
            if name in names:raise ValueError('Duplicate variable name')
            names.add(name);lo=bound(rec.get('lb',0));hi=bound(rec.get('ub',inf))
            if lo>hi:raise ValueError('Inconsistent variable bounds')
            staged[v]=(name,kind,lo,hi,exact(rec.get('obj',0)))
            records.append(dict(label=label,var=DraftRef(self.uid,'var',v)))
        result=Frame.from_records(records,schema=dict(label='str',var=draft_type(self)),key=('label',))
        self._add_vars.update(staged)
        return result

    def add_row(self,expr,*,name=None,lb=None,ub=None,because=None):
        """
        MIPFRAME HELP BEGIN
        Stage a new row with a new identity.

        Full contract: mf.help("api.Edit.add_row")
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import Claim
        from mipframe.edit.draft import DraftClaim,DraftRef
        self._guard();because=self._proof(because)
        if isinstance(expr,(Claim,DraftClaim)):
            if lb is not None or ub is not None:raise ValueError('Claim already specifies bounds')
            relation=expr.relation;terms,constant=self._expr(expr.expr)
            lo=-constant if relation in ('>=','==') else -inf
            hi=-constant if relation in ('<=','==') else inf
        else:
            terms,constant=self._expr(expr)
            lo=-inf if lb is None else bound(lb)-constant
            hi=inf if ub is None else bound(ub)-constant
        if lo>hi:raise ValueError('Inconsistent row bounds')
        if any(v in self._drop_vars or v in self._fixed for v,c in terms):raise ValueError('New row references eliminated variable; use an intermediate snapshot')
        ident=self._facts.row_capacity+len(self._add_rows)
        self._add_rows[ident]=(self._name(name,'row',ident),lo,hi,terms)
        self._operations.append(('add',(ident,),because))
        return DraftRef(self.uid,'row',ident)

    def add_rows(self,*,rows,terms,because=None):
        """
        MIPFRAME HELP BEGIN
        Stage a complete batch of new rows atomically.

        Full contract: mf.help("api.Edit.add_rows")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        from mipframe.edit.draft import DraftRef,draft_type
        self._guard();because=self._proof(because)
        specs=list(rows.to_records() if hasattr(rows,'to_records') else rows)
        index={r['label']:i for i,r in enumerate(specs)}
        if len(index)!=len(specs):raise ValueError('Duplicate row label')
        vectors=[{} for _ in specs]
        for rec in terms.to_records() if hasattr(terms,'to_records') else terms:
            d=vectors[index[rec['label']]];v=rec.get('var');dv=rec.get('draft_var')
            if (v is None)==(dv is None):raise ValueError('Expected exactly one variable endpoint')
            if v is not None:
                v=self._ids([v],'var')[0]
            else:
                if hasattr(dv,'ref'):dv=dv.ref
                if dv.edit_uid!=self.uid or dv.id not in self._add_vars:raise ValueError('Foreign draft variable')
                v=dv.id
            if v in self._drop_vars or v in self._fixed:raise ValueError('Removed variable in new row')
            d[v]=d.get(v,0)+exact(rec['coef'])
        staged={};records=[];names=set()
        for r,d in zip(specs,vectors):
            ident=self._facts.row_capacity+len(self._add_rows)+len(staged)
            name=self._name(r.get('name'),'row',ident)
            if name in names:raise ValueError('Duplicate row name')
            names.add(name);lo=bound(r.get('lb',-inf));hi=bound(r.get('ub',inf))
            if lo>hi:raise ValueError('Inconsistent row bounds')
            staged[ident]=(name,lo,hi,tuple(sorted((v,c) for v,c in d.items() if c)))
            records.append(dict(label=r['label'],row=DraftRef(self.uid,'row',ident)))
        result=Frame.from_records(records,schema=dict(label='str',row=draft_type(self,'row')),key=('label',))
        self._add_rows.update(staged);self._operations.append(('add',tuple(staged),because))
        return result

    def replace_rows(self,old,*,rows,terms,correspondence,because=None):
        # Explicit correspondence is data, never an inferred equivalence proof.
        """
        MIPFRAME HELP BEGIN
        Stage explicit row replacement relationships.

        Full contract: mf.help("api.Edit.replace_rows")
        MIPFRAME HELP END
        """
        ids=self._ids(old,'row');proofs=because or {}
        specs=list(rows.to_records() if hasattr(rows,'to_records') else rows)
        corr=list(correspondence.to_records() if hasattr(correspondence,'to_records') else correspondence)
        if {self._ids([r['old_row']],'row')[0] for r in corr}!=set(ids) or {r['new_label'] for r in corr}!={r['label'] for r in specs}:
            raise ValueError('Correspondence must cover both selections')
        self._proof(proofs.get('drop'));self._proof(proofs.get('add'))
        if any(r in self._row_bounds for r in ids):raise ValueError('Cannot replace a modified row')
        self._replacement_names={self._facts.row(i)[1] for i in ids}
        try:result=self.add_rows(rows=specs,terms=terms,because=proofs.get('add'))
        finally:del self._replacement_names
        self.drop_rows(ids,because=proofs.get('drop'))
        return result

    def set_objective(self,expr,*,sense='min'):
        """
        MIPFRAME HELP BEGIN
        Replace the entire objective independent of staging order.

        Full contract: mf.help("api.Edit.set_objective")
        MIPFRAME HELP END
        """
        self._guard()
        if sense not in ('min','max'):raise ValueError('Invalid objective sense')
        terms,constant=self._expr(expr)
        self._objective=(dict(terms),constant);self._sense=sense
        return self

    def _delta(self):
        from mipframe.data.ids import IDSet
        from itertools import chain
        compiled=self._compiled()
        dropped=IDSet(chain(self._drop_rows,compiled.trivial_rows) if compiled.trivial_rows else self._drop_rows,work=self._work)
        return Delta(dropped,self._drop_vars,self._fixed,self._var_bounds,
                     self._row_bounds,self._kinds,self._add_rows,self._add_vars,
                     self._objective,self._sense,tuple(self._operations),compiled)

    def _semantics(self):
        signature=(len(self._operations),len(self._add_vars),len(self._add_rows),id(self._objective),self._sense)
        cached=getattr(self,'_semantic_cache',None)
        if cached is not None and cached[0]==signature:
            return dict(cached[1],objective=dict(cached[1]['objective']))
        f=self._facts
        good=True; lp_forward=True; lp_reverse=True
        reverse={}; maps={}; affine_maps={}

        def pool(proof):
            return () if proof is None else proof if isinstance(proof,tuple) else (proof,)

        def normalized(terms, side, rhs):
            d={}
            for v,c in terms: d[v]=d.get(v,0)+c
            terms=tuple(sorted((v,c) for v,c in d.items() if c))
            scale=abs(terms[0][1]) if terms else 1
            sign=-1 if side=='lb' else 1
            return tuple((v,Fraction(sign*c,scale)) for v,c in terms),Fraction(sign*rhs,scale)

        def covers(proofs,ident,kind,side=None,value=None,terms=None):
            # Existing entity targets never need expression construction or scans.
            for p in pool(proofs):
                batch,positions=proof_parts(p); t=batch._targets
                if p not in maps:
                    maps[p]={int(t.ids[i]):int(i) for i in positions}
                i=maps[p].get(ident)
                if kind==t.kind and i is not None and (side is None or t.sides[i] in (side,'eq')):
                    proved=(f.row_bounds(ident)[0 if side=='lb' else 1] if t.kind=='row' else t.rhs[i]) if side is not None else None
                    if value is None or (proved<=value if side=='ub' else proved>=value):
                        return batch,i
                if kind=='integrality': continue
                if p not in affine_maps:
                    index={}
                    for j in positions:
                        if t.kind=='integrality':continue
                        ts,rhs,rel=t.claim(f,int(j))
                        for sd in (('lb','ub') if rel=='==' else ('ub',) if rel=='<=' else ('lb',)):
                            key,b=normalized(ts,sd,rhs)
                            index.setdefault(key,[]).append((b,int(j)))
                    affine_maps[p]=index
                if terms is None:
                    ts,rhs,_=f.side(kind,ident,side)
                    if value is not None:rhs=value
                else:ts,rhs=terms,value
                key,b=normalized(ts,side,rhs)
                for rhs,j in affine_maps[p].get(key,()):
                    if rhs<=b:return batch,j
            return None

        def require(proofs,ident,kind,side=None,value=None,terms=None,backward=False):
            nonlocal good
            hit=covers(proofs,ident,kind,side,value,terms)
            if hit is None: good=False;return False
            batch,i=hit
            if backward:reverse.setdefault(batch,set()).add(i)
            return batch._domain=='relaxation'

        bound_proofs={}
        for kind,ids,p in self._operations:
            if kind in ('var_bounds','row_bounds'):
                for v in ids:bound_proofs.setdefault((kind,v),[]).extend(pool(p))
        # Supplemental fixedness evidence covers the staged bound obligations.
        for kind,ids,p in self._operations:
            if kind=='substitute' and p is not None:
                for v in ids:bound_proofs.setdefault(('var_bounds',v),[]).extend(pool(p))
        visited_bounds=set()
        for kind,ids,p in self._operations:
            if kind=='drop':
                for r in ids:
                    for side,value in zip(('lb','ub'),f.row_bounds(r)):
                        if value not in (-inf,inf):lp_reverse &= require(p,r,'row',side,backward=True)
            elif kind in ('var_bounds','row_bounds'):
                axis=kind.split('_')[0]
                bounds=self._var_bounds if axis=='var' else self._row_bounds
                for v in ids:
                    if (kind,v) in visited_bounds:continue
                    visited_bounds.add((kind,v));p=tuple(bound_proofs[kind,v])
                    old=f.var_bounds(v) if axis=='var' else f.row(v)[2:4]
                    for side,a,z in zip(('lb','ub'),old,bounds[v]):
                        if a==z:continue
                        tightening=z>a if side=='lb' else z<a
                        if tightening:
                            ts=((v,1),) if axis=='var' else f.terms(v)
                            lp_forward &= require(p,v,axis,side,z,ts)
                        else:
                            # Evidence must establish the original bound from final roots.
                            ts=((v,1),) if axis=='var' else f.terms(v)
                            lp_reverse &= require(p,v,axis,side,a,ts,backward=True)
            elif kind=='domain':
                for v in ids:
                    if self._kinds[v]==f.var_kind(v):continue
                    if self._kinds[v]!='C':good=False
                    else:require(p,v,'integrality',backward=True)
            elif kind=='substitute':
                for v in ids:
                    old=f.var_bounds(v);new=self._fixed[v]
                    good &= old==(new,new) or v in self._var_bounds
            elif kind=='drop_vars':
                for v in ids:
                    lo,hi=f.var_bounds(v)
                    good &= lo<=hi and (f.var_kind(v)!='I' or lo==-inf or hi==inf or ceil_safe(lo)<=floor_safe(hi))
            elif kind=='add':
                for r in ids:
                    _,lo,hi,ts=self._add_rows[r]
                    for side,value in zip(('lb','ub'),(lo,hi)):
                        if value not in (-inf,inf):lp_forward &= require(p,r,'row',side,value,ts)
            else: good=False
        if self._add_vars or self._objective is not None or self._sense not in (None,f.sense):good=False

        def root(dep):
            kind,i,side=dep
            if kind=='row':
                if i in self._drop_rows:return False
                old=f.row_bounds(i);new=self._row_bounds.get(i,old)
            else:
                if i in self._fixed:
                    c=self._fixed[i];v=f.numeric_var(i)
                    return Fraction(c).denominator==1 if kind=='integer' else v[3]<=c<=v[4]
                if i in self._drop_vars:return False
                if kind=='integer':return self._kinds.get(i,f.var_kind(i))=='I'
                old=f.var_bounds(i);new=self._var_bounds.get(i,old)
            return new[0]>=old[0] if side=='lb' else new[1]<=old[1] if side=='ub' else new==old

        # The common retained-witness case traverses the flat source arrays once.
        pending=[];proved=set()
        for batch,positions in reverse.items():
            if len(positions)==len(batch._status) and all(root(d) for d in set(batch.dependencies())):
                t=batch._targets;axis='integer' if t.kind=='integrality' else t.kind
                for i in positions:
                    side='kind' if axis=='integer' else t.sides[i];v=int(t.ids[i])
                    proved.add((axis,v,side))
                    if side=='eq':proved.update(((axis,v,'lb'),(axis,v,'ub')))
                continue
            pending.extend((batch,i) for i in positions)
        # Resolve each dependency once; reverse edges wake only affected targets.
        from collections import defaultdict,deque
        waiting=defaultdict(list);counts=[];conclusions=[];queue=deque()
        for node,(batch,i) in enumerate(pending):
            t=batch._targets;axis='integer' if t.kind=='integrality' else t.kind
            side='kind' if axis=='integer' else t.sides[i];v=int(t.ids[i])
            out=((axis,v,side),)
            if side=='eq':out+=((axis,v,'lb'),(axis,v,'ub'))
            conclusions.append(out)
            missing={d for d in batch.dependencies(i) if d not in proved and not root(d)}
            counts.append(len(missing))
            if not missing:queue.append(node)
            for d in missing:waiting[d].append(node)
        resolved=0
        while queue:
            node=queue.popleft();resolved+=1
            for conclusion in conclusions[node]:
                if conclusion in proved:continue
                proved.add(conclusion)
                for dependent in waiting.pop(conclusion,()):
                    counts[dependent]-=1
                    if counts[dependent]==0:queue.append(dependent)
        if resolved!=len(pending):good=False
        coord='PROJECTED' if self._fixed or self._drop_vars else 'EXTENDED' if self._add_vars else 'SAME'
        lp='SAME' if lp_forward and lp_reverse else 'SUBSET' if lp_reverse else 'SUPERSET' if lp_forward else 'UNKNOWN'
        result = dict(coordinates=coord,lp=lp if good else 'UNKNOWN',
                    mip=('SAME' if coord=='SAME' else 'EQUIVALENT_UNDER_EXTENSION') if good else 'UNKNOWN',
                    objective=dict(kind='SAME' if self._objective is None and self._sense in (None,f.sense) else 'CHANGED',scale=1,offset=0),
                    optimal_value='PRESERVED' if good else 'UNKNOWN')
        if self._fixed:
            if result['lp']!='UNKNOWN':
                result['lp']='EQUIVALENT_UNDER_EXTENSION' if result['lp']=='SAME' else result['lp']+'_UNDER_EXTENSION'
            if result['objective']['kind']=='SAME':
                result['objective'].update(kind='SAME_UP_TO_OFFSET',constant_delta=self._compiled().objective_constant_delta)
        self._semantic_cache=(signature,result)
        return dict(result,objective=dict(result['objective']))

    def summary(self):
        """
        MIPFRAME HELP BEGIN
        Read edit counts and certificate-derived semantics without global analysis.

        Full contract: mf.help("api.Edit.summary")
        MIPFRAME HELP END
        """
        self._guard()
        return dict(rows_dropped=len(self._drop_rows),vars_eliminated=len(self._fixed),
                    bounds_changed=len(self._var_bounds)+len(self._row_bounds),
                    domains_changed=len(self._kinds),semantic=self._semantics(),
                    trivial_rows_dropped=len(self._compiled().trivial_rows),
                    status='INFEASIBLE' if self._compiled().infeasible_rows else 'UNDETERMINED')

    def build(self,*,allow_infeasible=False,prepare='logical'):
        """
        MIPFRAME HELP BEGIN
        Build a cheap overlay, including experimental unproved changes.

        Full contract: mf.help("api.Edit.build")
        MIPFRAME HELP END
        """
        self._guard()
        if prepare not in ('logical','indexed'):raise ValueError('Invalid preparation mode')
        semantic=self._semantics();delta=self._delta()
        if delta.substitution.infeasible_rows and not allow_infeasible:
            from mipframe.edit.substitution import SubstitutionInfeasibleError
            raise SubstitutionInfeasibleError(delta.substitution,self._facts,self._row_bounds)
        from mipframe.model import Model
        result=Model._from_facts(OverlayFacts(self._facts,delta),semantic)
        try:
            if prepare=='indexed':result.indexed()
        except BaseException:
            result.close();raise
        result._exact_facts.edit_uid=self.uid
        self._sealed=True
        self._built=result
        return result

    def commit(self):
        """
        MIPFRAME HELP BEGIN
        Require grounded MIP equivalence and objective-value preservation before building an overlay.

        Full contract: mf.help("api.Edit.commit")
        MIPFRAME HELP END
        """
        self._guard();semantic=self._semantics()
        if semantic['mip'] not in ('SAME','EQUIVALENT_UNDER_EXTENSION') or semantic['optimal_value']!='PRESERVED':
            raise ValueError('Commit requires grounded MIP-equivalence and objective-preservation certificates')
        return self.build()

    def patch_plan(self, *, format='mps'):
        """
        MIPFRAME HELP BEGIN
        Plan source patch support without sealing an edit or replaying proofs.

        Full contract: mf.help("api.Edit.patch_plan")
        MIPFRAME HELP END
        """
        from mipframe.store.patch import patch_plan
        self._guard()
        return patch_plan(OverlayFacts(self._facts,self._delta()),format=format,memory_mb=self.memory_mb,disk_mb=self.disk_mb)

    def write(self,path,*,format=None,strategy='auto',audit='readback',**options):
        """
        MIPFRAME HELP BEGIN
        Validate the final edit, write and audit atomically, then seal on success.

        Full contract: mf.help("api.Edit.write")
        MIPFRAME HELP END
        """
        self._guard();semantic=self._semantics()
        if semantic['mip'] not in ('SAME','EQUIVALENT_UNDER_EXTENSION') or semantic['optimal_value']!='PRESERVED':
            raise ValueError('Write requires grounded MIP-equivalence and objective-preservation certificates')
        delta=self._delta()
        if delta.substitution.infeasible_rows:
            from mipframe.edit.substitution import SubstitutionInfeasibleError
            raise SubstitutionInfeasibleError(delta.substitution,self._facts,self._row_bounds)
        from mipframe.model import Model
        child=Model._from_facts(OverlayFacts(self._facts,delta),semantic)
        try:
            receipt=child.write(path,format=format,strategy=strategy,audit=audit,
                                memory_mb=options.pop('memory_mb',self.memory_mb),
                                disk_mb=options.pop('disk_mb',self.disk_mb),**options)
        except BaseException:child.close();raise
        child._exact_facts.edit_uid=self.uid
        self._built=child;self._sealed=True
        return receipt

    def lift(self,solution,*,ancestor=None):
        """
        MIPFRAME HELP BEGIN
        Restore fixed variables to the direct parent or a requested ancestor.

        Full contract: mf.help("api.Edit.lift")
        MIPFRAME HELP END
        """
        if not self._sealed:raise ValueError('Build or write the Edit before lifting')
        return self._built.lift(solution,ancestor=ancestor)

    def close(self):
        """
        MIPFRAME HELP BEGIN
        Release the Edit and live draft resources.

        Full contract: mf.help("api.Edit.close")
        MIPFRAME HELP END
        """
        self._closed=True
        self._work.close()
    def __del__(self):
        if hasattr(self,"_work"):self._work.close()
    def __enter__(self):self._guard();return self
    def __exit__(self,*_):self.close()


def ceil_safe(x):
    from math import ceil
    return ceil(x)
def floor_safe(x):
    from math import floor
    return floor(x)


class DeltaDiff:
    """
    MIPFRAME HELP BEGIN
    Parent-child comparison with actual identity maps and verified semantic directions.

    Full contract: mf.help("api.ModelDiff")
    MIPFRAME HELP END
    """
    def __init__(self,target,source):
        self.target,self.source=target,source
        f=facts(target);s=facts(source);chain=[]
        while f is not s and f.parent is not None:
            chain.append(f.delta);f=f.parent
        if f is not s:raise ValueError('Snapshots have no common direct lineage')
        self.deltas=tuple(reversed(chain))
        self._semantic=dict(getattr(target,'_delta_semantic',dict(coordinates='SAME',lp='SAME',mip='SAME',objective=dict(kind='SAME',scale=1,offset=0),optimal_value='PRESERVED')))
        if any(d.fixed or d.drop_vars for d in chain):
            self._semantic['coordinates']='PROJECTED'
            if self._semantic['mip']=='SAME':self._semantic['mip']='EQUIVALENT_UNDER_EXTENSION'
        if len(chain)>1:
            forward=backward=mip=True;objective=True;offset=0
            cur=facts(target)
            while cur is not s:
                sem=getattr(cur,'semantic',{})
                mip &= sem.get('mip') in ('SAME','EQUIVALENT_UNDER_EXTENSION')
                relation=sem.get('lp','UNKNOWN').replace('_UNDER_EXTENSION','')
                if relation=='EQUIVALENT':relation='SAME'
                forward &= relation in ('SAME','SUBSET')
                backward &= relation in ('SAME','SUPERSET')
                objective &= sem.get('objective',{}).get('kind') in ('SAME','SAME_UP_TO_OFFSET')
                offset += sem.get('objective',{}).get('constant_delta',0)
                cur=cur.parent
            projected=self._semantic['coordinates']=='PROJECTED'
            relation='SAME' if forward and backward else 'SUBSET' if forward else 'SUPERSET' if backward else 'UNKNOWN'
            self._semantic['lp']=('EQUIVALENT_UNDER_EXTENSION' if relation=='SAME' else relation+'_UNDER_EXTENSION' if relation!='UNKNOWN' else relation) if projected else relation
            self._semantic['mip']=('EQUIVALENT_UNDER_EXTENSION' if projected else 'SAME') if mip else 'UNKNOWN'
            self._semantic['objective']=dict(kind='SAME_UP_TO_OFFSET' if projected else 'SAME',scale=1,offset=0,constant_delta=offset) if objective else dict(kind='UNKNOWN')
            self._semantic['optimal_value']='PRESERVED' if mip and objective else 'UNKNOWN'
        from mipframe.edit.lift import ExtensionMap
        self.lifting=ExtensionMap(d.fixed for d in self.deltas)
    @property
    def semantic(self):
        """
        MIPFRAME HELP BEGIN
        Read declared semantic scope; saved lineage summaries are historical, not live proofs.

        Full contract: mf.help("api.ModelDiff.semantic")
        MIPFRAME HELP END
        """
        return dict(self._semantic)
    @property
    def objective_changed(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff objective_changed.

        Full contract: mf.help("api.ModelDiff.objective_changed")
        MIPFRAME HELP END
        """
        return self._semantic['objective']['kind'] not in ('SAME','SAME_UP_TO_OFFSET')
    @property
    def row_nnz_stats(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff row_nnz_stats.

        Full contract: mf.help("api.ModelDiff.row_nnz_stats")
        MIPFRAME HELP END
        """
        return self._nnz_stats('row')
    @property
    def col_nnz_stats(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff col_nnz_stats.

        Full contract: mf.help("api.ModelDiff.col_nnz_stats")
        MIPFRAME HELP END
        """
        return self._nnz_stats('var')
    def _nnz_stats(self,axis):
        from math import ceil
        def summarize(model):
            f=facts(model)
            sizes=sorted(len(f.terms(r)) for r in f.row_ids()) if axis=='row' else sorted(sum(1 for _ in f.column(v)) for v in f.var_ids())
            n=len(sizes)
            return dict(max=sizes[-1] if n else 0,median=(Fraction(sizes[(n-1)//2]+sizes[n//2],2) if n else 0),p90=sizes[max(0,ceil(n*.9)-1)] if n else 0)
        return dict(before=summarize(self.source),after=summarize(self.target))
    @property
    def rows_added(self):return tuple(sorted({r for d in self.deltas for r in d.add_rows}-{r for d in self.deltas for r in d.drop_rows}))
    @property
    def vars_added(self):return tuple(sorted({v for d in self.deltas for v in d.add_vars}-{v for d in self.deltas for v in (*d.drop_vars,*d.fixed)}))
    def _changes(self,field,axis):
        changed={v for d in self.deltas for v in getattr(d,field)}
        if field=='row_bounds':
            changed.update(v for d in self.deltas if d.substitution is not None for v in d.substitution.shifts)
        removed=set(self.rows_dropped if axis=='row' else self.vars_dropped)
        added=set(self.rows_added if axis=='row' else self.vars_added)
        before,after=facts(self.source),facts(self.target)
        getter={'row_bounds':'row_bounds','var_bounds':'var_bounds','kinds':'var_kind'}[field]
        result={}
        for v in changed-removed-added:
            a,b=getattr(before,getter)(v),getattr(after,getter)(v)
            if a!=b:result[v]=(a,b)
        return result
    @property
    def row_bounds_changed(self):return self._changes('row_bounds','row')
    @property
    def var_bounds_changed(self):return self._changes('var_bounds','var')
    @property
    def domains_changed(self):return self._changes('kinds','var')
    @property
    def rows_dropped(self):return tuple(sorted({r for d in self.deltas for r in d.drop_rows}-{r for d in self.deltas for r in d.add_rows}))
    @property
    def vars_dropped(self):return tuple(sorted({v for d in self.deltas for v in (*d.fixed,*d.drop_vars)}-{v for d in self.deltas for v in d.add_vars}))
    @property
    def nnz_before(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff nnz_before.

        Full contract: mf.help("api.ModelDiff.nnz_before")
        MIPFRAME HELP END
        """
        return self.source.nnz
    @property
    def nnz_after(self):
        """
        MIPFRAME HELP BEGIN
        Read ModelDiff nnz_after.

        Full contract: mf.help("api.ModelDiff.nnz_after")
        MIPFRAME HELP END
        """
        return self.target.nnz
    def __repr__(self):return f'<DeltaDiff: {self.semantic}>'
