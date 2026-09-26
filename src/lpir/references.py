"""Artifact-local readable references. Internal identities remain unchanged."""

import re
import sqlite3

PREFIXES = {
    "sblock": "block",
    "smap": "mapping",
    "ssubset": "subset",
    "ssupport": "support",
    "sview": "view",
    "sobjective": "cost",
    "sfamily": "structural",
    "axis": "axis",
    "tensor": "X",
    "operator": "operator",
    "partition": "partition",
    "sets": "sets",
    "coarse": "sets",
    "f": "family",
    "e": "formula",
    "indexed": "analysis",
    "scope": "scope",
    "q2": "result",
    "ev": "evidence",
}
PATTERN = re.compile(
    r"^(smap|ssubset|ssupport|sview|sobjective|sblock|sfamily|axis|tensor|operator|partition|sets|coarse|f|e|indexed|scope|q2|ev)_[0-9a-f]{20,64}$"
)
KEYS = {
    "id",
    "tensor",
    "model_version",
    "family",
    "source_family",
    "handle",
    "analysis",
    "block",
    "input_block",
    "axes",
    "row_axes",
    "source_axis",
    "target_axis",
    "axis",
    "subsets",
    "assignment",
    "evidence_handle",
    "partitions",
    "key",
}


def translate(model, value, *, decode=False, reference=False):
    path = model.path / "references.sqlite"
    if decode and not path.exists():
        return value
    db = (
        sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        if decode
        else sqlite3.connect(path)
    )
    try:
        if not decode:
            db.execute(
                "CREATE TABLE IF NOT EXISTS refs(raw TEXT PRIMARY KEY,alias TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,number INTEGER NOT NULL,UNIQUE(kind,number))"
            )
            db.execute("BEGIN IMMEDIATE")
        cache = {}

        def ref(text):
            if not isinstance(text, str):
                return text
            if text in cache:
                return cache[text]
            if decode:
                row = db.execute(
                    "SELECT raw FROM refs WHERE alias=?", (text,)
                ).fetchone()
                result = row[0] if row else text
            else:
                match = PATTERN.fullmatch(text)
                kind = (
                    "model"
                    if text == model.manifest["model_version"]
                    else PREFIXES[match[1]]
                    if match
                    else None
                )
                if kind is None:
                    return text
                row = db.execute(
                    "SELECT alias FROM refs WHERE raw=?", (text,)
                ).fetchone()
                if row:
                    result = row[0]
                else:
                    number = db.execute(
                        "SELECT coalesce(max(number),0)+1 FROM refs WHERE kind=?",
                        (kind,),
                    ).fetchone()[0]
                    result = kind + str(number)
                    db.execute(
                        "INSERT INTO refs VALUES(?,?,?,?)", (text, result, kind, number)
                    )
            cache[text] = result
            return result

        def walk(x, key=None):
            if isinstance(x, dict):
                if key == 'index_maps':
                    return {ref(k): walk(v) for k,v in x.items()}
                if key == 'maps' and all(isinstance(v, str) for v in x.values()):
                    return {k: ref(v) for k,v in x.items()}
                out = {k: walk(v, k) for k, v in x.items()}
                # Only identifier comparisons resolve literals, never variable names/costs.
                if (
                    decode
                    and x.get("op") in ("eq", "ne")
                    and isinstance(x.get("args"), list)
                ):
                    identifier = any(
                        isinstance(a, dict) and a.get("field") in KEYS
                        for a in x["args"]
                    )
                    if identifier:
                        out["args"] = [
                            {"literal": ref(a["literal"])}
                            if isinstance(a, dict) and set(a) == {"literal"}
                            else walk(a)
                            for a in x["args"]
                        ]
                return out
            if isinstance(x, list):
                return [walk(v, key) for v in x]
            return ref(x) if key in KEYS else x

        result = ref(value) if reference else walk(value)
        if not decode:
            db.commit()
        return result
    finally:
        db.close()
