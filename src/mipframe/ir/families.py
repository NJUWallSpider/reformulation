"""Private exact signature grouping storage."""
import sqlite3
from collections.abc import Sequence
from mipframe.ir.algebraic import signature_method, signature_decoder
from mipframe.store.lifecycle import finalize_store

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
            from mipframe.store.lifecycle import register
            register(self.owner, self)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        from mipframe.store.lifecycle import close_connection
        close_connection(self)

    _close_connection = close

    def __del__(self):
        finalize_store(self)

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
