"""Disk-backed signature grouping with lazy family membership selections."""

import sqlite3
from collections.abc import Sequence

from mipframe.ir.algebraic import signature_decoder
from mipframe.ir.algebraic import signature_method
from mipframe.frame.entity import _Frame
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import _text
from mipframe.frame.entity import _table
from mipframe.options import options


class _FamilyStore:
    def __init__(self, rows, signature):
        method = signature_method(signature)
        self._decode = signature_decoder(signature)
        self.owner = rows._owner
        # SQLite's empty filename creates a private temporary on-disk database.
        self.db = sqlite3.connect("")
        try:
            self.db.executescript("""
                PRAGMA cache_size=-2048;
                CREATE TABLE families(fid INTEGER PRIMARY KEY, signature TEXT UNIQUE,
                                      n INTEGER, first_row INTEGER, position INTEGER UNIQUE);
                CREATE TABLE members(fid INTEGER, ordinal INTEGER, row_id INTEGER,
                                     PRIMARY KEY(fid,ordinal), UNIQUE(row_id));
            """)
            for row_id in rows.ids:
                key = method(self.owner, row_id).key()
                record = self.db.execute(
                    "SELECT fid,n FROM families WHERE signature=?", (key,)
                ).fetchone()
                if record is None:
                    cursor = self.db.execute(
                        "INSERT INTO families(signature,n,first_row) VALUES(?,0,?)", (key, row_id)
                    )
                    fid, count = cursor.lastrowid, 0
                else:
                    fid, count = record
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO members VALUES(?,?,?)", (fid, count, row_id)
                )
                if cursor.rowcount:
                    self.db.execute("UPDATE families SET n=n+1,first_row=min(first_row,?) WHERE fid=?", (row_id, fid))
            # Largest families first; deterministic ties by original row ID.
            ids = self.db.execute("SELECT fid FROM families ORDER BY n DESC,first_row")
            for i, (fid,) in enumerate(ids):
                self.db.execute("UPDATE families SET position=? WHERE fid=?", (i, fid))
            self.total = self.db.execute("SELECT count(*) FROM families").fetchone()[0]
            self.db.commit()
            self.db.execute("PRAGMA query_only=ON")
        except BaseException:
            self.db.close()
            raise

    def __del__(self):
        if hasattr(self, "db"):
            self.db.close()

    def record(self, position):
        self.owner.model
        fid, key, count = self.db.execute(
            "SELECT fid,signature,n FROM families WHERE position=?", (position,)
        ).fetchone()
        return fid, self._decode(key), count


class _MemberIndex(Sequence):
    """Sequence of original row IDs, backed by a family's temporary SQLite table."""

    unique = True

    def __init__(self, store, fid, ordinals):
        self.store, self.fid, self.ordinals = store, fid, ordinals

    def __len__(self):
        self.store.owner.model
        return len(self.ordinals)

    def __getitem__(self, position):
        self.store.owner.model
        if isinstance(position, slice):
            return type(self)(self.store, self.fid, self.ordinals[position])
        ordinal = self.ordinals[position]
        return self.store.db.execute(
            "SELECT row_id FROM members WHERE fid=? AND ordinal=?", (self.fid, ordinal)
        ).fetchone()[0]

    def __iter__(self):
        self.store.owner.model
        if self.ordinals.step == 1:
            cursor = self.store.db.execute(
                "SELECT row_id FROM members WHERE fid=? AND ordinal>=? AND ordinal<? ORDER BY ordinal",
                (self.fid, self.ordinals.start, self.ordinals.stop),
            )
            for row_id, in cursor:
                self.store.owner.model
                yield row_id
        else:
            for position in range(len(self)):
                yield self[position]

    def __contains__(self, row_id):
        self.store.owner.model
        row = self.store.db.execute(
            "SELECT ordinal FROM members WHERE fid=? AND row_id=?", (self.fid, row_id)
        ).fetchone()
        return row is not None and row[0] in self.ordinals


class FamilyFrame(_Frame):
    columns = ("id", "count", "nnz", "sense", "rhs", "pattern")
    _numeric = ("count", "nnz", "rhs")
    _kind, _unit = "family", "families"

    def __init__(self, store, index=None):
        self._store, self._owner = store, store.owner
        self._index = range(store.total) if index is None else index

    @property
    def _total(self):
        self._owner.model
        return self._store.total

    def _select(self, index):
        return type(self)(self._store, index)

    @property
    def loc(self):
        return _FamilyLoc(self)

    def __getitem__(self, position):
        self._owner.model
        if isinstance(position, slice):
            return self._select(self._index[position])
        if type(position) is not int:
            raise TypeError("Use an integer position or slice")
        pos = self._index[position]
        _, sig, count = self._store.record(pos)
        rhs = (sig.lhs, sig.rhs) if sig.sense == "range" else sig.rhs
        return {"id": f"F{pos}", "count": count, "nnz": sig.nnz,
                "sense": sig.sense, "rhs": rhs, "pattern": sig.pattern}

    def __iter__(self):
        for pos in range(len(self)):
            yield self[pos]

    def __repr__(self):
        def display_record(position):
            record = self[position]
            for column in ("count", "nnz"):
                record[column] = f"{record[column]:,}"
            if isinstance(record["rhs"], tuple):
                record["rhs"] = "[" + ", ".join(_text(v) for v in record["rhs"]) + "]"
            return record
        return _table(
            f"<FamilyFrame: {min(len(self), options.display.max_rows):,} of {self._total:,} families>",
            self.columns, self.ids, display_record,
        )


def families(rows, signature="algebratic"):
    rows._owner.model
    return FamilyFrame(_FamilyStore(rows, signature))


class _FamilyLoc:
    def __init__(self, frame):
        self.frame = frame

    def __getitem__(self, label):
        self.frame._owner.model
        if not isinstance(label, str):
            raise TypeError("Family labels must be strings such as 'F3'")
        try:
            position = int(label[1:])
        except ValueError:
            raise KeyError(label) from None
        if label != f"F{position}" or position not in self.frame.ids:
            raise KeyError(label)
        return ConstraintFamily(self.frame._store, position)


class ConstraintFamily:
    """
    MIPFRAME HELP BEGIN
    ConstraintFamily [api.ConstraintFamily]

    Read-only family facade with explicit original members.

    ConstraintFamily [type]

    When to use: Read-only family facade with explicit original members.

    Does not establish: A signature, name or draft identity does not establish a model equivalence.

    See also: concept.frame, concept.identity
    MIPFRAME HELP END
    """
    def __init__(self, store, position):
        self._store, self._position = store, position
        self._fid, self.signature, self.count = store.record(position)
        self.id = f"F{position}"

    @property
    def rows(self):
        """
        MIPFRAME HELP BEGIN
        StructuralConstraintFamily.rows [api.StructuralConstraintFamily.rows]

        Read StructuralConstraintFamily rows.

        StructuralConstraintFamily.rows [property]

        When to use: Read StructuralConstraintFamily rows.

        Returns: Bounded summary, Frame or domain result.

        Does not establish: Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.

        See also: concept.identity, recipe.roles
        MIPFRAME HELP END
        """
        rows = RowFrame(self._store.owner, _MemberIndex(self._store, self._fid, range(self.count)))
        rows._selection_total = self.count
        return rows

    def __repr__(self):
        self._store.owner.model
        sig = self.signature
        fields = [("rows", f"{self.count:,}"), ("nnz / row", f"{sig.nnz:,}"),
                  ("sense", sig.sense)]
        if sig.sense in ("range", "free"):
            fields.append(("normalized lhs", _text(sig.lhs)))
        fields.extend([("normalized rhs", _text(sig.rhs)), ("pattern", repr(sig.pattern))])
        return f'ConstraintFamily("{self.id}")\n' + "\n".join(
            f"  {label:<14}: {value}" for label, value in fields
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))
