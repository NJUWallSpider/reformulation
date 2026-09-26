"""L1/L2 sidecar: typed incidence coordinates and exact sparse indexed operators.

All mathematics is checked against the immutable numerical model. The sidecar is
an overlapping view, never an alternative numerical source or a presolver.
"""

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict, deque
from fractions import Fraction as F

from .jobs import Budget, BudgetExceeded
from .numbers import LPIRError

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


def js(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


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


def put(db, relation, key, value):
    db.execute(
        "INSERT OR REPLACE INTO records VALUES(?,?,?)", (relation, str(key), js(value))
    )


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
    with connect(m, True) as db:
        where = "relation=?"
        if relation == "index_operators":
            where += " AND json_extract(record,'$.verification')='verified'"
        if relation in ("index_rows", "index_kernel", "index_alignment"):
            where += " AND EXISTS(SELECT 1 FROM records p WHERE p.relation='index_operators' AND p.key=json_extract(records.record,'$.id') AND json_extract(p.record,'$.verification')='verified')"
        args = [relation]
        if key is not None:
            where += " AND json_extract(record,'$.id')=?"
            args.append(key)
        total = db.execute(
            "SELECT count(*) FROM records WHERE " + where, args
        ).fetchone()[0]
        items = [
            json.loads(x[0])
            for x in db.execute(
                "SELECT record FROM records WHERE "
                + where
                + " ORDER BY key LIMIT ? OFFSET ?",
                [*args, limit, offset],
            )
        ]
        state = json.loads(
            db.execute("SELECT value FROM meta WHERE key='state'").fetchone()[0]
        )
    return envelope(
        m,
        items=items,
        total=total,
        total_known=True,
        offset=offset,
        limit=limit,
        truncated=offset + len(items) < total,
        next_offset=offset + len(items) if offset + len(items) < total else None,
        scope={"relation": relation, "id": key},
        search_completeness="complete" if state["phase"] == "done" else "partial",
    )


def partition(db, g, budget):
    n = db.execute("SELECT count(*) FROM sets WHERE g=?", (g,)).fetchone()[0]
    degrees = db.execute("SELECT v,count(*) FROM members WHERE g=? GROUP BY v", (g,))
    size = 0
    overlaps = 0
    for v, d in degrees:
        if not db.execute(
            "SELECT 1 FROM records WHERE relation='index_degrees' AND key=?",
            (f"{g}:{v}",),
        ).fetchone():
            budget.tick()
        size += 1
        overlaps += d > 1
        put(db, "index_degrees", f"{g}:{v}", {"id": g, "variable": v, "degree": d})
    empty = db.execute(
        "SELECT count(*) FROM sets s WHERE g=? AND NOT EXISTS(SELECT 1 FROM members m WHERE m.g=s.g AND m.r=s.r)",
        (g,),
    ).fetchone()[0]
    universe_exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='scope_variables'"
    ).fetchone()
    uncovered = 0
    if universe_exists:
        uncovered = db.execute(
            "SELECT count(*) FROM (SELECT DISTINCT json_extract(record,'$.variable') v FROM scope_variables) u WHERE NOT EXISTS(SELECT 1 FROM members m WHERE m.g=? AND m.v=u.v)",
            (g,),
        ).fetchone()[0]
    rec = {
        "id": g,
        "sets": n,
        "variables": size,
        "overlapping_variables": overlaps,
        "empty_sets": empty,
        "scope": "provided universe" if universe_exists else "union of these sets",
        "uncovered_variables": uncovered,
        "disjoint": overlaps == 0,
        "partition": bool(n and size and not overlaps and not empty and not uncovered),
        "members": handle("index_members", g),
    }
    put(db, "index_partitions", g, rec)
    return rec


def split_two(db, g, budget):
    # Candidate discovery only: each member must connect exactly two distinct sets.
    n = db.execute("SELECT count(*) FROM members WHERE g=?", (g,)).fetchone()[0]
    budget.require(n * 160)
    if db.execute(
        "SELECT 1 FROM members WHERE g=? GROUP BY v HAVING count(*)<>2 LIMIT 1", (g,)
    ).fetchone():
        return
    adj = defaultdict(list)
    for v, a, b in db.execute(
        "SELECT v,min(r),max(r) FROM members WHERE g=? GROUP BY v", (g,)
    ):
        budget.tick(0)
        adj[a].append(b)
        adj[b].append(a)
    colors = {}
    for root in sorted(adj):
        if root in colors:
            continue
        queue = deque([root])
        colors[root] = 0
        component = []
        valid = True
        while queue:
            r = queue.popleft()
            component.append(r)
            for s in adj[r]:
                if s not in colors:
                    colors[s] = 1 - colors[r]
                    queue.append(s)
                elif colors[s] == colors[r]:
                    valid = False
        if not valid:
            continue
        for side in (0, 1):
            new = ident("partition_", [g, root, side])
            for r in component:
                if colors[r] != side:
                    continue
                db.execute("INSERT OR IGNORE INTO sets VALUES(?,?)", (new, r))
                db.execute(
                    "INSERT OR IGNORE INTO members SELECT ?,r,v FROM members WHERE g=? AND r=?",
                    (new, g, r),
                )
                source = get(db, "index_sets", f"{g}:{r}")
                put(
                    db,
                    "index_sets",
                    f"{new}:{r}",
                    {**source, "id": new, "source_group": g},
                )
                for (v,) in db.execute(
                    "SELECT v FROM members WHERE g=? AND r=?", (new, r)
                ):
                    put(
                        db,
                        "index_members",
                        f"{new}:{r}:{v}",
                        {"id": new, "set": r, "row_id": r, "variable": v},
                    )
            partition(db, new, budget)


def tensor(db, left, right, budget):
    # Compare complete universes before checking multiplicities; no dense m*n matrix.
    for a, b in ((left, right), (right, left)):
        if db.execute(
            "SELECT v FROM members WHERE g=? EXCEPT SELECT v FROM members WHERE g=? LIMIT 1",
            (a, b),
        ).fetchone():
            return
    collision = db.execute(
        "SELECT l.r,r.r,count(*) FROM members l JOIN members r ON l.v=r.v WHERE l.g=? AND r.g=? GROUP BY l.r,r.r HAVING count(*)>1 LIMIT 1",
        (left, right),
    ).fetchone()
    if collision:
        put(
            db,
            "index_evidence",
            ident("collision_", [left, right]),
            {
                "id": left,
                "other": right,
                "verification": "refuted",
                "reason": "multiple variables per cell",
                "counterexample": list(collision),
            },
        )
        return
    nr = db.execute("SELECT count(*) FROM sets WHERE g=?", (left,)).fetchone()[0]
    nc = db.execute("SELECT count(*) FROM sets WHERE g=?", (right,)).fetchone()[0]
    n = db.execute("SELECT count(*) FROM members WHERE g=?", (left,)).fetchone()[0]
    if nr < 2 or nc < 2:
        return
    # Prefer the unit-sum partition as axis 0; IDs never supply semantics.
    seed = db.execute(
        "SELECT record FROM records WHERE relation='index_sets' AND json_extract(record,'$.id')=? LIMIT 1",
        (right,),
    ).fetchone()
    if seed and json.loads(seed[0]).get("seed", [None, None, None])[-1] == "unit":
        left, right = right, left
        nr, nc = nc, nr
    budget.require((nr + nc) * 128)
    left_rows = [
        r for (r,) in db.execute("SELECT r FROM sets WHERE g=? ORDER BY r", (left,))
    ]
    right_rows = [
        r for (r,) in db.execute("SELECT r FROM sets WHERE g=? ORDER BY r", (right,))
    ]
    t = ident("tensor_", sorted([left_rows, right_rows]))
    if db.execute(
        "SELECT 1 FROM records WHERE relation='index_tensors' AND key=?", (t,)
    ).fetchone():
        return
    # Display orientation has no semantic force; prefer more sets when no unit seed exists.
    left_seed = db.execute(
        "SELECT record FROM records WHERE relation='index_sets' AND json_extract(record,'$.id')=? LIMIT 1",
        (left,),
    ).fetchone()
    if nr < nc and not (
        left_seed
        and json.loads(left_seed[0]).get("seed", [None, None, None])[-1] == "unit"
    ):
        left, right = right, left
        nr, nc = nc, nr
    budget.require((nr + nc) * 128)
    li = {
        r: i
        for i, (r,) in enumerate(
            db.execute("SELECT r FROM sets WHERE g=? ORDER BY r", (left,))
        )
    }
    ri = {
        r: i
        for i, (r,) in enumerate(
            db.execute("SELECT r FROM sets WHERE g=? ORDER BY r", (right,))
        )
    }
    for side, (g, ids) in enumerate(((left, li), (right, ri))):
        for r, i in ids.items():
            put(
                db,
                "index_axes",
                f"{t}:{side}:{i}",
                {"id": t, "axis": side, "index": i, "row_id": r, "partition": g},
            )
    for v, a, b in db.execute(
        "SELECT l.v,l.r,r.r FROM members l JOIN members r ON l.v=r.v WHERE l.g=? AND r.g=? ORDER BY l.v",
        (left, right),
    ):
        if not db.execute("SELECT 1 FROM coords WHERE t=? AND v=?", (t, v)).fetchone():
            budget.tick()
        db.execute(
            "INSERT OR REPLACE INTO coords VALUES(?,?,?,?)", (t, v, li[a], ri[b])
        )
        put(
            db,
            "index_coordinates",
            f"{t}:{v}",
            {"id": t, "variable": v, "axis0": li[a], "axis1": ri[b]},
        )
    constraints = []
    for side, g in enumerate((left, right)):
        bounds = Counter()
        kinds = Counter()
        for (txt,) in db.execute(
            "SELECT record FROM records WHERE relation='index_sets' AND json_extract(record,'$.id')=?",
            (g,),
        ):
            x = json.loads(txt)
            if "lower" in x:
                bounds[(x["lower"], x["upper"])] += 1
        for typ, num in db.execute(
            "SELECT kind,count(DISTINCT q.v) FROM terms q JOIN members mm ON mm.v=q.v AND mm.r=q.r WHERE mm.g=? GROUP BY kind",
            (g,),
        ):
            kinds[typ] = num
        constraints.append(
            {
                "axis": side,
                "formula": f"lower[a] <= Sum_b X[{'a,b' if side == 0 else 'b,a'}] <= upper[a]"
                if sum(bounds.values()) == (nr if side == 0 else nc)
                else "raw row expressions restricted to this axis; inspect instances",
                "bounds": [[*k, n] for k, n in bounds.most_common(4)],
                "distinct_bounds": len(bounds),
                "instances": handle("index_sets", g),
                "domains": dict(kinds),
            }
        )
    put(
        db,
        "index_tensors",
        t,
        {
            "id": t,
            "constraints": constraints,
            "shape": [nr, nc],
            "variables": n,
            "complete_product": n == nr * nc,
            "missing_cells": nr * nc - n,
            "partitions": [left, right],
            "verification": "verified",
            "coordinates": handle("index_coordinates", t),
            "axes": handle("index_axes", t),
            "meaning": "incidence coordinates; not a symmetry certificate",
        },
    )


def bundle_row(m, db, t, r, budget):
    # Scope is the entire original row: an extra external term must not disappear.
    total = db.execute("SELECT count(*) FROM terms WHERE r=?", (r,)).fetchone()[0]
    joined = db.execute(
        "SELECT count(*),min(a),max(a),min(b),max(b) FROM terms q JOIN coords c ON c.v=q.v WHERE q.r=? AND c.t=?",
        (r, t),
    ).fetchone()
    n, amin, amax, bmin, bmax = joined
    if not n or total != n + 1:
        return
    anchors = list(
        db.execute(
            "SELECT v,c,kind FROM terms q WHERE r=? AND NOT EXISTS(SELECT 1 FROM coords c WHERE c.t=? AND c.v=q.v)",
            (r, t),
        )
    )
    if len(anchors) != 1:
        return
    p, c, kind = anchors[0]
    # Distinguished variable must have a different domain type from tensor support.
    if db.execute(
        "SELECT 1 FROM terms q JOIN coords c ON q.v=c.v WHERE q.r=? AND c.t=? AND q.kind=? LIMIT 1",
        (r, t, kind),
    ).fetchone():
        return
    scale = F(c)
    row = m.rows[r]
    lo = m.num(row[2])
    hi = m.num(row[3])
    if scale < 0:
        lo, hi = hi, lo
    lo = None if lo is None else str(lo / scale)
    hi = None if hi is None else str(hi / scale)
    for axis, mn, mx in ((0, amin, amax), (1, bmin, bmax)):
        if mn != mx:
            continue
        budget.tick(0)
        db.execute(
            "INSERT OR REPLACE INTO bundles VALUES(?,?,?,?,?,?,?,?)",
            (t, axis, p, mn, r, str(scale), lo, hi),
        )
        other = "b" if axis == 0 else "a"
        for j, coeff in db.execute(
            f"SELECT c.{other},q.c FROM terms q JOIN coords c ON q.v=c.v WHERE q.r=? AND c.t=?",
            (r, t),
        ):
            db.execute(
                "INSERT OR REPLACE INTO slices VALUES(?,?,?,?,?)",
                (t, axis, r, j, str(-F(coeff) / scale)),
            )


def operator_ast(t, axis, invariant):
    return {
        "op": "range",
        "lower": {"parameter": "lower", "indices": ["p", "k"]},
        "upper": {"parameter": "upper", "indices": ["p", "k"]},
        "expression": {
            "op": "sub",
            "left": {"tensor": "y", "indices": ["p"]},
            "right": {
                "op": "sum",
                "index": "j",
                "domain": {"tensor": t, "fixed_axis": axis, "fixed_index": "k"},
                "term": {
                    "op": "mul",
                    "left": {
                        "parameter": "A",
                        "indices": ["p", "j"] if invariant else ["p", "k", "j"],
                    },
                    "right": {
                        "tensor": t,
                        "indices": ["k", "j"] if axis == 0 else ["j", "k"],
                    },
                },
            },
        },
    }


def compile_operator(m, db, t, axis, budget):
    count = db.execute(
        "SELECT count(*) FROM bundles WHERE t=? AND axis=?", (t, axis)
    ).fetchone()[0]
    if not count:
        return
    duplicate = db.execute(
        "SELECT p,k,count(*) FROM bundles WHERE t=? AND axis=? GROUP BY p,k HAVING count(*)>1 LIMIT 1",
        (t, axis),
    ).fetchone()
    if duplicate:
        put(
            db,
            "index_evidence",
            f"{t}:{axis}:duplicate",
            {
                "id": t,
                "verification": "refuted",
                "reason": "duplicate row coordinates",
                "counterexample": list(duplicate),
            },
        )
        return
    tr = get(db, "index_tensors", t)
    nk = tr["shape"][axis]
    nj = tr["shape"][1 - axis]
    ps = [
        p
        for (p,) in db.execute(
            "SELECT DISTINCT p FROM bundles WHERE t=? AND axis=? ORDER BY p", (t, axis)
        )
    ]
    budget.require((len(ps) + nj) * 512)
    invariant = count == len(ps) * nk
    oid = ident("operator_", [t, axis])
    for p in ps:
        mark = f"invariant:{oid}:{p}"
        old = db.execute("SELECT value FROM meta WHERE key=?", (mark,)).fetchone()
        if old is not None:
            invariant = invariant and json.loads(old[0])
            continue
        base = None
        equal = True
        for (r,) in db.execute(
            "SELECT r FROM bundles WHERE t=? AND axis=? AND p=? ORDER BY k",
            (t, axis, p),
        ):
            budget.tick(0)
            budget.require(
                db.execute(
                    "SELECT count(*) FROM slices WHERE t=? AND axis=? AND r=?",
                    (t, axis, r),
                ).fetchone()[0]
                * 512
            )
            vector = tuple(
                db.execute(
                    "SELECT j,c FROM slices WHERE t=? AND axis=? AND r=? ORDER BY j",
                    (t, axis, r),
                )
            )
            if base is None:
                base = vector
            elif base != vector:
                equal = False
        budget.tick()
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (mark, js(equal)))
        invariant = invariant and equal
    pi = {p: i for i, p in enumerate(ps)}
    for p, k, r, scale, lo, hi in db.execute(
        "SELECT p,k,r,scale,lo,hi FROM bundles WHERE t=? AND axis=? ORDER BY p,k",
        (t, axis),
    ):
        mark = f"kernel_row:{oid}:{r}"
        if db.execute("SELECT 1 FROM meta WHERE key=?", (mark,)).fetchone():
            continue
        budget.tick()
        put(
            db,
            "index_rows",
            f"{oid}:{r}",
            {
                "id": oid,
                "row_id": r,
                "p": pi[p],
                "k": k,
                "anchor": p,
                "scale": scale,
                "lower": lo,
                "upper": hi,
                "shift": "0",
            },
        )
        for j, c in db.execute(
            "SELECT j,c FROM slices WHERE t=? AND axis=? AND r=? ORDER BY j",
            (t, axis, r),
        ):
            kk = 0 if invariant else k
            put(
                db,
                "index_kernel",
                f"{oid}:{pi[p]}:{kk}:{j}",
                {
                    "id": oid,
                    "p": pi[p],
                    "k": None if invariant else k,
                    "j": j,
                    "value": c,
                },
            )
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (mark, "true"))
    ast = operator_ast(t, axis, invariant)
    card = {
        "id": oid,
        "tensor": t,
        "fixed_axis": axis,
        "rows": count,
        "shape": [len(ps), nk],
        "kernel_shape": [len(ps), nj] if invariant else [len(ps), nk, nj],
        "axis_invariant": invariant,
        "axis_identity": axis,
        "complete_row_product": count == len(ps) * nk,
        "kind": "broadcast_reduction" if invariant else "indexed_reduction",
        "ast": ast,
        "formula": f"lower[p,k] <= y[p] - Sum_j A[{'p,j' if invariant else 'p,k,j'}] * X[{'k,j' if axis == 0 else 'j,k'}] <= upper[p,k]",
        "kernel": handle("index_kernel", oid),
        "bindings": handle("index_rows", oid),
        "verification": "candidate",
    }
    put(db, "index_operators", oid, card)
    check = check_operator(m, db, oid, budget, checkpoint=True)
    if not check["valid"]:
        raise LPIRError("Internal operator expansion failure: " + js(check))
    card["verification"] = "verified"
    card["kernel_statistics"] = kernel_stats(
        db, oid, len(ps), nj, nk, invariant, budget
    )
    bounds = Counter()
    costs = Counter()
    domains = Counter()
    for p in ps:
        v = m.variables[p]
        bounds[(str(m.num(v[3])), str(m.num(v[4])))] += 1
        costs[str(m.num(v[5]))] += 1
        domains[v[2]] += 1
    card["anchor_profile"] = {
        "domains": dict(domains),
        "bounds": [[*k, n] for k, n in bounds.most_common(4)],
        "costs": costs.most_common(4),
        "distinct_bounds": len(bounds),
        "distinct_costs": len(costs),
        "axis": "p independent of input axis j",
    }
    rb = list(
        db.execute(
            "SELECT lo,hi,count(*) FROM bundles WHERE t=? AND axis=? GROUP BY lo,hi ORDER BY count(*) DESC LIMIT 4",
            (t, axis),
        )
    )
    card["bound_profile"] = [list(x) for x in rb]
    put(db, "index_operators", oid, card)


def kernel_stats(db, oid, np, nj, nk, invariant, budget):
    values = []
    row_sums = defaultdict(F)
    col_sums = defaultdict(F)
    peaks = {}
    ties = set()
    # Cache bounded by the parameter table size, not a high-order coefficient tensor.
    n = db.execute(
        "SELECT count(*) FROM records WHERE relation='index_kernel' AND json_extract(record,'$.id')=?",
        (oid,),
    ).fetchone()[0]
    budget.require(n * 512)
    A = {}
    for (s,) in db.execute(
        "SELECT record FROM records WHERE relation='index_kernel' AND json_extract(record,'$.id')=?",
        (oid,),
    ):
        budget.tick(0)
        x = json.loads(s)
        v = F(x["value"])
        values.append(v)
        row_sums[x["p"]] += v
        col_sums[x["j"]] += v
        A[x["p"], x["j"]] = v
    size = np * nj * (1 if invariant else nk)
    zeros = size - len(values)
    lo = min(values, default=F(0))
    hi = max(values, default=F(0))
    if zeros:
        lo = min(lo, 0)
        hi = max(hi, 0)
    stats = {
        "integer": all(v.denominator == 1 for v in values),
        "min": str(lo),
        "max": str(hi),
        "zeros": zeros,
        "stored_nonzeros": len(values),
        "implicit_zero": True,
    }
    for axis, sums, length in ((0, row_sums, np), (1, col_sums, nj)):
        for i in range(length):
            put(
                db,
                "index_evidence",
                f"{oid}:sum:{axis}:{i}",
                {
                    "id": oid,
                    "kind": "kernel_sum",
                    "axis": axis,
                    "index": i,
                    "value": str(sums[i]),
                },
            )
    if invariant and np == nj:
        rows = defaultdict(dict)
        for (p, j), v in A.items():
            rows[p][j] = v
        for p in range(np):
            budget.tick(0)
            row = rows[p]
            mx = max([*row.values(), *([F(0)] if len(row) < nj else [])])
            matches = [j for j, v in row.items() if v == mx]
            absent = nj - len(row) if mx == 0 else 0
            if len(matches) + absent != 1:
                ties.add(p)
            elif matches:
                peaks[p] = matches[0]
            else:
                peaks[p] = next(j for j in range(nj) if j not in row)
        if not ties and len(set(peaks.values())) == nj:
            inv = {j: p for p, j in peaks.items()}
            symmetric = all(
                v == A.get((inv[j], peaks[p]), 0) for (p, j), v in A.items()
            )
            diag = {A.get((p, j), F(0)) for p, j in peaks.items()}
            for p, j in peaks.items():
                put(
                    db,
                    "index_alignment",
                    f"{oid}:{p}",
                    {
                        "id": oid,
                        "p": p,
                        "j": j,
                        "basis": "unique row maximum bijection",
                    },
                )
            stats["alignment"] = {
                "mapping": handle("index_alignment", oid),
                "symmetric_after_mapping": symmetric,
                "diagonal_constant": str(next(iter(diag))) if len(diag) == 1 else None,
                "model_symmetry_claim": False,
            }
    return stats


def check_operator(m, db, oid, budget, override=None, checkpoint=False):
    card = get(db, "index_operators", oid)
    if card["ast"] != operator_ast(
        card["tensor"], card["fixed_axis"], card["axis_invariant"]
    ):
        return {
            "valid": False,
            "counterexample": {"reason": "AST disagrees with indexed expression"},
        }
    if override is not None:
        seen_keys = set()
        for x in override:
            key = (x["p"], x.get("k"), x["j"])
            if (
                key in seen_keys
                or not 0 <= x["p"] < card["shape"][0]
                or not 0 <= x["j"] < card["kernel_shape"][-1]
                or (
                    x.get("k") is not None
                    if card["axis_invariant"]
                    else not isinstance(x.get("k"), int)
                    or not 0 <= x["k"] < card["shape"][1]
                )
            ):
                return {
                    "valid": False,
                    "counterexample": {
                        "reason": "duplicate or out-of-domain kernel entry",
                        "entry": x,
                    },
                }
            seen_keys.add(key)
    if override is not None:
        # External parameters are a complete sparse table: omitted entries are zeros.
        kernels = {(x["p"], x.get("k"), x["j"]): F(x["value"]) for x in override}
    else:
        kernels = None
    t = card["tensor"]
    axis = card["fixed_axis"]
    checked = 0
    row_keys = set()
    anchor_map = {}
    for (s,) in db.execute(
        "SELECT record FROM records WHERE relation='index_rows' AND json_extract(record,'$.id')=? ORDER BY key",
        (oid,),
    ):
        x = json.loads(s)
        r = x["row_id"]
        key = (x["p"], x["k"])
        if (
            key in row_keys
            or not 0 <= x["p"] < card["shape"][0]
            or not 0 <= x["k"] < card["shape"][1]
            or (x["p"] in anchor_map and anchor_map[x["p"]] != x["anchor"])
        ):
            return {
                "valid": False,
                "counterexample": {
                    "reason": "invalid row/anchor coordinates",
                    "row": r,
                },
            }
        row_keys.add(key)
        anchor_map[x["p"]] = x["anchor"]
        mark = f"validated_row:{oid}:{r}"
        if (
            checkpoint
            and db.execute("SELECT 1 FROM meta WHERE key=?", (mark,)).fetchone()
        ):
            checked += 1
            continue
        budget.tick()
        scale = F(x["scale"])
        expected = {x["anchor"]: F(1)}
        if kernels is None:
            vals = (
                (q["j"], F(q["value"]))
                for q in (
                    json.loads(z[0])
                    for z in db.execute(
                        "SELECT record FROM records WHERE relation='index_kernel' AND json_extract(record,'$.id')=? AND json_extract(record,'$.p')=? AND (json_extract(record,'$.k') IS NULL OR json_extract(record,'$.k')=?)",
                        (oid, x["p"], x["k"]),
                    )
                )
            )
        else:
            vals = (
                (j, v)
                for (p, k, j), v in kernels.items()
                if p == x["p"] and k == (None if card["axis_invariant"] else x["k"])
            )
        for j, c in vals:
            a, b = (x["k"], j) if axis == 0 else (j, x["k"])
            match = db.execute(
                "SELECT v FROM coords WHERE t=? AND a=? AND b=?", (t, a, b)
            ).fetchone()
            if match is None:
                if c:
                    return {
                        "valid": False,
                        "counterexample": {
                            "row": r,
                            "reason": "kernel on missing coordinate",
                            "j": j,
                        },
                    }
            elif c:
                expected[match[0]] = expected.get(match[0], F(0)) - c
        expected = {v: c * scale for v, c in expected.items() if c}
        budget.require((len(expected) + int(m.row_ptr[r + 1] - m.row_ptr[r])) * 512)
        actual = dict(m.iter_terms(r))
        lo = None if x["lower"] is None else F(x["lower"]) * scale
        hi = None if x["upper"] is None else F(x["upper"]) * scale
        if scale < 0:
            lo, hi = hi, lo
        if expected != actual or (lo, hi) != (m.num(m.rows[r][2]), m.num(m.rows[r][3])):
            return {
                "valid": False,
                "counterexample": {"row": r, "reason": "coefficient or bound mismatch"},
            }
        if checkpoint:
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (mark, "true"))
        checked += 1
    if checked != card["rows"] or checked == 0:
        return {
            "valid": False,
            "counterexample": {"reason": "operator row coverage mismatch"},
        }
    return {"valid": True, "checked_rows": checked}


def analyze(
    m, kind="operators", scope=None, budget=None, handle=None, offset=0, limit=20
):
    if kind not in ("incidence", "coordinates", "operators"):
        raise LPIRError("Unknown indexed analysis")
    # One sidecar holds one explicit scope, including imported caller relations.
    scope = scope or {}
    if scope and not isinstance(m, ScopedModel):
        m = ScopedModel(m, ident("scope_", scope))
    if set(scope) - {"rows", "variable_kind", "members", "variables", "role"}:
        raise LPIRError(
            "Indexed scope supports rows, variable_kind, members, variables and role"
        )
    if scope.get("variable_kind") is not None and scope["variable_kind"] not in (
        "B",
        "C",
        "I",
    ):
        raise LPIRError("Unknown variable kind")
    token = ident("indexed_", [m.manifest["model_version"], VERSION, scope])
    if handle is not None and handle != token:
        raise LPIRError("Foreign indexed continuation")
    db = connect(m)
    b = Budget(**(budget or {}))
    reason = None
    old = db.execute("SELECT value FROM meta WHERE key='scope'").fetchone()
    if old and json.loads(old[0]) != scope:
        db.close()
        raise LPIRError(
            "Sidecar already has a different scope; use a separate artifact directory"
        )
    if not old:
        db.execute("INSERT INTO meta VALUES('scope',?)", (js(scope),))
        db.execute(
            "INSERT INTO meta VALUES('state',?)",
            (js({"phase": "scan", "row": 0, "term": 0, "cursor": 0, "work": 0}),),
        )
        db.commit()
    state = json.loads(
        db.execute("SELECT value FROM meta WHERE key='state'").fetchone()[0]
    )
    stops = {
        "incidence": {"pairs", "bundle", "operator", "done"},
        "coordinates": {"bundle", "operator", "done"},
        "operators": {"done"},
    }
    try:
        # Import caller-selected relations through the public executor once. Full,
        # exact materialization is required; no preview is treated as a whole set.
        if scope and not state.get("scope_ready"):
            from .api import IR
            from .evidence import require_result
            from .semantic import connect as analysis_db

            ir = IR(m.path)
            try:
                for field in ("rows", "members", "variables", "role"):
                    if field not in scope:
                        continue
                    result = ir.query(spec=scope[field], budget=budget, limit=1)
                    if result["completeness"] != "complete":
                        return {
                            **result,
                            "continuation": {"kind": kind, "scope": scope},
                            "reason": "scope materialization incomplete",
                        }
                    adb = analysis_db(m)
                    try:
                        require_result(m, adb, result["handle"])
                        db.execute(
                            f"CREATE TABLE IF NOT EXISTS scope_{field}(record TEXT)"
                        )
                        db.execute(f"DELETE FROM scope_{field}")
                        db.executemany(
                            f"INSERT INTO scope_{field} VALUES(?)",
                            adb.execute(
                                "SELECT record FROM results WHERE run=? ORDER BY ordinal",
                                (result["handle"],),
                            ),
                        )
                        columns = (
                            ["row_id", "variable"]
                            if field in ("members", "role")
                            else ["row_id"]
                            if field == "rows"
                            else ["variable"]
                        )
                        expressions = ",".join(
                            "json_extract(record,'$." + c + "')" for c in columns
                        )
                        db.execute(
                            f"CREATE INDEX IF NOT EXISTS scope_{field}_lookup ON scope_{field}({expressions})"
                        )
                    finally:
                        adb.close()
                state["scope_ready"] = True
                db.commit()
            finally:
                ir.close()
        while state["phase"] not in stops[kind]:
            phase = state["phase"]
            if phase == "scan":
                r = state["row"]
                if r == len(m.rows):
                    state.update(phase="partitions", cursor=0)
                    continue
                if (
                    "rows" in scope
                    and not db.execute(
                        "SELECT 1 FROM scope_rows WHERE json_extract(record,'$.row_id')=? LIMIT 1",
                        (r,),
                    ).fetchone()
                ):
                    state.update(row=r + 1, term=0)
                    continue
                # Mid-row cursor bounds memory even for one enormous expression.
                start = int(m.row_ptr[r])
                end = int(m.row_ptr[r + 1])
                for p in range(start + state["term"], end):
                    b.tick()
                    v = int(m.col_idx[p])
                    c = m.num(int(m.coeff_id[p]))
                    b.require(
                        (c.numerator.bit_length() + c.denominator.bit_length()) // 8
                        + 256
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO terms VALUES(?,?,?,?)",
                        (r, v, str(c), m.variables[v][2]),
                    )
                    state["term"] = p - start + 1
                    state["work"] += 1
                    if state["work"] % 2048 == 0:
                        db.execute(
                            "UPDATE meta SET value=? WHERE key='state'", (js(state),)
                        )
                        db.commit()
                finish_row(m, db, r, scope, b)
                state.update(row=r + 1, term=0)
            elif phase == "partitions":
                item = db.execute(
                    "SELECT DISTINCT g FROM sets WHERE g>? ORDER BY g LIMIT 1",
                    (state.get("last_group", ""),),
                ).fetchone()
                if item is None:
                    state.update(phase="pairs", cursor=0)
                    continue
                g = item[0]
                db.execute("SAVEPOINT candidate")
                try:
                    rec = partition(db, g, b)
                    if not rec["disjoint"] and not g.startswith("partition_"):
                        split_two(db, g, b)
                    db.execute("RELEASE candidate")
                except BudgetExceeded:
                    db.execute("UPDATE meta SET value=value WHERE key='state'")
                    db.execute("RELEASE candidate")
                    raise
                state["last_group"] = g
            elif phase == "pairs":
                # Cursor is a lexical pair, not a materialized quadratic candidate list.
                last = state.get("last_pair", ["", ""])
                item = db.execute(
                    "SELECT a.key,b.key FROM records a JOIN records b ON a.key<b.key WHERE a.relation='index_partitions' AND b.relation='index_partitions' AND json_extract(a.record,'$.partition')=1 AND json_extract(b.record,'$.partition')=1 AND json_extract(a.record,'$.variables')=json_extract(b.record,'$.variables') AND (a.key>? OR (a.key=? AND b.key>?)) ORDER BY a.key,b.key LIMIT 1",
                    (last[0], last[0], last[1]),
                ).fetchone()
                if item is None:
                    state.update(phase="bundle", tensor="", bundle_row=0)
                    continue
                b.tick(0)
                db.execute("SAVEPOINT candidate")
                try:
                    tensor(db, *item, b)
                    db.execute("RELEASE candidate")
                except BudgetExceeded:
                    db.execute("UPDATE meta SET value=value WHERE key='state'")
                    db.execute("RELEASE candidate")
                    raise
                state["last_pair"] = list(item)
            elif phase == "bundle":
                if not state["tensor"]:
                    item = db.execute(
                        "SELECT key FROM records WHERE relation='index_tensors' AND key>? ORDER BY key LIMIT 1",
                        (state.get("last_tensor", ""),),
                    ).fetchone()
                    if item is None:
                        state.update(phase="operator", last_operator=["", -1])
                        continue
                    state["tensor"] = item[0]
                    state["bundle_row"] = 0
                t = state["tensor"]
                r = state["bundle_row"]
                if r == len(m.rows):
                    state.update(last_tensor=t, tensor="")
                    continue
                b.tick()
                bundle_row(m, db, t, r, b)
                state["bundle_row"] = r + 1
            elif phase == "operator":
                t, a = state["last_operator"]
                item = db.execute(
                    "SELECT DISTINCT t,axis FROM bundles WHERE t>? OR (t=? AND axis>?) ORDER BY t,axis LIMIT 1",
                    (t, t, a),
                ).fetchone()
                if item is None:
                    state["phase"] = "done"
                    continue
                db.execute("SAVEPOINT candidate")
                try:
                    compile_operator(m, db, *item, b)
                    db.execute("RELEASE candidate")
                except BudgetExceeded:
                    db.execute("UPDATE meta SET value=value WHERE key='state'")
                    db.execute("RELEASE candidate")
                    raise
                state["last_operator"] = list(item)
            else:
                raise LPIRError("Unknown indexed checkpoint phase")
            db.execute("UPDATE meta SET value=? WHERE key='state'", (js(state),))
            db.commit()
    except BudgetExceeded:
        reason = "budget exhausted; resume the same scope and handle (candidate checks are atomic)"
    except Exception:
        db.rollback()
        raise
    finally:
        if reason is not None or state["phase"] in stops[kind]:
            db.execute("UPDATE meta SET value=? WHERE key='state'", (js(state),))
            db.commit()
        db.close()
    table = {
        "incidence": "index_partitions",
        "coordinates": "index_tensors",
        "operators": "index_operators",
    }[kind]
    result = page(m, table, offset=offset, limit=limit)
    complete = state["phase"] in stops[kind]
    result.update(
        handle=token,
        scope=scope,
        completeness="complete" if complete else "partial",
        total=result["total"] if complete else None,
        total_known=complete,
        checked_range=state,
        continuation=None
        if complete
        else {"kind": kind, "scope": scope, "handle": token},
        reason=reason,
    )
    manifest = {
        "version": VERSION,
        "model_version": m.manifest["model_version"],
        "scope": scope,
        "state": state,
        "handle": token,
    }
    tmp = m.path / filename(m, ".json.tmp")
    tmp.write_text(js(manifest))
    tmp.replace(m.path / filename(m, ".json"))
    if isinstance(m, ScopedModel):
        result = qualify(result, m.analysis)
        result["analysis"] = m.analysis
    return result


def finish_row(m, db, r, scope, budget):
    def member(g, v, metadata=False):
        if not db.execute(
            "SELECT 1 FROM members WHERE g=? AND r=? AND v=?", (g, r, v)
        ).fetchone():
            budget.tick()
        add_member(db, g, r, v, metadata=metadata)

    row = m.rows[r]
    lo = m.num(row[2])
    hi = m.num(row[3])
    if "members" in scope:
        for (s,) in db.execute(
            "SELECT record FROM scope_members WHERE json_extract(record,'$.row_id')=?",
            (r,),
        ):
            x = json.loads(s)
            v = x["variable"]
            g = "user_" + str(x["set_family"])
            if v is None:
                db.execute("INSERT OR IGNORE INTO sets VALUES(?,?)", (g, r))
                put(
                    db,
                    "index_sets",
                    f"{g}:{r}",
                    {"id": g, "set": r, "row_id": r, "source": "provided empty set"},
                )
                continue
            if not db.execute(
                "SELECT 1 FROM terms WHERE r=? AND v=?", (r, v)
            ).fetchone():
                raise LPIRError("Provided member is absent from original row")
            member(g, v, metadata=True)
        return
    predicates = ["t.r=?"]
    args = [r]
    if scope.get("variable_kind"):
        if scope["variable_kind"] not in ("B", "I", "C"):
            raise LPIRError("Unknown variable kind")
        predicates.append("t.kind=?")
        args.append(scope["variable_kind"])
    if "variables" in scope:
        predicates.append(
            "EXISTS(SELECT 1 FROM scope_variables WHERE json_extract(record,'$.variable')=t.v)"
        )
    if "role" in scope:
        predicates.append(
            "EXISTS(SELECT 1 FROM scope_role WHERE json_extract(record,'$.row_id')=t.r AND json_extract(record,'$.variable')=t.v)"
        )
    selected = " FROM terms t WHERE " + " AND ".join(predicates)
    types = list(db.execute("SELECT kind,count(*)" + selected + " GROUP BY kind", args))
    if len(types) != 1:
        return
    typ, n = types[0]
    projected = n != int(m.row_ptr[r + 1] - m.row_ptr[r])
    coeffs = list(db.execute("SELECT c" + selected + " GROUP BY c LIMIT 2", args))
    sense = (
        "eq" if lo == hi else "le" if lo is None else "ge" if hi is None else "range"
    )
    metadata = {
        "set": r,
        "row_id": r,
        "source": "typed incidence; coefficient magnitudes ignored",
        "projected_support": projected,
    }
    groups = [ident("coarse_", [typ, sense])]
    if len(coeffs) == 1 and not projected:
        c = F(coeffs[0][0])
        lower, upper = (lo, hi) if c > 0 else (hi, lo)
        lower = None if lower is None else lower / c
        upper = None if upper is None else upper / c
        normalized_sense = (
            "eq"
            if lower == upper
            else "le"
            if lower is None
            else "ge"
            if upper is None
            else "range"
        )
        seed = [typ, normalized_sense, "unit" if lower == upper == 1 else "other"]
        metadata.update(
            scale=str(c),
            lower=None if lower is None else str(lower),
            upper=None if upper is None else str(upper),
            seed=seed,
        )
        groups.append(ident("sets_", seed))
    for g in groups:
        db.execute("INSERT OR IGNORE INTO sets VALUES(?,?)", (g, r))
        put(db, "index_sets", f"{g}:{r}", {**metadata, "id": g})
        for (v,) in db.execute("SELECT v" + selected, args):
            member(g, v)


def add_member(db, g, r, v, metadata=True):
    db.execute("INSERT OR IGNORE INTO sets VALUES(?,?)", (g, r))
    db.execute("INSERT OR IGNORE INTO members VALUES(?,?,?)", (g, r, v))
    if metadata:
        put(
            db,
            "index_sets",
            f"{g}:{r}",
            {"id": g, "set": r, "row_id": r, "source": "provided membership"},
        )
    put(
        db,
        "index_members",
        f"{g}:{r}:{v}",
        {"id": g, "set": r, "row_id": r, "variable": v},
    )


def validate(m, spec, budget=None):
    b = Budget(**(budget or {}))
    kind = spec["kind"]
    if spec.get("analysis"):
        m = ScopedModel(m, spec["analysis"])
    db = connect(m, True) if (m.path / filename(m, ".sqlite")).exists() else None
    result = envelope(m, valid=True, scope=spec)
    temporary = None
    if kind == "indexed_operator" and "candidate" in spec:
        import tempfile

        candidate = spec["candidate"]
        b.require(len(js(candidate).encode()) * 8)
        if db is not None:
            db.close()
        temporary = tempfile.TemporaryDirectory(prefix="lpir-hypothesis-")
        db = sqlite3.connect(temporary.name + "/candidate.sqlite")
        db.executescript(
            "CREATE TABLE records(relation TEXT,key TEXT,record TEXT,PRIMARY KEY(relation,key)); CREATE TABLE coords(t TEXT,v INTEGER,a INTEGER,b INTEGER,PRIMARY KEY(t,v),UNIQUE(t,a,b));"
        )
        card = candidate["operator"]
        spec = {**spec, "id": card["id"]}
        put(db, "index_operators", card["id"], card)
        for x in candidate["coordinates"]:
            db.execute(
                "INSERT INTO coords VALUES(?,?,?,?)",
                (card["tensor"], x["variable"], x["axis0"], x["axis1"]),
            )
        for x in candidate["rows"]:
            put(db, "index_rows", str(x["row_id"]), {**x, "id": card["id"]})
        spec["kernel"] = candidate["kernel"]
    try:
        if kind == "indexed_operator":
            if db is None:
                raise LPIRError("Provide a candidate or run operator analysis first")
            card = get(db, "index_operators", spec["id"])
            if "ast" in spec and spec["ast"] != card["ast"]:
                return {
                    **result,
                    "valid": False,
                    "verification": "refuted",
                    "counterexample": {"reason": "unsupported or changed indexed AST"},
                }
            result.update(check_operator(m, db, spec["id"], b, spec.get("kernel")))
        elif kind == "indexed_partition":
            sets = spec.get("sets")
            universe = spec.get("variables")
            if sets is None:
                if db is None:
                    raise LPIRError("Provide sets or run incidence analysis first")
                g = spec["id"]
                sets = (
                    {
                        "row_id": r,
                        "variables": (
                            v
                            for (v,) in db.execute(
                                "SELECT v FROM members WHERE g=? AND r=?", (g, r)
                            )
                        ),
                    }
                    for (r,) in db.execute("SELECT r FROM sets WHERE g=?", (g,))
                )
            seen = set()
            number = 0
            for x in sets:
                b.tick()
                number += 1
                vs = set(x["variables"])
                b.require((len(seen) + len(vs)) * 128)
                actual = {v for v, c in m.iter_terms(x["row_id"])}
                if not vs or not vs <= actual or seen & vs:
                    result.update(
                        valid=False,
                        counterexample={
                            "row_id": x["row_id"],
                            "reason": "empty, overlapping or absent membership",
                        },
                    )
                    break
                seen |= vs
            if number == 0 or (universe is not None and seen != set(universe)):
                result.update(
                    valid=False,
                    counterexample={
                        "reason": "universe coverage mismatch or empty family"
                    },
                )
            result["checked_sets"] = number
        elif kind == "cartesian":
            if "partitions" in spec:
                # An arbitrary caller coordinate hypothesis can be checked with no discovery.
                parts = spec["partitions"]
                shape = [len(p) for p in parts]
                if len(parts) != 2:
                    raise LPIRError("Cartesian validator requires two partitions")
                axes = []
                for part in parts:
                    membership = {}
                    for i, x in enumerate(part):
                        b.tick()
                        vs = set(x["variables"])
                        actual = {v for v, c in m.iter_terms(x["row_id"])}
                        if not vs or not vs <= actual or membership.keys() & vs:
                            result.update(
                                valid=False,
                                counterexample={
                                    "row_id": x["row_id"],
                                    "reason": "invalid partition",
                                },
                            )
                            break
                        for v in vs:
                            membership[v] = i
                    axes.append(membership)
                coords = spec["coordinates"]
                expected_count = len(axes[0])
            else:
                if db is None:
                    raise LPIRError("Provide partitions and coordinates first")
                card = get(db, "index_tensors", spec["id"])
                shape = card["shape"]
                axes = []
                for side, g in enumerate(card["partitions"]):
                    membership = {}
                    for a in (
                        json.loads(x[0])
                        for x in db.execute(
                            "SELECT record FROM records WHERE relation='index_axes' AND json_extract(record,'$.id')=? AND json_extract(record,'$.axis')=?",
                            (card["id"], side),
                        )
                    ):
                        vs = {
                            v
                            for (v,) in db.execute(
                                "SELECT v FROM members WHERE g=? AND r=?",
                                (g, a["row_id"]),
                            )
                        }
                        actual = {v for v, c in m.iter_terms(a["row_id"])}
                        if not vs or not vs <= actual or membership.keys() & vs:
                            result.update(
                                valid=False,
                                counterexample={
                                    "row_id": a["row_id"],
                                    "reason": "partition disagrees with raw model",
                                },
                            )
                            break
                        for v in vs:
                            membership[v] = a["index"]
                    axes.append(membership)
                coords = spec.get("coordinates")
                if coords is None:
                    coords = (
                        json.loads(x[0])
                        for x in db.execute(
                            "SELECT record FROM records WHERE relation='index_coordinates' AND json_extract(record,'$.id')=?",
                            (card["id"],),
                        )
                    )
                expected_count = card["variables"]
            b.require(sum(len(a) for a in axes) * 160)
            seen = set()
            cells = set()
            for x in coords:
                b.tick()
                v = x["variable"]
                cell = (x["axis0"], x["axis1"])
                if (
                    v in seen
                    or cell in cells
                    or any(v not in a for a in axes)
                    or cell != (axes[0].get(v), axes[1].get(v))
                ):
                    result.update(valid=False, counterexample=x)
                    break
                seen.add(v)
                cells.add(cell)
            if (
                set(axes[0]) != set(axes[1])
                or seen != set(axes[0])
                or len(seen) != expected_count
            ):
                result.update(
                    valid=False,
                    counterexample={"reason": "coordinate universe mismatch"},
                )
            result["complete_product"] = len(cells) == shape[0] * shape[1]
        elif kind == "row_bundle":
            if db is None:
                raise LPIRError("Run coordinate analysis first")
            card = get(db, "index_operators", spec["id"])
            rows = spec.get("rows")
            if rows is None:
                rows = (
                    json.loads(x[0])
                    for x in db.execute(
                        "SELECT record FROM records WHERE relation='index_rows' AND json_extract(record,'$.id')=?",
                        (spec["id"],),
                    )
                )
            seen = set()
            original = set()
            anchors = {}
            for x in rows:
                b.tick()
                key = (x["p"], x["k"])
                actual = dict(m.iter_terms(x["row_id"]))
                anchor = x["anchor"]
                if (
                    key in seen
                    or x["row_id"] in original
                    or anchor not in actual
                    or (x["p"] in anchors and anchors[x["p"]] != anchor)
                ):
                    result.update(valid=False, counterexample=x)
                    break
                anchors[x["p"]] = anchor
                for v in actual:
                    if v == anchor:
                        continue
                    coord = db.execute(
                        "SELECT a,b FROM coords WHERE t=? AND v=?", (card["tensor"], v)
                    ).fetchone()
                    if coord is None or coord[card["fixed_axis"]] != x["k"]:
                        result.update(
                            valid=False,
                            counterexample={
                                "row_id": x["row_id"],
                                "variable": v,
                                "reason": "wrong fixed axis",
                            },
                        )
                seen.add(key)
                original.add(x["row_id"])
            if len(seen) != card["rows"] or len(set(anchors.values())) != len(anchors):
                result.update(
                    valid=False,
                    counterexample={
                        "reason": "row coverage or anchor bijection mismatch"
                    },
                )
        elif kind == "axis_alignment":
            if db is None:
                raise LPIRError("Run operator analysis first")
            card = get(db, "index_operators", spec["id"])
            oid = spec["id"]
            n, nj = card["kernel_shape"][:2]
            if not card["axis_invariant"] or n != nj:
                return {
                    **result,
                    "valid": False,
                    "verification": "refuted",
                    "counterexample": {"reason": "not a square invariant kernel"},
                }
            rows = spec.get("mapping")
            if rows is None:
                rows = (
                    json.loads(x[0])
                    for x in db.execute(
                        "SELECT record FROM records WHERE relation='index_alignment' AND json_extract(record,'$.id')=?",
                        (oid,),
                    )
                )
            seen = set()
            cols = set()
            for x in rows:
                b.tick()
                vals = {
                    q["j"]: F(q["value"])
                    for q in (
                        json.loads(z[0])
                        for z in db.execute(
                            "SELECT record FROM records WHERE relation='index_kernel' AND json_extract(record,'$.id')=? AND json_extract(record,'$.p')=?",
                            (oid, x["p"]),
                        )
                    )
                }
                mx = max([*vals.values(), *([F(0)] if len(vals) < nj else [])])
                matches = [j for j in range(nj) if vals.get(j, 0) == mx]
                if (
                    len(matches) != 1
                    or matches[0] != x["j"]
                    or x["p"] in seen
                    or x["j"] in cols
                ):
                    result.update(valid=False, counterexample=x)
                    break
                seen.add(x["p"])
                cols.add(x["j"])
            if len(seen) != n:
                result.update(
                    valid=False,
                    counterexample={"reason": "alignment coverage mismatch"},
                )
        else:
            raise LPIRError("Unknown indexed validation")
    except BudgetExceeded:
        result.update(
            valid=None,
            verification="candidate",
            completeness="partial",
            continuation={"spec": spec},
        )
    finally:
        if db is not None:
            db.close()
        if temporary is not None:
            temporary.cleanup()
    if result["valid"] is False:
        result["verification"] = "refuted"
    return result


def summary(m, max_bytes=6000):
    if not (m.path / "indexed.json").exists():
        return None
    meta = json.loads((m.path / "indexed.json").read_text())
    out = {
        "version": VERSION,
        "search_completeness": "complete"
        if meta["state"]["phase"] == "done"
        else "partial",
        "continuation": None
        if meta["state"]["phase"] == "done"
        else {"kind": "operators", "scope": meta["scope"], "handle": meta["handle"]},
        "tensors": [],
        "operators": [],
        "relations": list(RELATIONS),
        "omitted_cards": 0,
    }
    db = connect(m, True)
    try:
        for table, key in (
            ("index_tensors", "tensors"),
            ("index_operators", "operators"),
        ):
            for (s,) in db.execute(
                "SELECT record FROM records WHERE relation=? ORDER BY key", (table,)
            ):
                card = json.loads(s)
                if (
                    table == "index_operators"
                    and card.get("verification") != "verified"
                ):
                    continue
                out[key].append(card)
                if len(js(out).encode()) > max_bytes:
                    out[key].pop()
                    out["omitted_cards"] += 1
    finally:
        db.close()
    return out


def verify(m):
    """Recheck published coordinate and operator views; unfinished search is separate."""
    if not (m.path / "indexed.json").exists():
        return {"status": "not_run"}
    db = connect(m, True)
    counts = Counter()
    try:
        for relation, kind in (
            ("index_tensors", "cartesian"),
            ("index_operators", "indexed_operator"),
        ):
            for key, s in db.execute(
                "SELECT key,record FROM records WHERE relation=?", (relation,)
            ):
                if json.loads(s).get("verification") != "verified":
                    continue
                checked = validate(m, {"kind": kind, "id": key})
                if checked["valid"] is not True:
                    raise LPIRError(
                        "Indexed view verification failed or incomplete: " + js(checked)
                    )
                counts[kind] += 1
    finally:
        db.close()
    state = json.loads((m.path / "indexed.json").read_text())["state"]
    return {
        "status": "PASS",
        "views": dict(counts),
        "search_completeness": "complete" if state["phase"] == "done" else "partial",
    }
