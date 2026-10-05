"""Portable proof recipes and source-relative delta manifests."""
from fractions import Fraction
from collections.abc import Mapping
def encode(x):
    if isinstance(x,Fraction):return str(x)
    if hasattr(x,'tolist'):return encode(x.tolist())
    if isinstance(x,Mapping):return {str(k):encode(v) for k,v in x.items()}
    if isinstance(x,(tuple,list)):return [encode(v) for v in x]
    return x


def delta_spec(d, *, native=False):
    if native:
        from mipframe.edit.substitution import compiled_spec
        return dict(compiled=compiled_spec(d.substitution),drop_rows=d.drop_rows.array,drop_vars=d.drop_vars.array,fixed=d.fixed,
                    var_bounds=d.var_bounds,row_bounds=d.row_bounds,kinds=d.kinds,
                    add_rows=encode(dict(d.add_rows)),add_vars=encode(dict(d.add_vars)),
                    objective=encode(d.objective),sense=d.sense)
    from mipframe.edit.substitution import compiled_spec
    return dict(compiled=encode(compiled_spec(d.substitution)),drop_rows=sorted(d.drop_rows),drop_vars=sorted(d.drop_vars),
                fixed=encode(dict(d.fixed.items())),var_bounds=encode(dict(d.var_bounds.items())),
                row_bounds=encode(dict(d.row_bounds.items())),kinds=dict(d.kinds.items()),
                add_rows=encode(dict(d.add_rows)),add_vars=encode(dict(d.add_vars)),
                objective=encode(d.objective),sense=d.sense)


def proof_bundle(f, *, native=False):
    steps=[];cur=f
    while cur.parent is not None:
        if getattr(cur,'saved_bundle',None) is not None:
            bundle=dict(cur.saved_bundle,steps=[*cur.saved_bundle['steps'],*reversed(steps)])
            return bundle if native else encode(bundle)
        d=cur.delta;proofs=[];bindings=[];seen={}
        from mipframe.check.compact import ProofRef,ProofSelection,proof_parts
        def binding(p):
            if p is None:return None
            if isinstance(p,tuple):return [binding(x) for x in p]
            b,positions=proof_parts(p)
            if b not in seen:seen[b]=len(proofs);proofs.append(b._native_spec() if native else b.to_spec())
            return dict(proof=seen[b],index=p.index if isinstance(p,ProofRef) else None,
                        positions=(p.positions if native else list(map(int,p.positions))) if isinstance(p,ProofSelection) else None)
        for kind,ids,p in d.operations:bindings.append([kind,ids if native else list(map(int,ids)),binding(p)])
        steps.append(dict(uid=cur.uid,revision=cur.revision,parent=cur.parent.uid,delta=delta_spec(d,native=native),proofs=proofs,bindings=bindings))
        cur=cur.parent
    from mipframe.store.reformulation_audit import fingerprint
    return dict(schema='mipframe-reformulation-1',root=cur.uid,root_revision=cur.revision,root_fingerprint=fingerprint(cur),
                source_sha256=cur.provenance.get('source_sha256'),
                source_options=cur.provenance.get('build_options',{}),steps=list(reversed(steps)))
