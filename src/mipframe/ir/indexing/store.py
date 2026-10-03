"""Versioned, atomically published indexed results for exact indexed views."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from fractions import Fraction
from mipframe.store.query_store import QueryStore
from mipframe.store.numbers import MIPFrameError

VERSION = "3.0"
FILENAME = "indexing.sqlite"


def js(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda x: str(x) if isinstance(x, Fraction) else x,
    )


def ident(prefix, value):
    return prefix + hashlib.sha256(js(value).encode()).hexdigest()[:24]


SCHEMA = """
CREATE TABLE runs(id TEXT PRIMARY KEY, record TEXT);
CREATE TABLE domains(g INTEGER PRIMARY KEY, axis TEXT, digest TEXT, n INTEGER);
CREATE INDEX domain_digest ON domains(axis,digest,n);
CREATE TABLE domain_members(g INTEGER,id INTEGER,PRIMARY KEY(g,id));
CREATE TABLE candidates(g INTEGER PRIMARY KEY,run TEXT,rg INTEGER,vg INTEGER,record TEXT,UNIQUE(run,rg,vg));
CREATE TABLE members(g INTEGER,r INTEGER,v INTEGER,PRIMARY KEY(g,v,r));
CREATE INDEX members_row ON members(g,r,v);
CREATE TABLE cards(run TEXT,kind TEXT,id TEXT,record TEXT,PRIMARY KEY(run,kind,id));
CREATE TABLE coordinates(t TEXT,v INTEGER,i INTEGER,j INTEGER,a INTEGER,b INTEGER,PRIMARY KEY(t,v),UNIQUE(t,a,b));
CREATE TABLE bindings(o TEXT,r INTEGER,p INTEGER,k INTEGER,anchor INTEGER,scale TEXT,lo TEXT,hi TEXT,PRIMARY KEY(o,r),UNIQUE(o,p,k));
CREATE TABLE kernel(o TEXT,p INTEGER,k INTEGER,j INTEGER,value TEXT,PRIMARY KEY(o,p,k,j));
CREATE TABLE alignment(o TEXT,p INTEGER,j INTEGER,PRIMARY KEY(o,p),UNIQUE(o,j));
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
"""


class Store(QueryStore):
    def __init__(self, owner, memory_mb=128):
        super().__init__(owner, memory_mb)
        try:
            self._initialize(owner)
        except BaseException:
            self.close()
            raise

    def _initialize(self, owner):
        # Let Python deliver KeyboardInterrupt during long SQL operations.
        self.db.set_progress_handler(lambda: 0, 10000)
        self.db.executescript(SCHEMA)
        self.db.execute(
            "INSERT INTO meta VALUES(?,?)",
            ("identity", js([VERSION, owner.uid, owner.model.manifest["model_version"]])),
        )
        path = owner.model.path / FILENAME
        if path.exists():
            try:
                with closing(
                    sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                ) as source:
                    identity = source.execute(
                        "SELECT value FROM meta WHERE key='identity'"
                    ).fetchone()
                    if identity is None or json.loads(identity[0]) != [
                        VERSION,
                        owner.uid,
                        owner.model.manifest["model_version"],
                    ]:
                        raise MIPFrameError(
                            "Incompatible indexed result artifact; recompute in a fresh model"
                        )
                    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise MIPFrameError("Corrupt indexed result artifact")
                    self.db.commit()
                    source.backup(self.db)
                expected = self.db.execute(
                    "SELECT value FROM meta WHERE key='content_hash'"
                ).fetchone()
                if expected is None or expected[0] != self.content_hash():
                    raise MIPFrameError(
                        "Corrupt indexed result artifact: content hash mismatch"
                    )
                self._serial = max(
                    (
                        int(x[0][1:])
                        for x in self.db.execute(
                            "SELECT name FROM sqlite_master WHERE name GLOB 'q[0-9]*'"
                        )
                        if x[0][1:].isdigit()
                    ),
                    default=0,
                )
            except (sqlite3.Error, ValueError) as error:
                self.close()
                raise MIPFrameError(
                    f"Invalid indexed result artifact: {error}"
                ) from error

    def discard(self, table):
        try:
            self.db.execute(f"DROP TABLE {table}")
        except sqlite3.OperationalError as error:
            if "locked" not in str(error):
                raise
            if not hasattr(self, "_discarded"):
                self._discarded = set()
            self._discarded.add(table)

    def cleanup(self):
        for table in tuple(getattr(self, "_discarded", ())):
            self.db.execute(f"DROP TABLE {table}")
            self._discarded.remove(table)

    def content_hash(self):
        digest = hashlib.sha256()
        for table in (
            "runs",
            "domains",
            "domain_members",
            "candidates",
            "members",
            "cards",
            "coordinates",
            "bindings",
            "kernel",
            "alignment",
        ):
            digest.update(table.encode())
            for row in self.db.execute(
                f"SELECT * FROM {table} ORDER BY "
                + ",".join(
                    str(i + 1)
                    for i in range(
                        len(
                            self.db.execute(
                                f"SELECT * FROM {table} LIMIT 0"
                            ).description
                        )
                    )
                )
            ):
                digest.update(js(row).encode())
                digest.update(b"\n")
        return digest.hexdigest()

    def publish(self):
        self.cleanup()
        self.db.execute(
            "INSERT OR REPLACE INTO meta VALUES('content_hash',?)",
            (self.content_hash(),),
        )
        self.db.commit()
        path = self.owner.model.path / FILENAME
        fd, name = tempfile.mkstemp(
            prefix=".indexing-", suffix=".sqlite", dir=path.parent
        )
        os.close(fd)
        try:
            with closing(sqlite3.connect(name)) as dest:
                self.db.backup(dest)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def put(self, run, kind, key, record):
        old = self.db.execute(
            "SELECT record FROM cards WHERE run=? AND kind=? AND id=?", (run, kind, key)
        ).fetchone()
        if old:
            previous = json.loads(old[0])
            fields = {
                "partitions": ("rows_domain", "vars_domain"),
                "tensors": ("rows_domains", "vars_domain"),
                "operators": ("tensor", "fixed_axis"),
            }.get(kind)
            same = (
                all(previous[f] == record[f] for f in fields)
                if fields
                else previous == record
            )
            if not same:
                raise MIPFrameError(
                    "Indexed identifier collision; no result was published"
                )
        self.db.execute(
            "INSERT OR REPLACE INTO cards VALUES(?,?,?,?)", (run, kind, key, js(record))
        )

    def get(self, run, kind, key):
        row = self.execute(
            "SELECT record FROM cards WHERE run=? AND kind=? AND id=?", (run, kind, key)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def domain(self, axis, query, args=()):
        # SQL sort/dedup and exact comparison; digest is only a candidate filter.
        table = self.table("id INTEGER PRIMARY KEY")
        self.db.execute(f"INSERT OR IGNORE INTO {table} {query}", args)
        digest = hashlib.sha256()
        n = 0
        for (i,) in self.db.execute(f"SELECT id FROM {table} ORDER BY id"):
            digest.update(f"{i},".encode())
            n += 1
        h = digest.hexdigest()
        for (g,) in self.db.execute(
            "SELECT g FROM domains WHERE axis=? AND digest=? AND n=?", (axis, h, n)
        ):
            if (
                self.db.execute(
                    f"SELECT id FROM {table} EXCEPT SELECT id FROM domain_members WHERE g=? LIMIT 1",
                    (g,),
                ).fetchone()
                is None
            ):
                self.discard(table)
                return g
        g = self.db.execute(
            "INSERT INTO domains(axis,digest,n) VALUES(?,?,?)", (axis, h, n)
        ).lastrowid
        self.db.execute(f"INSERT INTO domain_members SELECT ?,id FROM {table}", (g,))
        self.discard(table)
        return g

    def size(self, g):
        return self.execute("SELECT n FROM domains WHERE g=?", (g,)).fetchone()[0]


def restore(owner):
    if (owner.model.path / FILENAME).exists():
        store = Store(owner)
        owner._indexing_store = store
        from mipframe.ir.indexing.views import IndexedResult

        owner._indexed_results = {
            r: IndexedResult(store, r)
            for (r,) in store.execute("SELECT id FROM runs ORDER BY id")
        }
