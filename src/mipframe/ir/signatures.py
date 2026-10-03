"""Public template/exact colors, hierarchy and numerical family observations."""

from collections.abc import Mapping
from collections.abc import Sequence
from fractions import Fraction
from itertools import islice
import json
from math import inf
import sqlite3
from dataclasses import dataclass

import numpy as np

from mipframe.ir.families import FamilyFrame
from mipframe.ir.families import ConstraintFamily
from mipframe.ir.families import _MemberIndex
from mipframe.ir.families import _FamilyLoc
from mipframe.frame.entity import _Frame
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from mipframe.frame.entity import _table
from mipframe.frame.entity import _text
from mipframe.ops.support import DegreeDescription
from mipframe.options import options
from mipframe.ir.refinement_store import RefinementStore
from mipframe.ir.refinement_store import validate


class Structure:
    """
    MIPFRAME HELP BEGIN
    Structure [api.Structure]

    Read-only Structure domain facade.

    Structure [type]

    When to use: Read-only Structure domain facade.

    Returns: Obtain from the corresponding Model analysis or lineage property.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity
    MIPFRAME HELP END
    """
    def __init__(self, owner):
        self.owner = owner

    def indexed(self, *, rows=None, vars=None, signatures=None, memory_mb=128):
        """
        Retrieve prepared indexed results, or discover an uncached custom scope.

        MIPFRAME HELP BEGIN
        Structure.indexed [api.Structure.indexed]

        Retrieve default completed indexed results or discover an explicitly selected custom scope.

        Structure.indexed(*, rows=None, vars=None, signatures=None, memory_mb=128)

        When to use: Retrieve default completed indexed results or discover an explicitly selected custom scope.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.ir.indexing import indexed
        return indexed(self.owner, rows=rows, vars=vars, signatures=signatures, memory_mb=memory_mb)

    def signatures(self, *, depth=2, mode="template", coefficient=None, rhs="sign", objective="sign", memory_mb=128, multiplicity_ratio=2):
        """
        MIPFRAME HELP BEGIN
        Structure.signatures [api.Structure.signatures]

        Compute or reuse structural refinement with explicit mode, depth and numeric policy.

        Structure.signatures(*, depth=2, mode='template', coefficient=None, rhs='sign', objective='sign', memory_mb=128, multiplicity_ratio=2)

        When to use: Compute or reuse structural refinement with explicit mode, depth and numeric policy.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        key = validate(depth, coefficient, rhs, objective, memory_mb, mode, multiplicity_ratio)
        self.owner.model
        store = getattr(self.owner, "_refinement_store", None)
        if store is None:
            store = RefinementStore(self.owner, memory_mb)
            self.owner._refinement_store = store
        else:
            store.set_memory(memory_mb)
        config = store.compute(key, depth)
        return StructuralSignatures(store, config, depth, key)

    refine = signatures

    def summary(self, *, depth=2, max_families=15, max_splits=5, **options):
        """
        MIPFRAME HELP BEGIN
        Structure.summary [api.Structure.summary]

        Read Structure summary.

        Structure.summary(*, depth=2, max_families=15, max_splits=5, **options)

        When to use: Read Structure summary.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.ir.display import validate_summary_limits
        validate_summary_limits(max_families, max_splits)
        return self.signatures(depth=depth, **options).summary(max_families=max_families, max_splits=max_splits)

    def splits(self, *, depth=2, from_depth=None, kind="row", **options):
        """
        MIPFRAME HELP BEGIN
        Structure.splits [api.Structure.splits]

        Read Structure splits.

        Structure.splits(*, depth=2, from_depth=None, kind='row', **options)

        When to use: Read Structure splits.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.ir.display import validate_transition
        start = depth-1 if from_depth is None else from_depth
        validate_transition(start, depth, kind)
        return self.signatures(depth=depth, **options).splits(depth=depth, from_depth=start, kind=kind)


def signature_result(owner, signature, depth, options):
    mode = "template" if signature == "structural" else "exact"
    options = dict(options)
    if options.pop("mode", mode) != mode:
        raise ValueError(f"signature={signature!r} requires mode={mode!r}")
    return owner.structure.signatures(depth=2 if depth is None else depth, mode=mode, **options)


class ColorVector(Sequence):
    """Guarded read-only integer sequence. NumPy conversion makes a safe copy."""
    def __init__(self, result, kind, depth):
        self.result, self.kind, self.depth = result, kind, depth

    @property
    def dtype(self):
        return self.result._store.array(self.result._config, self.kind, self.depth).dtype

    def __len__(self):
        return len(self.result._store.array(self.result._config, self.kind, self.depth))

    def __getitem__(self, index):
        values = self.result._store.array(self.result._config, self.kind, self.depth)
        if isinstance(index, slice):
            result = np.array(values[index], copy=True)
            result.flags.writeable = False
            return result
        return int(values[index])

    def __array__(self, dtype=None, copy=None):
        if copy is False:
            raise ValueError("ColorVector cannot expose a borrowed mmap")
        result = np.array(self.result._store.array(self.result._config, self.kind, self.depth), dtype=dtype, copy=True)
        result.flags.writeable = False
        return result

    def __repr__(self):
        return f"<ColorVector: {len(self):,} {self.kind} colors at depth {self.depth}>"


class ColorLevels(Mapping):
    def __init__(self, result, kind):
        self.result, self.kind = result, kind

    def __len__(self):
        self.result._store.guard()
        return self.result.depth + 1

    def __iter__(self):
        self.result._store.guard()
        return iter(range(self.result.depth + 1))

    def __getitem__(self, depth):
        self.result._check_depth(depth)
        return ColorVector(self.result, self.kind, depth)


class StructuralSignatures:
    """
    MIPFRAME HELP BEGIN
    StructuralSignatures [api.StructuralSignatures]

    Read-only StructuralSignatures domain facade.

    StructuralSignatures [type]

    When to use: Read-only StructuralSignatures domain facade.

    Returns: Obtain from the corresponding Model analysis or lineage property.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity
    MIPFRAME HELP END
    """
    def __init__(self, store, config, depth, key):
        self._store, self._config, self.depth, self._key = store, config, depth, key
        self.row_color = ColorLevels(self, "row")
        self.var_color = ColorLevels(self, "var")
        self._family_stores = {}

    @property
    def config(self):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.config [api.StructuralSignatures.config]

        Read StructuralSignatures config.

        StructuralSignatures.config [property]

        When to use: Read StructuralSignatures config.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        self._store.guard()
        return json.loads(self._key)

    @property
    def mode(self):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.mode [api.StructuralSignatures.mode]

        Read StructuralSignatures mode.

        StructuralSignatures.mode [property]

        When to use: Read StructuralSignatures mode.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        return self.config["mode"]

    def _check_depth(self, depth):
        self._store.guard()
        if type(depth) is not int or not 0 <= depth <= self.depth:
            raise ValueError(f"depth must be an integer between 0 and {self.depth}")

    @property
    def rows(self):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.rows [api.StructuralSignatures.rows]

        Read StructuralSignatures rows.

        StructuralSignatures.rows [property]

        When to use: Read StructuralSignatures rows.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import color_frame
        return color_frame(self,"row")

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.vars [api.StructuralSignatures.vars]

        Read StructuralSignatures vars.

        StructuralSignatures.vars [property]

        When to use: Read StructuralSignatures vars.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import color_frame
        return color_frame(self,"var")

    def _row_families(self, depth=None):
        return structural_families(self, self._store.owner.rows, self.depth if depth is None else depth)

    def _var_families(self, depth=None):
        return structural_families(self, self._store.owner.vars, self.depth if depth is None else depth)

    def row_families(self, depth=None):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.row_families [api.StructuralSignatures.row_families]

        Read StructuralSignatures row_families.

        StructuralSignatures.row_families(depth=None)

        When to use: Read StructuralSignatures row_families.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import family_directory
        return family_directory(self._row_families(depth))

    def var_families(self, depth=None):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.var_families [api.StructuralSignatures.var_families]

        Read StructuralSignatures var_families.

        StructuralSignatures.var_families(depth=None)

        When to use: Read StructuralSignatures var_families.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import family_directory
        return family_directory(self._var_families(depth))

    def family(self, label, *, depth=None):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.family [api.StructuralSignatures.family]

        Read StructuralSignatures family.

        StructuralSignatures.family(label, *, depth=None)

        When to use: Read StructuralSignatures family.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        depth=self.depth if depth is None else depth
        return (self._row_families(depth) if label.startswith('F') else self._var_families(depth)).loc[label]

    def summary(self, depth=None, *, max_families=15, max_splits=5):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.summary [api.StructuralSignatures.summary]

        Read StructuralSignatures summary.

        StructuralSignatures.summary(depth=None, *, max_families=15, max_splits=5)

        When to use: Read StructuralSignatures summary.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.ir.display import StructuralSummary
        depth = self.depth if depth is None else depth
        self._check_depth(depth)
        return StructuralSummary(self, depth, max_families=max_families, max_splits=max_splits)

    def splits(self, *, depth=None, from_depth=None, kind="row"):
        """
        MIPFRAME HELP BEGIN
        StructuralSignatures.splits [api.StructuralSignatures.splits]

        Read StructuralSignatures splits.

        StructuralSignatures.splits(*, depth=None, from_depth=None, kind='row')

        When to use: Read StructuralSignatures splits.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.ir.display import RefinementDelta
        depth = self.depth if depth is None else depth
        return RefinementDelta(self, depth-1 if from_depth is None else from_depth, depth, kind=kind)

    def __repr__(self):
        self._store.guard()
        return f"<StructuralSignatures: {self.mode}, depth 0..{self.depth}, {len(self.rows):,} rows, {len(self.vars):,} vars>"

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


@dataclass(frozen=True)
class Multiplicity:
    """Observed range for a template mode; only mode participates in its key."""
    mode: int
    min: int
    max: int

    def __repr__(self):
        return str(self.min) if self.min == self.max else f"varying[{self.min},{self.max}]"


class RefinementPattern(Sequence):
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


class OrientationPatterns(tuple):
    def __repr__(self):
        return "orientations(" + " | ".join(repr(pattern) for pattern in self) + ")"


class StructuralSignature:
    """
    MIPFRAME HELP BEGIN
    StructuralSignature [api.StructuralSignature]

    One entity role in a bound structural refinement configuration.

    StructuralSignature [type]

    When to use: One entity role in a bound structural refinement configuration.

    Does not establish: A signature, name or draft identity does not establish a model equivalence.

    See also: concept.frame, concept.identity
    MIPFRAME HELP END
    """
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
            return OrientationPatterns()
        if self.kind == "var":
            return OrientationPatterns([RefinementPattern(self.result, "var", self.depth, self.color)])
        states = sorted({int(store.array(self.result._config, kind, self.depth)[self.ident]) for kind in ("pos", "neg")} - {-1})
        return OrientationPatterns(RefinementPattern(self.result, "state", self.depth, color) for color in states)

    @property
    def pattern(self):
        patterns = self.patterns
        return patterns[0] if len(patterns) == 1 else patterns

    def key(self):
        return json.dumps([self.result._store.owner.model.manifest["model_version"], self.result._key,
                           self.kind, self.depth, self.color], separators=(",", ":"))

    def __eq__(self, other):
        return isinstance(other, StructuralSignature) and self.key() == other.key()

    def __hash__(self):
        return hash(self.key())

    def __repr__(self):
        return (f"StructuralSignature({self.kind}, mode={self.mode}, depth={self.depth}, color={self.color}, "
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
        return fid, StructuralSignature(self.result, self.kind, ident, self.depth), count


class _StructuralLoc(_FamilyLoc):
    def __getitem__(self, label):
        self.frame._owner.model
        if isinstance(label, str) and '@' in label:
            raise KeyError(label)
        if self.frame._store.kind == "var" and isinstance(label, str) and label.startswith("V"):
            self.frame._owner.model
            try:
                color = int(label[1:])
            except ValueError:
                raise KeyError(label) from None
            record = self.frame._store.db.execute("SELECT position FROM families WHERE color=?", (color,)).fetchone()
            if label != f"V{color}" or record is None or record[0] not in self.frame.ids:
                raise KeyError(label)
            return VariableFamily(self.frame._store, record[0])
        original = super().__getitem__(label)
        cls = StructuralConstraintFamily if self.frame._store.kind == "row" else VariableFamily
        return cls(self.frame._store, original._position)


class StructuralFamilyFrame(FamilyFrame):
    columns = ("id", "count", "parent", "relation", "rhs_class", "nnz", "structure")
    _numeric = ("count", "nnz")

    @property
    def loc(self):
        return _StructuralLoc(self)

    def __getitem__(self, position):
        if isinstance(position, str):
            return self.loc[position]
        if isinstance(position, slice):
            return self._select(self._index[position])
        if type(position) is not int:
            raise TypeError("Use an integer position or slice")
        pos = self._index[position]
        family = self.loc[f"F{pos}"]
        sig = family.signature
        record = {"id": family.id, "count": family.count, "depth": sig.depth, "color": sig.color,
                "parent": family.parent, "parent_depth": family.parent_depth,
                "parent_color": sig.parent_color, "base": sig.base,
                "nnz": family.nnz, "pattern": sig.pattern,
                "structure": family.structure, "sign_symmetric": family.sign_symmetric,
                "attributes": family.attributes}
        if self._store.kind == "row":
            record.update(relation=sig.base[0], rhs_class=sig.base[1])
        return record

    def show(self, *, limit=15):
        from mipframe.ir.display import family_table
        from mipframe.frame.row import Formula
        return Formula(family_table(self, limit=limit))

    def __repr__(self):
        return str(self.show())


class VarFamilyFrame(StructuralFamilyFrame):
    columns = ("id", "count", "parent", "type", "bounds", "objective", "row_degree")
    _numeric = ("count", "row_degree")

    def __getitem__(self, position):
        record = super().__getitem__(position)
        if isinstance(position, (str, slice)):
            return record
        kind, bounds, objective = record["base"]
        record.update(type=kind, bounds=bounds, objective={"positive": "+", "negative": "-", "zero": "0"}.get(objective, objective), row_degree=record["nnz"], degree=record["nnz"])
        return record



class _ColorOrder(Sequence):
    """Disk-backed color order for the variable legend; no graph-sized tuple."""
    def __init__(self, store, ordinals=None):
        self.store = store
        self.ordinals = range(store.total) if ordinals is None else ordinals

    def __len__(self):
        self.store.owner.model
        return len(self.ordinals)

    def __getitem__(self, position):
        self.store.owner.model
        if isinstance(position, slice):
            return type(self)(self.store, self.ordinals[position])
        ordinal = self.ordinals[position]
        return self.store.db.execute("SELECT position FROM role_order WHERE ordinal=?", (ordinal,)).fetchone()[0]

    def __iter__(self):
        self.store.owner.model
        if self.ordinals.step == 1:
            for position, in self.store.db.execute("SELECT position FROM role_order WHERE ordinal>=? AND ordinal<? ORDER BY ordinal", (self.ordinals.start, self.ordinals.stop)):
                self.store.owner.model
                yield position
        else:
            for i in range(len(self)):
                yield self[i]

    def __contains__(self, position):
        self.store.owner.model
        row = self.store.db.execute("SELECT ordinal FROM role_order WHERE position=?", (position,)).fetchone()
        if row is None:
            return False
        return row[0] in self.ordinals


def structural_families(result, frame, depth):
    cls = StructuralFamilyFrame if isinstance(frame, RowFrame) else VarFamilyFrame
    result._check_depth(depth)
    kind = "row" if isinstance(frame, RowFrame) else "var"
    size = len(frame._owner.model.rows if kind == "row" else frame._owner.model.variables)
    if isinstance(frame.ids, range) and frame.ids == range(size):
        key = kind, depth
        if key not in result._family_stores:
            result._family_stores[key] = _StructuralFamilyStore(result, frame, depth)
        store = result._family_stores[key]
    else:
        store = _StructuralFamilyStore(result, frame, depth)
    if kind == "var":
        return cls(store, _ColorOrder(store))
    return cls(store)


class _FamilyDetails:
    @property
    def depth(self):
        self._store.owner.model
        return self.signature.depth

    @property
    def parent_depth(self):
        return self.depth - 1 if self.depth else None

    @property
    def parent(self):
        sig = self.signature
        if not sig.depth:
            return None
        if self._store.kind == "var":
            return f"V{sig.parent_color}"
        previous = self._store.result._row_families(sig.depth-1)
        position = previous._store.db.execute("SELECT position FROM families WHERE color=?", (sig.parent_color,)).fetchone()[0]
        return f"F{position}"

    @property
    def structure(self):
        from mipframe.ir.display import structure_text
        return structure_text(self.signature)

    @property
    def sign_symmetric(self):
        sig = self.signature
        return self._store.kind == "row" and sig.base == ("=", "zero")

    @property
    def _parameters(self):
        from mipframe.ir.display import FamilyParameters
        return FamilyParameters(self)

    @property
    def params(self):
        """
        MIPFRAME HELP BEGIN
        StructuralConstraintFamily.params [api.StructuralConstraintFamily.params]

        Inspect observed signed-role coefficient parameters, distinct values and ranges.

        StructuralConstraintFamily.params [property]

        When to use: Inspect observed signed-role coefficient parameters, distinct values and ranges.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import family_parameters
        return family_parameters(self)

    @property
    def coefficients(self):
        return self.params

    def inspect(self, *, verbose=False, samples=3, sample_formula="auto", max_terms=12):
        """
        MIPFRAME HELP BEGIN
        StructuralConstraintFamily.inspect [api.StructuralConstraintFamily.inspect]

        Display bounded family details, including changing numeric roles.

        StructuralConstraintFamily.inspect(*, verbose=False, samples=3, sample_formula='auto', max_terms=12)

        When to use: Display bounded family details, including changing numeric roles.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        from mipframe.ir.display import FamilyInspection
        return FamilyInspection(self, verbose=verbose, samples=samples, sample_formula=sample_formula, max_terms=max_terms)
    @property
    def attributes(self):
        from mipframe.ir.family_attributes import FamilyAttributes
        self._store.owner.model
        if not hasattr(self, "_attributes"):
            self._attributes = FamilyAttributes(self)
        return self._attributes

    @property
    def nnz(self):
        self._store.owner.model
        lo, hi = self._store.db.execute("SELECT nnz_lo,nnz_hi FROM families WHERE fid=?", (self._fid,)).fetchone()
        return lo if lo == hi else (lo, hi)

    def __repr__(self):
        self._store.owner.model
        unit = "rows" if self._store.kind == "row" else "variables"
        return (f'<{"ConstraintFamily" if self._store.kind == "row" else "VariableFamily"} {self.id}: '
                f'{self.count:,} {unit}, depth={self.signature.depth}, mode={self.signature.mode}>\n'
                f'  structure: {self.structure}\n  parent: {self.parent}\n'
                f'  sign_symmetric: {self.sign_symmetric}\n  Use .params or .inspect(); .inspect(verbose=True) for diagnostics.')


class StructuralConstraintFamily(_FamilyDetails, ConstraintFamily):
    """
    MIPFRAME HELP BEGIN
    StructuralConstraintFamily [api.StructuralConstraintFamily]

    Read-only StructuralConstraintFamily domain facade.

    StructuralConstraintFamily [type]

    When to use: Read-only StructuralConstraintFamily domain facade.

    Returns: Obtain from the corresponding Model analysis or lineage property.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity
    MIPFRAME HELP END
    """
    def __init__(self, store, position):
        super().__init__(store, position)
        self.id = f"F{position}"

    @property
    def rhs(self):
        return BoundSeries(self, "hi")

    @property
    def lhs(self):
        return BoundSeries(self, "lo")


class VariableFamily(_FamilyDetails, ConstraintFamily):
    """
    MIPFRAME HELP BEGIN
    VariableFamily [api.VariableFamily]

    Read-only VariableFamily domain facade.

    VariableFamily [type]

    When to use: Read-only VariableFamily domain facade.

    Returns: Obtain from the corresponding Model analysis or lineage property.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity
    MIPFRAME HELP END
    """
    def __init__(self, store, position):
        super().__init__(store, position)
        self.id = f"V{self.signature.color}"

    @property
    def structure(self):
        from mipframe.ir.display import degree_text
        kind, bounds, objective = self.signature.base
        return f"{kind}, {bounds}, objective={objective}, row_degree={degree_text(self.nnz)}"

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        VariableFamily.vars [api.VariableFamily.vars]

        Read VariableFamily vars.

        VariableFamily.vars [property]

        When to use: Read VariableFamily vars.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        frame = VarFrame(self._store.owner, _MemberIndex(self._store, self._fid, range(self.count)))
        frame._selection_total = self.count
        return frame

    @property
    def rows(self):
        raise AttributeError("VariableFamily members use .vars")


class BoundSeries(Sequence):
    """Exact canonical bounds, with external sorting for distribution statistics."""
    def __init__(self, family, column, index=None):
        self.family, self.column = family, column
        self.ids = family.rows.ids if index is None else index

    def __len__(self):
        self.family._store.owner.model
        return len(self.ids)

    def __getitem__(self, position):
        store = self.family._store.result._store
        store.guard()
        if isinstance(position, slice):
            return type(self)(self.family, self.column, self.ids[position])
        value = store.db.execute(f"SELECT {self.column} FROM row_data WHERE config=? AND id=?",
                                (self.family._store.result._config, self.ids[position])).fetchone()[0]
        return Fraction(value) if value is not None else -inf if self.column == "lo" else inf

    def head(self, n=5):
        return self[:_Frame._size(n)]

    def describe(self):
        self.family._store.owner.model
        db = sqlite3.connect("")
        try:
            db.execute("PRAGMA cache_size=-512")
            from mipframe.store.numbers import compare_numeric_text
            db.create_collation("rational", compare_numeric_text)
            db.execute("CREATE TABLE hist(value TEXT COLLATE rational PRIMARY KEY,n INTEGER)")
            count, total = 0, Fraction(0)
            for value in self:
                if isinstance(value, Fraction):
                    count += 1
                    total += value
                    db.execute("INSERT INTO hist VALUES(?,1) ON CONFLICT(value) DO UPDATE SET n=n+1", (str(value),))
            def at(rank):
                seen = 0
                for value, n in db.execute("SELECT value,n FROM hist ORDER BY value"):
                    seen += n
                    if rank < seen:
                        return Fraction(value)
            def quantile(q):
                if not count:
                    return None
                pos = (count-1)*q
                lo = pos.numerator//pos.denominator
                a = at(lo)
                return a+(pos-lo)*(at(min(lo+1, count-1))-a)
            return DegreeDescription({"count": count, "mean": total/count if count else None,
                                      "min": at(0) if count else None, "25%": quantile(Fraction(1,4)),
                                      "median": quantile(Fraction(1,2)), "75%": quantile(Fraction(3,4)),
                                      "max": at(count-1) if count else None})
        finally:
            db.close()

    def __repr__(self):
        return _table(f"<BoundSeries: {len(self):,} normalized {'rhs' if self.column == 'hi' else 'lhs'} values>",
                      ("value",), self.ids, lambda i: {"value": self[i]})

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))
