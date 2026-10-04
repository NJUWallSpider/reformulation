"""Template and exact disk-backed refinement on the orientation-expanded graph.

Only integer buffers are used for edges. SQLite holds exact roles, interning
keys and bounded binary chunks; hashes are lookup accelerators, never identity.
"""

from collections import OrderedDict
from fractions import Fraction
from itertools import chain, islice, zip_longest
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import math
import sqlite3
import struct
from weakref import WeakSet

import numpy as np

from mipframe.frame.entity import _sense
from mipframe.store.numbers import MIPFrameError
from mipframe.ir.typed import bound_class

VERSION = "2.0"
TOKEN = struct.Struct(">qqq")
KEY_CHUNK = 256


def _new_hash():
    return hashlib.blake2b(digest_size=32)


def _json(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def validate(depth, coefficient, rhs, objective, memory_mb, mode="template", multiplicity_ratio=2):
    if type(depth) is not int or depth < 0:
        raise ValueError("depth must be a nonnegative integer")
    if mode not in ("template", "exact"):
        raise ValueError("mode must be 'template' or 'exact'")
    expected = "sign" if mode == "template" else "scale_normalized"
    if coefficient is not None and coefficient != expected:
        raise ValueError(f"mode={mode!r} requires coefficient={expected!r}; use mode='exact' for scale_normalized labels")
    if isinstance(multiplicity_ratio, bool) or not isinstance(multiplicity_ratio, (int, float, Fraction)):
        raise ValueError("multiplicity_ratio must be finite and greater than 1")
    try:
        ratio = Fraction(str(multiplicity_ratio))
    except (ValueError, OverflowError):
        raise ValueError("multiplicity_ratio must be finite and greater than 1") from None
    if ratio <= 1:
        raise ValueError("multiplicity_ratio must be finite and greater than 1")
    if mode == "exact" and ratio != 2:
        raise ValueError("multiplicity_ratio only applies to mode='template'")
    if rhs not in ("sign", "nonzero"):
        raise ValueError("rhs must be 'sign' or 'nonzero'")
    if objective != "sign":
        raise ValueError("objective must be 'sign'")
    if isinstance(memory_mb, bool) or not isinstance(memory_mb, (int, float)) or not math.isfinite(memory_mb) or memory_mb <= 0:
        raise ValueError("memory_mb must be finite and positive")
    # Fixed interning chunks and SQLite require a small minimum workspace.
    if memory_mb * 1024**2 < 16384:
        raise ValueError("memory_mb must provide at least 16 KiB")
    return _json({"mode": mode, "coefficient": expected, "rhs": rhs, "objective": objective,
                  "multiplicity_ratio": str(ratio) if mode == "template" else None,
                  "equality": "dual_zero_rhs", "algorithm": "2.0"})


def number_class(value, policy="sign"):
    if value is None:
        return "infinite"
    if not value:
        return "zero"
    if policy == "nonzero":
        return "nonzero"
    return "positive" if value > 0 else "negative"


class RefinementStore:
    def __init__(self, owner, memory_mb):
        self.owner = owner
        self.temp = TemporaryDirectory(prefix="mipframe-refinement-")
        self.arrays = {}
        self.maps = []
        self.serial = 0
        self.role_cache = OrderedDict()
        self.value_cache = OrderedDict()
        self.intern_cache = OrderedDict()
        self.counts = {}
        self.family_stores = WeakSet()
        self.db = None
        try:
            self.db = sqlite3.connect(owner.model.path / "refinement.sqlite")
            self.db.execute("PRAGMA temp_store=FILE")
            self.set_memory(memory_mb)
            expected = _json([owner.uid, owner.model.manifest["model_version"], VERSION])
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                old = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
                if old and old[0] != expected:
                    raise MIPFrameError("Refinement sidecar version/model mismatch; rebuild in a new artifact directory")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS configs(id INTEGER PRIMARY KEY,key TEXT UNIQUE,depth INTEGER);
                CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY,value TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS row_data(config INTEGER,id INTEGER,base TEXT,
                    scale TEXT,lo TEXT,hi TEXT,dual INTEGER,PRIMARY KEY(config,id));
                CREATE TABLE IF NOT EXISTS var_data(config INTEGER,id INTEGER,base TEXT,PRIMARY KEY(config,id));
                CREATE TABLE IF NOT EXISTS definitions(config INTEGER,kind TEXT,depth INTEGER,
                    color INTEGER,header TEXT,digest BLOB,n_tokens INTEGER,
                    PRIMARY KEY(config,kind,depth,color));
                CREATE INDEX IF NOT EXISTS definition_hash ON definitions(config,kind,depth,digest,header);
                CREATE TABLE IF NOT EXISTS key_chunks(config INTEGER,kind TEXT,depth INTEGER,
                    color INTEGER,part INTEGER,data BLOB,PRIMARY KEY(config,kind,depth,color,part));
                CREATE TABLE IF NOT EXISTS arrays(config INTEGER,kind TEXT,depth INTEGER,
                    part INTEGER,start INTEGER,n INTEGER,dtype TEXT,data BLOB,
                    PRIMARY KEY(config,kind,depth,part));
                CREATE TEMP TABLE candidate(part INTEGER PRIMARY KEY,data BLOB);
                CREATE TEMP TABLE scratch(a INTEGER,b INTEGER);
                CREATE INDEX scratch_tokens ON scratch(a,b);
                CREATE TABLE IF NOT EXISTS template_ranges(config INTEGER,kind TEXT,depth INTEGER,
                    color INTEGER,a INTEGER,b INTEGER,lo INTEGER,hi INTEGER,
                    PRIMARY KEY(config,kind,depth,color,a,b));
                CREATE TEMP TABLE template_nodes(id INTEGER PRIMARY KEY,coarse INTEGER,color INTEGER);
                CREATE TEMP TABLE template_tokens(id INTEGER,a INTEGER,b INTEGER,n INTEGER,
                    PRIMARY KEY(id,a,b));
                CREATE TEMP TABLE template_modes(coarse INTEGER,a INTEGER,b INTEGER,n INTEGER,mode INTEGER,
                    PRIMARY KEY(coarse,a,b,n));
            """)
            old = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
            if not old:
                self.db.execute("INSERT INTO meta VALUES('source',?)", (expected,))
                self.db.commit()
            from mipframe.store.lifecycle import register
            register(owner, self)
            self.dtype = np.dtype("<i4" if max(2 * len(owner.model.rows), len(owner.model.variables)) <= np.iinfo(np.int32).max else "<i8")
        except BaseException:
            self.close()
            raise

    def set_memory(self, memory_mb):
        self.memory = int(memory_mb * 1024**2)
        self.block = max(1, min(65536, self.memory // 128))
        if self.db is not None:
            kb = max(1, min(2048, self.memory // 4096))
            self.db.execute(f"PRAGMA cache_size=-{kb}")
            self.db.execute(f"PRAGMA temp.cache_size=-{kb}")
        # Numeric caches stay bounded even under small caller budgets.
        self.cache_limit = max(1, min(1024, self.memory // 4096))
        self.role_cache.clear()
        self.value_cache.clear()
        self.intern_cache.clear()

    def guard(self):
        self.owner.model
        if self.db is None:
            raise MIPFrameError("Refinement store is closed")

    def _close_connection(self):
        from mipframe.store.lifecycle import release_all, close_connection
        actions = [store.db.close for store in self.family_stores]
        actions.append(lambda: close_connection(self))
        actions.extend(array._mmap.close for array in self.maps if not array._mmap.closed)
        release_all(actions)
        self.maps.clear()
        self.arrays.clear()
        self.intern_cache.clear()

    def close(self):
        from mipframe.store.lifecycle import release_all, cleanup_directory
        temp, self.temp = self.temp, None
        release_all((self._close_connection, lambda: cleanup_directory(temp)))

    def _work(self, size, dtype=None):
        self.serial += 1
        if not size:
            return np.empty(0, dtype=dtype or self.dtype)
        path = Path(self.temp.name) / f"array-{self.serial}.bin"
        result = np.memmap(path, mode="w+", dtype=dtype or self.dtype, shape=(size,))
        self.maps.append(result)
        return result

    def _write_array(self, config, kind, depth, array):
        for part, start in enumerate(range(0, len(array), self.block)):
            values = array[start:start + self.block]
            self.db.execute("INSERT INTO arrays VALUES(?,?,?,?,?,?,?,?)",
                            (config, kind, depth, part, start, len(values), array.dtype.str, values.tobytes()))

    def array(self, config, kind, depth):
        self.guard()
        key = config, kind, depth
        if key not in self.arrays:
            m = self.owner.model
            size = len(m.col_idx) if kind == "edge" else len(m.variables) if kind == "var" else len(m.rows)
            dtype = np.dtype("<i8") if kind == "edge" else self.dtype
            result = self._work(size, dtype)
            seen = 0
            for start, n, stored_dtype, data in self.db.execute(
                "SELECT start,n,dtype,data FROM arrays WHERE config=? AND kind=? AND depth=? ORDER BY part", key,
            ):
                if start != seen or stored_dtype != dtype.str or len(data) != n * dtype.itemsize or start + n > size:
                    raise MIPFrameError("Invalid refinement color array")
                result[start:start+n] = np.frombuffer(data, dtype=dtype)
                seen += n
            if seen != size:
                raise MIPFrameError("Incomplete refinement color array")
            result.flags.writeable = False
            self.arrays[key] = result
        return self.arrays[key]

    def role(self, value):
        """Signed integer label; magnitude IDs identify exact rational values."""
        magnitude = abs(value)
        text = str(magnitude)
        ident = self.role_cache.get(text)
        if ident is None:
            row = self.db.execute("SELECT id FROM roles WHERE value=?", (text,)).fetchone()
            ident = row[0] if row else self.db.execute("INSERT INTO roles(value) VALUES(?)", (text,)).lastrowid
            self.role_cache[text] = ident
            if len(self.role_cache) > self.cache_limit:
                self.role_cache.popitem(last=False)
        else:
            self.role_cache.move_to_end(text)
        return ident if value > 0 else -ident

    def role_value(self, ident):
        self.guard()
        ident = int(ident)
        magnitude = abs(ident)
        if magnitude not in self.value_cache:
            self.value_cache[magnitude] = Fraction(self.db.execute("SELECT value FROM roles WHERE id=?", (magnitude,)).fetchone()[0])
            if len(self.value_cache) > self.cache_limit:
                self.value_cache.popitem(last=False)
        self.value_cache.move_to_end(magnitude)
        return self.value_cache[magnitude] if ident > 0 else -self.value_cache[magnitude]

    def _intern(self, config, kind, depth, header, tokens=()):
        """Compare canonical binary chunks exactly, including on hash collisions."""
        header = _json(header)
        # Most low-degree nodes share a handful of short, identical signatures.
        # Cache their complete keys (not hashes), within the workspace budget.
        tokens = iter(tokens)
        prefix = list(islice(tokens, 11))
        cache_key = None
        if len(prefix) <= 10 and len(header) <= 256:
            data = b''.join(TOKEN.pack(int(a), int(b), int(n)) for a, b, n in prefix)
            cache_key = (config, kind, depth, header, data)
            if cache_key in self.intern_cache:
                self.intern_cache.move_to_end(cache_key)
                return self.intern_cache[cache_key]

        def remember(color):
            if cache_key is not None:
                self.intern_cache[cache_key] = color
                if len(self.intern_cache) > self.cache_limit:
                    self.intern_cache.popitem(last=False)
            return color

        digest = _new_hash()
        digest.update(header.encode())
        self.db.execute("DELETE FROM candidate")
        buffer = bytearray()
        part = total = 0
        for a, b, count in chain(prefix, tokens):
            buffer.extend(TOKEN.pack(int(a), int(b), int(count)))
            total += 1
            if len(buffer) == KEY_CHUNK * TOKEN.size:
                data = bytes(buffer)
                self.db.execute("INSERT INTO candidate VALUES(?,?)", (part, data))
                digest.update(data)
                buffer.clear()
                part += 1
        if buffer:
            data = bytes(buffer)
            self.db.execute("INSERT INTO candidate VALUES(?,?)", (part, data))
            digest.update(data)
        hashed = digest.digest()
        for color, in self.db.execute(
            "SELECT color FROM definitions WHERE config=? AND kind=? AND depth=? AND digest=? AND header=? ORDER BY color",
            (config, kind, depth, hashed, header),
        ):
            saved = self.db.execute("SELECT data FROM key_chunks WHERE config=? AND kind=? AND depth=? AND color=? ORDER BY part",
                                    (config, kind, depth, color))
            candidate = self.db.execute("SELECT data FROM candidate ORDER BY part")
            if all(a == b for a, b in zip_longest(saved, candidate)):
                return remember(color)
        key = config, kind, depth
        if key not in self.counts:
            self.counts[key] = self.db.execute("SELECT count(*) FROM definitions WHERE config=? AND kind=? AND depth=?", key).fetchone()[0]
        color = self.counts[key]
        self.counts[key] += 1
        self.db.execute("INSERT INTO definitions VALUES(?,?,?,?,?,?,?)", (*key, color, header, hashed, total))
        self.db.execute("INSERT INTO key_chunks SELECT ?,?,?,?,part,data FROM candidate", (*key, color))
        return remember(color)

    def tokens(self, config, kind, depth, color):
        self.guard()
        for data, in self.db.execute("SELECT data FROM key_chunks WHERE config=? AND kind=? AND depth=? AND color=? ORDER BY part",
                                     (config, kind, depth, int(color))):
            self.guard()
            yield from TOKEN.iter_unpack(data)

    def token_count(self, config, kind, depth, color):
        self.guard()
        return self.db.execute("SELECT n_tokens FROM definitions WHERE config=? AND kind=? AND depth=? AND color=?",
                               (config, kind, depth, int(color))).fetchone()[0]

    def _aggregate(self, blocks, upper_bound):
        """Canonical RLE with bounded NumPy sorting, or SQLite external sorting."""
        if upper_bound <= min(32, self.memory // 128):
            # NumPy's structured sort setup dominates columns with 2–4 edges.
            counts = {}
            for data in blocks:
                for a, b in data:
                    pair = int(a), int(b)
                    counts[pair] = counts.get(pair, 0) + 1
            for (a, b), n in sorted(counts.items()):
                yield a, b, n
        elif upper_bound <= self.memory // 128:
            pieces = list(blocks)
            if not pieces:
                return
            data = np.concatenate(pieces)
            unique, counts = np.unique(data, axis=0, return_counts=True)
            for pair, count in zip(unique, counts):
                yield int(pair[0]), int(pair[1]), int(count)
        else:
            self.db.execute("DELETE FROM scratch")
            for data in blocks:
                self.db.executemany("INSERT INTO scratch VALUES(?,?)",
                                    ((int(a), int(b)) for a, b in data))
            yield from self.db.execute("SELECT a,b,count(*) FROM scratch GROUP BY a,b ORDER BY a,b")

    def _row_tokens(self, row, edge, old_vars, reverse=False):
        m = self.owner.model
        start, stop = int(m.row_ptr[row]), int(m.row_ptr[row+1])
        def blocks():
            for begin in range(start, stop, self.block):
                end = min(begin + self.block, stop)
                data = np.empty((end-begin, 2), dtype=np.int64)
                data[:, 0] = -edge[begin:end] if reverse else edge[begin:end]
                data[:, 1] = old_vars[m.col_idx[begin:end]]
                yield data
        return self._aggregate(blocks(), stop-start)

    def _var_tokens(self, var, edge, old_pos, old_neg):
        m = self.owner.model
        start, stop = int(m.col_ptr[var]), int(m.col_ptr[var+1])
        def blocks():
            for begin in range(start, stop, self.block):
                positions = m.col_positions[begin:min(begin+self.block, stop)]
                rows = np.searchsorted(m.row_ptr, positions, side="right") - 1
                data = np.empty((len(rows), 2), dtype=np.int64)
                data[:, 0], data[:, 1] = edge[positions], old_pos[rows]
                yield data
                dual = old_neg[rows] >= 0
                if np.any(dual):
                    data = np.empty((int(np.count_nonzero(dual)), 2), dtype=np.int64)
                    data[:, 0], data[:, 1] = -edge[positions[dual]], old_neg[rows[dual]]
                    yield data
        return self._aggregate(blocks(), 2*(stop-start))

    def _prepare(self, config, policy):
        m = self.owner.model
        template = json.loads(self.db.execute("SELECT key FROM configs WHERE id=?", (config,)).fetchone()[0]).get("mode") == "template"
        nr, nv = len(m.rows), len(m.variables)
        arrays = {kind: self._work(nv if kind == "var" else nr) for kind in ("row", "var", "pos", "neg")}
        edge = self._work(len(m.col_idx), np.dtype("<i8"))
        arrays["neg"][:] = -1
        for row, lo_id, hi_id in m.db.execute("SELECT id,lo,hi FROM rows ORDER BY id"):
            lo, hi = m.num(lo_id), m.num(hi_id)
            relation = _sense(lo, hi)
            start, stop = int(m.row_ptr[row]), int(m.row_ptr[row+1])
            scale = Fraction(0)
            for begin in range(start, stop, self.block):
                for ident in np.unique(m.coeff_id[begin:min(begin+self.block, stop)]):
                    scale = max(scale, abs(m.num(int(ident))))
            scale = scale or Fraction(1)
            if relation == ">=":
                scale = -scale
                lo, hi, relation = None, -lo, "<="
            elif relation == "=" and hi < 0:
                scale = -scale
                lo, hi = -hi, -lo
            # Bounds above already have their direction transformed; divide by |scale|.
            lo = None if lo is None else lo / abs(scale)
            hi = None if hi is None else hi / abs(scale)
            rhs_class = [number_class(lo, policy), number_class(hi, policy)] if relation in ("range", "free") else number_class(hi, policy)
            base = [relation, rhs_class]
            dual = relation == "=" and hi == 0
            self.db.execute("INSERT INTO row_data VALUES(?,?,?,?,?,?,?)", (config, row, _json(base), str(scale),
                            None if lo is None else str(lo), None if hi is None else str(hi), int(dual)))
            arrays["row"][row] = self._intern(config, "row", 0, base)
            state = self._intern(config, "state", 0, base)
            arrays["pos"][row] = state
            if dual:
                arrays["neg"][row] = state
            for begin in range(start, stop, self.block):
                end = min(begin+self.block, stop)
                ids, inverse = np.unique(m.coeff_id[begin:end], return_inverse=True)
                def label(ident):
                    value = m.num(int(ident))/scale
                    return self.role(Fraction(1 if value > 0 else -1) if template else value)
                labels = np.array([label(ident) for ident in ids], dtype=np.int64)
                edge[begin:end] = labels[inverse]
        for var, kind, lo, hi, obj in m.db.execute("SELECT id,kind,lo,hi,obj FROM variables ORDER BY id"):
            base = [kind, bound_class(m.num(lo), m.num(hi)), number_class(m.num(obj))]
            self.db.execute("INSERT INTO var_data VALUES(?,?,?)", (config, var, _json(base)))
            arrays["var"][var] = self._intern(config, "var", 0, base)
        self._write_array(config, "edge", -1, edge)
        for kind, array in arrays.items():
            self._write_array(config, kind, 0, array)
        self.db.execute("UPDATE configs SET depth=0 WHERE id=?", (config,))
        return {("edge", -1): edge, **{(kind, 0): array for kind, array in arrays.items()}}

    def _round(self, config, depth):
        settings = json.loads(self.db.execute("SELECT key FROM configs WHERE id=?", (config,)).fetchone()[0])
        if settings.get("mode") == "template":
            return self._template_round(config, depth, Fraction(settings["multiplicity_ratio"]))
        m = self.owner.model
        edge = self.array(config, "edge", -1)
        old_row, old_var, old_pos, old_neg = (self.array(config, kind, depth-1) for kind in ("row", "var", "pos", "neg"))
        arrays = {kind: self._work(len(m.variables) if kind == "var" else len(m.rows)) for kind in ("row", "var", "pos", "neg")}
        arrays["neg"][:] = -1
        for row in range(len(m.rows)):
            pos = self._intern(config, "state", depth, int(old_pos[row]), self._row_tokens(row, edge, old_var))
            arrays["pos"][row] = pos
            states = [pos]
            if old_neg[row] >= 0:
                neg = self._intern(config, "state", depth, int(old_neg[row]), self._row_tokens(row, edge, old_var, True))
                arrays["neg"][row] = neg
                states.append(neg)
            arrays["row"][row] = self._intern(config, "row", depth, [int(old_row[row]), sorted(states)])
        for var in range(len(m.variables)):
            arrays["var"][var] = self._intern(config, "var", depth, int(old_var[var]), self._var_tokens(var, edge, old_pos, old_neg))
        for kind, array in arrays.items():
            self._write_array(config, kind, depth, array)
        self.db.execute("UPDATE configs SET depth=? WHERE id=?", (depth, config))
        return {(kind, depth): array for kind, array in arrays.items()}

    def _template_partition(self, config, kind, depth, nodes, size, ratio):
        """Two external passes: skeletons, then per-role multiplicity modes.

        Tokens store mode numbers, not observed counts. Observed ranges are
        separate metadata and never feed the next refinement round.
        """
        for table in ("template_nodes", "template_tokens", "template_modes"):
            self.db.execute(f"DELETE FROM {table}")
        for ident, old, tokens in nodes:
            self.db.executemany("INSERT INTO template_tokens VALUES(?,?,?,?)",
                                ((ident, a, b, n) for a, b, n in tokens))
            skeleton = self.db.execute("SELECT a,b,1 FROM template_tokens WHERE id=? ORDER BY a,b", (ident,))
            coarse = self._intern(config, kind + "_coarse", depth, int(old), skeleton)
            self.db.execute("INSERT INTO template_nodes VALUES(?,?,NULL)", (ident, coarse))
        previous_role, previous_n, mode = None, None, 0
        counts = self.db.execute("""SELECT DISTINCT s.coarse,t.a,t.b,t.n
            FROM template_tokens t JOIN template_nodes s ON s.id=t.id
            ORDER BY s.coarse,t.a,t.b,t.n""")
        for coarse, a, b, n in counts:
            role = coarse, a, b
            if role != previous_role:
                mode = 1
            elif n * ratio.denominator > previous_n * ratio.numerator:
                mode += 1
            self.db.execute("INSERT INTO template_modes VALUES(?,?,?,?,?)", (*role, n, mode))
            previous_role, previous_n = role, n
        result = self._work(size)
        result[:] = -1
        for ident, coarse in self.db.execute("SELECT id,coarse FROM template_nodes ORDER BY id"):
            tokens = self.db.execute("""SELECT t.a,t.b,m.mode FROM template_tokens t
                JOIN template_modes m ON m.coarse=? AND m.a=t.a AND m.b=t.b AND m.n=t.n
                WHERE t.id=? ORDER BY t.a,t.b""", (coarse, ident))
            color = self._intern(config, kind, depth, coarse, tokens)
            result[ident] = color
            self.db.execute("UPDATE template_nodes SET color=? WHERE id=?", (color, ident))
        self.db.execute("""INSERT INTO template_ranges
            SELECT ?,?,?,s.color,t.a,t.b,min(t.n),max(t.n)
            FROM template_tokens t JOIN template_nodes s ON s.id=t.id
            GROUP BY s.color,t.a,t.b""", (config, kind, depth))
        return result

    def multiplicity_range(self, config, kind, depth, color, a, b):
        self.guard()
        return self.db.execute("""SELECT lo,hi FROM template_ranges
            WHERE config=? AND kind=? AND depth=? AND color=? AND a=? AND b=?""",
            (config, kind, depth, int(color), a, b)).fetchone()

    def _template_round(self, config, depth, ratio):
        m = self.owner.model
        edge = self.array(config, "edge", -1)
        old_row, old_var, old_pos, old_neg = (self.array(config, kind, depth-1)
                                             for kind in ("row", "var", "pos", "neg"))
        def states():
            for row in range(len(m.rows)):
                yield 2*row, old_pos[row], self._row_tokens(row, edge, old_var)
                if old_neg[row] >= 0:
                    yield 2*row+1, old_neg[row], self._row_tokens(row, edge, old_var, True)
        start = len(self.maps)
        state = self._template_partition(config, "state", depth, states(), 2*len(m.rows), ratio)
        arrays = {kind: self._work(len(m.rows)) for kind in ("row", "pos", "neg")}
        arrays["pos"][:] = state[::2]
        arrays["neg"][:] = state[1::2]
        # The intermediate state buffer is not a published color array.
        if len(self.maps) > start and self.maps[start] is state:
            path = Path(state.filename)
            state._mmap.close()
            self.maps.pop(start)
            path.unlink(missing_ok=True)
        for row in range(len(m.rows)):
            colors = [int(arrays["pos"][row])]
            if arrays["neg"][row] >= 0:
                colors.append(int(arrays["neg"][row]))
            arrays["row"][row] = self._intern(config, "row", depth, [int(old_row[row]), sorted(colors)])
        nodes = ((var, old_var[var], self._var_tokens(var, edge, old_pos, old_neg))
                 for var in range(len(m.variables)))
        arrays["var"] = self._template_partition(config, "var", depth, nodes, len(m.variables), ratio)
        for kind, array in arrays.items():
            self._write_array(config, kind, depth, array)
        self.db.execute("UPDATE configs SET depth=? WHERE id=?", (depth, config))
        return {(kind, depth): array for kind, array in arrays.items()}

    def _publish(self, config, arrays):
        for (kind, depth), array in arrays.items():
            array.flags.writeable = False
            self.arrays[config, kind, depth] = array

    def _discard_work(self, start):
        """Release failed-round files while retaining newly loaded prior checkpoints."""
        retained = {id(array) for array in self.arrays.values()}
        keep = self.maps[:start]
        for array in self.maps[start:]:
            if id(array) in retained:
                keep.append(array)
            else:
                path = Path(array.filename)
                array._mmap.close()
                path.unlink(missing_ok=True)
        self.maps = keep

    def compute(self, key, depth):
        self.guard()
        row = self.db.execute("SELECT id,depth FROM configs WHERE key=?", (key,)).fetchone()
        if row is None:
            start = len(self.maps)
            try:
                self.db.execute("BEGIN")
                config = self.db.execute("INSERT INTO configs(key,depth) VALUES(?,-1)", (key,)).lastrowid
                arrays = self._prepare(config, json.loads(key)["rhs"])
                self.db.commit()
            except BaseException:
                self.db.rollback()
                self.counts.clear()
                self.role_cache.clear()
                self.intern_cache.clear()
                self._discard_work(start)
                raise
            self._publish(config, arrays)
            current = 0
        else:
            config, current = row
        for layer in range(current+1, depth+1):
            start = len(self.maps)
            try:
                self.db.execute("BEGIN")
                arrays = self._round(config, layer)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                self.counts.clear()
                self.intern_cache.clear()
                self._discard_work(start)
                raise
            self._publish(config, arrays)
        return config
