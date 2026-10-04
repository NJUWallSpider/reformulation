"""Private refinement encoding and cached full-model partitions."""
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import islice
import json
import sqlite3
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import _text
from mipframe.options import options
from mipframe.ir.refinement_store import RefinementStore, validate

class _Refinement:
    def __init__(self, store, config, depth, key):
        self._store, self._config, self.depth, self._key = store, config, depth, key
        if not hasattr(store, '_partitions'): store._partitions = {}
        self._family_stores = store._partitions.setdefault(key,{})
    @property
    def config(self):
        self._store.guard()
        return json.loads(self._key)
    @property
    def mode(self):
        return self.config['mode']
    def _check_depth(self, depth):
        self._store.guard()
        if type(depth) is not int or not 0 <= depth <= self.depth:
            raise ValueError(f'depth must be between 0 and {self.depth}')
    def partition(self, kind, depth):
        self._check_depth(depth)
        key = kind, depth
        if key not in self._family_stores:
            entities = self._store.owner.rows if kind == 'row' else self._store.owner.vars
            self._family_stores[key] = _StructuralFamilyStore(self, entities, depth)
        return self._family_stores[key]

def refinement(owner, *, depth=2, mode='template', coefficient=None, rhs='sign', objective='sign', memory_mb=128, multiplicity_ratio=2):
    key = validate(depth, coefficient, rhs, objective, memory_mb, mode, multiplicity_ratio)
    owner.model
    store = getattr(owner, '_refinement_store', None)
    if store is None:
        store = RefinementStore(owner, memory_mb)
        owner._refinement_store = store
    store.set_memory(memory_mb)
    config = store.compute(key, depth)
    if not hasattr(store, '_contexts'): store._contexts = {}
    # Bound depth is immutable; partitions are shared across requested depths.
    context_key = key, depth
    if context_key not in store._contexts:
        store._contexts[context_key] = _Refinement(store, config, depth, key)
    return store._contexts[context_key]

@dataclass(frozen=True)
class Multiplicity:
    """Observed range for a template mode; only mode participates in its key."""
    mode: int
    min: int
    max: int

    def __repr__(self):
        return str(self.min) if self.min == self.max else f"varying[{self.min},{self.max}]"

class _Pattern(Sequence):
    """Lazy counted tokens; row neighbors are V colors, variable neighbors R states."""
    def __init__(self, result, kind, depth, color):
        self.result, self.kind, self.depth, self.color = result, kind, depth, int(color)

    def __len__(self):
        return self.result._store.token_count(self.result._config, self.kind, self.depth, self.color)

    def __iter__(self):
        store = self.result._store
        for coef, color, count in store.tokens(self.result._config, self.kind, self.depth, self.color):
            if self.result.mode == "template":
                lo, hi = store.multiplicity_range(self.result._config, self.kind, self.depth, self.color, coef, color)
                count = Multiplicity(count, lo, hi)
            yield store.role_value(coef), color, count

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return tuple(islice(self, start, stop, step))
        if type(index) is not int:
            raise TypeError("Use an integer position or slice")
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return next(islice(self, index, index+1))

    def __repr__(self):
        label = "V" if self.kind == "state" else "Rstate"
        parts = []
        for coef, color, count in islice(self, options.display.max_cols):
            token = f'({"+" if coef > 0 else ""}{_text(coef)}, {label}{color})'
            if isinstance(count, Multiplicity):
                token += f" × {count!r}"
            elif count != 1:
                token += f" × {count:,}"
            parts.append(token)
        if len(self) > options.display.max_cols:
            parts.append(f"... ({len(self)-options.display.max_cols:,} more tokens)")
        return "[" + ", ".join(parts) + "]"

class _Orientations(tuple):
    def __repr__(self):
        return "orientations(" + " | ".join(repr(pattern) for pattern in self) + ")"

class _Signature:

    numeric_kind = "exact"
    level = "structural"

    def __init__(self, result, kind, ident, depth=None):
        self.result, self.kind, self.ident = result, kind, ident
        self.depth = result.depth if depth is None else depth
        result._check_depth(self.depth)

    @property
    def color(self):
        return int(self.result._store.array(self.result._config, self.kind, self.depth)[self.ident])

    @property
    def mode(self):
        return self.result.mode

    @property
    def parent_color(self):
        self.result._store.guard()
        return None if not self.depth else int(self.result._store.array(self.result._config, self.kind, self.depth-1)[self.ident])

    @property
    def base(self):
        store = self.result._store
        store.guard()
        table = "row_data" if self.kind == "row" else "var_data"
        values = json.loads(store.db.execute(f"SELECT base FROM {table} WHERE config=? AND id=?",
                                             (self.result._config, self.ident)).fetchone()[0])
        return tuple(tuple(value) if isinstance(value, list) else value for value in values)

    @property
    def patterns(self):
        store = self.result._store
        store.guard()
        if not self.depth:
            return _Orientations()
        if self.kind == "var":
            return _Orientations([_Pattern(self.result, "var", self.depth, self.color)])
        states = sorted({int(store.array(self.result._config, kind, self.depth)[self.ident]) for kind in ("pos", "neg")} - {-1})
        return _Orientations(_Pattern(self.result, "state", self.depth, color) for color in states)

    @property
    def pattern(self):
        patterns = self.patterns
        return patterns[0] if len(patterns) == 1 else patterns

    def key(self):
        return json.dumps([self.result._store.owner.model.manifest["model_version"], self.result._key,
                           self.kind, self.depth, self.color], separators=(",", ":"))

    def __eq__(self, other):
        return isinstance(other, _Signature) and self.key() == other.key()

    def __hash__(self):
        return hash(self.key())

    def __repr__(self):
        return (f"_Signature({self.kind}, mode={self.mode}, depth={self.depth}, color={self.color}, "
                f"parent={self.parent_color}, base={self.base}, pattern={self.pattern!r})")

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))

class _StructuralFamilyStore:
    def __init__(self, result, frame, depth):
        result._check_depth(depth)
        self.result, self.owner, self.depth = result, frame._owner, depth
        self.kind = "row" if isinstance(frame, RowFrame) else "var"
        self.db = sqlite3.connect("")
        try:
            self.db.executescript("""
                PRAGMA cache_size=-2048;
                CREATE TABLE families(fid INTEGER PRIMARY KEY,color INTEGER UNIQUE,n INTEGER,first_row INTEGER,position INTEGER UNIQUE,nnz_lo INTEGER,nnz_hi INTEGER);
                CREATE TABLE members(fid INTEGER,ordinal INTEGER,row_id INTEGER,PRIMARY KEY(fid,ordinal),UNIQUE(row_id));
                CREATE TABLE role_order(ordinal INTEGER PRIMARY KEY,position INTEGER UNIQUE);
            """)
            colors = result._store.array(result._config, self.kind, depth)
            m = self.owner.model
            ptr = m.row_ptr if self.kind == "row" else m.col_ptr
            for ident in frame.ids:
                color = int(colors[ident])
                fid = color + 1
                degree = int(ptr[ident+1]-ptr[ident])
                self.db.execute("INSERT OR IGNORE INTO families VALUES(?,?,0,?,NULL,?,?)", (fid, color, ident, degree, degree))
                count = self.db.execute("SELECT n FROM families WHERE fid=?", (fid,)).fetchone()[0]
                cursor = self.db.execute("INSERT OR IGNORE INTO members VALUES(?,?,?)", (fid, count, ident))
                if cursor.rowcount:
                    self.db.execute("UPDATE families SET n=n+1,first_row=min(first_row,?),nnz_lo=min(nnz_lo,?),nnz_hi=max(nnz_hi,?) WHERE fid=?", (ident, degree, degree, fid))
            for pos, (fid,) in enumerate(self.db.execute("SELECT fid FROM families ORDER BY n DESC,first_row")):
                self.db.execute("UPDATE families SET position=? WHERE fid=?", (pos, fid))
            if self.kind == "var":
                for ordinal, (position,) in enumerate(self.db.execute("SELECT position FROM families ORDER BY color")):
                    self.db.execute("INSERT INTO role_order VALUES(?,?)", (ordinal, position))
            self.total = self.db.execute("SELECT count(*) FROM families").fetchone()[0]
            self.db.commit()
            self.db.execute("PRAGMA query_only=ON")
            result._store.family_stores.add(self)
        except BaseException:
            self.db.close()
            raise

    def __del__(self):
        if hasattr(self, "db"):
            self.db.close()

    def record(self, position):
        self.owner.model
        fid, ident, count = self.db.execute("SELECT fid,first_row,n FROM families WHERE position=?", (position,)).fetchone()
        return fid, _Signature(self.result, self.kind, ident, self.depth), count
