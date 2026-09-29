"""Coordinate discovery and candidate family compilation."""

from collections import defaultdict
from fractions import Fraction as F
import json
from .structural_store import js, ident
from .structural_algebra import parameter


def components(vertices, groups):
    parent = {v: v for v in vertices}

    def root(v):
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for group in groups:
        group = list(group)
        if not group:
            continue
        for v in group[1:]:
            parent[root(v)] = root(group[0])
    out = defaultdict(set)
    for v in vertices:
        out[root(v)].add(v)
    return sorted(out.values(), key=lambda s: min(s))


class Discovery:
    def __init__(self, m, rows, budget, blocks=None):
        self.m, self.rows, self.budget = m, rows, budget
        self.blocks = blocks or []
        self.fiber_indexes = {}
        self.owner = {}
        for block in self.blocks:
            block["coords"] = {int(v): tuple(c) for v, c in block["coords"].items()}
            self.owner.update({v: block for v in block["coords"]})
        self.kinds = {
            v: m.variables[v][2] for v in {v for row in rows for v, c in row["terms"]}
        }

    def members(self, block, axes, values):
        key = (block["id"], tuple(axes))
        if key not in self.fiber_indexes:
            index = defaultdict(set)
            for v, c in block["coords"].items():
                index[tuple(c[a] for a in axes)].add(v)
            self.fiber_indexes[key] = index
        return self.fiber_indexes[key].get(tuple(values), set())

    def add(self, coords, axes, origin):
        if (
            not coords
            or any(v in self.owner for v in coords)
            or len(set(coords.values())) != len(coords)
        ):
            return None
        bid = ident("sblock", sorted(coords))
        b = {"id": bid, "coords": coords, "axes": axes, "origin": origin}
        self.blocks.append(b)
        self.owner.update({v: b for v in coords})
        return b

    def seeds(self):
        if not (self.m.path / "indexed.sqlite").exists():
            return
        from .indexed_store import connect as index_connect

        db = index_connect(self.m, True)
        try:
            for (txt,) in db.execute(
                "SELECT record FROM records WHERE relation='index_tensors' ORDER BY json_extract(record,'$.variables') DESC,key"
            ):
                self.budget.tick(0)
                t = json.loads(txt)
                if t.get("verification") != "verified":
                    continue
                self.budget.require(t["variables"] * 1024)
                coords = {
                    v: (a, b)
                    for v, a, b in db.execute(
                        "SELECT v,a,b FROM coords WHERE t=?", (t["id"],)
                    )
                }
                # A matching of missing cells supplies an explicit axis bijection.
                axes = [ident("axis", [t["id"], j]) for j in range(2)]
                alignment = None
                n, p = t["shape"]
                if n == p and len(coords) == n * (n - 1) and n > 2:
                    support = set(coords.values())
                    missing = {
                        a: next(b for b in range(n) if (a, b) not in support)
                        for a in range(n)
                        if sum((a, b) in support for b in range(n)) == n - 1
                    }
                    if len(missing) == n and len(set(missing.values())) == n:
                        inverse = {b: a for a, b in missing.items()}
                        coords = {v: (a, inverse[b]) for v, (a, b) in coords.items()}
                        axes[1] = axes[0]
                        alignment = sorted(missing.items())
                # Scope limits rows; coordinates remain references to global verified seeds.
                if any(v in self.kinds for v in coords):
                    self.add(
                        coords,
                        axes,
                        {
                            "kind": "seed",
                            "tensor": t["id"],
                            "missing_cell_alignment": alignment,
                            "coordinate_scope": "global verified seed",
                        },
                    )
        finally:
            db.close()

    def transfers(self):
        groups = defaultdict(list)
        for row in self.rows:
            self.budget.tick(0)
            terms = row["terms"]
            known = [(v, c) for v, c in terms if v in self.owner]
            unknown = [(v, c) for v, c in terms if v not in self.owner]
            if len(known) != 1 or len(unknown) != 1:
                continue
            v, c = unknown[0]
            w, d = known[0]
            block = self.owner[w]
            sense = "eq" if row["lo"] == row["hi"] else "bound"
            groups[(block["id"], self.kinds[v], sense, F(c) * F(d) > 0)].append(
                (v, w, row["row_id"])
            )
        changed = False
        for key, bindings in sorted(groups.items()):
            block = next(b for b in self.blocks if b["id"] == key[0])
            candidates = defaultdict(set)
            for v, w, r in bindings:
                candidates[v].add(block["coords"][w])
            if any(len(cs) != 1 for cs in candidates.values()) or any(
                v in self.owner for v in candidates
            ):
                continue
            coords = {v: next(iter(cs)) for v, cs in candidates.items()}
            origin = {
                "kind": "coordinate_transfer",
                "input_block": block["id"],
                "rows": sorted(r for v, w, r in bindings),
            }
            if len(set(coords.values())) == len(coords):
                if len(coords) > 1:
                    changed |= self.add(coords, block["axes"], origin) is not None
                continue
            selected = set(coords)
            incidence = []
            covered = set()
            witness = []
            for row in self.rows:
                ts = row["terms"]
                vs = {v for v, c in ts}
                cs = [F(c) for v, c in ts]
                if (
                    row["lo"] is not None
                    and row["lo"] == row["hi"]
                    and vs <= selected
                    and any(c > 0 for c in cs)
                    and any(c < 0 for c in cs)
                    and len({abs(c) for c in cs}) == 1
                ):
                    incidence.append(vs)
                    covered.update(vs)
                    witness.append(row["row_id"])
            if covered != selected:
                continue
            parts = components(selected, incidence)
            if len(parts) < 2 or any(len(p) < 2 for p in parts):
                continue
            if any(len({coords[v] for v in p}) != len(p) for p in parts):
                continue
            axis = ident("axis", sorted(selected))
            labels = list(range(len(parts)))
            alignments = []
            for a, domain in enumerate(block["axes"]):
                universe = {c[a] for c in block["coords"].values()}
                missing = [universe - {coords[v][a] for v in p} for p in parts]
                if all(len(s) == 1 for s in missing) and len(
                    {next(iter(s)) for s in missing}
                ) == len(parts) == len(universe):
                    alignments.append((domain, [next(iter(s)) for s in missing], a))
            if len(alignments) == 1:
                axis, labels, a = alignments[0]
                origin["alignment"] = {"excluded_axis": a, "labels": labels}
            origin.update(kind="incidence_lift", incidence_rows=witness)
            lifted = {
                v: (labels[j], *coords[v]) for j, p in enumerate(parts) for v in p
            }
            changed |= self.add(lifted, [axis, *block["axes"]], origin) is not None
        return changed

    def definitions(self):
        groups = defaultdict(list)
        for row in self.rows:
            self.budget.tick(0)
            if row["lo"] != row["hi"] or row["lo"] is None:
                continue
            known = [(v, F(c)) for v, c in row["terms"] if v in self.owner]
            unknown = [(v, F(c)) for v, c in row["terms"] if v not in self.owner]
            if len(unknown) != 1 or len(known) < 2:
                continue
            v, c = unknown[0]
            blocks = {self.owner[w]["id"] for w, d in known}
            if len(blocks) != 1:
                continue
            block = self.owner[known[0][0]]
            cs = [block["coords"][w] for w, d in known]
            fixed = tuple(
                a for a in range(len(block["axes"])) if len({x[a] for x in cs}) == 1
            )
            # Only complete observed fibers justify transferring coordinates.
            selected = {w for w, d in known}
            if selected != self.members(block, fixed, [cs[0][a] for a in fixed]):
                continue
            groups[(block["id"], fixed, self.kinds[v])].append(
                (v, tuple(cs[0][a] for a in fixed), row["row_id"])
            )
        changed = False
        for (bid, fixed, kind), bindings in sorted(groups.items()):
            choices = defaultdict(set)
            for v, c, r in bindings:
                choices[v].add(c)
            if any(len(c) != 1 for c in choices.values()):
                continue
            coords = {v: next(iter(c)) for v, c in choices.items()}
            block = next(b for b in self.blocks if b["id"] == bid)
            changed |= (
                self.add(
                    coords,
                    [block["axes"][a] for a in fixed],
                    {
                        "kind": "definition",
                        "input_block": bid,
                        "fixed_axes": list(fixed),
                        "rows": sorted(r for v, c, r in bindings),
                    },
                )
                is not None
            )
        return changed

    def objectives(self):
        nonzero = {
            v
            for v in self.kinds
            if self.m.num(self.m.variables[v][5]) != 0 and v not in self.owner
        }
        if len(nonzero) == 1:
            v = nonzero.pop()
            self.add({v: ()}, [], {"kind": "objective_scalar"})

    def discover(self):
        self.seeds()
        self.objectives()
        while True:
            changed = self.transfers()
            changed |= self.definitions()
            if not changed:
                break
        from .structural_domain import reduce_blocks

        return reduce_blocks(self.blocks)


def normalized(row, owner):
    terms = [(v, F(c)) for v, c in row["terms"]]
    lo = F(row["lo"]) if row["lo"] is not None else None
    hi = F(row["hi"]) if row["hi"] is not None else None
    scale = min((abs(c) for v, c in terms), default=F(1))
    if lo is not None and hi is None:
        scale = -scale
    elif lo == hi:
        pos = sum(c > 0 for v, c in terms)
        if pos < len(terms) - pos:
            scale = -scale
        elif pos == len(terms) - pos and terms:
            v, c = min(
                terms,
                key=lambda vc: (
                    len(owner[vc[0]]["axes"]),
                    owner[vc[0]]["id"],
                    owner[vc[0]]["coords"][vc[0]],
                ),
            )
            if c < 0:
                scale = -scale
    if scale < 0:
        lo, hi = hi, lo
    return (
        [(v, str(c / scale)) for v, c in terms],
        (str(lo / scale) if lo is not None else None),
        (str(hi / scale) if hi is not None else None),
        str(scale),
    )


def draft(row, discovery):
    owner = discovery.owner
    if not row["terms"] or any(v not in owner for v, c in row["terms"]):
        return None
    terms, lo, hi, scale = normalized(row, owner)
    grouped = defaultdict(list)
    for v, c in terms:
        grouped[(owner[v]["id"], F(c) > 0)].append((v, c))
    groups, signature = [], []
    for (bid, positive), items in sorted(grouped.items()):
        block = owner[items[0][0]]
        cs = [block["coords"][v] for v, c in items]
        fixed = [a for a in range(len(block["axes"])) if len({x[a] for x in cs}) == 1]
        members = discovery.members(block, fixed, [cs[0][a] for a in fixed])
        from .structural_domain import support_restriction, matching_support

        pattern = None
        restriction = support_restriction(
            cs, [block["coords"][v] for v in members], fixed
        )
        if restriction is None:
            pattern = matching_support(cs, [block["coords"][v] for v in members])
            if pattern is None:
                return None
            restriction = {}
        groups.append(
            {
                "block": bid,
                "restriction": restriction,
                "support_pattern": pattern,
                "fixed_axes": fixed,
                "fixed_values": [cs[0][a] for a in fixed],
                "values": [[list(block["coords"][v]), c] for v, c in items],
            }
        )
        signature.append([bid, positive, fixed, restriction, pattern])
    sense = "eq" if lo == hi else "le" if lo is None else "range"
    return {
        "row_id": row["row_id"],
        "signature": js([sense, signature]),
        "sense": sense,
        "groups": groups,
        "lo": lo,
        "hi": hi,
        "scale": scale,
    }


def compile_family(drafts, blocks, budget):
    if len(drafts) < 2:
        return None
    byid = {b["id"]: b for b in blocks}
    columns = []
    for g, group in enumerate(drafts[0]["groups"]):
        b = byid[group["block"]]
        for j, a in enumerate(group["fixed_axes"]):
            columns.append(
                (
                    g,
                    j,
                    b["axes"][a],
                    tuple(d["groups"][g]["fixed_values"][j] for d in drafts),
                )
            )
    from .structural_domain import unify_columns, domain_descriptor

    keep, bindings, index_counts = unify_columns(columns, budget)
    unique = [(columns[i][2], columns[i][3]) for i in keep]
    maps = {}
    index_maps = {}
    term_maps = defaultdict(dict)
    for col, (g, j, axis, values) in enumerate(columns):
        slot, pairs = bindings[col]
        maps[g, j] = slot
        if pairs is not None:
            mid = ident("smap", [unique[slot][0], axis, pairs])
            index_maps[mid] = {
                "id": mid,
                "source_axis": unique[slot][0],
                "target_axis": axis,
                "values": [list(x) for x in pairs],
                "verification": "verified",
            }
            term_maps[g][str(drafts[0]["groups"][g]["fixed_axes"][j])] = mid
    domain = [tuple(values[r] for axis, values in unique) for r in range(len(drafts))]
    if len(set(domain)) != len(domain):
        return None
    row_axes = [axis for axis, values in unique]
    terms = []
    params = {}
    for g, group in enumerate(drafts[0]["groups"]):
        b = byid[group["block"]]
        fixed = [[a, maps[g, j]] for j, a in enumerate(group["fixed_axes"])]
        free = [a for a in range(len(b["axes"])) if a not in group["fixed_axes"]]
        inputs = [{"row": a} for a in range(len(row_axes))] + [
            {"axis": a} for a in free
        ]
        points = [
            (tuple(rc) + tuple(c[a] for a in free), value)
            for d, rc in zip(drafts, domain)
            for c, value in d["groups"][g]["values"]
        ]
        name = "P" + str(g)
        params[name] = parameter(points, inputs, budget)
        terms.append(
            {
                "block": b["id"],
                "fixed": fixed,
                "sum_axes": free,
                "parameter": name,
                "maps": term_maps[g],
                "restriction": group.get("restriction", {}),
                "support_pattern": group.get("support_pattern"),
            }
        )
    for name, key in (("lower", "lo"), ("upper", "hi")):
        params[name] = parameter(
            [(rc, d[key]) for d, rc in zip(drafts, domain)],
            [{"row": a} for a in range(len(row_axes))],
            budget,
        )
    ast = {
        "op": "affine_range",
        "terms": terms,
        "lower": "lower",
        "upper": "upper",
        "sense": drafts[0]["sense"],
    }
    fid = ident("sfamily", sorted(d["row_id"] for d in drafts))
    # Comparable representations omit raw IDs/provenance on both sides.
    compact = {
        "ast": ast,
        "domain": domain,
        "parameters": params,
        "index_maps": index_maps,
    }
    flat = [
        {
            "terms": [
                [g["block"], c, value] for g in d["groups"] for c, value in g["values"]
            ],
            "lower": d["lo"],
            "upper": d["hi"],
        }
        for d in drafts
    ]
    lengths = {
        "indexed_bytes": len(js(compact).encode()),
        "flat_bytes": len(js(flat).encode()),
        "map_bytes": len(js(index_maps).encode()),
        "encoding": "canonical-json-v1; raw provenance excluded on both sides",
    }
    if lengths["indexed_bytes"] >= lengths["flat_bytes"]:
        return None
    definition = None
    for term in terms:
        b = byid[term["block"]]
        if (
            not term["sum_axes"]
            and b["origin"]["kind"] == "definition"
            and set(d["row_id"] for d in drafts) <= set(b["origin"]["rows"])
        ):
            definition = {"block": b["id"], "kind": "exact_linear_definition"}
            break
    return {
        "id": fid,
        "row_axes": row_axes,
        "index_maps": index_maps,
        "independent_indices": index_counts,
        "symbolic_domain": domain_descriptor(domain, row_axes),
        "domain": [list(x) for x in domain],
        "ast": ast,
        "parameters": params,
        "rows": len(drafts),
        "definition": definition,
        "description_length": lengths,
        "verification": "verified",
        "instances": [
            {"row_id": d["row_id"], "coordinates": list(rc), "scale": d["scale"]}
            for d, rc in zip(drafts, domain)
        ],
    }
