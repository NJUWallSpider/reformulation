"""Materialize compiled queries with resumable output and bounded SQLite work."""

from contextlib import closing
import hashlib
import json
import sqlite3
from .numbers import LPIRError
from .semantic import connect
from .budget import Budget, BudgetExceeded
from .results import result_page, assignment_dependency, ASSIGNMENT_VERSION
from .query_sources import attach_sources
from .query_sql import compile_query, install_functions

from .query_catalog import QUERY_VERSION


def execute_query(m, spec, budget=None, offset=0, limit=20):
    with closing(connect(m)) as db:
        return _execute(m, db, spec, budget, offset, limit)


def _execute(m, db, spec, budget, offset, limit):
    sources = attach_sources(m, db, spec)
    indexed_manifest = sources.indexed
    structure_manifest = sources.structure
    error = install_functions(m, db)
    sql, params = compile_query(m, db, spec)
    version = QUERY_VERSION + (
        ASSIGNMENT_VERSION if assignment_dependency(spec) else ""
    )
    if sources.explanation is not None:
        version += ":" + sources.explanation
    if indexed_manifest is not None:
        version += ":" + json.dumps(indexed_manifest, sort_keys=True)
    if structure_manifest is not None:
        version += ":" + json.dumps(structure_manifest, sort_keys=True)
    run = (
        "q2_"
        + hashlib.sha256(
            (
                m.manifest["model_version"] + json.dumps(spec, sort_keys=True) + version
            ).encode()
        ).hexdigest()[:32]
    )
    row = db.execute("SELECT status,cursor FROM runs WHERE id=?", (run,)).fetchone()
    if row and row[0] == "complete":
        out = result_page(m, db, run, offset, limit)
        return out
    cursor = row[1] if row else 0
    if not row:
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
            (
                run,
                "query",
                json.dumps(spec),
                "partial",
                0,
                0,
                json.dumps(
                    {
                        "query_version": QUERY_VERSION,
                        **(
                            {
                                "indexed_source": indexed_manifest,
                                "scope_semantics": "materialized snapshot of published facts; search may be partial",
                            }
                            if indexed_manifest is not None
                            else {}
                        ),
                    }
                ),
            ),
        )
        db.commit()
    b = Budget(**(budget or {}))
    status = "complete"
    if "numerical_terms" in sql:
        db.execute(
            "CREATE TABLE IF NOT EXISTS numerical_terms(row_id INTEGER,variable INTEGER,coefficient TEXT,PRIMARY KEY(row_id,variable))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS numerical_term_progress(id INTEGER PRIMARY KEY,cursor INTEGER)"
        )
        db.execute("INSERT OR IGNORE INTO numerical_term_progress VALUES(0,0)")
        pos = db.execute(
            "SELECT cursor FROM numerical_term_progress WHERE id=0"
        ).fetchone()[0]
        import numpy as np

        try:
            for start in range(pos, len(m.col_idx), 1024):
                b.tick()
                end = min(start + 1024, len(m.col_idx))
                for p in range(start, end):
                    r = int(np.searchsorted(m.row_ptr, p, side="right") - 1)
                    db.execute(
                        "INSERT OR REPLACE INTO numerical_terms VALUES(?,?,?)",
                        (r, int(m.col_idx[p]), str(m.num(int(m.coeff_id[p])))),
                    )
                db.execute(
                    "UPDATE numerical_term_progress SET cursor=? WHERE id=0", (end,)
                )
            db.commit()
        except BudgetExceeded:
            db.commit()
            out = result_page(m, db, run, offset, limit)
            out["summary"] = {"reason": "building paged numerical term relation"}
            return out

    # SQLite temp data spills to disk; interrupt expensive joins/sorts before output too.
    db.set_progress_handler(lambda: int(time_expired(b)), 10000)
    try:
        for (record,) in db.execute(
            f"SELECT rec FROM ({sql}) LIMIT -1 OFFSET ?", (*params, cursor)
        ):
            b.tick()
            b.require(len(record.encode()) * 4)
            db.execute("INSERT INTO results VALUES(?,?,?)", (run, cursor, record))
            cursor += 1
    except BudgetExceeded:
        status = "partial"
    except sqlite3.OperationalError as e:
        if error:
            raise LPIRError(str(error[0])) from e
        if "interrupted" in str(e):
            status = "partial"
        else:
            raise LPIRError(str(e)) from e
    finally:
        db.set_progress_handler(None, 0)
    db.execute(
        "UPDATE runs SET status=?,cursor=?,processed=? WHERE id=?",
        (status, cursor, cursor, run),
    )
    db.commit()
    out = result_page(m, db, run, offset, limit)
    return out


def time_expired(b):
    import time

    return time.monotonic() > b.deadline
