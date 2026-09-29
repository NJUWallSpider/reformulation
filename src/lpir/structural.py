"""Resumable structural analysis orchestration and existing module entry points."""

from contextlib import closing
import json
import sqlite3
import time
from .budget import Budget, BudgetExceeded
from .numbers import LPIRError
from .artifact_io import atomic_write
from .structural_store import (
    VERSION as VERSION,
    RELATIONS as RELATIONS,
    js as js,
    ident as ident,
    filename as filename,
    connect as connect,
    meta as meta,
    readmeta as readmeta,
    put as put,
    records as records,
    handle as handle,
    qualify as qualify,
    page as page,
    block_card as block_card,
    save_blocks as save_blocks,
    load_blocks as load_blocks,
    save_family as save_family,
    load_family as load_family,
)
from .structural_algebra import (
    parameter as parameter,
    parameter_value as parameter_value,
    prepare_family as prepare_family,
    fiber as fiber,
    expand as expand,
    check_family as check_family,
)
from .structural_discovery import (
    components as components,
    Discovery as Discovery,
    normalized as normalized,
    draft as draft,
    compile_family as compile_family,
)
from .structural_validation import (
    validate as validate,
    verify as verify,
)
from .structural_summary import (
    formula as formula,
    summary as summary,
    handle_old as handle_old,
)


def scope_rows(m, scope, budget):
    if not scope:
        return list(range(len(m.rows)))
    if not isinstance(scope, dict) or set(scope) != {"rows"}:
        raise LPIRError("Structure scope supports {'rows': row IDs or query}")
    rows = scope["rows"]
    if isinstance(rows, dict):
        from .query import run_query

        result = run_query(m, rows, budget=budget, limit=1000)
        if result["completeness"] != "complete":
            raise LPIRError("Materialize the scope query before structure analysis")
        items = result["items"]
        while result.get("next_offset") is not None:
            result = run_query(
                m, rows, budget=budget, offset=result["next_offset"], limit=1000
            )
            items += result["items"]
        rows = [x["row_id"] for x in items]
    if not isinstance(rows, list) or any(
        type(r) is not int or not 0 <= r < len(m.rows) for r in rows
    ):
        raise LPIRError("Invalid structure rows")
    return sorted(set(rows))


def analyze(m, scope=None, budget=None, handle=None, offset=0, limit=20):
    analysis = ident("structure_scope", scope) if scope else None
    token = ident("structure_run", [m.manifest["model_version"], VERSION, scope])
    if handle is not None and handle != token:
        raise LPIRError("Foreign structure continuation")
    b = Budget(**(budget or {}))
    db = connect(m, analysis, True)
    start = time.monotonic()
    state = readmeta(db, "state", {"phase": "dependencies", "cursor": 0})
    reason = None
    try:
        rows = readmeta(db, "scope_rows")
        if rows is None:
            rows = scope_rows(m, scope, budget)
            meta(db, "scope_rows", rows)
            meta(db, "scope", scope)
        while state["phase"] != "done":
            phase = state["phase"]
            if phase == "dependencies":
                from .indexed_store import connect as indexed_connect

                seed_state = {}
                count = 0
                if (m.path / "indexed.sqlite").exists():
                    with closing(indexed_connect(m, True)) as seeds:
                        saved = seeds.execute(
                            "SELECT value FROM meta WHERE key='state'"
                        ).fetchone()
                        seed_state = json.loads(saved[0]) if saved else {}
                        count = seeds.execute(
                            "SELECT count(*) FROM records WHERE relation='index_tensors'"
                        ).fetchone()[0]
                if seed_state.get("phase") not in ("bundle", "operator", "done"):
                    reason = "coordinate discovery is incomplete; resume analyze('coordinates') first"
                    break
                # Without coordinate seeds there is no supported lifting candidate.
                # Do not materialize giant unrelated rows merely to prove this.
                state = {"phase": "scan" if count else "residuals", "cursor": 0}
            elif phase == "scan":
                while state["cursor"] < len(rows):
                    b.tick()
                    r = rows[state["cursor"]]
                    b.require((int(m.row_ptr[r + 1]) - int(m.row_ptr[r])) * 512)
                    row = {
                        "row_id": r,
                        "terms": [[v, str(c)] for v, c in m.iter_terms(r)],
                        "lo": None
                        if m.rows[r][2] is None
                        else str(m.num(m.rows[r][2])),
                        "hi": None
                        if m.rows[r][3] is None
                        else str(m.num(m.rows[r][3])),
                    }
                    db.execute("INSERT OR REPLACE INTO work VALUES(?,?)", (r, js(row)))
                    state["cursor"] += 1
                state = {"phase": "discover", "cursor": 0}
            elif phase in ("discover", "draft"):
                if phase == "discover":
                    b.tick()
                size = db.execute(
                    "SELECT coalesce(sum(length(record)),0) FROM work"
                ).fetchone()[0]
                b.require(size * 12)
                work = [
                    json.loads(x[0])
                    for x in db.execute("SELECT record FROM work ORDER BY row_id")
                ]
                if phase == "discover":
                    d = Discovery(m, work, b)
                    blocks = d.discover()
                    save_blocks(m, db, blocks)
                    state = {"phase": "analytics", "cursor": 0}
                else:
                    blocks = list(load_blocks(db).values())
                    d = Discovery(m, work, b, blocks)
                    while state["cursor"] < len(work):
                        b.tick()
                        row = work[state["cursor"]]
                        result = draft(row, d)
                        if result:
                            db.execute(
                                "INSERT OR REPLACE INTO drafts VALUES(?,?,?)",
                                (row["row_id"], result["signature"], js(result)),
                            )
                        state["cursor"] += 1
                    state = {"phase": "families", "last": ""}
            elif phase == "analytics":
                from .structural_relations import artifacts

                views, subsets, objectives = artifacts(
                    m, list(load_blocks(db).values()), b
                )
                for relation, items in [
                    ("structure_views", views),
                    ("structure_subsets", subsets),
                    ("structure_objective", objectives),
                ]:
                    for item in items:
                        put(db, relation, item["id"], item)
                meta(
                    db,
                    "objective_constant",
                    str(m.num(m.manifest["objective_constant"])),
                )
                state = {"phase": "draft", "cursor": 0}
            elif phase == "families":
                item = db.execute(
                    "SELECT signature FROM drafts WHERE signature>? ORDER BY signature LIMIT 1",
                    (state["last"],),
                ).fetchone()
                if item is None:
                    state = {"phase": "residuals", "cursor": 0}
                    continue
                b.tick()
                drafts = [
                    json.loads(x[0])
                    for x in db.execute(
                        "SELECT record FROM drafts WHERE signature=? ORDER BY row_id",
                        (item[0],),
                    )
                ]
                b.require(sum(len(js(x)) for x in drafts) * 12)
                blocks = load_blocks(db)
                f = compile_family(drafts, list(blocks.values()), b)
                if f:
                    check_family(m, f, blocks, b)
                    # Original local families remain explicit provenance, not naming heuristics.
                    ep = m.path / "explanations.sqlite"
                    if ep.exists():
                        ex = sqlite3.connect(f"file:{ep.resolve()}?mode=ro", uri=True)
                        for x in f["instances"]:
                            old = ex.execute(
                                "SELECT family FROM instances WHERE row_id=?",
                                (x["row_id"],),
                            ).fetchone()
                            x["source_family"] = old[0] if old else None
                        ex.close()
                    save_family(db, f)
                else:
                    put(
                        db,
                        "structure_evidence",
                        ident("rejected", item[0]),
                        {
                            "id": ident("rejected", item[0]),
                            "rows": [d["row_id"] for d in drafts],
                            "reason": "no unique indexed row domain or no description-length reduction",
                        },
                    )
                state["last"] = item[0]
            else:
                covered = {x["row_id"] for x in records(db, "structure_rows")}
                while state["cursor"] < len(rows):
                    b.tick()
                    r = rows[state["cursor"]]
                    if r not in covered:
                        put(
                            db,
                            "structure_residuals",
                            f"{r:012d}",
                            {"row_id": r, "row": handle_fn_row(r)},
                        )
                    state["cursor"] += 1
                state = {"phase": "done"}
    except BudgetExceeded:
        reason = "budget exhausted; resume with the returned handle (increase memory/time for atomic discovery or family verification)"
    except BaseException:
        db.rollback()
        db.close()
        raise
    try:
        meta(db, "state", state)
        elapsed = readmeta(db, "elapsed_seconds", 0) + time.monotonic() - start
        meta(db, "elapsed_seconds", elapsed)
        revision = readmeta(db, "revision", 0) + 1
        meta(db, "revision", revision)
        db.commit()
    finally:
        db.close()
    manifest = {
        "version": VERSION,
        "model_version": m.manifest["model_version"],
        "state": state,
        "scope": scope,
        "handle": token,
        "revision": revision,
        "elapsed_seconds": elapsed,
    }
    atomic_write(m.path / filename(analysis, ".json"), js(manifest))
    out = page(m, "structure_families", offset=offset, limit=limit, analysis=analysis)
    return {
        **out,
        "handle": token,
        "analysis": analysis,
        "checked_range": state,
        "reason": reason,
        "dependency": {"kind": "coordinates"}
        if state["phase"] == "dependencies"
        else None,
        "continuation": None
        if state["phase"] == "done"
        else {"kind": "structure", "scope": scope, "handle": token},
    }


def handle_fn_row(r):
    return {"table": "terms", "row_id": r}
