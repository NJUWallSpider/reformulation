"""Optional graph views. Scores rank proposals; they are not bound guarantees."""

from collections import Counter, defaultdict
import hashlib, math
import numpy as np
from .numbers import LPIRError


class DSU:
    def __init__(self, n):
        self.p = np.arange(n, dtype=np.int64)
        self.rank = np.zeros(n, dtype=np.int8)

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = int(self.p[x])
        return x

    def union(self, x, y):
        x, y = self.find(x), self.find(y)
        if x == y:
            return
        if self.rank[x] < self.rank[y]:
            x, y = y, x
        self.p[y] = x
        if self.rank[x] == self.rank[y]:
            self.rank[x] += 1


def graph_job(m, db, kind, scope, b):
    if scope is not None and (not isinstance(scope, dict) or set(scope) != {"rows"}):
        raise LPIRError('Graph scope must be {"rows": [row IDs]}')
    rows = sorted(set(scope["rows"])) if scope else range(len(m.rows))
    if any(type(r) is not int or r < 0 or r >= len(m.rows) for r in rows):
        raise LPIRError("Invalid scope row")
    n = len(m.variables)
    nr = len(m.rows)
    z = sum(int(m.row_ptr[r + 1] - m.row_ptr[r]) for r in rows)
    b.require((n + nr) * (512 if kind == "decomposition" else 80) + z * 192)
    edges = []
    variables = set(range(n)) if scope is None else set()
    for r in rows:
        b.tick()
        for v, c in m.iter_terms(r):
            edges.append((r, nr + v))
            variables.add(v)
    if kind == "network":
        selected = set(rows)
        arcs = []
        rejected = 0
        for v in sorted(variables):
            b.tick()
            inc = []
            for r in m.incident(v):
                if r in selected:
                    # Fetch a single coefficient via the sorted CSR row.
                    start, end = int(m.row_ptr[r]), int(m.row_ptr[r + 1])
                    j = start + int(np.searchsorted(m.col_idx[start:end], v))
                    inc.append((r, m.num(int(m.coeff_id[j]))))
            if len(inc) == 2 and inc[0][1] == -inc[1][1]:
                source = next(r for r, c in inc if c < 0)
                target = next(r for r, c in inc if c > 0)
                var = m.variables[v]
                arcs.append(
                    {
                        "variable": v,
                        "source": source,
                        "target": target,
                        "column_scale": str(abs(inc[0][1])),
                        "lower": str(m.num(var[3])) if var[3] is not None else None,
                        "upper": str(m.num(var[4])) if var[4] is not None else None,
                        "cost": str(m.num(var[5])),
                        "evidence": "two opposite exact coefficients in scoped rows",
                    }
                )
            else:
                rejected += 1
        return arcs, {
            "verified_arcs": len(arcs),
            "rejected_columns": rejected,
            "whole_scope_is_incidence_matrix": rejected == 0,
            "scope_rows": len(rows),
            "interpretation": "signed incidence submatrix; no total-unimodularity or whole-model equivalence claim",
        }

    def partition(border):
        d = DSU(nr + n)
        for r, v in edges:
            if r not in border:
                d.union(r, v)
        by = defaultdict(lambda: {"rows": [], "variables": []})
        for r in rows:
            if r not in border:
                by[d.find(r)]["rows"].append(r)
        for v in sorted(variables):
            by[d.find(nr + v)]["variables"].append(v)
        blocks = list(by.values())
        lookup = {v: i for i, x in enumerate(blocks) for v in x["variables"]}
        counts = Counter(lookup[v - nr] for r, v in edges if r not in border)
        total = sum(counts.values())
        q = 1 - sum((c / total) ** 2 for c in counts.values()) if total else 0
        ratio = len(border) / len(rows) if rows else 0
        score = q * math.exp(-5 * ratio)
        return {
            "blocks": blocks,
            "border_rows": sorted(border),
            "score": score,
            "border_fraction": ratio,
            "block_nonzeros": list(counts.values()),
            "integer_variables": sum(m.variables[v][2] != "C" for v in variables),
            "objective_variables": sum(
                bool(m.num(m.variables[v][5])) for v in variables
            ),
            "verification": "verified",
            "score_status": "heuristic",
            "evidence": "every original scoped incidence is in its block or the row border",
        }

    if kind == "components":
        rec = partition(set())
        return [rec], {"blocks": len(rec["blocks"]), "scope_rows": len(rows)}
    if kind == "wl":
        vertices = list(rows) + [nr + v for v in sorted(variables)]
        adj = defaultdict(list)
        labels = {r: "row" for r in rows}
        labels.update({nr + v: "variable:" + m.variables[v][2] for v in variables})
        for r, v in edges:
            adj[r].append(v)
            adj[v].append(r)
        for _ in range(3):
            b.tick(len(vertices))
            labels = {
                v: hashlib.sha256(
                    repr((labels[v], sorted(labels[u] for u in adj[v]))).encode()
                ).hexdigest()
                for v in vertices
            }
        return [
            {
                "vertex": v,
                "kind": "row" if v < nr else "variable",
                "id": v if v < nr else v - nr,
                "color": labels[v],
            }
            for v in vertices
        ], {
            "rounds": 3,
            "classes": len(set(labels.values())),
            "symmetry_certificate": False,
        }
    try:
        import igraph as ig
    except ImportError as e:
        raise LPIRError(
            "decomposition requires optional dependency: install lpir[graph]"
        ) from e
    initial = partition(set())
    candidates = [initial]
    border = set()
    seen = set()

    def keep(rec):
        key = hashlib.sha256(repr(rec["border_rows"]).encode()).hexdigest()
        if key in seen:
            return
        seen.add(key)
        candidates.append(rec)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        del candidates[8:]

    vertices = list(rows) + [nr + v for v in sorted(variables)]
    index = {v: i for i, v in enumerate(vertices)}
    # Fast-greedy is used only to propose separators; connected components define blocks.
    g = ig.Graph(
        n=len(vertices), edges=[(index[r], index[v]) for r, v in edges], directed=False
    )
    membership = (
        g.community_fastgreedy().as_clustering().membership
        if edges
        else list(range(len(vertices)))
    )
    noise = Counter(r for r, v in edges if membership[index[r]] != membership[index[v]])
    for r, degree in sorted(noise.items(), key=lambda x: (-x[1], x[0])):
        b.tick()
        border.add(r)
        rec = partition(border)
        if len(rec["blocks"]) > 1:
            keep(rec)
    # Greedy merge: returning a border row merges precisely the blocks it touches.
    while border:
        b.tick()
        best = None
        for r in sorted(border):
            b.tick()
            rec = partition(border - {r})
            if best is None or rec["score"] > best[0]:
                best = (rec["score"], r, rec)
        border.remove(best[1])
        keep(best[2])
        if len(best[2]["blocks"]) <= 2:
            break
    candidates.sort(key=lambda r: r["score"], reverse=True)
    return candidates[:8], {
        "candidate_count": min(8, len(candidates)),
        "ranking": "Q * exp(-5 * border_fraction)",
        "strengthening_guarantee": False,
    }
