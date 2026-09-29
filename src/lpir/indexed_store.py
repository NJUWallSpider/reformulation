"""Persistence and paged reads for indexed sidecars."""

import hashlib
import json
import sqlite3
from contextlib import closing
from .numbers import LPIRError
from .sidecar import canonical_json as js, put_record as put, record_page, page_fields


VERSION = "1.0"
RELATIONS = (
    "index_sets",
    "index_members",
    "index_degrees",
    "index_partitions",
    "index_tensors",
    "index_coordinates",
    "index_operators",
    "index_rows",
    "index_kernel",
    "index_alignment",
    "index_evidence",
    "index_axes",
)


def ident(prefix, x):
    return prefix + hashlib.sha256(js(x).encode()).hexdigest()[:20]


class ScopedModel:
    """Numerical access is delegated unchanged; only the analysis sidecar differs."""

    def __init__(self, model, analysis):
        if (
            not isinstance(analysis, str)
            or not analysis.startswith("scope_")
            or len(analysis) != 26
            or any(c not in "0123456789abcdef" for c in analysis[6:])
        ):
            raise LPIRError("Invalid indexed analysis identifier")
        self.model = model
        self.analysis = analysis

    def __getattr__(self, name):
        return getattr(self.model, name)


def filename(m, suffix):
    return "indexed" + ("_" + m.analysis if isinstance(m, ScopedModel) else "") + suffix


def qualify(value, analysis):
    if isinstance(value, dict):
        return {
            **{k: qualify(v, analysis) for k, v in value.items()},
            **({"analysis": analysis} if value.get("table") in RELATIONS else {}),
        }
    if isinstance(value, list):
        return [qualify(x, analysis) for x in value]
    return value


def connect(m, readonly=False):
    path = m.path / filename(m, ".sqlite")
    if readonly and not path.exists():
        raise LPIRError('No indexed analysis; run analyze("operators")')
    db = (
        sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        if readonly
        else sqlite3.connect(path)
    )
    db.execute("PRAGMA cache_size=-8192")
    db.execute("PRAGMA temp_store=FILE")
    if not readonly:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS records(relation TEXT,key TEXT,record TEXT,PRIMARY KEY(relation,key));
        CREATE INDEX IF NOT EXISTS records_id ON records(relation,json_extract(record,'$.id'));
        CREATE TABLE IF NOT EXISTS terms(r INTEGER,v INTEGER,c TEXT,kind TEXT,PRIMARY KEY(r,v));
        CREATE INDEX IF NOT EXISTS terms_v ON terms(v,r);
        CREATE TABLE IF NOT EXISTS sets(g TEXT,r INTEGER,PRIMARY KEY(g,r));
        CREATE TABLE IF NOT EXISTS members(g TEXT,r INTEGER,v INTEGER,PRIMARY KEY(g,r,v));
        CREATE INDEX IF NOT EXISTS members_v ON members(g,v,r);
        CREATE TABLE IF NOT EXISTS coords(t TEXT,v INTEGER,a INTEGER,b INTEGER,PRIMARY KEY(t,v));
        CREATE UNIQUE INDEX IF NOT EXISTS coords_cell ON coords(t,a,b);
        CREATE TABLE IF NOT EXISTS bundles(t TEXT,axis INTEGER,p INTEGER,k INTEGER,r INTEGER,scale TEXT,lo TEXT,hi TEXT,PRIMARY KEY(t,axis,r));
        CREATE TABLE IF NOT EXISTS slices(t TEXT,axis INTEGER,r INTEGER,j INTEGER,c TEXT,PRIMARY KEY(t,axis,r,j));
        """)
    old = db.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    if old and json.loads(old[0]) != [m.manifest["model_version"], VERSION]:
        db.close()
        raise LPIRError(
            "Indexed sidecar version mismatch; use a new artifact directory"
        )
    if not old and not readonly:
        db.execute(
            "INSERT INTO meta VALUES('model',?)",
            (js([m.manifest["model_version"], VERSION]),),
        )
        db.commit()
    return db


def get(db, relation, key):
    row = db.execute(
        "SELECT record FROM records WHERE relation=? AND key=?", (relation, str(key))
    ).fetchone()
    if row is None:
        raise LPIRError(f"Unknown {relation}: {key}")
    return json.loads(row[0])


def handle(table, id=None):
    return {"table": table, **({"id": id} if id is not None else {})}


def envelope(m, **kw):
    return dict(
        model_version=m.manifest["model_version"],
        analysis_version="indexed-" + VERSION,
        exactness="exact",
        verification="verified",
        completeness="complete",
        provenance={
            "artifact": "indexed.sqlite",
            "numerical_source": "immutable numerical IR",
        },
        **kw,
    )


def page(m, relation, key=None, offset=0, limit=20):
    if relation not in RELATIONS or offset < 0 or not 1 <= limit <= 1000:
        raise LPIRError("Invalid indexed relation/page")
    with closing(connect(m, True)) as db:
        predicate = None
        if relation == "index_operators":
            predicate = "json_extract(record,'$.verification')='verified'"
        if relation in ("index_rows", "index_kernel", "index_alignment"):
            predicate = "EXISTS(SELECT 1 FROM records p WHERE p.relation='index_operators' AND p.key=json_extract(records.record,'$.id') AND json_extract(p.record,'$.verification')='verified')"
        items, total = record_page(
            db, relation, key, offset, limit, predicate=predicate
        )
        state = json.loads(
            db.execute("SELECT value FROM meta WHERE key='state'").fetchone()[0]
        )
    return envelope(
        m,
        **page_fields(items, total, offset, limit),
        total_known=True,
        scope={"relation": relation, "id": key},
        search_completeness="complete" if state["phase"] == "done" else "partial",
    )


# Existing module consumers also use the record writer.
__all__ = [
    "VERSION",
    "RELATIONS",
    "ident",
    "ScopedModel",
    "filename",
    "qualify",
    "connect",
    "get",
    "handle",
    "envelope",
    "page",
    "js",
    "put",
]
