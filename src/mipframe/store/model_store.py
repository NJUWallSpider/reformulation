"""Native v5 array-backed numerical store with a persistent control catalog."""

from collections import OrderedDict
from collections.abc import Mapping
from collections.abc import Sequence
from fractions import Fraction as F
from pathlib import Path
import json
import sqlite3
import struct
import time
import sys
import numpy as np
from mipframe.store.numbers import MIPFrameError
from mipframe.store.artifacts import sha256
from mipframe.store.artifacts import dump


_versions = {}


def _rational(numerator, denominator):
    return F(int(numerator), int(denominator))


def content_digest(path,sense,constant,*,scan=None):
    from mipframe.data.snapshot import SnapshotData
    from mipframe.data.fingerprint import fingerprint
    db=sqlite3.connect(Path(path)/'metadata.sqlite')
    data=SnapshotData(path,db)
    try:
        result=fingerprint(data,sense,constant)
        if scan is not None:
            scan.read_number=data.numbers.parts;scan.number_rows=data.numbers.rows
            scan.prepare_numbers(len(data.numbers))
            for variable in data.variables:scan.variable(variable)
            ptr,coeff=data.arrays['row_ptr'],data.arrays['coeff_id']
            for row in data.rows:
                r=row[0];scan.row(row,coeff[int(ptr[r]):int(ptr[r+1])])
        return result
    finally:data.release();db.close()


class Table(Sequence):
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.size = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def __len__(self):
        return self.size

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(self.size)
            return [self[i] for i in range(start, stop, step)]
        if key < 0:
            key += self.size
        row = self.db.execute(
            f"SELECT * FROM {self.table} WHERE id=?", (int(key),)
        ).fetchone()
        if row is None:
            raise IndexError(key)
        return row

    def __iter__(self):
        return iter(self.db.execute(f"SELECT * FROM {self.table} ORDER BY id"))


class Names(Mapping):
    def __init__(self, table):
        self.table = table

    def __len__(self):
        return len(self.table)

    def __iter__(self):
        return (r[1] for r in self.table)

    def __getitem__(self, key):
        row = self.table.db.execute(
            f"SELECT id FROM {self.table.table} WHERE name=?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]


from mipframe.data.builder import Writer


class NumericalStore:
    def guard(self):
        if getattr(self, 'db', None) is None:
            raise MIPFrameError('Model is closed')

    def entity_ids(self, axis):
        self.guard()
        return range(len(self.rows if axis == 'row' else self.variables))

    def capacity(self, axis):
        return len(self.rows if axis == 'row' else self.variables)

    def __init__(self, path, *, scan_memory_mb=None, _verified_content=None, _verified_files=None):
        try:
            self._open(path, scan_memory_mb=scan_memory_mb, verified_content=_verified_content, verified_files=_verified_files)
        except BaseException:
            self.close()
            raise

    @classmethod
    def from_facts(cls,f):
        """Acquire another handle on the same immutable runtime, without remapping."""
        self=cls.__new__(cls)
        self.path=f.data.path
        self.manifest=dict(f.provenance)
        self.db=sqlite3.connect((self.path/'metadata.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
        self.data=f.data.acquire()
        self.rows,self.variables=self.data.rows,self.data.variables
        self.row_names=Names(Table(self.db,'rows'));self.var_names=Names(Table(self.db,'variables'))
        for name in ('row_ptr','col_idx','coeff_id','col_ptr','col_positions','csc_row_ids','csc_value_ids'):
            setattr(self,name,self.data.arrays[name])
        self._scan_data=getattr(self.data,"scan",None);self.verified_content=f._canonical_fingerprint
        return self

    def _open(self, path, *, scan_memory_mb=None, verified_content=None, verified_files=None):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        if self.manifest["schema_version"] != 7:
            raise MIPFrameError("Expected native Model v7; re-import the original LP/MPS file")
        for name, expected in self.manifest["base_sha256"].items():
            if (verified_files.get(name) if verified_files is not None else sha256(self.path / name)) != expected:
                raise MIPFrameError(f"Corrupt native model file: {name}")
        if self.manifest.get('stage')=='edited' or 'edit_artifact' in self.manifest:
            raise MIPFrameError('Native v7 edits require a snapshot delta manifest; re-import the original model')
        uid, version = self.manifest["uid"], self.manifest["model_version"]
        self.db = sqlite3.connect(
            (self.path / "metadata.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )
        self.db.execute("PRAGMA cache_size=-8192")
        from mipframe.data.snapshot import SnapshotData
        self.data = SnapshotData(self.path, self.db)
        self.variables, self.rows = self.data.variables, self.data.rows
        # Names remain a catalog lookup at the presentation boundary.
        self.var_names = Names(Table(self.db, 'variables'))
        self.row_names = Names(Table(self.db, 'rows'))
        self._num_cache = OrderedDict()
        for name in ('row_ptr','col_idx','coeff_id','col_ptr','col_positions',
                     'csc_row_ids','csc_value_ids'):
            setattr(self,name,self.data.arrays[name])
        from mipframe.store.integrity import dimensions_valid
        if not dimensions_valid(self):raise MIPFrameError('Invalid sparse model arrays or orientations')
        from mipframe.data.fingerprint import fingerprint
        actual=verified_content or fingerprint(self.data,self.manifest['sense'],self.manifest['objective_constant'])
        if actual!=version:raise MIPFrameError('Native mathematical content fingerprint mismatch')
        self.verified_content=actual
        if uid in _versions and _versions[uid] != version:
            raise MIPFrameError("Snapshot uid has conflicting content versions")
        _versions[uid] = version
        self._scan_data = None
        if scan_memory_mb is not None:
            self.prepare_scan(scan_memory_mb)

    def prepare_scan(self, memory_mb):
        from mipframe.store.scan import Scan
        scan = Scan(memory_mb)
        try:
            scan.read_number = self.data.numbers.parts
            scan.number_rows = self.data.numbers.rows
            scan.prepare_numbers(len(self.data.numbers))
            for v in self.variables:
                scan.variable(v)
            for row in self.rows:
                lo,hi = map(int,self.row_ptr[row[0]:row[0]+2])
                scan.row(row,self.coeff_id[lo:hi])
            self._scan_data = scan.export(self.row_ptr,self.col_ptr)
            self.data.scan = self._scan_data
        finally:
            scan.close()

    @property
    def nnz(self):
        self.guard()
        return len(self.col_idx)

    @property
    def sparse(self):
        from mipframe.store.sparse_store import SparseStore
        if self.db is None: raise MIPFrameError('Model is closed')
        return SparseStore(self)

    def gather(self,axis,fields,ids):
        if self.db is None: raise MIPFrameError('Model is closed')
        table='rows' if axis=='row' else 'variables' if axis=='var' else None
        allowed={'name','lo','hi','line'} | ({'kind','obj'} if axis=='var' else set())
        if table is None or not set(fields)<=allowed: raise ValueError('Invalid entity fields')
        if len(ids) == 0: return {}
        if tuple(fields)==('name',):return {int(i):{'name':self.data.names[axis][int(i)]} for i in ids}
        a=self.data.arrays;names=self.data.names[axis]
        def value(name,i):
            if name=='name':return names[i]
            if name=='lo':return int(a[axis+'_lb_id'][i]) if a[axis+'_lb_finite'][i] else None
            if name=='hi':return int(a[axis+'_ub_id'][i]) if a[axis+'_ub_finite'][i] else None
            if name=='kind':return 'I' if a['var_type'][i] else 'C'
            if name=='obj':return int(a['var_obj_id'][i])
            return int(a[axis+'_line'][i])
        return {int(i):{name:value(name,int(i)) for name in fields} for i in ids}

    def name(self,axis,ident):
        self.guard()
        return self.data.names[axis][int(ident)]

    def num(self, i):
        if self.db is None:
            raise MIPFrameError('Model is closed')
        return self.data.numbers[i]

    def variable_type(self, variable):
        from mipframe.frame.sources import binary
        from math import inf
        v = self.variables[variable] if isinstance(variable, int) else variable
        lo, hi = self.num(v[3]), self.num(v[4])
        return 'B' if binary(v[2], -inf if lo is None else lo, inf if hi is None else hi) else v[2]

    def degree(self, axis, ident):
        self.guard()
        ptr = self.row_ptr if axis == 'row' else self.col_ptr
        return int(ptr[ident+1])-int(ptr[ident])

    def coefficient(self, row, var):
        return self.num(int(self.coeff_id[self.sparse.position(row,var)]))

    def support(self, r):
        self.guard()
        return self.col_idx[int(self.row_ptr[r]):int(self.row_ptr[r+1])]

    def row_bounds(self, r):
        self.guard()
        a = self.data.arrays
        return tuple(self.num(int(a['row_'+side+'_id'][r])) if a['row_'+side+'_finite'][r] else None for side in ('lb','ub'))

    def variable_kind(self, v):
        self.guard()
        return 'I' if self.data.arrays['var_type'][v] else 'C'

    def uniform_coefficient(self, r):
        start,stop = map(int,self.row_ptr[r:r+2])
        ids = self.coeff_id[start:stop]
        if len(ids) and np.all(ids==ids[0]):return self.num(int(ids[0]))
        return None

    def iter_terms(self, r):
        for p in range(int(self.row_ptr[r]), int(self.row_ptr[r + 1])):
            yield int(self.col_idx[p]), self.num(int(self.coeff_id[p]))

    def row_ids(self, r):
        return [
            (int(self.col_idx[p]), int(self.coeff_id[p]))
            for p in range(int(self.row_ptr[r]), int(self.row_ptr[r + 1]))
        ]

    def row(self, r):
        return dict(self.iter_terms(r))

    def iter_column(self, v):
        lo,hi=map(int,self.col_ptr[v:v+2])
        for r,c in zip(self.csc_row_ids[lo:hi],self.csc_value_ids[lo:hi]):
            yield int(r),self.num(int(c))

    def incident(self, v):
        lo,hi=map(int,self.col_ptr[v:v+2])
        yield from map(int,self.csc_row_ids[lo:hi])

    def close(self):
        observer = getattr(self, "_scan_observer", None)
        if observer is not None:
            observer.close()
            self._scan_observer = None
        from mipframe.store.lifecycle import release_all, close_connection
        actions = [lambda: close_connection(self)]
        data = getattr(self, 'data', None)
        if data is not None:
            self.data = None
            actions.append(data.release)
        if hasattr(self, '_num_cache'):
            self._num_cache.clear()
        release_all(actions)

    _close_connection = close

    def __del__(self):
        if getattr(self, "db", None) is not None:
            try:
                self.close()
            except Exception:
                pass


def build_numerical(source, out, memory_mb=128, format=None, *, disk_mb=None, **options):
    if memory_mb < 32:
        raise MIPFrameError(
            "build memory budget must be at least 32 MiB (cooperative working-set budget)"
        )
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise MIPFrameError("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    w = Writer(out, memory_mb=memory_mb,disk_mb=disk_mb)
    try:
        if format == "mps" or (format is None and str(source).lower().endswith(".mps")):
            from mipframe.store.mps import parse
        else:
            from mipframe.store.stream_lp import parse
        parse(source, w, **options)
        parsed = time.perf_counter()
        w.finish()
        from mipframe.control.catalog import publish_import
        publish_import(w)
        indexed = time.perf_counter()
    finally:
        w.close()
    source_digest = getattr(w,"source_digest",None) or sha256(source)
    import hashlib

    model_version = content_digest(out, w.sense, w.constant)
    import uuid
    manifest = {
        "schema_version": 7,
        "uid": str(uuid.uuid4()),
        "parent": None,
        "stage": "imported",
        "format": format or Path(source).suffix.lstrip("."),
        "numeric_provenance": "canonical exact rational values saved; original token spelling not retained",
        "source_sha256": source_digest,
        "build_options": options,
        "working_memory_mb": memory_mb,
        "model_version": model_version,
        "source": str(Path(source).resolve()),
        "source_bytes": getattr(w,"source_bytes",None) or Path(source).stat().st_size,
        "sense": w.sense,
        "objective_constant": w.constant,
        "variables": w.nvars,
        "rows": w.rows,
        "nonzeros": w.nnz,
        "timings": {"parse": parsed - start, "index": indexed - parsed},
    }
    manifest["base_sha256"] = {
        p.name: sha256(p) for p in out.iterdir() if p.suffix in (".sqlite", ".npy") or p.name in ("source.mps","source_info.json")
    }
    dump(out / "manifest.json", manifest)
    return NumericalStore(out, scan_memory_mb=memory_mb, _verified_content=model_version)
