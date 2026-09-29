"""Demand-driven exact evidence. No inference about substitutions or fixed variables.

Materialized selections and CSR/CSC cursors live on disk; an interrupted row can
resume at its next nonzero. Only complete exact relations may be used as inputs.
"""

import hashlib
import json
from fractions import Fraction as F

from .budget import Budget, BudgetExceeded
from .results import result_page
from .numbers import LPIRError
from .semantic import connect

VERSION = "1.0"
FACT_OPS = {"terms", "row_activity", "column_incidence"}


def js(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


def require_result(m, db, handle):
    page = result_page(m, db, handle, limit=1)
    if page["completeness"] != "complete":
        raise LPIRError(
            "Incomplete relation cannot be used as a complete input; resume its producer first"
        )
    if page["scope"] == "probe" or page["scope"].startswith("plugin:"):
        raise LPIRError(
            "This operator requires a complete exact relation, not numerical/candidate results"
        )
    return page


def input_relation(m, node, budget):
    from .query import run_query

    if isinstance(node, str):
        node = {"op": "result", "handle": node}
    if not isinstance(node, dict):
        raise LPIRError("Expected a query AST or relation handle")
    return run_query(m, node, budget, limit=1)


def begin(m, db, kind, spec):
    run = (
        "ev_"
        + hashlib.sha256(
            (m.manifest["model_version"] + VERSION + kind + js(spec)).encode()
        ).hexdigest()[:32]
    )
    db.execute(
        "INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?)",
        (run, kind, js(spec), "partial", 0, 0, js({"version": VERSION})),
    )
    db.commit()
    return run


def emit(db, run, ordinal, rec):
    db.execute("INSERT INTO results VALUES(?,?,?)", (run, ordinal, js(rec)))
    return ordinal + 1


def save(db, run, state, ordinal, complete=False, commit=True):
    state["version"] = VERSION
    db.execute(
        "UPDATE runs SET status=?,cursor=?,processed=?,summary=? WHERE id=?",
        (
            "complete" if complete else "partial",
            ordinal,
            state.get("work_done", 0),
            js(state),
            run,
        ),
    )
    if commit:
        db.commit()


def activity_start():
    return {
        "minimum": "0",
        "maximum": "0",
        "positive_coefficients": 0,
        "negative_coefficients": 0,
        "coefficient_min": None,
        "coefficient_max": None,
        "all_lower_nonnegative": True,
        "unbounded_lower_variables": 0,
        "unbounded_upper_variables": 0,
        "empty_box": False,
        "domains": {"B": 0, "I": 0, "C": 0},
        "objective_signs": {"positive": 0, "negative": 0, "zero": 0},
    }


def activity_term(m, state, v, c):
    var = m.variables[v]
    lo, hi, cost = (m.num(var[k]) for k in (3, 4, 5))
    state["positive_coefficients"] += c > 0
    state["negative_coefficients"] += c < 0
    state["domains"][var[2]] += 1
    state["objective_signs"][
        "positive" if cost > 0 else "negative" if cost < 0 else "zero"
    ] += 1
    state["all_lower_nonnegative"] &= lo is not None and lo >= 0
    state["unbounded_lower_variables"] += lo is None
    state["unbounded_upper_variables"] += hi is None
    state["empty_box"] |= lo is not None and hi is not None and lo > hi
    for key, fn in (("coefficient_min", min), ("coefficient_max", max)):
        state[key] = str(c if state[key] is None else fn(F(state[key]), c))
    for key, bound in (
        ("minimum", lo if c > 0 else hi),
        ("maximum", hi if c > 0 else lo),
    ):
        state[key] = (
            None
            if bound is None or state[key] is None
            else str(F(state[key]) + c * bound)
        )


def fact_query(m, spec, budget=None, offset=0, limit=20):
    kind = spec["op"]
    column = kind == "column_incidence"
    scope_key = "variables" if column else "rows"
    field = spec.get("field", "variable" if column else "row_id")
    if set(spec) - {"op", scope_key, "field"}:
        raise LPIRError("Unknown evidence operator fields")
    scope = spec.get(scope_key)
    dependency = None
    if scope is not None and not isinstance(scope, list):
        dependency = input_relation(m, scope, budget)
        if dependency["completeness"] != "complete":
            return {
                **dependency,
                "requested_spec": spec,
                "continuation": {"spec": spec},
                "reason": "input relation is incomplete",
            }
    db = connect(m)
    identity = {**spec, "input_handle": dependency["handle"] if dependency else None}
    run = begin(m, db, "evidence:" + kind, identity)
    saved = db.execute(
        "SELECT status,cursor,summary FROM runs WHERE id=?", (run,)
    ).fetchone()
    if saved[0] == "complete":
        out = result_page(m, db, run, offset, limit)
        db.close()
        return out
    ordinal = saved[1]
    state = json.loads(saved[2])
    b = Budget(**(budget or {}))
    db.execute(
        "CREATE TABLE IF NOT EXISTS evidence_scope(run TEXT,id INTEGER,PRIMARY KEY(run,id))"
    )
    state.setdefault("phase", "scope")
    state.setdefault("scope_cursor", 0)
    state.setdefault("last_id", -1)
    state.setdefault("work_done", 0)
    size = len(m.variables) if column else len(m.rows)
    if isinstance(scope, list) and len(scope) > 1000:
        raise LPIRError("Inline scope limit is 1000; use a relation handle")
    try:
        if state["phase"] == "scope":
            if dependency:
                require_result(m, db, dependency["handle"])
                source = (
                    (i, json.loads(rec).get(field))
                    for i, rec in db.execute(
                        "SELECT ordinal,record FROM results WHERE run=? AND ordinal>=? ORDER BY ordinal",
                        (dependency["handle"], state["scope_cursor"]),
                    )
                )
            else:
                source = enumerate(scope if scope is not None else range(size), start=0)
            for i, x in source:
                if i < state["scope_cursor"]:
                    continue
                b.tick()
                if isinstance(x, bool) or not isinstance(x, int) or not 0 <= x < size:
                    raise LPIRError(f"Invalid {scope_key} identifier: {x!r}")
                db.execute("INSERT OR IGNORE INTO evidence_scope VALUES(?,?)", (run, x))
                state["scope_cursor"] = i + 1
                state["work_done"] += 1
            state["scope_total"] = db.execute(
                "SELECT count(*) FROM evidence_scope WHERE run=?", (run,)
            ).fetchone()[0]
            state["phase"] = "scan"
            state["checked_entities"] = 0
        for (ident,) in db.execute(
            "SELECT id FROM evidence_scope WHERE run=? AND id>? ORDER BY id",
            (run, state["last_id"]),
        ):
            start = int((m.col_ptr if column else m.row_ptr)[ident])
            end = int((m.col_ptr if column else m.row_ptr)[ident + 1])
            position = state.setdefault("position", start)
            if kind == "row_activity":
                state.setdefault("activity", activity_start())
            for p in range(position, end):
                b.tick()
                if column:
                    import numpy as np

                    matrix_pos = int(m.col_positions[p])
                    v = ident
                    r = int(np.searchsorted(m.row_ptr, matrix_pos, side="right") - 1)
                else:
                    matrix_pos = p
                    v = int(m.col_idx[p])
                    r = ident
                c = m.num(int(m.coeff_id[matrix_pos]))
                b.require(
                    1024 + (c.numerator.bit_length() + c.denominator.bit_length()) // 8
                )
                if kind == "row_activity":
                    activity_term(m, state["activity"], v, c)
                else:
                    ordinal = emit(
                        db,
                        run,
                        ordinal,
                        {
                            "row_id": r,
                            "variable": v,
                            "coefficient": str(c),
                            "view_layer": "raw",
                        },
                    )
                state["position"] = p + 1
                state["work_done"] += 1
            # A separate tick bounds work even for empty rows/columns.
            b.tick()
            if kind == "row_activity":
                row = m.rows[ident]
                a = state.pop("activity")
                if a["empty_box"]:
                    a["minimum"] = a["maximum"] = None
                ordinal = emit(
                    db,
                    run,
                    ordinal,
                    {
                        "row_id": ident,
                        "nnz": end - start,
                        "lower": str(m.num(row[2])) if row[2] is not None else None,
                        "upper": str(m.num(row[3])) if row[3] is not None else None,
                        "view_layer": "raw",
                        "basis": "declared variable-bound box; other rows and integrality ignored",
                        "source_line": row[4],
                        **a,
                    },
                )
            state["last_id"] = ident
            state.pop("position", None)
            state["checked_entities"] += 1
        state["phase"] = "complete"
    except BudgetExceeded:
        pass
    except BaseException:
        db.rollback()
        db.close()
        raise
    save(db, run, state, ordinal, state["phase"] == "complete")
    out = result_page(m, db, run, offset, limit)
    db.close()
    return out
