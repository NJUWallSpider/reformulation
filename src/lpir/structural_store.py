"""Persistence and paged reads for published structural views."""

from collections import Counter
from contextlib import closing
from itertools import product
import hashlib
import json
import sqlite3
from .numbers import LPIRError
from .sidecar import canonical_json as js, put_record as put, record_page, page_fields

VERSION = "2.0"
RELATIONS = tuple(
    "structure_" + x
    for x in (
        "blocks",
        "coordinates",
        "families",
        "rows",
        "parameters",
        "links",
        "evidence",
        "residuals",
        "views",
        "index_maps",
        "subsets",
        "objective",
    )
)


def ident(prefix, x):
    return prefix + "_" + hashlib.sha256(js(x).encode()).hexdigest()[:20]


def filename(analysis=None, suffix=".sqlite"):
    if analysis is not None and (
        not isinstance(analysis, str)
        or not analysis.startswith("structure_scope_")
        or len(analysis) != 36
        or any(c not in "0123456789abcdef" for c in analysis[16:])
    ):
        raise LPIRError("Invalid structure scope identifier")
    return "structure" + ("_" + analysis if analysis else "") + suffix


def connect(m, analysis=None, write=False):
    path = m.path / filename(analysis)
    if not path.exists() and not write:
        raise LPIRError("No structure analysis; run analyze('structure')")
    db = sqlite3.connect(
        path if write else f"file:{path.resolve()}?mode=ro", uri=not write
    )
    if write:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS records(relation TEXT,key TEXT,record TEXT,PRIMARY KEY(relation,key));
        CREATE INDEX IF NOT EXISTS records_id ON records(relation,json_extract(record,'$.id'));
        CREATE TABLE IF NOT EXISTS work(row_id INTEGER PRIMARY KEY,record TEXT);
        CREATE TABLE IF NOT EXISTS drafts(row_id INTEGER PRIMARY KEY,signature TEXT,record TEXT);
        CREATE INDEX IF NOT EXISTS draft_signature ON drafts(signature);
        """)
    old = db.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    expected = [m.manifest["model_version"], VERSION]
    if old and (
        json.loads(old[0])[0] != expected[0] or json.loads(old[0])[1] != VERSION
    ):
        db.close()
        raise LPIRError(
            "Structure sidecar version mismatch; rebuild in a new directory"
        )
    if write and not old:
        meta(db, "model", expected)
        db.commit()
    return db


def meta(db, k, v):
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, js(v)))


def readmeta(db, k, default=None):
    r = db.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def records(db, rel):
    return [
        json.loads(r[0])
        for r in db.execute(
            "SELECT record FROM records WHERE relation=? ORDER BY key", (rel,)
        )
    ]


def handle(rel, key=None, analysis=None):
    return {
        "table": rel,
        **({"id": key} if key else {}),
        **({"analysis": analysis} if analysis else {}),
    }


def qualify(x, analysis):
    if isinstance(x, dict):
        return {
            **{k: qualify(v, analysis) for k, v in x.items()},
            **(
                {"analysis": analysis}
                if x.get("table") in RELATIONS and analysis
                else {}
            ),
        }
    if isinstance(x, list):
        return [qualify(v, analysis) for v in x]
    return x


def page(m, relation, key=None, offset=0, limit=20, analysis=None):
    if relation not in RELATIONS or offset < 0 or not 1 <= limit <= 1000:
        raise LPIRError("Invalid structure relation/page")
    if isinstance(key, dict):
        analysis, key = key.get("analysis", analysis), key.get("id")
    with closing(connect(m, analysis)) as db:
        items, total = record_page(db, relation, key, offset, limit)
        state = readmeta(db, "state", {})
        version = readmeta(db, "model")[1]
    return qualify(
        {
            **page_fields(items, total, offset, limit),
            "completeness": "complete" if state.get("phase") == "done" else "partial",
            "verification": "verified",
            "exactness": "exact",
            "model_version": m.manifest["model_version"],
            "analysis_version": version,
        },
        analysis,
    )


def block_card(m, b):
    coords = list(b["coords"].values())
    rank = len(b["axes"])
    domains = [sorted({c[a] for c in coords}) for a in range(rank)]
    shape = list(map(len, domains))
    mask = [
        [a, d]
        for a in range(rank)
        for d in range(a + 1, rank)
        if b["axes"][a] == b["axes"][d] and all(c[a] != c[d] for c in coords)
    ]
    # Enumeration is bounded by the discovered block, not an unbounded dense tensor.
    size = 1
    for n in shape:
        size *= n
    exact_mask = False
    if size <= max(4096, 4 * len(coords)):
        exact_mask = set(coords) == {
            x for x in product(*domains) if all(x[a] != x[d] for a, d in mask)
        }
    metadata = Counter()
    for v in b["coords"]:
        x = m.variables[v]
        metadata[
            (
                x[2],
                str(m.num(x[3])) if x[3] is not None else None,
                str(m.num(x[4])) if x[4] is not None else None,
                str(m.num(x[5])),
            )
        ] += 1
    return {
        "id": b["id"],
        "axes": b["axes"],
        "shape": shape,
        "axis_domains": domains,
        "variables": len(coords),
        "domain": {
            "kind": "inequalities" if exact_mask else "explicit",
            "not_equal": mask if exact_mask else [],
            "coordinates": handle("structure_coordinates", b["id"]),
        },
        "metadata": [
            {"kind": k, "lower": lo, "upper": hi, "objective": obj, "count": n}
            for (k, lo, hi, obj), n in metadata.items()
        ],
        "origin": b["origin"],
        "verification": "verified",
    }


def save_blocks(m, db, blocks):
    for b in blocks:
        put(db, "structure_blocks", b["id"], block_card(m, b))
        for v, c in b["coords"].items():
            x = m.variables[v]
            put(
                db,
                "structure_coordinates",
                f"{b['id']}:{v:012d}",
                {
                    "id": b["id"],
                    "variable": v,
                    "coordinates": list(c),
                    "kind": x[2],
                    "lower": None if x[3] is None else str(m.num(x[3])),
                    "upper": None if x[4] is None else str(m.num(x[4])),
                    "objective": str(m.num(x[5])),
                },
            )
        if any(m.num(m.variables[v][5]) for v in b["coords"]):
            put(
                db,
                "structure_links",
                b["id"] + ":objective",
                {"id": b["id"], "block": b["id"], "relation": "objective"},
            )


def load_blocks(db):
    blocks = {b["id"]: {**b, "coords": {}} for b in records(db, "structure_blocks")}
    for c in records(db, "structure_coordinates"):
        blocks[c["id"]]["coords"][c["variable"]] = tuple(c["coordinates"])
    return blocks


def save_family(db, f):
    fid = f["id"]
    card = {k: v for k, v in f.items() if k not in ("instances", "parameters")}
    card["parameters_handle"] = handle("structure_parameters", fid)
    card["rows_handle"] = handle("structure_rows", fid)
    put(db, "structure_families", fid, card)
    for mid, mapping in f.get("index_maps", {}).items():
        put(db, "structure_index_maps", mid, mapping)
    for term in f["ast"]["terms"]:
        for a, values in term.get("restriction", {}).items():
            sid = ident("ssupport", [term["block"], a, values])
            put(
                db,
                "structure_subsets",
                sid,
                {
                    "id": sid,
                    "block": term["block"],
                    "position": int(a),
                    "values": values,
                    "size": len(values),
                    "evidence": "constraint_support",
                    "verification": "verified",
                },
            )
    for name, p in f["parameters"].items():
        put(
            db, "structure_parameters", fid + ":" + name, {"id": fid, "name": name, **p}
        )
    for x in f["instances"]:
        put(db, "structure_rows", f"{fid}:{x['row_id']:012d}", {"id": fid, **x})
    for t in f["ast"]["terms"]:
        put(
            db,
            "structure_links",
            fid + ":" + t["block"] + ":" + t["parameter"],
            {
                "id": fid,
                "family": fid,
                "block": t["block"],
                "fixed": t["fixed"],
                "sum_axes": t["sum_axes"],
                "relation": "definition"
                if f["definition"] and f["definition"]["block"] == t["block"]
                else "input",
            },
        )


def load_family(db, fid):
    row = db.execute(
        "SELECT record FROM records WHERE relation='structure_families' AND key=?",
        (fid,),
    ).fetchone()
    if not row:
        raise LPIRError("Unknown structural family")
    f = json.loads(row[0])
    f["parameters"] = {
        p["name"]: {k: v for k, v in p.items() if k not in ("id", "name")}
        for p in records(db, "structure_parameters")
        if p["id"] == fid
    }
    f["instances"] = [r for r in records(db, "structure_rows") if r["id"] == fid]
    return f
