"""Lazy, disk-backed numerical observations, independent of refinement identity.

Roles always refer to the canonical graph orientation. ``basis`` changes only
observed values. Each zero-RHS equality orientation has its own profile; they
are never summed together. Sparse member/value counts supply implicit zeros.
"""

from collections.abc import Mapping
from collections.abc import Sequence
from fractions import Fraction
from itertools import islice
from math import inf
import sqlite3
import json

import numpy as np

from mipframe.frame.entity import _text
from mipframe.options import options
from mipframe.store.numbers import compare_numeric_text


def _number(text):
    return float(text) if text in ("inf", "-inf") else Fraction(text)


def _compare(a, b):
    return compare_numeric_text(a, b)


class Distribution(Mapping):
    """Exact value -> frequency mapping, streamed from SQLite.

    ``dict(distribution)`` explicitly materializes the complete histogram.
    ``repr`` is bounded, and reports truncation and the complete range.
    """
    def __init__(self, attributes, query, parameters=()):
        self.attributes, self.query, self.parameters = attributes, query, parameters

    def _cursor(self, query=None, parameters=None):
        db = self.attributes._ensure()
        # Counts and lookups need no numeric sort of a high-cardinality profile.
        # Only iteration and explicit extrema request exact numeric ordering.
        query = self.query + ' ORDER BY value COLLATE rational' if query is None else query
        return db.execute(query, self.parameters if parameters is None else parameters)

    def __iter__(self):
        for value, _ in self._cursor():
            self.attributes._guard()
            yield _number(str(value))

    def items(self):
        for value, n in self._cursor():
            self.attributes._guard()
            yield _number(str(value)), n

    def __len__(self):
        return self._cursor(f"SELECT count(*) FROM ({self.query})").fetchone()[0]

    def __getitem__(self, value):
        row = self._cursor(f"SELECT n FROM ({self.query}) WHERE CAST(value AS TEXT) COLLATE rational=?",
                           (*self.parameters, str(value))).fetchone()
        if row is None:
            raise KeyError(value)
        return row[0]

    @property
    def histogram(self):
        return self

    @property
    def count(self):
        return self._cursor(f"SELECT coalesce(sum(n),0) FROM ({self.query})").fetchone()[0]

    @property
    def distinct(self):
        return len(self)

    def _extreme(self, direction):
        row = self._cursor(f"SELECT value FROM ({self.query}) ORDER BY CAST(value AS TEXT) COLLATE rational {direction} LIMIT 1").fetchone()
        return None if row is None else _number(str(row[0]))

    @property
    def min(self):
        return self._extreme("ASC")

    @property
    def max(self):
        return self._extreme("DESC")

    @property
    def constant(self):
        return self.min if len(self) == 1 else None

    @property
    def varying(self):
        return self._cursor(f"SELECT 1 FROM ({self.query}) LIMIT 1 OFFSET 1").fetchone() is not None

    def __repr__(self):
        size = len(self)
        if not size:
            return "empty"
        if size == 1:
            return f"constant({_text(self.min)})"
        values = ", ".join(f"{_text(v)}:{n}" for v, n in islice(self.items(), options.display.max_cols))
        suffix = f", ... ({size} distinct; range=[{_text(self.min)},{_text(self.max)}])" if size > options.display.max_cols else ""
        return "{" + values + suffix + "}"


class RoleProfile:
    def __init__(self, attributes, orientation, sign, neighbor):
        self.attributes, self.orientation = attributes, orientation
        self.sign, self.neighbor = sign, neighbor

    @property
    def neighbor_depth(self):
        self.attributes._guard()
        return max(0, self.attributes.family._store.depth-1)

    @property
    def neighbor_kind(self):
        self.attributes._guard()
        return "V" if self.attributes.family._store.kind == "row" else "Rstate"

    @property
    def neighbor_base(self):
        """The base type/relation behind this model-local neighbor color."""
        fs = self.attributes.family._store
        store, config = fs.result._store, fs.result._config
        kinds = ("var",) if fs.kind == "row" else ("pos", "neg")
        table = "var_data" if fs.kind == "row" else "row_data"
        for kind in kinds:
            colors = store.array(config, kind, self.neighbor_depth)
            for start in range(0, len(colors), store.block):
                match = np.flatnonzero(colors[start:start+store.block] == self.neighbor)
                if len(match):
                    raw = store.db.execute(f"SELECT base FROM {table} WHERE config=? AND id=?", (config, start+int(match[0]))).fetchone()[0]
                    return json.loads(raw)
        raise RuntimeError("Missing neighbor color")

    @property
    def multiplicity_mode(self):
        sig = self.attributes.family.signature
        if sig.mode != "template" or not sig.depth:
            return None
        patterns = sig.patterns
        pattern = patterns[min(self.orientation, len(patterns)-1)] if sig.kind == "row" else patterns[0]
        for sign, neighbor, multiplicity in pattern:
            if sign == self.sign and neighbor == self.neighbor:
                return multiplicity.mode
        return None

    def _multiplicity(self, value=None):
        where = "orientation=? AND sign=? AND neighbor=?"
        params = (self.orientation, self.sign, self.neighbor)
        if value is not None:
            where += " AND value=?"
            params += (str(Fraction(value)),)
        query = f"""WITH present AS (
            SELECT member,sum(n) AS amount FROM terms WHERE {where} GROUP BY member
        ), counts AS (
            SELECT CAST(amount AS TEXT) AS value,count(*) AS n FROM present GROUP BY amount
            UNION ALL
            SELECT '0', (SELECT count(*) FROM populations WHERE orientation=?) - count(*) FROM present
        ) SELECT value,n FROM counts WHERE n>0"""
        return Distribution(self.attributes, query, (*params, self.orientation))

    @property
    def multiplicity(self):
        return self._multiplicity()

    @property
    def coefficients(self):
        return Distribution(self.attributes, """SELECT value,sum(n) AS n FROM terms
            WHERE orientation=? AND sign=? AND neighbor=? GROUP BY value""",
            (self.orientation, self.sign, self.neighbor))

    def coefficient_multiplicity(self, value):
        """Occurrences per member, including members where this value is absent."""
        return self._multiplicity(value)

    def __repr__(self):
        multiplicity = self.multiplicity
        counts = f"varying[{_text(multiplicity.min)},{_text(multiplicity.max)}] {multiplicity!r}" if multiplicity.varying else repr(multiplicity)
        return (f"({'+' if self.sign > 0 else '-'}, {self.neighbor_kind}{self.neighbor}, depth={self.neighbor_depth} "
                f"{self.neighbor_base}): multiplicity={counts}, coefficients={self.coefficients!r}")


class RoleProfiles(Sequence):
    def __init__(self, attributes, orientation):
        self.attributes, self.orientation = attributes, orientation

    def __len__(self):
        return self.attributes._ensure().execute("SELECT count(*) FROM (SELECT DISTINCT sign,neighbor FROM terms WHERE orientation=?)", (self.orientation,)).fetchone()[0]

    def __iter__(self):
        for sign, neighbor in self.attributes._ensure().execute("SELECT DISTINCT sign,neighbor FROM terms WHERE orientation=? ORDER BY sign,neighbor", (self.orientation,)):
            self.attributes._guard()
            yield RoleProfile(self.attributes, self.orientation, sign, neighbor)

    def __getitem__(self, position):
        if isinstance(position, slice):
            return tuple(self[i] for i in range(*position.indices(len(self))))
        if position < 0:
            position += len(self)
        if not 0 <= position < len(self):
            raise IndexError(position)
        return next(islice(self, position, position+1))


class OrientationProfile:
    def __init__(self, attributes, orientation):
        self.attributes, self.orientation = attributes, orientation

    @property
    def count(self):
        return self.attributes._ensure().execute("SELECT count(*) FROM populations WHERE orientation=?", (self.orientation,)).fetchone()[0]

    @property
    def roles(self):
        return RoleProfiles(self.attributes, self.orientation)

    def _bound(self, column):
        return Distribution(self.attributes, f"SELECT {column} AS value,count(*) AS n FROM bounds WHERE orientation=? GROUP BY {column}", (self.orientation,))

    @property
    def lhs(self):
        return self._bound("lo")

    @property
    def rhs(self):
        return self._bound("hi")

    @property
    def objective(self):
        return Distribution(self.attributes, "SELECT objective AS value,count(*) AS n FROM bounds WHERE orientation=? AND objective IS NOT NULL GROUP BY objective", (self.orientation,))


class Sources(Sequence):
    """Trace each observation back to a member and an original numerical row."""
    def __init__(self, attributes):
        self.attributes = attributes

    def __len__(self):
        return self.attributes._ensure().execute("SELECT count(*) FROM sources").fetchone()[0]

    def __iter__(self):
        for o, member, row, factor, scale in self.attributes._ensure().execute("SELECT * FROM sources ORDER BY orientation,member,row"):
            self.attributes._guard()
            yield dict(orientation=o, member=member, row=row, factor=Fraction(factor), normalization_scale=Fraction(scale))

    def __getitem__(self, position):
        if isinstance(position, slice):
            return tuple(self[i] for i in range(*position.indices(len(self))))
        if position < 0:
            position += len(self)
        if not 0 <= position < len(self):
            raise IndexError(position)
        return next(islice(self, position, position+1))


class FamilyAttributes:
    def __init__(self, family, basis="canonical"):
        self._db = None
        if basis not in ("raw", "canonical", "scale_normalized"):
            raise ValueError("basis must be 'raw', 'canonical', or 'scale_normalized'")
        self.family, self.basis, self._db = family, basis, None
        family._store.result._store.attribute_views.add(self)

    def _guard(self):
        self.family._store.owner.model

    def view(self, *, basis="canonical"):
        self._guard()
        return self if basis == self.basis else type(self)(self.family, basis)

    def __del__(self):
        self._close()

    def _close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    def _ensure(self):
        self._guard()
        if self._db is not None:
            return self._db
        db = sqlite3.connect("")
        try:
            db.create_collation("rational", _compare)
            store = self.family._store.result._store
            kb = max(1, min(2048, store.memory // 4096))
            db.execute(f"PRAGMA cache_size=-{kb}")
            db.execute("PRAGMA temp_store=FILE")
            # Observation values are canonical Fraction strings. Binary keys
            # preserve exact equality without numeric comparisons on insertion.
            db.executescript("""
                CREATE TABLE populations(orientation INTEGER,member INTEGER,PRIMARY KEY(orientation,member));
                CREATE TABLE terms(orientation INTEGER,member INTEGER,sign INTEGER,neighbor INTEGER,
                    value TEXT,n INTEGER,PRIMARY KEY(orientation,member,sign,neighbor,value));
                CREATE INDEX term_roles ON terms(orientation,sign,neighbor,value,member);
                CREATE TABLE bounds(orientation INTEGER,member INTEGER,lo TEXT COLLATE rational,
                    hi TEXT COLLATE rational,objective TEXT COLLATE rational,PRIMARY KEY(orientation,member));
                CREATE TABLE sources(orientation INTEGER,member INTEGER,row INTEGER,factor TEXT,scale TEXT,
                    PRIMARY KEY(orientation,member,row));
                CREATE TABLE row_directions(row INTEGER PRIMARY KEY,first INTEGER);
                CREATE TEMP TABLE direction_terms(direction INTEGER,neighbor INTEGER,value TEXT,n INTEGER,
                    PRIMARY KEY(direction,neighbor,value));
            """)
            self._build(db)
            db.commit()
            db.execute("PRAGMA query_only=ON")
        except BaseException:
            db.close()
            raise
        self._db = db
        return db

    def _build(self, db):
        family = self.family
        fs = family._store
        result = fs.result
        store, m, depth = result._store, fs.owner.model, fs.depth
        config = result._config
        neighbor_depth = max(0, depth-1)
        vc = store.array(config, "var", neighbor_depth)
        pos = store.array(config, "pos", depth)
        neg = store.array(config, "neg", depth)
        rp = store.array(config, "pos", neighbor_depth)
        rn = store.array(config, "neg", neighbor_depth)
        members = family.rows.ids if fs.kind == "row" else family.vars.ids

        def row_info(row):
            scale, lo, hi, dual = store.db.execute("SELECT scale,lo,hi,dual FROM row_data WHERE config=? AND id=?", (config, row)).fetchone()
            scale = Fraction(scale)
            directions = [(0, 1)]
            if dual:
                cached = db.execute("SELECT first FROM row_directions WHERE row=?", (row,)).fetchone()
                if cached:
                    first = cached[0]
                else:
                    first = 1 if pos[row] <= neg[row] else -1
                    if pos[row] == neg[row]:
                        # Numeric tie-breaking is *only* for presentation, never
                        # refinement. External aggregation also handles huge rows.
                        db.execute("DELETE FROM direction_terms")
                        for position in range(int(m.row_ptr[row]), int(m.row_ptr[row+1])):
                            value = m.num(int(m.coeff_id[position]))
                            neighbor = int(vc[int(m.col_idx[position])])
                            for direction in (1, -1):
                                db.execute("""INSERT INTO direction_terms VALUES(?,?,?,1)
                                    ON CONFLICT(direction,neighbor,value) DO UPDATE SET n=n+1""",
                                    (direction, neighbor, str(direction*value)))
                        positive = db.execute("SELECT neighbor,value,n FROM direction_terms WHERE direction=1 ORDER BY neighbor,value COLLATE rational")
                        negative = db.execute("SELECT neighbor,value,n FROM direction_terms WHERE direction=-1 ORDER BY neighbor,value COLLATE rational")
                        for a, b in zip(positive, negative):
                            ka, kb = (a[0], Fraction(a[1]), a[2]), (b[0], Fraction(b[1]), b[2])
                            if ka != kb:
                                first = 1 if ka < kb else -1
                                break
                    db.execute("INSERT INTO row_directions VALUES(?,?)", (row, first))
                directions = [(0, first), (1, -first)]
            return scale, lo, hi, directions

        def observe(member, row, positions, info):
            scale, lo, hi, directions = info
            canonical = Fraction(1 if scale > 0 else -1)
            for orientation, reverse in directions:
                factor = Fraction(1) if self.basis == "raw" else reverse/scale if self.basis == "scale_normalized" else reverse*canonical
                db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?)", (orientation, member, row, str(factor), str(scale)))
                db.execute("INSERT OR IGNORE INTO populations VALUES(?,?)", (orientation, member))
                for position in positions():
                    variable = int(m.col_idx[position])
                    value = m.num(int(m.coeff_id[position]))
                    sign = 1 if value*canonical*reverse > 0 else -1
                    neighbor = int(vc[variable]) if fs.kind == "row" else int(rp[row] if reverse == 1 else rn[row])
                    db.execute("""INSERT INTO terms VALUES(?,?,?,?,?,1)
                        ON CONFLICT(orientation,member,sign,neighbor,value) DO UPDATE SET n=n+1""",
                        (orientation, member, sign, neighbor, str(value*factor)))
                if fs.kind == "row":
                    if self.basis == "raw":
                        lo_id, hi_id = m.db.execute("SELECT lo,hi FROM rows WHERE id=?", (row,)).fetchone()
                        lower, upper = m.num(lo_id), m.num(hi_id)
                    else:
                        multiplier = abs(scale) if self.basis == "canonical" else 1
                        lower = None if lo is None else Fraction(lo)*multiplier
                        upper = None if hi is None else Fraction(hi)*multiplier
                    db.execute("INSERT INTO bounds VALUES(?,?,?,?,NULL)", (orientation, member, str(-inf if lower is None else lower), str(inf if upper is None else upper)))

        for member in members:
            db.execute("INSERT INTO populations VALUES(0,?)", (member,))
            if fs.kind == "row":
                begin, end = int(m.row_ptr[member]), int(m.row_ptr[member+1])
                observe(member, member, lambda: range(begin, end), row_info(member))
            else:
                lo_id, hi_id, obj_id = m.db.execute("SELECT lo,hi,obj FROM variables WHERE id=?", (member,)).fetchone()
                lower, upper, objective = m.num(lo_id), m.num(hi_id), m.num(obj_id)
                db.execute("INSERT INTO bounds VALUES(0,?,?,?,?)", (member, str(-inf if lower is None else lower), str(inf if upper is None else upper), str(objective)))
                for offset in range(int(m.col_ptr[member]), int(m.col_ptr[member+1])):
                    position = int(m.col_positions[offset])
                    # CSC contains sparse positions; binary search avoids a
                    # graph-sized Python row lookup table.
                    row = int(np.searchsorted(m.row_ptr, position, side="right"))-1
                    observe(member, row, lambda: (position,), row_info(row))
        if db.execute("SELECT 1 FROM populations WHERE orientation=1 LIMIT 1").fetchone():
            db.execute("INSERT OR IGNORE INTO populations SELECT 1,member FROM populations WHERE orientation=0")
        # Variable bounds do not depend on incident-row orientations.
        if fs.kind == "var":
            db.execute("INSERT INTO bounds SELECT 1,member,lo,hi,objective FROM bounds WHERE orientation=0 AND EXISTS(SELECT 1 FROM populations WHERE orientation=1)")

    @property
    def orientations(self):
        db = self._ensure()
        return tuple(OrientationProfile(self, o) for o, in db.execute("SELECT DISTINCT orientation FROM populations ORDER BY orientation"))

    @property
    def roles(self):
        return RoleProfiles(self, 0)

    @property
    def rhs(self):
        return OrientationProfile(self, 0).rhs

    @property
    def lhs(self):
        return OrientationProfile(self, 0).lhs

    @property
    def objective(self):
        return OrientationProfile(self, 0).objective

    @property
    def sources(self):
        return Sources(self)

    def __repr__(self):
        profiles = []
        for profile in self.orientations:
            roles = "; ".join(repr(role) for role in islice(profile.roles, options.display.max_cols))
            if len(profile.roles) > options.display.max_cols:
                roles += f"; ... ({len(profile.roles)} roles)"
            profiles.append(f"orientation {profile.orientation}: {roles}; lhs={profile.lhs!r}; rhs={profile.rhs!r}")
        return f"{self.basis}[" + " | ".join(profiles) + "]"

    def details(self):
        lines = [f"  attributes ({self.basis}; numeric observations):"]
        for profile in self.orientations:
            lines.append(f"    orientation {profile.orientation}: lhs={profile.lhs!r}; rhs={profile.rhs!r}")
            for role in islice(profile.roles, options.display.max_cols):
                lines.append(f"      {role!r}")
                for value in islice(role.coefficients, options.display.max_cols):
                    lines.append(f"        coefficient {_text(value)} / member: {role.coefficient_multiplicity(value)!r}")
                if len(role.coefficients) > options.display.max_cols:
                    lines.append("        ... (complete per-value counts available through coefficient_multiplicity)")
            if len(profile.roles) > options.display.max_cols:
                lines.append(f"      ... ({len(profile.roles)} roles total)")
        return "\n".join(lines)
