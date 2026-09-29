"""Budgeted, materialized discovery tasks with explicit continuation cursors."""

from collections import defaultdict, deque
from fractions import Fraction as F
import hashlib, json
from .numbers import LPIRError
from .semantic import connect
from .budget import Budget, BudgetExceeded


from .results import (
    ASSIGNMENT_VERSION as ASSIGNMENT_VERSION,
    assignment_dependency as assignment_dependency,
    require_current_assignments as require_current_assignments,
    result_page as result_page,
)


def canonical(m, r, proportional=False):
    terms = list(m.iter_terms(r))
    meta = m.rows[r]
    lo, hi = m.num(meta[2]), m.num(meta[3])
    scale = terms[0][1] if proportional and terms else F(1)
    if scale < 0:
        lo, hi = hi, lo
    return (
        tuple((v, str(c / scale)) for v, c in terms),
        str(lo / scale) if lo is not None else None,
        str(hi / scale) if hi is not None else None,
    )


def column_signature(m, i, proportional=False):
    terms = list(m.iter_column(i))
    v = m.variables[i]
    if proportional:
        scale = terms[0][1] if terms else F(1)
        return tuple((r, str(c / scale)) for r, c in terms)
    return (
        tuple((r, str(c)) for r, c in terms),
        v[2],
        str(m.num(v[3])),
        str(m.num(v[4])),
        str(m.num(v[5])),
    )


def analyze(m, kind, scope=None, budget=None, handle=None, offset=0, limit=20):
    allowed = {
        "duplicates",
        "proportional_rows",
        "duplicate_columns",
        "proportional_columns",
        "subsets",
        "graph",
        "assignments",
        "components",
        "wl",
        "decomposition",
        "network",
    }
    if kind not in allowed:
        raise LPIRError(f"Unknown analysis {kind}")
    db = connect(m)
    b = Budget(**(budget or {}))
    spec = {
        "kind": kind,
        "scope": scope,
        "version": ASSIGNMENT_VERSION if kind == "assignments" else "2.0",
        "model": m.manifest["model_version"],
    }
    run = (
        "a_"
        + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:32]
    )
    if handle and handle != run:
        raise LPIRError("Continuation does not match analysis spec")
    old = db.execute(
        "SELECT status,cursor,processed FROM runs WHERE id=?", (run,)
    ).fetchone()
    if old and old[0] == "complete":
        out = result_page(m, db, run, offset, limit)
        db.close()
        return out
    if not old:
        if kind == "assignments":
            db.execute("UPDATE runs SET status='obsolete' WHERE kind='assignments'")
            db.execute("DELETE FROM assignment_members")
            db.execute("DELETE FROM assignment_edges")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='temporal_assignment'"
            ).fetchone():
                db.execute("DELETE FROM temporal_assignment")
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
            (run, kind, json.dumps(spec), "partial", 0, 0, "{}"),
        )
        db.commit()
    cursor = old[1] if old else 0
    processed = old[2] if old else 0
    ordinal = db.execute("SELECT count(*) FROM results WHERE run=?", (run,)).fetchone()[
        0
    ]

    def emit(rec):
        nonlocal ordinal
        db.execute("INSERT INTO results VALUES(?,?,?)", (run, ordinal, json.dumps(rec)))
        ordinal += 1

    summary = {}
    status = "complete"
    try:
        if kind in (
            "duplicates",
            "proportional_rows",
            "duplicate_columns",
            "proportional_columns",
        ):
            columns = kind.endswith("columns")
            db.execute(
                "CREATE TABLE IF NOT EXISTS fingerprints(run TEXT,key TEXT,representative INTEGER,PRIMARY KEY(run,key,representative))"
            )
            n = len(m.variables) if columns else len(m.rows)
            for i in range(cursor, n):
                b.tick()
                size = (
                    int(m.col_ptr[i + 1] - m.col_ptr[i])
                    if columns
                    else int(m.row_ptr[i + 1] - m.row_ptr[i])
                )
                b.require(size * 256)
                if columns:
                    sig = column_signature(m, i, kind == "proportional_columns")
                else:
                    sig = canonical(m, i, kind == "proportional_rows")
                h = hashlib.sha256(repr(sig).encode()).hexdigest()
                matched_row = None
                for (other,) in db.execute(
                    "SELECT representative FROM fingerprints WHERE run=? AND key=?",
                    (run, h),
                ):
                    if columns:
                        other_sig = column_signature(
                            m, other, kind == "proportional_columns"
                        )
                    else:
                        other_sig = canonical(m, other, kind == "proportional_rows")
                    if other_sig == sig:
                        matched_row = other
                        break
                if matched_row is None:
                    db.execute("INSERT INTO fingerprints VALUES(?,?,?)", (run, h, i))
                else:
                    emit(
                        {
                            "variable" if columns else "row": i,
                            "representative": matched_row,
                            "relation": kind,
                            "evidence": "exact normalized matrix comparison"
                            if kind == "proportional_columns"
                            else "exact coefficient/domain comparison",
                            **(
                                {
                                    "scale": str(
                                        next(m.iter_column(i), (None, F(1)))[1]
                                        / next(
                                            m.iter_column(matched_row), (None, F(1))
                                        )[1]
                                    ),
                                    "scope": "matrix columns; no variable substitution or domain equivalence asserted",
                                }
                                if kind == "proportional_columns"
                                else {}
                            ),
                        }
                    )
                cursor = i + 1
                processed += 1
            summary = {"duplicates": ordinal}
        elif kind == "subsets":
            # Candidate supersets must contain the rarest literal of the smaller support.
            db.execute(
                "CREATE TABLE IF NOT EXISTS support_representatives(row_id INTEGER PRIMARY KEY,key TEXT)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS support_key ON support_representatives(key)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS support_aliases(row_id INTEGER PRIMARY KEY,representative INTEGER)"
            )
            for (row_id,) in db.execute(
                "SELECT row_id FROM features WHERE class='set_partitioning' AND row_id NOT IN (SELECT row_id FROM support_aliases) ORDER BY row_id"
            ):
                b.tick()
                sig = tuple(
                    db.execute(
                        "SELECT variable,complement FROM bindings WHERE row_id=? ORDER BY variable",
                        (row_id,),
                    )
                )
                h = hashlib.sha256(repr(sig).encode()).hexdigest()
                representative = row_id
                for (other,) in db.execute(
                    "SELECT row_id FROM support_representatives WHERE key=?", (h,)
                ):
                    if sig == tuple(
                        db.execute(
                            "SELECT variable,complement FROM bindings WHERE row_id=? ORDER BY variable",
                            (other,),
                        )
                    ):
                        representative = other
                        break
                if representative == row_id:
                    db.execute(
                        "INSERT INTO support_representatives VALUES(?,?)", (row_id, h)
                    )
                db.execute(
                    "INSERT INTO support_aliases VALUES(?,?)", (row_id, representative)
                )
            rows = db.execute(
                "SELECT row_id,nnz FROM features WHERE class='set_partitioning' AND row_id>=? AND row_id IN (SELECT row_id FROM support_representatives) ORDER BY row_id",
                (cursor,),
            )
            for r, n in rows:
                b.tick()
                b.require(n * 256)
                literals = {
                    (v, c)
                    for v, c in db.execute(
                        "SELECT variable,complement FROM bindings WHERE row_id=?", (r,)
                    )
                }
                if not literals:
                    cursor = r + 1
                    continue
                rare = min(
                    literals,
                    key=lambda vc: db.execute(
                        "SELECT count(*) FROM bindings WHERE variable=? AND complement=?",
                        vc,
                    ).fetchone()[0],
                )
                staged = []
                for (s,) in db.execute(
                    "SELECT b.row_id FROM bindings b JOIN features f ON f.row_id=b.row_id WHERE b.variable=? AND b.complement=? AND f.class='set_partitioning' AND f.nnz>? AND b.row_id IN (SELECT row_id FROM support_representatives)",
                    (*rare, n),
                ):
                    b.tick()
                    sn = db.execute(
                        "SELECT nnz FROM features WHERE row_id=?", (s,)
                    ).fetchone()[0]
                    b.require((n + sn) * 256)
                    sup = {
                        (v, c)
                        for v, c in db.execute(
                            "SELECT variable,complement FROM bindings WHERE row_id=?",
                            (s,),
                        )
                    }
                    if literals < sup:
                        diff = sorted(sup - literals)
                        staged.append(
                            {
                                "smaller_row": r,
                                "larger_row": s,
                                "difference": [
                                    {
                                        "variable": v,
                                        "complement": bool(c),
                                        "implied_value": "1" if c else "0",
                                    }
                                    for v, c in diff
                                ],
                                "evidence": "subtract normalized exactly-one equalities; nonnegative binary literals",
                            }
                        )
                        b.require(sum(len(x["difference"]) for x in staged) * 256)
                for rec in staged:
                    emit(rec)
                cursor = r + 1
                processed += 1
            fixed = set()
            for (rec,) in db.execute("SELECT record FROM results WHERE run=?", (run,)):
                for x in json.loads(rec)["difference"]:
                    fixed.add((x["variable"], x["implied_value"]))
            summary = {
                "proper_inclusions": ordinal,
                "implied_variable_values": len(fixed),
                "variables": len({v for v, val in fixed}),
                "applied": False,
                "pair_scope": "distinct normalized supports; duplicate row aliases retained in support_aliases",
            }
        elif kind == "graph":
            b.tick()
            directed = db.execute("SELECT count(*) FROM edges").fetchone()[0]
            asymmetric = db.execute(
                "SELECT count(*) FROM edges e WHERE NOT EXISTS(SELECT 1 FROM edges f WHERE f.source=e.target AND f.target=e.source)"
            ).fetchone()[0]
            centers = db.execute(
                "SELECT count(DISTINCT center) FROM graphs"
            ).fetchone()[0]
            summary = {
                "centers": centers,
                "rows": db.execute("SELECT count(*) FROM graphs").fetchone()[0],
                "directed_edges": directed,
                "undirected_edges": directed // 2 if asymmetric == 0 else None,
                "symmetric": asymmetric == 0,
                "asymmetric_edges": asymmetric,
                "interpretation": "union of verified center-neighborhood incidence; individual rows retained",
            }
            emit(summary)
            processed = 1
            cursor = 1
        elif kind == "assignments":
            # Complete one connected incidence component before persisting it.
            b.require((len(m.rows) + len(m.variables) + len(m.col_idx)) * 80)
            rr = [
                r
                for (r,) in db.execute(
                    "SELECT f.row_id FROM features f WHERE f.class='set_partitioning' AND NOT EXISTS(SELECT 1 FROM bindings b WHERE b.row_id=f.row_id AND b.complement<>0) ORDER BY f.row_id"
                )
            ]
            incidence = defaultdict(list)
            for r in rr:
                for v, c in db.execute(
                    "SELECT variable,complement FROM bindings WHERE row_id=?", (r,)
                ):
                    if c:
                        raise LPIRError(
                            "Complemented literal reached unsigned assignment detector"
                        )
                    incidence[v].append(r)
            adj = defaultdict(list)
            bad = set()
            for v, rs in incidence.items():
                if len(rs) != 2:
                    bad.update(rs)
                else:
                    a, c = rs
                    adj[a].append((c, v))
                    adj[c].append((a, v))
            seen = set()
            for root in rr:
                if root in seen:
                    continue
                q = deque([root])
                colors = {root: 0}
                component = []
                ok = True
                while q:
                    r = q.popleft()
                    seen.add(r)
                    component.append(r)
                    if r in bad:
                        ok = False
                    for s, v in adj[r]:
                        if s not in colors:
                            colors[s] = 1 - colors[r]
                            q.append(s)
                        elif colors[s] == colors[r]:
                            ok = False
                if root < cursor:
                    continue
                b.tick(len(component))
                if ok:
                    left = sorted(r for r in component if colors[r] == 0)
                    right = sorted(r for r in component if colors[r] == 1)
                    if not left or not right:
                        raise LPIRError("Empty side in assignment candidate")
                    li = {r: i for i, r in enumerate(left)}
                    ri = {r: i for i, r in enumerate(right)}
                    block = "assignment_" + str(root)
                    for side, items in enumerate((left, right)):
                        db.executemany(
                            "INSERT OR REPLACE INTO assignment_members VALUES(?,?,?,?)",
                            ((block, side, i, r) for i, r in enumerate(items)),
                        )
                    pairs = set()
                    edgecount = 0
                    for r in left:
                        for s, v in adj[r]:
                            db.execute(
                                "INSERT OR REPLACE INTO assignment_edges VALUES(?,?,?,?)",
                                (block, v, li[r], ri[s]),
                            )
                            pairs.add((li[r], ri[s]))
                            edgecount += 1
                    emit(
                        {
                            "id": block,
                            "shape": [len(left), len(right)],
                            "variables": edgecount,
                            "missing_pairs": len(left) * len(right) - len(pairs),
                            "duplicate_pairs": edgecount - len(pairs),
                            "complete": len(pairs) == len(left) * len(right),
                            "evidence": "complete unsigned exactly-one rows; degree-two bipartite incidence",
                        }
                    )
                cursor = root + 1
                processed += len(component)
            db.execute(
                "CREATE TABLE IF NOT EXISTS temporal_assignment(row_id INTEGER,block TEXT,orientation TEXT,source_left INTEGER,source_right INTEGER,target_left INTEGER,target_right INTEGER,PRIMARY KEY(row_id,block,orientation))"
            )
            switch = {
                v: sw
                for v, sw in db.execute(
                    "SELECT source,min(switch) FROM temporal GROUP BY source HAVING count(DISTINCT switch)=1"
                )
            }
            for (block,) in db.execute("SELECT DISTINCT block FROM assignment_edges"):
                binding = {
                    v: (i, j)
                    for v, i, j in db.execute(
                        "SELECT variable,left_idx,right_idx FROM assignment_edges WHERE block=?",
                        (block,),
                    )
                }
                for r, src, tgt, sw, arc in db.execute(
                    "SELECT row_id,source,target,switch,arc FROM temporal"
                ):
                    b.tick()
                    if (
                        sw != switch.get(src)
                        or sw not in binding
                        or switch.get(tgt) not in binding
                        or arc not in binding
                    ):
                        continue
                    i, j = binding[sw]
                    k, l = binding[switch[tgt]]
                    for orientation, expected in [
                        ("forward", (i, l)),
                        ("transposed", (k, j)),
                    ]:
                        if binding[arc] == expected:
                            db.execute(
                                "INSERT OR REPLACE INTO temporal_assignment VALUES(?,?,?,?,?,?,?)",
                                (r, block, orientation, i, j, k, l),
                            )
            summary = {
                "blocks": ordinal,
                "eligible_rows": len(rr),
                "excluded_complemented_rows": db.execute(
                    "SELECT count(*) FROM features WHERE class='set_partitioning'"
                ).fetchone()[0]
                - len(rr),
                "temporal_index_joins": db.execute(
                    "SELECT count(DISTINCT row_id) FROM temporal_assignment"
                ).fetchone()[0],
            }
        elif kind in ("components", "wl", "decomposition", "network"):
            from .graph_analysis import graph_job

            records, summary = graph_job(m, db, kind, scope, b)
            for rec in records:
                emit(rec)
            cursor = 1
            processed = 1
        db.execute(
            "UPDATE runs SET status=?,cursor=?,processed=?,summary=? WHERE id=?",
            (status, cursor, processed, json.dumps(summary), run),
        )
    except BudgetExceeded:
        status = "partial"
        summary = {
            "reason": "working-set, candidate or time budget exhausted; increase budget to continue",
            "applied": False,
        }
        db.execute(
            "UPDATE runs SET status=?,cursor=?,processed=?,summary=? WHERE id=?",
            (status, cursor, processed, json.dumps(summary), run),
        )
    db.commit()
    out = result_page(m, db, run, offset, limit)
    db.close()
    return out
