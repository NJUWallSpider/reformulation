"""Compile an Edit to an independent, unpublished native numerical snapshot."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import struct
import uuid
from fractions import Fraction
from mipframe.store.model_store import Writer, NumericalStore, content_digest
from mipframe.store.artifacts import dump, sha256
from mipframe.frame.schema import encode
from mipframe.edit.plan import num


def build(edit, p, allow_infeasible):
    from mipframe.model import Model

    temp = TemporaryDirectory(prefix="mipframe-edit-")
    out = Path(temp.name) / "model"
    out.mkdir()
    edit._store.paths.add(Path(temp.name))
    w = None
    target = None
    store = None
    try:
        w = Writer(out, memory_mb=edit.memory_mb)
        source = edit._owner.model
        # Numbers are copied in their canonical representation, without decoding coefficients.
        w.db.execute("DELETE FROM numbers")
        w.db.executemany(
            "INSERT INTO numbers VALUES(?,?,?,?)",
            source.db.execute("SELECT * FROM numbers ORDER BY id"),
        )
        w.nnums = w.db.execute("SELECT coalesce(max(id)+1,0) FROM numbers").fetchone()[
            0
        ]
        w.constant = w.number(p.constant)
        w.sense = p.sense
        for i, ns, old, name, kind, lo, hi, cost, n in p.s.execute(
            f"SELECT * FROM {p.vars} ORDER BY id"
        ):
            w.db.execute(
                "INSERT INTO variables VALUES(?,?,?,?,?,?,?)",
                (
                    i,
                    name,
                    kind,
                    w.number(None if lo == "-inf" else num(lo)),
                    w.number(None if hi == "inf" else num(hi)),
                    w.number(num(cost)),
                    0,
                ),
            )
            w.nvars += 1
            if w.nvars % 256 == 0:
                edit._store.disk_check()
        for i, ns, old, name, lo, hi, n in p.s.execute(
            f"SELECT * FROM {p.rows} ORDER BY id"
        ):
            w.db.execute(
                "INSERT INTO rows VALUES(?,?,?,?,?)",
                (
                    i,
                    name,
                    w.number(None if lo == "-inf" else num(lo)),
                    w.number(None if hi == "inf" else num(hi)),
                    0,
                ),
            )
            w.rows += 1
            if w.rows % 256 == 0:
                edit._store.disk_check()
        ptr = w._memmap(
            out / "row_ptr.npy", mode="w+", dtype="<i8", shape=(w.rows + 1,)
        )
        ptr[:] = 0
        current = 0
        with open(out / "entries.tmp", "wb") as raw:
            for r, v, cid, c, old in p.entries():
                if v is None:
                    raise RuntimeError("Unvalidated removed variable")
                while current <= r:
                    ptr[current] = w.nnz
                    current += 1
                raw.write(
                    struct.pack(
                        "<qq", v, cid if cid is not None else w.number(Fraction(c))
                    )
                )
                w.nnz += 1
                if w.nnz % 1024 == 0:
                    edit._store.disk_check()
            while current <= w.rows:
                ptr[current] = w.nnz
                current += 1
        ptr.flush()
        ptr._mmap.close()
        w.db.execute("DROP TABLE stage")
        w.db.execute(
            "CREATE TABLE declarations(variable INTEGER PRIMARY KEY,kind TEXT)"
        )
        w.db.execute("INSERT INTO declarations SELECT id,kind FROM variables")
        w.db.commit()
        w.arrays()
        w.db.close()
        manifest = dict(
            schema_version=3,
            uid=str(uuid.uuid4()),
            parent=dict(uid=edit._owner.uid, version=edit._owner.version),
            stage="edited",
            format="edit",
            source=None,
            source_sha256=None,
            source_bytes=0,
            build_options=dict(allow_infeasible=allow_infeasible),
            numeric_provenance="canonical exact rational values constructed by Edit; no original text tokens",
            working_memory_mb=edit.memory_mb,
            model_version=content_digest(out, w.sense, w.constant),
            sense=w.sense,
            objective_constant=w.constant,
            variables=w.nvars,
            rows=w.rows,
            nonzeros=w.nnz,
            timings={},
        )
        manifest["base_sha256"] = {
            f.name: sha256(f) for f in out.iterdir() if f.suffix in (".sqlite", ".npy")
        }
        write_lineage(edit, p, out, manifest)
        dump(out / "manifest.json", manifest)
        edit._store.disk_check()
        store = NumericalStore(out)
        target = Model._from_store(store, temp, memory_mb=edit.memory_mb)
        store = None
        edit._store.disk_check()
        return target
    except BaseException:
        if target is not None:
            target.close()
        elif store is not None:
            store.close()
        temp.cleanup()
        raise
    finally:
        edit._store.paths.discard(Path(temp.name))
        if w is not None:
            w.close()


def write_lineage(edit, p, out, manifest):
    from mipframe.check.serialization import events

    db = sqlite3.connect(out / "lineage.sqlite")
    try:
        db.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE proof_events(root INTEGER,n INTEGER,event TEXT,PRIMARY KEY(root,n)); CREATE TABLE row_map(id INTEGER PRIMARY KEY,ns INTEGER,source_id INTEGER); CREATE TABLE var_map(id INTEGER PRIMARY KEY,ns INTEGER,source_id INTEGER);"
        )
        info = dict(
            version=1,
            edit_uid=edit.uid,
            parent=manifest["parent"],
            target=dict(uid=manifest["uid"], version=manifest["model_version"]),
            semantic=p.semantic,
            validation=dict(
                errors=p.errors,
                directly_infeasible=p.infeasible,
                allow_infeasible=manifest["build_options"]["allow_infeasible"],
                scope="complete static checks; general feasibility not established",
            ),
        )
        for k, v in info.items():
            db.execute(
                "INSERT INTO meta VALUES(?,?)", (k, json.dumps(v, default=encode))
            )
        for table in (
            "operations",
            "fields",
            "subjects",
            "proofs",
            "changes",
            "dropped",
            "new_vars",
            "new_rows",
            "new_terms",
            "objective",
            "settings",
            "replaces",
        ):
            sql = edit._store.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            db.execute(sql)
            cursor = edit._store.execute(f"SELECT * FROM {table}")
            marks = ",".join("?" for _ in cursor.description)
            db.executemany(f"INSERT INTO {table} VALUES({marks})", cursor)
        for axis, t in (("row", p.rows), ("var", p.vars)):
            db.executemany(
                f"INSERT INTO {axis}_map VALUES(?,?,?)",
                p.s.execute(f"SELECT id,ns,old FROM {t} ORDER BY id"),
            )
        db.execute(
            "CREATE TABLE validation_findings(id INTEGER PRIMARY KEY,category TEXT,axis TEXT,entity INTEGER,reason TEXT)"
        )
        db.executemany(
            "INSERT INTO validation_findings VALUES(?,?,?,?,?)",
            p.s.execute(f"SELECT * FROM {p.bad} ORDER BY id"),
        )
        for root, report in enumerate(edit._proofs):
            for n, event in enumerate(events(report)):
                db.execute(
                    "INSERT INTO proof_events VALUES(?,?,?)",
                    (root, n, json.dumps(event, default=encode)),
                )
        db.commit()
    finally:
        db.close()
    manifest["edit_artifact"] = dict(
        version=1, file="lineage.sqlite", sha256=sha256(out / "lineage.sqlite")
    )
