"""Version 3 immutable numerical store. Metadata is paged, not eagerly loaded."""

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


def content_digest(path, sense, constant):
    """Hash canonical ordered facts, independently of interning and source spelling."""
    import hashlib
    digest = hashlib.sha256()
    def emit(value):
        digest.update((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    db = sqlite3.connect(Path(path) / "metadata.sqlite")
    try:
        pool, used = OrderedDict(), 0
        budget = 4 * 1024**2

        def remember(ident, value):
            nonlocal used
            size = 256 + sys.getsizeof(ident) + sum(sys.getsizeof(x) for x in value)
            if size <= budget:
                while pool and used + size > budget:
                    _, (_, old_size) = pool.popitem(last=False)
                    used -= old_size
                pool[ident] = (value, size)
                used += size

        # Preserve the exact original strings and digest stream. Small dictionaries
        # need one query; unusually large dictionaries retain a byte-bounded LRU.
        for ident, numerator, denominator in db.execute("SELECT id,numerator,denominator FROM numbers ORDER BY id"):
            value = (numerator, denominator)
            size = 256 + sys.getsizeof(ident) + sum(sys.getsizeof(x) for x in value)
            if used + size > budget:
                break
            remember(ident, value)

        def number(ident):
            if ident is None:
                return None
            if ident in pool:
                pool.move_to_end(ident)
                return pool[ident][0]
            value = db.execute("SELECT numerator,denominator FROM numbers WHERE id=?", (ident,)).fetchone()
            if value is not None:
                remember(ident, value)
            return value
        emit((sense, number(constant)))
        for ident, name, kind, lo, hi, obj, line in db.execute("SELECT * FROM variables ORDER BY id"):
            emit((ident, name, kind, number(lo), number(hi), number(obj)))
        for ident, name, lo, hi, line in db.execute("SELECT * FROM rows ORDER BY id"):
            emit((ident, name, number(lo), number(hi)))
        arrays = [np.load(Path(path) / (n + ".npy"), mmap_mode="r") for n in ("row_ptr", "col_idx", "coeff_id")]
        ptr, cols, coeff = arrays
        try:
            for r in range(len(ptr)-1):
                for pos in range(int(ptr[r]), int(ptr[r+1])):
                    emit((r, int(cols[pos]), number(int(coeff[pos]))))
        finally:
            for array in arrays:
                array._mmap.close()
    finally:
        db.close()
    return digest.hexdigest()


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


class Writer:
    def __init__(self, path, memory_mb=128):
        from mipframe.store.query_store import memory_bytes
        self.memory = memory_bytes(memory_mb)
        self.block = max(1, min(65536, self.memory // 128))
        self._maps = []
        self.path = Path(path)
        self.db = sqlite3.connect(self.path / "metadata.sqlite")
        self.db.executescript("""PRAGMA temp_store=FILE; PRAGMA cache_size=-8192;
        CREATE TABLE numbers(id INTEGER PRIMARY KEY,numerator TEXT,denominator TEXT,approx REAL,UNIQUE(numerator,denominator));
        CREATE TABLE variables(id INTEGER PRIMARY KEY,name TEXT UNIQUE,kind TEXT,lo INTEGER,hi INTEGER,obj INTEGER,line INTEGER);
        CREATE TABLE rows(id INTEGER PRIMARY KEY,name TEXT UNIQUE,lo INTEGER,hi INTEGER,line INTEGER);
        CREATE TABLE stage(r INTEGER,v INTEGER,value TEXT);
        """)
        self.db.execute(f"PRAGMA cache_size=-{max(1, self.memory // 8192)}")
        self.db.execute(f"PRAGMA temp.cache_size=-{max(1, self.memory // 8192)}")
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.rows = 0
        self.nvars = 0
        self.nnums = 0
        self.sense = "min"
        self.constant = self.number(F(0))
        self.nnz = 0

    def _memmap(self, *args, **kwargs):
        array = np.lib.format.open_memmap(*args, **kwargs)
        self._maps.append(array)
        return array

    def close(self):
        self.db.close()
        for array in self._maps:
            if not array._mmap.closed:
                array._mmap.close()
        self._maps.clear()

    def number(self, value):
        if value is None:
            return None
        value = F(value)
        key = (str(value.numerator), str(value.denominator))
        row = self.db.execute(
            "SELECT id FROM numbers WHERE numerator=? AND denominator=?", key
        ).fetchone()
        if row:
            return row[0]
        i = self.nnums
        self.nnums += 1
        try:
            approx = float(value)
        except OverflowError:
            approx = None
        self.db.execute("INSERT INTO numbers VALUES(?,?,?,?)", (i, *key, approx))
        return i

    def num(self, i):
        return (
            None
            if i is None
            else _rational(
                *self.db.execute(
                    "SELECT numerator,denominator FROM numbers WHERE id=?", (i,)
                ).fetchone()
            )
        )

    def variable(self, name, line):
        if name in self.cache:
            self.cache.move_to_end(name)
            return self.cache[name]
        row = self.db.execute(
            "SELECT id FROM variables WHERE name=?", (name,)
        ).fetchone()
        if row:
            i = row[0]
        else:
            i = self.nvars
            self.nvars += 1
            z = self.number(F(0))
            self.db.execute(
                "INSERT INTO variables VALUES(?,?,?,?,?,?,?)",
                (i, name, "C", z, None, z, line),
            )
        self.cache[name] = i
        self.cache_bytes += len(name.encode()) + 128
        while len(self.cache) > 8192 or self.cache_bytes > min(2 * 1024**2, self.memory // 8):
            old, _ = self.cache.popitem(last=False)
            self.cache_bytes -= len(old.encode()) + 128
        return i

    def set_kind(self, v, kind):
        self.db.execute(
            "UPDATE variables SET kind=? WHERE id=? AND NOT(kind='B' AND ?='I')",
            (kind, v, kind),
        )

    def set_bound(self, v, lo, hi, setlo, sethi):
        if setlo:
            self.db.execute(
                "UPDATE variables SET lo=? WHERE id=?", (self.number(lo), v)
            )
        if sethi:
            self.db.execute(
                "UPDATE variables SET hi=? WHERE id=?", (self.number(hi), v)
            )

    def term(self, r, v, value):
        if value:
            self.db.execute("INSERT INTO stage VALUES(?,?,?)", (r, v, str(value)))

    def new_row(self, name, line):
        r = self.rows
        self.rows += 1
        try:
            self.db.execute(
                "INSERT INTO rows VALUES(?,?,?,?,?)", (r, name, None, None, line)
            )
        except sqlite3.IntegrityError as e:
            raise MIPFrameError(f"line {line}: duplicate row {name}") from e
        return r

    def row_bounds(self, r, lo, hi):
        self.db.execute(
            "UPDATE rows SET lo=?,hi=? WHERE id=?",
            (self.number(lo), self.number(hi), r),
        )

    def row(self, name, a, lo, hi, line):
        r = self.new_row(name, line)
        for v, c in a.items():
            self.term(r, v, c)
        self.row_bounds(r, lo, hi)

    def objective(self, a, c):
        for v, value in a.items():
            self.term(-1, v, value)
        self.constant = self.number(c)

    def finish(self):
        # SQLite's external index sorter merges terms even for a single enormous row.
        self.db.execute("CREATE INDEX stage_order ON stage(r,v)")
        ptr = self._memmap(
            self.path / "row_ptr.npy", mode="w+", dtype="<i8", shape=(self.rows + 1,)
        )
        ptr[:] = 0
        current_row = 0
        with open(self.path / "entries.tmp", "wb") as raw:

            def emit(key, value):
                nonlocal current_row
                if key is None or not value:
                    return
                r, v = key
                cid = self.number(value)
                if r == -1:
                    self.db.execute("UPDATE variables SET obj=? WHERE id=?", (cid, v))
                    return
                while current_row <= r:
                    ptr[current_row] = self.nnz
                    current_row += 1
                raw.write(struct.pack("<qq", v, cid))
                self.nnz += 1

            key = None
            value = F(0)
            for r, v, s in self.db.execute("SELECT r,v,value FROM stage ORDER BY r,v"):
                if key != (r, v):
                    emit(key, value)
                    key = (r, v)
                    value = F(0)
                value += F(s)
            emit(key, value)
            while current_row <= self.rows:
                ptr[current_row] = self.nnz
                current_row += 1
        ptr.flush()
        ptr._mmap.close()
        for v, lo, hi in self.db.execute(
            "SELECT id,lo,hi FROM variables WHERE kind='B'"
        ):
            self.set_bound(
                v,
                max(F(0), self.num(lo)) if lo is not None else F(0),
                min(F(1), self.num(hi)) if hi is not None else F(1),
                True,
                True,
            )
        self.db.execute("CREATE TABLE declarations(variable INTEGER PRIMARY KEY, kind TEXT)")
        self.db.execute("INSERT INTO declarations SELECT id,kind FROM variables")
        self.db.execute("UPDATE variables SET kind='I' WHERE kind='B'")
        self.db.execute("DROP TABLE stage")
        self.db.commit()
        self.db.execute("VACUUM")
        self.db.close()
        self.arrays()

    def arrays(self):
        z, n = self.nnz, self.nvars
        cols = self._memmap(
            self.path / "col_idx.npy", mode="w+", dtype="<i8", shape=(z,)
        )
        coeff = self._memmap(
            self.path / "coeff_id.npy", mode="w+", dtype="<i8", shape=(z,)
        )
        count = self._memmap(
            self.path / "counts.tmp.npy", mode="w+", dtype="<i8", shape=(n,)
        )
        count[:] = 0
        if z:
            raw = np.memmap(
                self.path / "entries.tmp", dtype="<i8", mode="r", shape=(z, 2)
            )
            self._maps.append(raw)
            for start in range(0, z, self.block):
                end = min(z, start + self.block)
                block = raw[start:end]
                cols[start:end] = block[:, 0]
                coeff[start:end] = block[:, 1]
                np.add.at(count, block[:, 0], 1)
            del block
            raw._mmap.close()
            del raw
        cp = self._memmap(
            self.path / "col_ptr.npy", mode="w+", dtype="<i8", shape=(n + 1,)
        )
        cp[0] = 0
        np.cumsum(count, out=cp[1:])
        count[:] = cp[:-1]
        pos = self._memmap(
            self.path / "col_positions.npy", mode="w+", dtype="<i8", shape=(z,)
        )
        for start in range(0, z, self.block):
            for p, v in enumerate(cols[start : start + self.block], start):
                pos[count[v]] = p
                count[v] += 1
        for a in (cols, coeff, cp, pos, count):
            a.flush()
            a._mmap.close()
        del count
        (self.path / "counts.tmp.npy").unlink()
        (self.path / "entries.tmp").unlink()


class NumericalStore:
    def __init__(self, path):
        try:
            self._open(path)
        except BaseException:
            self.close()
            raise

    def _open(self, path):
        self.path = Path(path)
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        if self.manifest["schema_version"] != 3:
            raise MIPFrameError("Expected native Model v3; re-import the original LP/MPS file")
        for name, expected in self.manifest["base_sha256"].items():
            if sha256(self.path / name) != expected:
                raise MIPFrameError(f"Corrupt native model file: {name}")
        artifact = self.manifest.get("edit_artifact")
        if self.manifest.get("stage") == "edited" and artifact is None:
            raise MIPFrameError("Edited snapshot is missing lineage")
        if artifact is not None:
            from mipframe.edit.lineage import validate_artifact
            validate_artifact(self.path, self.manifest)
        uid, version = self.manifest["uid"], self.manifest["model_version"]
        if uid in _versions and _versions[uid] != version:
            raise MIPFrameError("Snapshot uid has conflicting content versions")
        self.db = sqlite3.connect(
            (self.path / "metadata.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )
        self.db.execute("PRAGMA cache_size=-8192")
        self.variables = Table(self.db, "variables")
        self.rows = Table(self.db, "rows")
        self.var_names = Names(self.variables)
        self.row_names = Names(self.rows)
        if content_digest(self.path, self.manifest["sense"], self.manifest["objective_constant"]) != self.manifest["model_version"]:
            raise MIPFrameError("Native model content fingerprint mismatch")
        _versions[uid] = version
        self._num_cache = OrderedDict()
        self._num_cache_bytes = 0
        for name in ("row_ptr", "col_idx", "coeff_id", "col_ptr", "col_positions"):
            setattr(self, name, np.load(self.path / (name + ".npy"), mmap_mode="r"))

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
        if not ids: return {}
        columns=tuple(fields)
        query=f'SELECT id,{",".join(columns)} FROM {table} WHERE id IN ({",".join("?" for _ in ids)})'
        return {int(r[0]):dict(zip(columns,r[1:])) for r in self.db.execute(query,tuple(map(int,ids)))}

    def name(self,axis,ident):
        return self.gather(axis,('name',),(ident,))[ident]['name']

    def num(self, i):
        if i is None:
            return None
        i = int(i)
        if i in self._num_cache:
            self._num_cache.move_to_end(i)
            return self._num_cache[i]
        row = self.db.execute(
            "SELECT numerator,denominator FROM numbers WHERE id=?", (int(i),)
        ).fetchone()
        if row is None:
            raise MIPFrameError(f"Unknown number ID {i}")
        result = _rational(*row)
        self._num_cache[i] = result
        self._num_cache_bytes += (
            sys.getsizeof(result.numerator) + sys.getsizeof(result.denominator) + 128
        )
        while len(self._num_cache) > 8192 or self._num_cache_bytes > 4 * 1024**2:
            _, old = self._num_cache.popitem(last=False)
            self._num_cache_bytes -= (
                sys.getsizeof(old.numerator) + sys.getsizeof(old.denominator) + 128
            )
        return result

    def variable_type(self, variable):
        from mipframe.frame.sources import binary
        from math import inf
        v = self.variables[variable] if isinstance(variable, int) else variable
        lo, hi = self.num(v[3]), self.num(v[4])
        return 'B' if binary(v[2], -inf if lo is None else lo, inf if hi is None else hi) else v[2]

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
        for p in self.col_positions[int(self.col_ptr[v]) : int(self.col_ptr[v + 1])]:
            yield (
                int(np.searchsorted(self.row_ptr, p, side="right") - 1),
                self.num(int(self.coeff_id[p])),
            )

    def incident(self, v):
        for p in self.col_positions[int(self.col_ptr[v]) : int(self.col_ptr[v + 1])]:
            yield int(np.searchsorted(self.row_ptr, p, side="right") - 1)

    def close(self):
        from mipframe.store.lifecycle import release_all, close_connection
        actions = [lambda: close_connection(self)]
        for name in ('row_ptr', 'col_idx', 'coeff_id', 'col_ptr', 'col_positions'):
            mmap = getattr(getattr(self, name, None), '_mmap', None)
            if mmap is not None and not mmap.closed:
                actions.append(mmap.close)
        if hasattr(self, '_num_cache'):
            self._num_cache.clear()
        release_all(actions)

    _close_connection = close

    def __del__(self):
        if hasattr(self, "db"):
            try:
                self.close()
            except Exception:
                pass


def build_numerical(source, out, memory_mb=128, format=None, **options):
    if memory_mb < 32:
        raise MIPFrameError(
            "build memory budget must be at least 32 MiB (cooperative working-set budget)"
        )
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise MIPFrameError("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    w = Writer(out, memory_mb=memory_mb)
    try:
        if format == "mps" or (format is None and str(source).lower().endswith(".mps")):
            from mipframe.store.mps import parse
        else:
            from mipframe.store.stream_lp import parse
        parse(source, w, **options)
        parsed = time.perf_counter()
        w.finish()
        indexed = time.perf_counter()
    finally:
        w.close()
    source_digest = sha256(source)
    import hashlib

    model_version = content_digest(out, w.sense, w.constant)
    import uuid
    manifest = {
        "schema_version": 3,
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
        "source_bytes": Path(source).stat().st_size,
        "sense": w.sense,
        "objective_constant": w.constant,
        "variables": w.nvars,
        "rows": w.rows,
        "nonzeros": w.nnz,
        "timings": {"parse": parsed - start, "index": indexed - parsed},
    }
    manifest["base_sha256"] = {
        p.name: sha256(p) for p in out.iterdir() if p.suffix in (".sqlite", ".npy")
    }
    dump(out / "manifest.json", manifest)
    return NumericalStore(out)
