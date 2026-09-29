"""Disk-backed, resumable undirected connectivity on caller-specified relations."""

import json

from .evidence import begin, emit, input_relation, js, require_result, save
from .budget import Budget, BudgetExceeded
from .results import result_page
from .numbers import LPIRError
from .semantic import connect


def components(m, scope, budget=None, handle=None, offset=0, limit=20):
    if not isinstance(scope, dict) or "edges" not in scope:
        raise LPIRError("relation_components requires an edges query/handle")
    if set(scope) - {"edges", "vertices", "source", "target", "vertex", "evidence"}:
        raise LPIRError("Unknown graph scope fields")
    edges = input_relation(m, scope["edges"], budget)
    vertices = (
        input_relation(m, scope["vertices"], budget) if "vertices" in scope else None
    )
    for dep in (edges, vertices):
        if dep and dep["completeness"] != "complete":
            return {
                **dep,
                "requested_scope": scope,
                "continuation": {"kind": "relation_components", "scope": scope},
                "reason": "input relation is incomplete",
            }
    source = scope.get("source", "source")
    target = scope.get("target", "target")
    vertex = scope.get("vertex", "variable")
    evidence = scope.get("evidence", "row_id")
    deps = [edges["handle"]] + ([vertices["handle"]] if vertices else [])
    spec = {"scope": scope, "dependencies": deps, "mode": "undirected_connectivity"}
    db = connect(m)
    for h in deps:
        require_result(m, db, h)
    run = begin(m, db, "relation_components", spec)
    if handle is not None and handle != run:
        raise LPIRError("Graph handle does not match scope")
    old = db.execute(
        "SELECT status,cursor,summary FROM runs WHERE id=?", (run,)
    ).fetchone()
    if old[0] == "complete":
        page = result_page(m, db, run, offset, limit)
        db.close()
        return page
    edge_run = begin(m, db, "relation_component_edges", spec)
    component_run = begin(m, db, "relation_component_sizes", spec)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS relation_nodes(run TEXT,node TEXT,parent TEXT,size INTEGER,PRIMARY KEY(run,node));
    CREATE TABLE IF NOT EXISTS relation_edges(run TEXT,ordinal INTEGER,source TEXT,target TEXT,evidence TEXT,PRIMARY KEY(run,ordinal));
    """)
    state = json.loads(old[2])
    state.setdefault("phase", "vertices" if vertices else "edges")
    state.setdefault("input_cursor", 0)
    state.setdefault("edge_cursor", 0)
    state.setdefault("last_node", "")
    state.setdefault("work_done", 0)
    state["outputs"] = {"members": run, "edges": edge_run, "components": component_run}
    state["meaning"] = (
        "undirected connectivity of supplied edges; no variable-equivalence claim"
    )
    member_n = old[1]
    edge_n = db.execute(
        "SELECT count(*) FROM results WHERE run=?", (edge_run,)
    ).fetchone()[0]
    component_n = db.execute(
        "SELECT count(*) FROM results WHERE run=?", (component_run,)
    ).fetchone()[0]
    b = Budget(**(budget or {}))

    def node_key(x):
        if isinstance(x, bool) or not isinstance(x, (int, str)):
            raise LPIRError("Graph vertices must be integer or string keys")
        return js(x)

    def add(x):
        k = node_key(x)
        db.execute("INSERT OR IGNORE INTO relation_nodes VALUES(?,?,?,1)", (run, k, k))
        return k

    def find(k):
        start = k
        while True:
            parent = db.execute(
                "SELECT parent FROM relation_nodes WHERE run=? AND node=?", (run, k)
            ).fetchone()[0]
            if parent == k:
                break
            k = parent
        if start != k:
            db.execute(
                "UPDATE relation_nodes SET parent=? WHERE run=? AND node=?",
                (k, run, start),
            )
        return k

    def union(a, b):
        a = find(a)
        b = find(b)
        if a == b:
            return
        sa = db.execute(
            "SELECT size FROM relation_nodes WHERE run=? AND node=?", (run, a)
        ).fetchone()[0]
        sb = db.execute(
            "SELECT size FROM relation_nodes WHERE run=? AND node=?", (run, b)
        ).fetchone()[0]
        if (sa, a) < (sb, b):
            a, b, sa, sb = b, a, sb, sa
        db.execute(
            "UPDATE relation_nodes SET parent=? WHERE run=? AND node=?", (a, run, b)
        )
        db.execute(
            "UPDATE relation_nodes SET size=? WHERE run=? AND node=?", (sa + sb, run, a)
        )

    try:
        if state["phase"] == "vertices":
            for i, rec in db.execute(
                "SELECT ordinal,record FROM results WHERE run=? AND ordinal>=? ORDER BY ordinal",
                (vertices["handle"], state["input_cursor"]),
            ):
                b.tick()
                b.require(len(rec) * 4)
                item = json.loads(rec)
                if vertex not in item:
                    raise LPIRError("Missing graph vertex field")
                add(item[vertex])
                state["input_cursor"] = i + 1
                state["work_done"] += 1
            state["phase"] = "edges"
        if state["phase"] == "edges":
            for i, rec in db.execute(
                "SELECT ordinal,record FROM results WHERE run=? AND ordinal>=? ORDER BY ordinal",
                (edges["handle"], state["edge_cursor"]),
            ):
                b.tick()
                b.require(len(rec) * 4)
                item = json.loads(rec)
                if any(k not in item for k in (source, target, evidence)):
                    raise LPIRError("Missing graph endpoint/evidence field")
                a = add(item[source])
                z = add(item[target])
                union(a, z)
                db.execute(
                    "INSERT INTO relation_edges VALUES(?,?,?,?,?)",
                    (run, i, a, z, js(item[evidence])),
                )
                state["edge_cursor"] = i + 1
                state["work_done"] += 1
            state["phase"] = "members"
        if state["phase"] == "members":
            for (node,) in db.execute(
                "SELECT node FROM relation_nodes WHERE run=? AND node>? ORDER BY node",
                (run, state["last_node"]),
            ):
                b.tick()
                root = find(node)
                member_n = emit(
                    db,
                    run,
                    member_n,
                    {"vertex": json.loads(node), "component": json.loads(root)},
                )
                state["last_node"] = node
                state["work_done"] += 1
            state["phase"] = "evidence"
            state["output_edge_cursor"] = 0
        if state["phase"] == "evidence":
            for i, a, z, proof in db.execute(
                "SELECT ordinal,source,target,evidence FROM relation_edges WHERE run=? AND ordinal>=? ORDER BY ordinal",
                (run, state["output_edge_cursor"]),
            ):
                b.tick()
                root = find(a)
                if root != find(z):
                    raise LPIRError("Connectivity verification failed")
                edge_n = emit(
                    db,
                    edge_run,
                    edge_n,
                    {
                        "edge_ordinal": i,
                        "component": json.loads(root),
                        "source": json.loads(a),
                        "target": json.loads(z),
                        "evidence": json.loads(proof),
                    },
                )
                state["output_edge_cursor"] = i + 1
                state["work_done"] += 1
            state["phase"] = "sizes"
            state["last_root"] = ""
        if state["phase"] == "sizes":
            for root, size in db.execute(
                "SELECT node,size FROM relation_nodes WHERE run=? AND parent=node AND node>? ORDER BY node",
                (run, state["last_root"]),
            ):
                b.tick()
                component_n = emit(
                    db,
                    component_run,
                    component_n,
                    {"component": json.loads(root), "vertices": size},
                )
                state["last_root"] = root
                state["work_done"] += 1
            state.update(
                phase="complete",
                vertices=member_n,
                edges=edge_n,
                components=component_n,
                checked_entities=edge_n,
                scope_total=edges["total"],
            )
            state["size_distribution"] = [
                {"vertices": n, "components": c}
                for n, c in db.execute(
                    "SELECT size,count(*) FROM relation_nodes WHERE run=? AND parent=node GROUP BY size ORDER BY size LIMIT 20",
                    (run,),
                )
            ]
            state["size_distribution_truncated"] = (
                db.execute(
                    "SELECT count(DISTINCT size) FROM relation_nodes WHERE run=? AND parent=node",
                    (run,),
                ).fetchone()[0]
                > 20
            )
    except BudgetExceeded:
        pass
    except BaseException:
        db.rollback()
        db.close()
        raise
    complete = state["phase"] == "complete"
    state["checked_entities"] = state["edge_cursor"]
    state["scope_total"] = edges["total"]
    save(db, run, state, member_n, complete, commit=False)
    save(
        db,
        edge_run,
        {"work_done": state["work_done"], "phase": state["phase"], "parent": run},
        edge_n,
        complete,
        commit=False,
    )
    save(
        db,
        component_run,
        {"work_done": state["work_done"], "phase": state["phase"], "parent": run},
        component_n,
        complete,
        commit=False,
    )
    db.commit()
    out = result_page(m, db, run, offset, limit)
    db.close()
    return out
