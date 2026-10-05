"""Portable shared base arrays and source-relative overlay manifests.

Stored proofs are historical recipes. Loading never grants proof authority.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import numpy as np
from mipframe.store.artifacts import sha256
from mipframe.store.numbers import MIPFrameError


def copy_artifact(source,target):
    """Clone immutable bytes where supported, otherwise stream a real copy."""
    import os,sys
    if sys.platform=='darwin':
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True)
        if libc.clonefile(os.fsencode(source),os.fsencode(target),0)==0:return
    elif sys.platform.startswith('linux'):
        import fcntl
        try:
            with open(source,'rb') as src,open(target,'xb') as dst:
                fcntl.ioctl(dst.fileno(),0x40049409,src.fileno())
            return
        except OSError:Path(target).unlink(missing_ok=True)
    shutil.copyfile(source,target)


class ArtifactTree:
    """Move homogeneous manifest vectors out of JSON into typed artifacts."""
    def __init__(self, path):
        self.path = Path(path)
        self.sequence = 0
        self.maps = []
        from mipframe.data.runtime import WorkContext,DiskReservation
        self.work=WorkContext();self.reservation=DiskReservation(self.work)

    def save_array(self,name,array):
        self.reservation.add(array.nbytes+256)
        np.save(self.path/name,array,allow_pickle=False)

    def any(self,predicate,*columns):
        """Validate mapped columns without a whole-vector boolean temporary."""
        block=max(1,min(65536,self.work.memory//256,self.work.available//256))
        with self.work.reserve(block*64+128):
            for start in range(0,len(columns[0]),block):
                if np.any(predicate(*(a[start:start+block] for a in columns))):return True
        return False

    def encode(self, value):
        from fractions import Fraction
        from mipframe.check.keys import Keys
        if isinstance(value,Keys):
            from mipframe.data.runtime import WorkContext
            with WorkContext() as work:
                def column(buffer):
                    result=work.array(len(buffer),buffer.dtype)
                    block=max(1,work.memory//128)
                    for start in range(0,len(buffer),block):result[start:start+block]=buffer[start:start+block]
                    return self.encode(result)
                return {'$keys':{n:column(getattr(value.pool,n)) for n in ('data','offsets','lengths')}}
        from mipframe.data.columns import ExactColumn,EnumColumn
        from mipframe.data.overrides import Overrides,BoundColumn
        if isinstance(value,Overrides):
            return {'$overrides':dict(kind=value.kind,ids=self.encode(value.ids),columns=[self.encode(c) for c in value.columns])}
        if isinstance(value,BoundColumn):
            return {'$bound':dict(flags=self.encode(value.flags),exact=self.encode(value.exact))}
        if isinstance(value,np.ndarray):
            if value.dtype.kind not in 'iub':raise ValueError('Native vectors require typed integer values')
            if not value.size:return {'$empty':value.dtype.str}
            if value.ndim==1 and value.strides==(0,):return {'$broadcast':dict(dtype=value.dtype.str,value=int(value[0]),size=len(value))}
            name=f'vector-{self.sequence}.npy';self.sequence+=1
            self.save_array(name,value)
            return {'$array':name}
        if isinstance(value,ExactColumn) and hasattr(value.values,'native_arrays'):
            return {'$exact_arrays':dict(ids=self.encode(value.ids),arrays={k:self.encode(v) for k,v in value.values.native_arrays().items()})}
        if isinstance(value,ExactColumn):
            return {'$exact':dict(ids=self.encode(value.ids),values=[str(v) for v in value.values])}
        if isinstance(value,EnumColumn):
            return {'$enum':dict(codes=self.encode(value.codes),labels=value.labels)}
        if isinstance(value,Fraction):return {'q':str(value)}
        if isinstance(value, dict):
            return {str(k): self.encode(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            if len(value) > 16 and all(type(v) is int for v in value):
                lo, hi = min(value), max(value)
                if 0 <= lo and hi < 2**64:
                    dtype = '<u4' if hi < 2**32 else '<u8'
                elif -2**63 <= lo and hi < 2**63:
                    dtype = '<i8'
                else:
                    dtype = None
                if dtype is not None:
                    name = f'vector-{self.sequence}.npy'
                    self.sequence += 1
                    with self.work.fork() as scratch:
                        array=scratch.array(len(value),dtype)
                        block=max(1,scratch.memory//128)
                        for start in range(0,len(value),block):array[start:start+block]=value[start:start+block]
                        self.save_array(name,array)
                    return {'$array': name}
            if len(value) > 16 and all(type(v) is str for v in value):
                from mipframe.data.snapshot import write_strings
                name = f'strings-{self.sequence}'
                self.sequence += 1
                write_strings(self.path, name, value)
                return {'$strings': name}
            return [self.encode(v) for v in value]
        return value

    def decode(self, value):
        if isinstance(value, dict):
            if set(value)=={'$keys'}:
                from mipframe.check.keys import EncodedKeys
                return EncodedKeys({n:self.decode(v) for n,v in value['$keys'].items()})
            if set(value)=={'$broadcast'}:
                r=value['$broadcast'];dtype=np.dtype(r['dtype']);n=r['size'];v=r['value']
                if dtype.kind not in 'iub' or type(n) is not int or n<0 or n>2**63-1:raise MIPFrameError('Invalid broadcast column')
                scalar=np.asarray([v],dtype=dtype)
                if int(scalar[0])!=v:raise MIPFrameError('Invalid broadcast scalar')
                return np.broadcast_to(np.frombuffer(scalar.tobytes(),dtype=dtype),(n,))
            if set(value)=={'$empty'}:
                dtype=np.dtype(value['$empty'])
                if dtype.kind not in 'iub':raise MIPFrameError('Invalid empty native vector')
                return np.frombuffer(b'',dtype=dtype)
            if set(value)=={'$overrides'}:
                from mipframe.data.overrides import Overrides,BoundColumn
                from mipframe.data.columns import ExactColumn,EnumColumn
                record=value['$overrides'];result=object.__new__(Overrides)
                result.kind=record['kind'];result.ids=self.decode(record['ids'])
                result.columns=tuple(self.decode(c) for c in record['columns'])
                if result.kind not in ('exact','kind','bounds') or len(result.columns)!=(2 if result.kind=='bounds' else 1):
                    raise MIPFrameError('Invalid override columns')
                column_type={'exact':ExactColumn,'kind':EnumColumn,'bounds':BoundColumn}[result.kind]
                if any(not isinstance(c,column_type) for c in result.columns):raise MIPFrameError('Invalid override type')
                if result.kind=='kind' and result.columns[0].labels!=('C','I'):raise MIPFrameError('Invalid variable domain labels')
                if self.any(lambda x:x<0,result.ids) or self.any(lambda x,y:x<=y,result.ids[1:],result.ids[:-1]) or any(len(c)!=len(result.ids) for c in result.columns):
                    raise MIPFrameError('Invalid override IDs or dimensions')
                return result
            if set(value)=={'$bound'}:
                from mipframe.data.overrides import BoundColumn
                record=value['$bound'];result=object.__new__(BoundColumn)
                result.flags=self.decode(record['flags']);result.exact=self.decode(record['exact'])
                if len(result.flags)!=len(result.exact) or self.any(lambda x:(x<-1)|(x>1),result.flags):
                    raise MIPFrameError('Invalid bound column')
                return result
            if set(value)=={'$exact_arrays'}:
                from mipframe.data.columns import ExactColumn,MappedExactValues
                record=value['$exact_arrays'];arrays={k:self.decode(v) for k,v in record['arrays'].items()}
                if set(arrays)!={'n','d','offset','length','big'}:raise MIPFrameError('Invalid exact array fields')
                size=len(arrays['n'])
                if any(len(arrays[k])!=size for k in ('d','offset','length')) or self.any(lambda x:x<0,arrays['d']):raise MIPFrameError('Invalid exact arrays')
                if self.any(lambda x:x>len(arrays['big']),arrays['offset']) or self.any(lambda x,y:x>len(arrays['big'])-y,arrays['length'],arrays['offset']):raise MIPFrameError('Invalid bigint extents')
                column=object.__new__(ExactColumn);column.values=MappedExactValues(arrays);column.ids=self.decode(record['ids'])
                if self.any(lambda x:x>=size,column.ids):raise MIPFrameError('Invalid exact IDs')
                return column
            if set(value)=={'$exact'}:
                from mipframe.data.columns import ExactColumn
                from mipframe.check.kernel import exact
                r=value['$exact'];column=object.__new__(ExactColumn)
                column.ids=self.decode(r['ids']);column.values=tuple(exact(v) for v in r['values'])
                if self.any(lambda x:(x<0)|(x>=len(column.values)),column.ids):raise MIPFrameError('Invalid exact dictionary ID')
                return column
            if set(value)=={'$enum'}:
                from mipframe.data.columns import EnumColumn
                r=value['$enum'];column=object.__new__(EnumColumn)
                column.codes=self.decode(r['codes']);column.labels=tuple(r['labels'])
                if self.any(lambda x:(x<0)|(x>=len(column.labels)),column.codes):raise MIPFrameError('Invalid category ID')
                return column
            if set(value) == {'$array'}:
                name = value['$array']
                if Path(name).name != name:
                    raise MIPFrameError('Invalid native array path')
                a=np.load(self.path/name,mmap_mode='r',allow_pickle=False)
                if a.ndim!=1 or a.dtype.kind not in 'iub':raise MIPFrameError('Invalid native vector')
                self.maps.append(a);return np.asarray(a)
            if set(value) == {'$strings'}:
                from mipframe.data.snapshot import StringArray
                name = value['$strings']
                if Path(name).name != name:
                    raise MIPFrameError('Invalid native string path')
                offsets=np.load(self.path/(name+'_offsets.npy'),mmap_mode='r',allow_pickle=False)
                data=np.load(self.path/(name+'_utf8.npy'),mmap_mode='r',allow_pickle=False)
                self.maps.extend((offsets,data));return StringArray(offsets,data)
            return {k:self.decode(v) for k,v in value.items()}
        if isinstance(value, list):
            return [self.decode(v) for v in value]
        return value


def save(owner, directory):
    from mipframe.check.kernel import facts
    from mipframe.control.proofs import proof_bundle
    f = facts(owner)
    root = f
    while root.parent is not None:
        root = root.parent
    target = Path(directory).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    if target.resolve().is_relative_to(root.data.path.resolve()):
        raise MIPFrameError('Save outside the source snapshot directory')
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.mipframe-native-', dir=target.parent) as tmp:
        out = Path(tmp)/'snapshot'
        out.mkdir()
        base = out/'base'
        base.mkdir()
        # Copy immutable files directly; no matrix reconstruction or array copy.
        manifest = json.loads((root.data.path/'manifest.json').read_text())
        for name in (*manifest['base_sha256'], 'manifest.json'):
            copy_artifact(root.data.path/name, base/name)
        # Completed derived caches are portable; SQLite backup captures committed state.
        import sqlite3
        from contextlib import closing
        cache_root=getattr(root.data,'cache_directory',None) or root.data.path
        for filename in ('refinement.sqlite','indexing.sqlite'):
            cache=cache_root/filename
            if not cache.exists():continue
            with closing(sqlite3.connect(cache.resolve().as_uri()+'?mode=ro',uri=True)) as src:
                with closing(sqlite3.connect(base/cache.name)) as dest:src.backup(dest)
        if (cache_root/'derived').exists():
            shutil.copytree(cache_root/'derived',base/'derived')
        if hasattr(root.data,'scan'):
            from mipframe.frame.schema import encode
            def exact_tree(value):
                if isinstance(value,dict):return {k:exact_tree(v) for k,v in value.items()}
                if isinstance(value,(tuple,list)):return [exact_tree(v) for v in value]
                return encode(value)
            (out/'scan.json').write_text(json.dumps(dict(version=1,snapshot=root.revision,
                data=exact_tree(root.data.scan)),separators=(',',':'),allow_nan=False))
        artifacts = out/'artifacts'
        artifacts.mkdir()
        bundle = getattr(f, 'saved_bundle', None) or proof_bundle(f,native=True)
        tree = ArtifactTree(artifacts).encode(bundle)
        (artifacts/'proofs.json').write_text(json.dumps(tree, separators=(',', ':')))
        result = dict(schema_version=7, features=['compiled_substitution','source_records'], kind='snapshot', uid=f.uid, revision=f.revision,
                      validation='UNVERIFIED_AFTER_LOAD',
                      historical_semantic=getattr(f,'semantic',None),
                      sha256={str(p.relative_to(out)):sha256(p) for p in out.rglob('*') if p.is_file()})
        (out/'manifest.json').write_text(json.dumps(result, separators=(',', ':')))
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        out.rename(target)
    return target


def load_facts(path):
    from mipframe.check.kernel import Facts, OverlayFacts, bound, exact
    from mipframe.edit.delta import Delta
    path = Path(path)
    manifest = json.loads((path/'manifest.json').read_text())
    if manifest.get('schema_version') != 7 or manifest.get('kind') != 'snapshot':
        raise MIPFrameError('Expected native v7 snapshot; re-import the original LP/MPS file')
    if set(manifest.get('features',()))-{'compiled_substitution','source_records'}:raise MIPFrameError('Unsupported native feature')
    inventory = manifest.get('sha256', {})
    actual = {str(p.relative_to(path)) for p in path.rglob('*') if p.is_file()}-{'manifest.json'}
    if set(inventory) != actual:
        raise MIPFrameError('Invalid native artifact inventory')
    for name, digest in inventory.items():
        if Path(name).is_absolute() or '..' in Path(name).parts or (path/name).is_symlink():
            raise MIPFrameError('Invalid native artifact path')
        if sha256(path/name) != digest:
            raise MIPFrameError('Corrupt native artifact: '+name)
    tree=ArtifactTree(path/'artifacts')
    bundle=tree.decode(json.loads((path/'artifacts/proofs.json').read_text()))
    from mipframe.store.model_store import NumericalStore
    from mipframe.store.integrity import dimensions_valid
    store=NumericalStore(path/'base',_verified_files={name[5:]:digest for name,digest in inventory.items() if name.startswith('base/')})
    try:
        f=Facts.from_store(store)
        f.data.read_only_source=True
        f.data.artifact_maps=tree.maps
    finally:store.close()
    if (path/'scan.json').exists():
        from mipframe.frame.schema import decode_tree
        scan=json.loads((path/'scan.json').read_text())
        if scan.get('version')==1 and scan.get('snapshot')==f.revision:
            f.data.scan=decode_tree(scan['data'])
    if f.uid != bundle['root'] or f.revision != bundle['root_revision']:
        raise MIPFrameError('Native base identity mismatch')
    for step in bundle['steps']:
        if step['parent'] != f.uid:
            raise MIPFrameError('Broken native snapshot chain')
        d = step['delta']
        from mipframe.data.overrides import Overrides
        numeric = lambda key: d[key] if isinstance(d[key],Overrides) else {int(k):tuple(map(bound,v)) for k,v in d[key].items()}
        delta = Delta(drop_rows=d['drop_rows'], drop_vars=d['drop_vars'],
            fixed=d['fixed'] if isinstance(d['fixed'],Overrides) else {int(k):exact(v) for k,v in d['fixed'].items()},
            var_bounds=numeric('var_bounds'), row_bounds=numeric('row_bounds'),
            kinds=d['kinds'] if isinstance(d['kinds'],Overrides) else {int(k):v for k,v in d['kinds'].items()},
            add_rows={int(k):(v[0],bound(v[1]),bound(v[2]),tuple((int(i),exact(c)) for i,c in v[3])) for k,v in d['add_rows'].items()},
            add_vars={int(k):(v[0],v[1],bound(v[2]),bound(v[3]),exact(v[4])) for k,v in d['add_vars'].items()},
            objective=None if d['objective'] is None else ({int(k):exact(v) for k,v in d['objective'][0].items()},exact(d['objective'][1])),
            sense=d['sense'])
        if d.get('compiled') is not None:
            from mipframe.edit.substitution import restore_compiled
            from dataclasses import replace
            delta=replace(delta,substitution=restore_compiled(d['compiled'],delta.fixed))
        for axis, ids in (('row',delta.drop_rows), ('var',set(delta.drop_vars)|set(delta.fixed))):
            for ident in ids:
                f.validate(axis, ident)
        for axis,values in (('row',delta.row_bounds),('var',delta.var_bounds),('var',delta.kinds)):
            for ident in values:f.validate(axis,ident)
        f = OverlayFacts(f, delta)
        f.uid, f.revision = step['uid'], step['revision']
    if (f.uid,f.revision) != (manifest['uid'],manifest['revision']):
        raise MIPFrameError('Native snapshot identity mismatch')
    f.saved_bundle = bundle
    f.historical_semantic = manifest.get('historical_semantic')
    return f
