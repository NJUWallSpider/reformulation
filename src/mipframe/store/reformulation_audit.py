"""Explicit verification of saved proof-carrying deltas against a bound source.

Saved verdicts never enter the trust path. Every certificate is re-executed and
every delta is committed again before numerical comparison with the artifact.
"""
from copy import copy
from fractions import Fraction
from pathlib import Path
import hashlib
import json
from mipframe.check.kernel import facts,exact,bound,OverlayFacts
from mipframe.check.compact import BatchProof,ProofRef,ProofSelection
from mipframe.edit.delta import Delta,DeltaEdit


def fingerprint(f):
    if f.parent is None and getattr(f,'_canonical_fingerprint',None):
        return 'canonical-v2:'+f._canonical_fingerprint
    h=hashlib.sha256()
    def add(value):h.update(repr(value).encode());h.update(b'\n')
    add((f.sense,f.constant))
    for v in f.var_ids():add(f.var(v))
    for r in f.row_ids():
        add(f.row(r))
        for v,c in f.terms(r):add((v,str(c)))
        add("end-row")
    return h.hexdigest()


def verify_bundle(bundle,source,*,target=None,artifact=None):
    if isinstance(bundle,(str,Path)):bundle=json.loads(Path(bundle).read_text())
    from mipframe.control.proofs import encode
    # Native arrays remain typed through replay. JSON bundles are decoded on demand.
    from mipframe.data.overrides import Overrides
    def at(values,i):return values[int(i)] if isinstance(values,Overrides) else values[str(i)]
    sf=facts(source)
    if bundle.get('schema')!='mipframe-reformulation-1' or bundle.get('root_fingerprint')!=fingerprint(sf):
        raise ValueError('The bound source differs from the saved numerical root')
    # Rebinding is allowed only after full numerical comparison, not uid alone.
    root=copy(sf);root.uid=bundle['root'];root.revision=bundle['root_revision']
    current=type(source)._from_facts(root,dict(coordinates='SAME',lp='SAME',mip='SAME',optimal_value='PRESERVED',objective=dict(kind='SAME')))
    owned=[current]
    try:
        for step in bundle['steps']:
            if step['parent']!=facts(current).uid:raise ValueError('Broken snapshot chain')
            proofs=[BatchProof.replay(spec,model=current) for spec in step['proofs']]
            def binding(spec):
                if spec is None:return None
                if isinstance(spec,list):return tuple(binding(x) for x in spec)
                b=proofs[spec['proof']]
                if spec.get('positions') is not None:return ProofSelection(b,spec['positions'])
                return ProofRef(b,spec['index']) if spec.get('index') is not None else b
            d=step['delta'];e=DeltaEdit(current)
            # Re-run public staging validation and require exhaustive operation coverage.
            for kind,ids,p in step['bindings']:
                proof=binding(p)
                if kind=='drop':e.drop_rows(ids,because=proof)
                elif kind in ('var_bounds','row_bounds'):
                    from mipframe.data.overrides import BoundSide
                    e._set_bounds(ids,kind.split('_')[0],BoundSide(d[kind],0),BoundSide(d[kind],1),proof)
                elif kind=='domain':
                    from mipframe.data.ids import IDSet
                    for domain in ('C','I'):
                        selected=IDSet((i for i in ids if at(d['kinds'],i)==domain),work=e._work)
                        if selected:e.set_domains(selected.array,kind=domain,because=proof)
                elif kind in ('eliminate','substitute'):
                    import numpy as np
                    compiled=d.get('compiled') or {};policy=compiled.get('drop_policy')
                    drop=(policy if isinstance(policy,bool) else
                          next((flag for chosen,flag in (policy or ()) if np.array_equal(chosen,ids)),len(compiled.get('trivial_rows',()))>0))
                    e.substitute_fixed(ids,because=proof,drop_trivial_rows=drop)
                elif kind=='drop_vars':e.drop_vars(ids)
                elif kind=='add':
                    from mipframe.linear.expr import LinExpr
                    for i in ids:
                        name,lo,hi,ts=d['add_rows'][str(i)]
                        terms=tuple((v,exact(c)) for v,c in ts)
                        ref=e.add_row(LinExpr(current,items=lambda ts=terms:iter(ts)),name=name,lb=bound(lo),ub=bound(hi),because=proof)
                        if ref.id!=i:raise ValueError('Noncanonical added row identity')
                else:raise ValueError('Unsupported committed delta operation')
            from mipframe.control.proofs import delta_spec
            expected=delta_spec(e._delta(),native=isinstance(d['fixed'],Overrides))
            def equal(a,b):
                import numpy as np
                from collections.abc import Mapping
                if isinstance(a,Mapping) and isinstance(b,Mapping):
                    return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
                if isinstance(a,np.ndarray) or isinstance(b,np.ndarray):return np.array_equal(a,b)
                if isinstance(a,(tuple,list)) and isinstance(b,(tuple,list)):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
                return a==b
            if 'compiled' not in d:expected.pop('compiled',None)
            elif 'drop_policy' not in (d['compiled'] or {}):expected['compiled'].pop('drop_policy',None)
            if not equal(expected,d):raise ValueError('Uncovered or modified delta data')
            child=e.commit();owned.append(child)
            cf=facts(child);cf.uid=step['uid'];cf.revision=step['revision']
            child._store.manifest.update(uid=cf.uid,model_version=cf.revision)
            current=child
        if target is not None and fingerprint(facts(target))!=fingerprint(facts(current)):
            raise ValueError('Stored numerical model differs from proved delta')
        if artifact is not None:
            from mipframe.store.mps_output import compare_readback
            compare_readback(facts(current),artifact)
        result=dict(status='PROVED',kind='explicit_reformulation_audit',steps=len(bundle['steps']),
                    semantic=current.diff(owned[0]).semantic)
        return result
    finally:
        for model in reversed(owned):model.close()
