"""Persisted relation handles, dependency checks and result paging."""

import hashlib
import json
from .numbers import LPIRError


ASSIGNMENT_VERSION = "2.0.1"


def assignment_dependency(spec):
    if isinstance(spec, dict):
        if spec.get("op") == "scan" and spec.get("table") in (
            "assignment_members",
            "assignment_edges",
        ):
            return True
        return any(
            assignment_dependency(spec.get(key)) for key in ("input", "left", "right")
        )
    return False


def require_current_assignments(db):
    current = db.execute(
        "SELECT status FROM runs WHERE kind='assignments' AND json_extract(spec,'$.version')=?",
        (ASSIGNMENT_VERSION,),
    ).fetchone()
    if current is None:
        if db.execute("SELECT 1 FROM runs WHERE kind='assignments' LIMIT 1").fetchone():
            raise LPIRError(
                "Obsolete assignment analysis; rerun analyze('assignments') before reading bindings"
            )
        raise LPIRError(
            "Assignment analysis has not run; run analyze('assignments') first"
        )
    if current[0] != "complete":
        raise LPIRError(
            "Assignment discovery is incomplete; resume analyze('assignments') before querying its full relations"
        )


def result_page(m, db, run, offset=0, limit=20, _seen=None):
    seen = set() if _seen is None else set(_seen)
    if run in seen or len(seen) > 32:
        raise LPIRError("Cyclic/deep relation dependency")
    seen.add(run)
    if offset < 0 or not 1 <= limit <= 1000:
        raise LPIRError("offset >= 0 and 1 <= limit <= 1000 required")
    row = db.execute(
        "SELECT kind,status,cursor,processed,summary,spec FROM runs WHERE id=?", (run,)
    ).fetchone()
    if row is None:
        raise LPIRError("Unknown or foreign result handle")
    spec = json.loads(row[5])
    summary = json.loads(row[4])

    def dependencies(node):
        if isinstance(node, dict):
            if set(node) == {"literal"}:
                return
            if node.get("op") == "result":
                yield node["handle"]
            if node.get("input_handle"):
                yield node["input_handle"]
            for h in node.get("dependencies", []):
                yield h
            for v in node.values():
                yield from dependencies(v)
        elif isinstance(node, list):
            for v in node:
                yield from dependencies(v)

    for dependency in set(dependencies(spec)):
        parent = result_page(m, db, dependency, limit=1, _seen=seen)
        if parent["completeness"] != "complete":
            raise LPIRError("Incomplete source relation; resume its producer")
    if row[0] == "query" and run.startswith("q2_"):
        from .query_catalog import QUERY_VERSION

        if summary.get("query_version") != QUERY_VERSION:
            raise LPIRError("Obsolete query result; repeat its query AST")
    if row[0] == "assignments" and spec.get("version") != ASSIGNMENT_VERSION:
        raise LPIRError(
            "Obsolete assignment result handle; rerun analyze('assignments')"
        )
    if row[0] == "query" and assignment_dependency(spec) and not run.startswith("q2_"):
        expected = (
            "q_"
            + hashlib.sha256(
                (
                    m.manifest["model_version"]
                    + json.dumps(spec, sort_keys=True)
                    + ASSIGNMENT_VERSION
                ).encode()
            ).hexdigest()[:32]
        )
        if run != expected:
            raise LPIRError(
                "Obsolete assignment-dependent query handle; repeat its query AST"
            )
        require_current_assignments(db)
    if row[0] == "query" and run.startswith("q2_") and assignment_dependency(spec):
        require_current_assignments(db)
    if row[0] == "external" and str(spec.get("key", "")).startswith("assignment_"):
        expected = (
            "e_"
            + hashlib.sha256(
                (
                    m.manifest["model_version"] + str(spec["key"]) + ASSIGNMENT_VERSION
                ).encode()
            ).hexdigest()[:32]
        )
        if run != expected:
            raise LPIRError(
                "Obsolete assignment external-coupling handle; repeat the query"
            )
        require_current_assignments(db)
    count = db.execute("SELECT count(*) FROM results WHERE run=?", (run,)).fetchone()[0]
    items = [
        json.loads(r[0])
        for r in db.execute(
            "SELECT record FROM results WHERE run=? ORDER BY ordinal LIMIT ? OFFSET ?",
            (run, limit, offset),
        )
    ]
    complete = row[1] == "complete"
    return {
        "model_version": m.manifest["model_version"],
        "analysis_version": ASSIGNMENT_VERSION
        if row[0] == "assignments"
        or (row[0] == "query" and assignment_dependency(spec))
        else summary.get("query_version", summary.get("version", "2.0")),
        "exactness": "exact",
        "verification": "verified",
        "completeness": row[1],
        "scope": row[0],
        "total": count if complete else None,
        "total_known": complete,
        "materialized_count": count,
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(items) < count or not complete,
        "next_offset": offset + len(items) if offset + len(items) < count else None,
        "handle": run,
        "continuation": None if complete else run,
        "processed": row[3],
        "checked_range": {
            "phase": summary.get(
                "phase", "query output" if row[0] == "query" else row[0]
            ),
            "entities": summary.get("checked_entities"),
            "scope_total": summary.get("scope_total"),
            "work_units": row[3],
            "input_scan_total_known": "scope_total" in summary,
        },
        "exceptions": {"assessed": False},
        "evidence_handle": run,
        "summary": summary,
        "items": items,
        "provenance": {"artifact": "analysis.sqlite", "run": run},
    }
