"""Common record persistence and pagination; analysis-specific policy stays local."""

import json
from .numbers import LPIRError


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def read_checkpoint(db, alias=None):
    """Committed state is authoritative; exported JSON can lag after a crash.

    Alias is a trusted attachment name supplied by the executor.
    """
    table = f"{alias}.meta" if alias else "meta"
    values = {k: json.loads(v) for k, v in db.execute(f"SELECT key,value FROM {table}")}
    model, version = values["model"]
    return {
        "model_version": model,
        "version": version,
        "scope": values.get("scope"),
        "state": values.get("state", {}),
        **({"revision": values["revision"]} if "revision" in values else {}),
    }


def put_record(db, relation, key, value):
    db.execute(
        "INSERT OR REPLACE INTO records VALUES(?,?,?)",
        (relation, str(key), canonical_json(value)),
    )


def check_page(offset, limit):
    if offset < 0 or not 1 <= limit <= 1000:
        raise LPIRError("offset >= 0 and 1 <= limit <= 1000 required")


def record_page(db, relation, key, offset, limit, *, predicate=None):
    """Predicate is trusted internal SQL, never caller input."""
    check_page(offset, limit)
    where, args = "relation=?", [relation]
    if predicate:
        where += " AND " + predicate
    if key is not None:
        where += " AND json_extract(record,'$.id')=?"
        args.append(key)
    total = db.execute("SELECT count(*) FROM records WHERE " + where, args).fetchone()[
        0
    ]
    items = [
        json.loads(row[0])
        for row in db.execute(
            "SELECT record FROM records WHERE "
            + where
            + " ORDER BY key LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
    ]
    return items, total


def page_fields(items, total, offset, limit):
    """Pagination of published records, independent of search completeness."""
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(items) < total,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
    }
