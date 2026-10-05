"""Version 2 proof DAGs and atomic typed artifact bundles."""
from dataclasses import fields
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
from mipframe.core.resources import memory_bytes,size
from mipframe.core.errors import MaterializationLimitError


def native_graph(root):
    nodes=[];ids={};active=set();stack=[(root,False)]
    while stack:
        proof,done=stack.pop()
        if proof in ids:continue
        if not done:
            if proof in active:raise ValueError('Cyclic certificate dependency')
            active.add(proof);stack.append((proof,True))
            stack.extend((p,False) for p in reversed(proof._parents));continue
        active.remove(proof)
        def record(value):return {f.name:getattr(value,f.name) for f in fields(value) if not f.metadata.get('resource')}
        ids[proof]=len(nodes)
        nodes.append(dict(schema='mipframe-certificate-node-2',snapshot=proof._facts.uid,revision=proof._facts.revision,
            rule=proof._rule,targets=None if proof._rule=='check_graph' else record(proof._targets),certificate=None if proof._certificate is None else record(proof._certificate),
            recipe=proof.graph_record() if proof._rule=='check_graph' else proof._recipe,parents=[ids[p] for p in proof._parents],keys=getattr(proof,'_external_keys',None)))
    return dict(schema='mipframe-certificate-3' if any(n['rule']=='check_graph' for n in nodes) else 'mipframe-certificate-2',snapshot=root._facts.uid,revision=root._facts.revision,root=ids[root],nodes=nodes)


def materialize(value,materialize_mb=None):
    from mipframe.options import options
    from collections.abc import Mapping,Sequence
    import numpy as np
    budget=memory_bytes(options.query.materialize_mb if materialize_mb is None else materialize_mb);used=0
    def charge(n):
        nonlocal used
        used+=n
        if used>budget:raise MaterializationLimitError('Certificate exceeds materialize_mb; use write(path)')
    def visit(v):
        if isinstance(v,np.generic):v=v.item()
        if isinstance(v,Fraction):return visit({'q':str(v)})
        if isinstance(v,np.ndarray) and v.ndim==1 and v.dtype.kind in 'iub':
            # Check the complete Python output allocation before NumPy creates
            # it. Conversion preserves every integer bit and avoids recursively
            # visiting hundreds of thousands of temporary NumPy scalars.
            scalar_size=size(True) if v.dtype.kind=='b' else max(size(int(np.iinfo(v.dtype).min)),size(int(np.iinfo(v.dtype).max)))
            charge(64+len(v)*(16+scalar_size))
            return v.tolist()
        if isinstance(v,Mapping):
            charge(128);out={}
            for k,x in v.items():charge(size(str(k))+48);out[str(k)]=visit(x)
            return out
        if isinstance(v,(Sequence,np.ndarray)) and not isinstance(v,(str,bytes)):
            charge(64);out=[]
            for x in v:charge(16);out.append(visit(x))
            return out
        charge(size(v));return v
    return visit(value)


def write_bundle(spec,path):
    from mipframe.store.native7 import ArtifactTree
    from mipframe.store.artifacts import sha256
    path=Path(path)
    if path.exists() or path.is_symlink():raise FileExistsError(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='.'+path.name+'-',dir=path.parent) as tmp:
        stage=Path(tmp)/'proof';stage.mkdir();tree=ArtifactTree(stage)
        encoded=tree.encode(spec)
        checksums={p.name:sha256(p) for p in stage.iterdir() if p.is_file()}
        with (stage/'manifest.json').open('w') as out:
            json.dump(dict(format='mipframe-proof-bundle',version=2,proof=encoded,checksums=checksums),out,separators=(',',':'))
            out.flush();os.fsync(out.fileno())
        if path.exists() or path.is_symlink():raise FileExistsError(path)
        stage.rename(path)
    return path


def read_bundle(path):
    from mipframe.store.native7 import ArtifactTree
    from mipframe.store.artifacts import sha256
    path=Path(path);manifest=json.loads((path/'manifest.json').read_text())
    if manifest.get('format')!='mipframe-proof-bundle' or manifest.get('version')!=2:raise ValueError('Unsupported proof bundle')
    actual={p.name for p in path.iterdir() if p.is_file()}-{'manifest.json'}
    if set(manifest['checksums'])!=actual:raise ValueError('Invalid proof artifact inventory')
    for name,digest in manifest['checksums'].items():
        if Path(name).name!=name or (path/name).is_symlink() or sha256(path/name)!=digest:raise ValueError('Corrupt proof artifact')
    tree=ArtifactTree(path)
    try:return tree.decode(manifest['proof']),tree
    except BaseException:
        for a in tree.maps:a._mmap.close()
        raise
