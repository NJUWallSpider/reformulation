"""Exact, name-independent coordinate propagation and indexed affine families.

This sidecar is an additive view of the immutable matrix. Discovery never assigns
model semantics, eliminates variables, or fills unobserved parameter cells.
"""

from collections import Counter, defaultdict
from contextlib import closing
from fractions import Fraction as F
from itertools import combinations, product
import hashlib
import json
import sqlite3
import time

from .jobs import Budget, BudgetExceeded
from .numbers import LPIRError

VERSION = "2.0"
RELATIONS = tuple(
    "structure_" + x
    for x in (
        "blocks",
        "coordinates",
        "families",
        "rows",
        "parameters",
        "links",
        "evidence",
        "residuals",
        "views",
        "index_maps",
        "subsets",
        "objective",
    )
)


def js(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


def ident(prefix, x):
    return prefix + "_" + hashlib.sha256(js(x).encode()).hexdigest()[:20]


def filename(analysis=None, suffix=".sqlite"):
    if analysis is not None and (
        not isinstance(analysis, str)
        or not analysis.startswith("structure_scope_")
        or len(analysis) != 36
        or any(c not in "0123456789abcdef" for c in analysis[16:])
    ):
        raise LPIRError("Invalid structure scope identifier")
    return "structure" + ("_" + analysis if analysis else "") + suffix


def connect(m, analysis=None, write=False):
    path = m.path / filename(analysis)
    if not path.exists() and not write:
        raise LPIRError("No structure analysis; run analyze('structure')")
    db = sqlite3.connect(
        path if write else f"file:{path.resolve()}?mode=ro", uri=not write
    )
    if write:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS records(relation TEXT,key TEXT,record TEXT,PRIMARY KEY(relation,key));
        CREATE INDEX IF NOT EXISTS records_id ON records(relation,json_extract(record,'$.id'));
        CREATE TABLE IF NOT EXISTS work(row_id INTEGER PRIMARY KEY,record TEXT);
        CREATE TABLE IF NOT EXISTS drafts(row_id INTEGER PRIMARY KEY,signature TEXT,record TEXT);
        CREATE INDEX IF NOT EXISTS draft_signature ON drafts(signature);
        """)
    old = db.execute("SELECT value FROM meta WHERE key='model'").fetchone()
    expected = [m.manifest["model_version"], VERSION]
    if old and (
        json.loads(old[0])[0] != expected[0] or json.loads(old[0])[1] != VERSION
    ):
        db.close()
        raise LPIRError(
            "Structure sidecar version mismatch; rebuild in a new directory"
        )
    if write and not old:
        meta(db, "model", expected)
        db.commit()
    return db


def meta(db, k, v):
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, js(v)))


def readmeta(db, k, default=None):
    r = db.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def put(db, rel, key, value):
    db.execute(
        "INSERT OR REPLACE INTO records VALUES(?,?,?)", (rel, str(key), js(value))
    )


def records(db, rel):
    return [
        json.loads(r[0])
        for r in db.execute(
            "SELECT record FROM records WHERE relation=? ORDER BY key", (rel,)
        )
    ]


def handle(rel, key=None, analysis=None):
    return {
        "table": rel,
        **({"id": key} if key else {}),
        **({"analysis": analysis} if analysis else {}),
    }


def qualify(x, analysis):
    if isinstance(x, dict):
        return {
            **{k: qualify(v, analysis) for k, v in x.items()},
            **(
                {"analysis": analysis}
                if x.get("table") in RELATIONS and analysis
                else {}
            ),
        }
    if isinstance(x, list):
        return [qualify(v, analysis) for v in x]
    return x


def page(m, relation, key=None, offset=0, limit=20, analysis=None):
    if relation not in RELATIONS or offset < 0 or not 1 <= limit <= 1000:
        raise LPIRError("Invalid structure relation/page")
    if isinstance(key, dict):
        analysis, key = key.get("analysis", analysis), key.get("id")
    with closing(connect(m, analysis)) as db:
        where, args = "relation=?", [relation]
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
                (*args, limit, offset),
            )
        ]
        state = readmeta(db, "state", {})
        version = readmeta(db, "model")[1]
    return qualify(
        {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
            "truncated": offset + len(items) < total,
            "completeness": "complete" if state.get("phase") == "done" else "partial",
            "verification": "verified",
            "exactness": "exact",
            "model_version": m.manifest["model_version"],
            "analysis_version": version,
        },
        analysis,
    )


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
        from .indexed import connect as index_connect

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
        from .structural_relations import reduce_blocks

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
        from .structural_relations import support_restriction, matching_support

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


def parameter(points, inputs, budget):
    """Choose the smallest exact projection on the observed domain, never impute."""
    n = len(inputs)
    for size in range(n + 1):
        for keep in combinations(range(n), size):
            budget.tick(0)
            values = {}
            valid = True
            for key, value in points:
                k = tuple(key[a] for a in keep)
                if k in values and values[k] != value:
                    valid = False
                    break
                values[k] = value
            if valid:
                return {
                    "inputs": [inputs[a] for a in keep],
                    "values": [[list(k), v] for k, v in sorted(values.items())],
                    "invariant_inputs": [inputs[a] for a in range(n) if a not in keep],
                }
    raise LPIRError("Conflicting parameter cells")


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
    from .structural_relations import unify_columns, domain_descriptor

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


def parameter_value(p, rowcoords, coords=(), values=None):
    key = tuple(
        rowcoords[x["row"]] if "row" in x else coords[x["axis"]] for x in p["inputs"]
    )
    if values is None:
        values = {tuple(k): v for k, v in p["values"]}
    if len(values) != len(p["values"]) or key not in values:
        raise LPIRError("Duplicate or missing parameter cell")
    return values[key]


def prepare_family(family, blocks):
    """Index each distinct fiber projection once, not once per numerical row."""
    indexes = {}
    for term in family["ast"]["terms"]:
        axes = tuple(a for a, j in term["fixed"])
        key = (term["block"], axes)
        if key not in indexes:
            index = defaultdict(list)
            for v, c in blocks[term["block"]]["coords"].items():
                index[tuple(c[a] for a in axes)].append((v, c))
            indexes[key] = index
    indexes["maps"] = family.get("index_maps", {})
    tables = {
        name: {tuple(k): v for k, v in p["values"]}
        for name, p in family["parameters"].items()
    }
    return indexes, tables


def fiber(indexes, term, rc):
    key = (term["block"], tuple(a for a, j in term["fixed"]))
    from .structural_relations import map_value, matches_support

    maps = term.get("maps", {})
    values = tuple(
        map_value(indexes["maps"][maps[str(a)]], rc[j]) if str(a) in maps else rc[j]
        for a, j in term["fixed"]
    )
    candidates = indexes[key].get(values, ())
    restriction = term.get("restriction", {})
    pattern = term.get("support_pattern")
    return (
        [
            (v, c)
            for v, c in candidates
            if all(c[int(a)] in domain for a, domain in restriction.items())
            and matches_support(c, pattern)
        ]
        if restriction or pattern
        else candidates
    )


def expand(family, instance, blocks, prepared=None):
    indexes, tables = (
        prepared if prepared is not None else prepare_family(family, blocks)
    )
    terms = defaultdict(F)
    rc = instance["coordinates"]
    for t in family["ast"]["terms"]:
        b = blocks[t["block"]]
        fixed = dict(t["fixed"])
        if sorted([*fixed, *t["sum_axes"]]) != list(range(len(b["axes"]))):
            raise LPIRError("Invalid coordinate projection")
        for v, c in fiber(indexes, t, rc):
            value = parameter_value(
                family["parameters"][t["parameter"]], rc, c, tables[t["parameter"]]
            )
            terms[v] += F(value)
    lo = parameter_value(
        family["parameters"][family["ast"]["lower"]],
        rc,
        values=tables[family["ast"]["lower"]],
    )
    hi = parameter_value(
        family["parameters"][family["ast"]["upper"]],
        rc,
        values=tables[family["ast"]["upper"]],
    )
    scale = F(instance["scale"])
    if not scale:
        raise LPIRError("Zero row scale")
    lo = None if lo is None else F(lo) * scale
    hi = None if hi is None else F(hi) * scale
    if scale < 0:
        lo, hi = hi, lo
    return {v: c * scale for v, c in terms.items() if c}, lo, hi


def check_family(m, family, blocks, budget):
    if family["ast"]["op"] != "affine_range":
        raise LPIRError("Unknown structural AST")
    instances = family["instances"]
    domain = [tuple(x) for x in family["domain"]]
    if (
        len(set(domain)) != len(domain)
        or len(instances) != family["rows"]
        or len({x["row_id"] for x in instances}) != len(instances)
        or sorted(domain) != sorted(tuple(x["coordinates"]) for x in instances)
    ):
        raise LPIRError("Family row domain/coverage mismatch")
    if "symbolic_domain" in family:
        from .structural_relations import domain_descriptor

        if family["symbolic_domain"] != domain_descriptor(domain, family["row_axes"]):
            raise LPIRError("Symbolic domain mismatch")
    referenced_maps = {
        mid for t in family["ast"]["terms"] for mid in t.get("maps", {}).values()
    }
    if referenced_maps != set(family.get("index_maps", {})):
        raise LPIRError("Unreferenced index maps")
    used_parameters = set()
    expected_cells = defaultdict(set)
    prepared = prepare_family(family, blocks)
    for t in family["ast"]["terms"]:
        block = blocks[t["block"]]
        if len({a for a, j in t["fixed"]}) != len(t["fixed"]) or any(
            type(a) is not int
            or type(j) is not int
            or not 0 <= a < len(block["axes"])
            or not 0 <= j < len(family["row_axes"])
            for a, j in t["fixed"]
        ):
            raise LPIRError("Invalid fixed coordinate positions")
        for a, j in t["fixed"]:
            mid = t.get("maps", {}).get(str(a))
            if mid is not None:
                mapping = family.get("index_maps", {})[mid]
                if (
                    mapping["source_axis"] != family["row_axes"][j]
                    or mapping["target_axis"] != block["axes"][a]
                ):
                    raise LPIRError("Invalid mapped axis alignment")
                expected_keys = {x["coordinates"][j] for x in instances}
                pairs = mapping["values"]
                if len(dict(pairs)) != len(pairs) or set(dict(pairs)) != expected_keys:
                    raise LPIRError("Index map domain mismatch")
            elif block["axes"][a] != family["row_axes"][j]:
                raise LPIRError("Unproved cross-family axis alignment")
        pattern = t.get("support_pattern")
        if pattern:
            a, b = pattern["axes"]
            pairs = pattern["pairs"]
            if (
                a == b
                or not 0 <= a < len(block["axes"])
                or not 0 <= b < len(block["axes"])
            ):
                raise LPIRError("Invalid support axes")
            if len({x for x, y in pairs}) != len(pairs) or len(
                {y for x, y in pairs}
            ) != len(pairs):
                raise LPIRError("Non-bijective matching support")
            observed = {(c[a], c[b]) for c in block["coords"].values()}
            if not set(map(tuple, pairs)) <= observed:
                raise LPIRError("Unobserved matching cells")
        if set(t.get("maps", {})) - {str(a) for a, j in t["fixed"]}:
            raise LPIRError("Map on unfixed coordinate")
        for a, domain_values in t.get("restriction", {}).items():
            if int(a) not in t["sum_axes"] or len(set(domain_values)) != len(
                domain_values
            ):
                raise LPIRError("Invalid restricted sum domain")
            seen = {
                c[int(a)]
                for x in instances
                for v, c in fiber(prepared[0], t, x["coordinates"])
            }
            if seen != set(domain_values):
                raise LPIRError("Unobserved restricted coordinate")
        name = t["parameter"]
        used_parameters.add(name)
        p = family["parameters"][name]
        available = [{"row": a} for a in range(len(family["row_axes"]))] + [
            {"axis": a} for a in t["sum_axes"]
        ]
        if sorted(map(js, p["inputs"] + p["invariant_inputs"])) != sorted(
            map(js, available)
        ):
            raise LPIRError("Parameter axis-independence certificate mismatch")
        for x in instances:
            rc = x["coordinates"]
            for v, c in fiber(prepared[0], t, rc):
                expected_cells[name].add(
                    tuple(
                        rc[u["row"]] if "row" in u else c[u["axis"]]
                        for u in p["inputs"]
                    )
                )
    for side in ("lower", "upper"):
        name = family["ast"][side]
        used_parameters.add(name)
        p = family["parameters"][name]
        available = [{"row": a} for a in range(len(family["row_axes"]))]
        if sorted(map(js, p["inputs"] + p["invariant_inputs"])) != sorted(
            map(js, available)
        ):
            raise LPIRError("Bound parameter projection mismatch")
        expected_cells[name] = {
            tuple(x["coordinates"][u["row"]] for u in p["inputs"]) for x in instances
        }
    if used_parameters != set(family["parameters"]):
        raise LPIRError("Unreferenced parameter table")
    for name, cells in expected_cells.items():
        values = family["parameters"][name]["values"]
        if len(values) != len(cells) or {tuple(k) for k, v in values} != cells:
            raise LPIRError(
                "Parameter domain has duplicate, missing, or unobserved cells"
            )
    for x in instances:
        budget.tick(0)
        if len(x["coordinates"]) != len(family["row_axes"]):
            raise LPIRError("Row coordinate rank mismatch")
        terms, lo, hi = expand(family, x, blocks, prepared)
        if family["ast"].get("sense") == "eq" and (lo is None or lo != hi):
            raise LPIRError("Equality sense mismatch")
        r = x["row_id"]
        if terms != dict(m.iter_terms(r)) or (lo, hi) != (
            m.num(m.rows[r][2]),
            m.num(m.rows[r][3]),
        ):
            raise LPIRError("Exact family expansion mismatch at row " + str(r))
    return len(instances)


def block_card(m, b):
    coords = list(b["coords"].values())
    rank = len(b["axes"])
    domains = [sorted({c[a] for c in coords}) for a in range(rank)]
    shape = list(map(len, domains))
    mask = [
        [a, d]
        for a in range(rank)
        for d in range(a + 1, rank)
        if b["axes"][a] == b["axes"][d] and all(c[a] != c[d] for c in coords)
    ]
    # Enumeration is bounded by the discovered block, not an unbounded dense tensor.
    size = 1
    for n in shape:
        size *= n
    exact_mask = False
    if size <= max(4096, 4 * len(coords)):
        exact_mask = set(coords) == {
            x for x in product(*domains) if all(x[a] != x[d] for a, d in mask)
        }
    metadata = Counter()
    for v in b["coords"]:
        x = m.variables[v]
        metadata[
            (
                x[2],
                str(m.num(x[3])) if x[3] is not None else None,
                str(m.num(x[4])) if x[4] is not None else None,
                str(m.num(x[5])),
            )
        ] += 1
    return {
        "id": b["id"],
        "axes": b["axes"],
        "shape": shape,
        "axis_domains": domains,
        "variables": len(coords),
        "domain": {
            "kind": "inequalities" if exact_mask else "explicit",
            "not_equal": mask if exact_mask else [],
            "coordinates": handle("structure_coordinates", b["id"]),
        },
        "metadata": [
            {"kind": k, "lower": lo, "upper": hi, "objective": obj, "count": n}
            for (k, lo, hi, obj), n in metadata.items()
        ],
        "origin": b["origin"],
        "verification": "verified",
    }


def save_blocks(m, db, blocks):
    for b in blocks:
        put(db, "structure_blocks", b["id"], block_card(m, b))
        for v, c in b["coords"].items():
            x = m.variables[v]
            put(
                db,
                "structure_coordinates",
                f"{b['id']}:{v:012d}",
                {
                    "id": b["id"],
                    "variable": v,
                    "coordinates": list(c),
                    "kind": x[2],
                    "lower": None if x[3] is None else str(m.num(x[3])),
                    "upper": None if x[4] is None else str(m.num(x[4])),
                    "objective": str(m.num(x[5])),
                },
            )
        if any(m.num(m.variables[v][5]) for v in b["coords"]):
            put(
                db,
                "structure_links",
                b["id"] + ":objective",
                {"id": b["id"], "block": b["id"], "relation": "objective"},
            )


def load_blocks(db):
    blocks = {b["id"]: {**b, "coords": {}} for b in records(db, "structure_blocks")}
    for c in records(db, "structure_coordinates"):
        blocks[c["id"]]["coords"][c["variable"]] = tuple(c["coordinates"])
    return blocks


def save_family(db, f):
    fid = f["id"]
    card = {k: v for k, v in f.items() if k not in ("instances", "parameters")}
    card["parameters_handle"] = handle("structure_parameters", fid)
    card["rows_handle"] = handle("structure_rows", fid)
    put(db, "structure_families", fid, card)
    for mid, mapping in f.get("index_maps", {}).items():
        put(db, "structure_index_maps", mid, mapping)
    for term in f["ast"]["terms"]:
        for a, values in term.get("restriction", {}).items():
            sid = ident("ssupport", [term["block"], a, values])
            put(
                db,
                "structure_subsets",
                sid,
                {
                    "id": sid,
                    "block": term["block"],
                    "position": int(a),
                    "values": values,
                    "size": len(values),
                    "evidence": "constraint_support",
                    "verification": "verified",
                },
            )
    for name, p in f["parameters"].items():
        put(
            db, "structure_parameters", fid + ":" + name, {"id": fid, "name": name, **p}
        )
    for x in f["instances"]:
        put(db, "structure_rows", f"{fid}:{x['row_id']:012d}", {"id": fid, **x})
    for t in f["ast"]["terms"]:
        put(
            db,
            "structure_links",
            fid + ":" + t["block"] + ":" + t["parameter"],
            {
                "id": fid,
                "family": fid,
                "block": t["block"],
                "fixed": t["fixed"],
                "sum_axes": t["sum_axes"],
                "relation": "definition"
                if f["definition"] and f["definition"]["block"] == t["block"]
                else "input",
            },
        )


def load_family(db, fid):
    row = db.execute(
        "SELECT record FROM records WHERE relation='structure_families' AND key=?",
        (fid,),
    ).fetchone()
    if not row:
        raise LPIRError("Unknown structural family")
    f = json.loads(row[0])
    f["parameters"] = {
        p["name"]: {k: v for k, v in p.items() if k not in ("id", "name")}
        for p in records(db, "structure_parameters")
        if p["id"] == fid
    }
    f["instances"] = [r for r in records(db, "structure_rows") if r["id"] == fid]
    return f


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
    db = connect(m, analysis, True)
    b = Budget(**(budget or {}))
    start = time.monotonic()
    state = readmeta(db, "state", {"phase": "dependencies", "cursor": 0})
    rows = readmeta(db, "scope_rows")
    if rows is None:
        rows = scope_rows(m, scope, budget)
        meta(db, "scope_rows", rows)
        meta(db, "scope", scope)
    reason = None
    try:
        while state["phase"] != "done":
            phase = state["phase"]
            if phase == "dependencies":
                manifest_path = m.path / "indexed.json"
                seed_state = (
                    json.loads(manifest_path.read_text()).get("state", {})
                    if manifest_path.exists()
                    else {}
                )
                if seed_state.get("phase") not in ("bundle", "operator", "done"):
                    reason = "coordinate discovery is incomplete; resume analyze('coordinates') first"
                    break
                from .indexed import connect as indexed_connect

                with closing(indexed_connect(m, True)) as seeds:
                    count = seeds.execute(
                        "SELECT count(*) FROM records WHERE relation='index_tensors'"
                    ).fetchone()[0]
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
    finally:
        meta(db, "state", state)
        elapsed = readmeta(db, "elapsed_seconds", 0) + time.monotonic() - start
        meta(db, "elapsed_seconds", elapsed)
        revision = readmeta(db, "revision", 0) + 1
        meta(db, "revision", revision)
        db.commit()
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
    (m.path / filename(analysis, ".json")).write_text(js(manifest))
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


def validate(m, spec, budget=None):
    b = Budget(**(budget or {}))
    try:
        with closing(connect(m, spec.get("analysis"))) as db:
            blocks = load_blocks(db)
            if spec["kind"] == "structure_family":
                f = spec.get("candidate") or load_family(db, spec["id"])
                if spec.get("candidate") and spec.get("id"):
                    original = load_family(db, spec["id"])
                    if sorted(x["row_id"] for x in f["instances"]) != sorted(
                        x["row_id"] for x in original["instances"]
                    ):
                        raise LPIRError("Candidate does not cover the published family")
                n = check_family(m, f, blocks, b)
                return {"valid": True, "verification": "verified", "checked_rows": n}
            if spec["kind"] == "structure_block":
                bid = spec["id"]
                block = blocks[bid]
                supplied = spec.get("coordinates")
                if supplied is not None:
                    actual = [
                        {"variable": v, "coordinates": list(c)}
                        for v, c in block["coords"].items()
                    ]
                    supplied = [
                        {"variable": c["variable"], "coordinates": c["coordinates"]}
                        for c in supplied
                    ]
                    if sorted(actual, key=js) != sorted(supplied, key=js):
                        raise LPIRError("Coordinates differ from published witness")
                if len(set(block["coords"].values())) != len(block["coords"]):
                    raise LPIRError("Coordinate collision")
                # Replay discovery from numerical rows and verified seeds to check provenance.
                rows = [
                    json.loads(x[0])
                    for x in db.execute("SELECT record FROM work ORDER BY row_id")
                ]
                for row in rows:
                    r = row["row_id"]
                    if dict((v, F(c)) for v, c in row["terms"]) != dict(
                        m.iter_terms(r)
                    ) or tuple(
                        None if row[k] is None else F(row[k]) for k in ("lo", "hi")
                    ) != (m.num(m.rows[r][2]), m.num(m.rows[r][3])):
                        raise LPIRError("Discovery witness differs from numerical row")
                replay = Discovery(m, rows, b).discover()
                candidate = next((x for x in replay if x["id"] == bid), None)
                if (
                    candidate is None
                    or candidate["coords"] != block["coords"]
                    or candidate["axes"] != block["axes"]
                ):
                    raise LPIRError("Coordinate provenance replay mismatch")
                if js(block_card(m, candidate)) != js(
                    {k: v for k, v in block.items() if k != "coords"}
                ):
                    raise LPIRError("Block metadata mismatch")
                for c in records(db, "structure_coordinates"):
                    if c["id"] != bid:
                        continue
                    v = m.variables[c["variable"]]
                    if (c["kind"], c["lower"], c["upper"], c["objective"]) != (
                        v[2],
                        None if v[3] is None else str(m.num(v[3])),
                        None if v[4] is None else str(m.num(v[4])),
                        str(m.num(v[5])),
                    ):
                        raise LPIRError("Variable metadata mismatch")
                return {
                    "valid": True,
                    "verification": "verified",
                    "checked_variables": len(block["coords"]),
                }
            if spec["kind"] in (
                "structure_view",
                "structure_subset",
                "structure_index_map",
                "structure_objective",
            ):
                from .structural_relations import check_artifact

                relation = {
                    "structure_view": "structure_views",
                    "structure_subset": "structure_subsets",
                    "structure_index_map": "structure_index_maps",
                    "structure_objective": "structure_objective",
                }[spec["kind"]]
                candidate = spec.get("candidate") or next(
                    x for x in records(db, relation) if x["id"] == spec["id"]
                )
                check_artifact(m, db, spec["kind"], candidate, blocks, b)
                return {"valid": True, "verification": "verified"}
            raise LPIRError("Unknown structural validation")
    except BudgetExceeded:
        return {"valid": None, "verification": "incomplete"}
    except (LPIRError, KeyError, ValueError, TypeError, IndexError, StopIteration) as e:
        return {
            "valid": False,
            "verification": "refuted",
            "counterexample": {"reason": str(e)},
        }


def verify(m):
    if not (m.path / filename()).exists():
        return {"status": "not_run"}
    with closing(connect(m)) as db:
        blocks = load_blocks(db)
        families = records(db, "structure_families")
        checked = 0
        covered = set()
        for bid in blocks:
            result = validate(m, {"kind": "structure_block", "id": bid})
            if result["valid"] is not True:
                raise LPIRError("Structure block verification failed: " + js(result))
        for f in families:
            full = load_family(db, f["id"])
            checked += check_family(m, full, blocks, Budget())
            rs = {r["row_id"] for r in full["instances"]}
            if covered & rs:
                raise LPIRError("Overlapping primary structural families")
            covered |= rs
        state = readmeta(db, "state")
        objective_check = {"status": "not_run"}
        if (
            readmeta(db, "model")[1] == VERSION
            and readmeta(db, "objective_constant") is not None
        ):
            from .structural_relations import verify_artifacts

            objective_check = verify_artifacts(m, db, blocks, Budget())
        residuals = {x["row_id"] for x in records(db, "structure_residuals")}
        if state["phase"] == "done" and (
            covered & residuals
            or covered | residuals != set(readmeta(db, "scope_rows"))
        ):
            raise LPIRError("Structural coverage mismatch")
        return {
            "status": "PASS",
            "objective": objective_check,
            "blocks": len(blocks),
            "families": len(families),
            "checked_rows": checked,
            "residual_rows": len(residuals),
            "search_completeness": "complete"
            if state["phase"] == "done"
            else "partial",
        }


def formula(f, aliases):
    symbols = [
        chr(97 + i) if i < 26 else "i" + str(i) for i in range(len(f["row_axes"]))
    ]

    def param(name, free_symbols=None):
        p = f["parameters"][name]
        if not p["inputs"]:
            return str(p["values"][0][1])
        indices = [
            symbols[x["row"]] if "row" in x else free_symbols[x["axis"]]
            for x in p["inputs"]
        ]
        return name + "[" + ",".join(indices) + "]"

    terms = []
    definition_target = None
    definition_index = None
    for t in f["ast"]["terms"]:
        fixed = dict(t["fixed"])
        free = {a: "s" + str(a) for a in t["sum_axes"]}
        idx = [
            symbols[fixed[a]] if a in fixed else free[a]
            for a in range(len(fixed) + len(free))
        ]
        for a, mid in t.get("maps", {}).items():
            label = aliases.get(
                mid, "μ" + str(list(f.get("index_maps", {})).index(mid) + 1)
            )
            idx[int(a)] = label + "(" + idx[int(a)] + ")"
        expression = aliases[t["block"]] + ("[" + ",".join(idx) + "]" if idx else "")
        p = param(t["parameter"], free)
        if (
            f.get("definition")
            and f["definition"]["block"] == t["block"]
            and p == "-1"
            and not free
        ):
            definition_target, definition_index = expression, len(terms)
        if p not in ("1", "-1"):
            expression = p + "·" + expression
        if free:
            expression = (
                "∑_{"
                + ",".join(
                    symbol
                    + (
                        "∈"
                        + aliases.get(
                            ident(
                                "ssupport",
                                [t["block"], str(a), t["restriction"][str(a)]],
                            ),
                            "A" + str(a),
                        )
                        if str(a) in t.get("restriction", {})
                        else ""
                    )
                    for a, symbol in free.items()
                )
                + "} "
                + expression
            )
        if t.get("support_pattern"):
            pattern = t["support_pattern"]
            expression = expression + " [support: " + pattern["kind"] + "]"
        if p == "-1":
            expression = "-" + expression
        terms.append(expression)
    lo, hi = param("lower"), param("upper")

    def joined(expressions):
        return " + ".join(expressions).replace(" + -", " - ") or "0"

    if definition_target is not None and lo == hi == "0":
        return (
            definition_target
            + " = "
            + joined([x for j, x in enumerate(terms) if j != definition_index])
        )
    expression = joined(terms)
    if lo == hi or f["ast"].get("sense") == "eq":
        return expression + " = " + hi
    if lo == "None":
        return expression + " ≤ " + hi
    if hi == "None":
        return expression + " ≥ " + lo
    return lo + " ≤ " + expression + " ≤ " + hi


def summary(m, max_bytes=7000):
    if not (m.path / filename()).exists():
        return None
    with closing(connect(m)) as db:
        blocks = records(db, "structure_blocks")
        families = records(db, "structure_families")
        # Seed, lifted, defined blocks; names are display aliases, never discovery inputs.
        blocks.sort(
            key=lambda b: (
                {
                    "seed": 0,
                    "incidence_lift": 1,
                    "coordinate_transfer": 2,
                    "definition": 3,
                    "objective_scalar": 4,
                }[b["origin"]["kind"]],
                -b["variables"],
                b["id"],
            )
        )
        aliases = {b["id"]: "X" + str(j + 1) for j, b in enumerate(blocks)}
        all_maps = records(db, "structure_index_maps")
        aliases.update({x["id"]: "μ" + str(i + 1) for i, x in enumerate(all_maps)})
        objective_cards = []
        subsets_byid = {x["id"]: x for x in records(db, "structure_subsets")}
        block_axes = {b["id"]: b["axes"] for b in blocks}
        subset_aliases, subset_keys, subset_cards = {}, {}, []
        for sid, sub in subsets_byid.items():
            axis = sub.get("axis", block_axes[sub["block"]][sub["position"]])
            key = (axis, tuple(sub["values"]))
            if key not in subset_keys:
                label = "U" + str(len(subset_keys) + 1)
                subset_keys[key] = label
                subset_cards.append(
                    {
                        "id": sid,
                        "name": label,
                        "size": sub["size"],
                        "block": aliases[sub["block"]],
                        "axis": sub["position"],
                        "evidence": sub["evidence"],
                        "handle": handle("structure_subsets", sid),
                    }
                )
            else:
                card = next(c for c in subset_cards if c["name"] == subset_keys[key])
                if sub["evidence"] not in card["evidence"]:
                    card["evidence"] += " + " + sub["evidence"]
            subset_aliases[sid] = subset_keys[key]
        aliases.update(subset_aliases)
        for o in records(db, "structure_objective"):
            p = o["parameter"]
            objective_cards.append(
                {
                    "id": o["id"],
                    "block": aliases[o["block"]],
                    "domain": [
                        subset_aliases.get(sid, "observed") for sid in o["subsets"]
                    ],
                    "shape": [len(x) for x in o["domain"].get("axes", [])],
                    "cells": o["cells"],
                    "inputs": p["inputs"],
                    "value": p["values"][0][1] if not p["inputs"] else None,
                    "handle": handle("structure_objective", o["id"]),
                }
            )
        out = {
            "objective_tiles": objective_cards,
            "axis_subsets": subset_cards,
            "index_maps": [
                {
                    "id": x["id"],
                    "name": aliases[x["id"]],
                    "size": len(x["values"]),
                    "source_axis": x["source_axis"],
                    "target_axis": x["target_axis"],
                    "source_subset": next(
                        (
                            subset_aliases[sid]
                            for sid, sub in subsets_byid.items()
                            if sub.get(
                                "axis", block_axes[sub["block"]][sub["position"]]
                            )
                            == x["source_axis"]
                            and set(sub["values"]) == {k for k, v in x["values"]}
                        ),
                        None,
                    ),
                    "image_subset": next(
                        (
                            subset_aliases[sid]
                            for sid, sub in subsets_byid.items()
                            if sub.get("axis") == x["target_axis"]
                            and set(sub["values"]) == {v for k, v in x["values"]}
                        ),
                        None,
                    ),
                    "handle": handle("structure_index_maps", x["id"]),
                }
                for x in all_maps
            ],
            "search_completeness": "complete"
            if readmeta(db, "state")["phase"] == "done"
            else "partial",
            "blocks": [],
            "families": [],
            "covered_rows": sum(f["rows"] for f in families),
            "scope_rows": len(readmeta(db, "scope_rows")),
            "residual_rows": len(records(db, "structure_residuals")),
            "residuals_handle": handle("structure_residuals"),
            "elapsed_seconds": readmeta(db, "elapsed_seconds"),
            "description_length": {
                "indexed_bytes": sum(
                    f["description_length"]["indexed_bytes"] for f in families
                ),
                "flat_bytes": sum(
                    f["description_length"]["flat_bytes"] for f in families
                ),
            },
            "diagnostics": {
                "operators": handle_old("index_operators"),
                "local_formulas": {"table": "explanation_families"},
            },
        }
        for b in blocks:
            out["blocks"].append(
                {
                    "id": b["id"],
                    "name": aliases[b["id"]],
                    "shape": b["shape"],
                    "variables": b["variables"],
                    "domain": b["domain"],
                    "metadata": b["metadata"],
                    "handle": handle("structure_blocks", b["id"]),
                }
            )
        for j, f in enumerate(sorted(families, key=lambda f: (-f["rows"], f["id"]))):
            full = load_family(db, f["id"])
            parameter_profiles = []
            for name, p in full["parameters"].items():
                if not p["inputs"] or (
                    name == "lower" and full["ast"].get("sense") == "eq"
                ):
                    continue
                values = [F(v) for k, v in p["values"] if v is not None]
                if not values:
                    continue
                parameter_profiles.append(
                    {
                        "name": name,
                        "shape": [
                            len({k[a] for k, v in p["values"]})
                            for a in range(len(p["inputs"]))
                        ],
                        "cells": len(p["values"]),
                        "min": str(min(values)),
                        "max": str(max(values)),
                        "invariant_inputs": p["invariant_inputs"],
                    }
                )
            out["families"].append(
                {
                    "id": f["id"],
                    "name": "S" + str(j + 1),
                    "rows": f["rows"],
                    "formula": formula(full, aliases),
                    "handle": handle("structure_families", f["id"]),
                    "definition": f["definition"],
                    "independent_indices": f.get("independent_indices"),
                    "maps": [
                        {"name": aliases[mid], "id": mid, "size": len(mp["values"])}
                        for i, (mid, mp) in enumerate(
                            full.get("index_maps", {}).items()
                        )
                    ],
                    "subsets": [
                        {
                            "name": aliases.get(
                                ident("ssupport", [t["block"], a, v]), "A" + str(a)
                            ),
                            "size": len(v),
                        }
                        for t in full["ast"]["terms"]
                        for a, v in t.get("restriction", {}).items()
                    ],
                    "parameters": parameter_profiles,
                    "domain": full.get("symbolic_domain"),
                }
            )
        from .structural_relations import description_metrics

        if readmeta(db, "model")[1] == VERSION:
            out["description_length"] = description_metrics(db)
        out["omitted_cards"] = 0
        for card in out["blocks"]:
            metadata = card["metadata"]
            card["metadata"] = [
                dict(kind=k, lower=lo, upper=hi)
                for k, lo, hi in sorted(
                    {(x["kind"], x["lower"], x["upper"]) for x in metadata}, key=js
                )
            ]
            costs = [F(x["objective"]) for x in metadata]
            card["objective_range"] = [str(min(costs)), str(max(costs))]
        while len(js(out).encode()) > max_bytes and any(
            out[k]
            for k in (
                "families",
                "blocks",
                "objective_tiles",
                "axis_subsets",
                "index_maps",
            )
        ):
            next(
                out[k]
                for k in (
                    "families",
                    "objective_tiles",
                    "axis_subsets",
                    "index_maps",
                    "blocks",
                )
                if out[k]
            ).pop()
            out["omitted_cards"] += 1
        return out


def handle_old(table):
    return {"table": table}
