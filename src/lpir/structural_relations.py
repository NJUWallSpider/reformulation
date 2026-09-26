"""Exact relational coordinate operations; no model semantics or symmetry claims."""

from collections import defaultdict
from itertools import combinations, product

from .numbers import LPIRError


def reduce_blocks(blocks):
    """Remove only columns bijective to another existing coordinate column."""
    for b in blocks:
        original = dict(b["coords"])
        rank = len(b["axes"])
        keep = []
        for a in range(rank):
            if not any(
                bijective(
                    [c[k] for c in original.values()], [c[a] for c in original.values()]
                )
                for k in keep
            ):
                keep.append(a)
        if len(keep) != rank:
            b["origin"]["coordinate_view"] = {
                "axes": list(b["axes"]),
                "keep": keep,
                "coordinates": [[v, list(c)] for v, c in sorted(original.items())],
            }
            b["axes"] = [b["axes"][k] for k in keep]
            b["coords"] = {v: tuple(c[k] for k in keep) for v, c in original.items()}
    return blocks


def function_table(source, target):
    pairs = dict(zip(source, target))
    return pairs if all(pairs[a] == b for a, b in zip(source, target)) else None


def bijective(source, target):
    table = function_table(source, target)
    return table is not None and len(set(table.values())) == len(table)


def unify_columns(columns, budget):
    """Choose a minimal exact single-input reconstruction, counting map costs."""
    from .structural import js

    values = [x[3] for x in columns]
    n = len(values)
    if not n:
        return [], {}, {}
    # Collapse equal columns first, then enumerate bases (bounded by candidate budget).
    representatives = []
    for j, col in enumerate(values):
        if col not in [values[k] for k in representatives]:
            representatives.append(j)
    tables = {
        (i, j): function_table(values[i], values[j])
        for i in representatives
        for j in range(n)
    }
    best = None
    if len(representatives) > 12:
        # Conservative equality-only fallback for unusually wide families.
        bindings = {}
        for j in range(n):
            i = next(i for i in representatives if values[i] == values[j])
            pairs = (
                None
                if columns[i][2] == columns[j][2]
                else sorted(set(zip(values[i], values[j])))
            )
            bindings[j] = (representatives.index(i), pairs)
        return representatives, bindings, {"before": n, "after": len(representatives)}
    for size in range(1, len(representatives) + 1):
        for keep in combinations(representatives, size):
            budget.tick(0)
            bindings = {}
            for j in range(n):
                choices = []
                for slot, i in enumerate(keep):
                    table = tables[i, j]
                    if table is None:
                        continue
                    identity = columns[i][2] == columns[j][2] and values[i] == values[j]
                    mapping = None if identity else sorted(table.items())
                    choices.append((len(js(mapping)), slot, mapping))
                if not choices:
                    break
                _, slot, mapping = min(choices, key=lambda x: (x[0], x[1]))
                bindings[j] = (slot, mapping)
            if len(bindings) != n:
                continue
            domain = list(zip(*(values[i] for i in keep)))
            # Shared maps are charged once, identity maps are free.
            maps = {
                js([columns[keep[s]][2], columns[j][2], pairs])
                for j, (s, pairs) in bindings.items()
                if pairs is not None
            }
            cost = len(js(domain)) + sum(len(x) for x in maps) + len(js(bindings))
            candidate = (cost, size, keep, bindings)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        if best is not None:
            # A dependent column is a binding, not an additional free index.
            # Compare encodings among minimal generators; the entire compiled
            # family (including these tables) still must beat flat expansion.
            break
    if best is None:
        raise LPIRError("No coordinate reconstruction")
    _, _, keep, bindings = best
    return list(keep), bindings, {"before": n, "after": len(keep)}


def support_restriction(coords, all_coords, fixed):
    free = [a for a in range(len(coords[0])) if a not in fixed]
    selected = set(coords)
    domains = {a: sorted({c[a] for c in coords}) for a in free}
    restricted = {c for c in all_coords if all(c[a] in domains[a] for a in free)}
    if selected != restricted:
        return None
    return {
        str(a): d for a, d in domains.items() if set(d) != {c[a] for c in all_coords}
    }


def map_value(mapping, value):
    pairs = mapping["values"]
    table = dict(pairs)
    if len(table) != len(pairs) or value not in table:
        raise LPIRError("Invalid or incomplete index map")
    return table[value]


def artifacts(m, blocks, budget):
    """Build exact indexed-coordinate aliases and objective tiles from observed cells only."""
    from .structural import ident, parameter, js

    views, objectives, subsets = [], [], []
    owner = {v: b for b in blocks for v in b["coords"]}
    if (m.path / "indexed.sqlite").exists():
        import sqlite3

        with sqlite3.connect(
            f"file:{(m.path / 'indexed.sqlite').resolve()}?mode=ro", uri=True
        ) as db:
            ids = [x[0] for x in db.execute("SELECT DISTINCT t FROM coords ORDER BY t")]
            for tid in ids:
                groups = defaultdict(list)
                for v, a, c in db.execute("SELECT v,a,b FROM coords WHERE t=?", (tid,)):
                    if v in owner:
                        b = owner[v]
                        groups[b["id"]].append([v, [a, c], list(b["coords"][v])])
                for bid, cells in groups.items():
                    base = next(b for b in blocks if b["id"] == bid)
                    compact = view_encoding(cells, base)
                    views.append(
                        {
                            "id": ident("sview", [tid, bid]),
                            "tensor": tid,
                            "block": bid,
                            "kind": "coordinate_alias",
                            "coordinate_map": compact,
                            "cells": sorted(cells),
                            "verification": "verified",
                        }
                    )
    for b in blocks:
        budget.tick(0)
        rank = len(b["axes"])
        budget.require(max(1, len(b["coords"])) * max(1024, rank * 256))
        costs = {c: str(m.num(m.variables[v][5])) for v, c in b["coords"].items()}
        domains = [sorted({c[a] for c in costs}) for a in range(rank)]
        partitions = []
        for a, domain in enumerate(domains):
            profiles = defaultdict(list)
            fibers = defaultdict(list)
            for c, value in costs.items():
                fibers[c[a]].append((c[:a] + c[a + 1 :], value))
            for value in domain:
                profiles[js(sorted(fibers[value]))].append(value)
            repeated = [v for v in profiles.values() if len(v) > 1]
            remaining = sorted(set(domain) - {v for group in repeated for v in group})
            groups = sorted(repeated) + ([remaining] if remaining else [])
            # Avoid a combinatorial partition explosion: exact dense/observed fallback.
            if len(groups) > 16:
                groups = [domain]
            axis_subsets = []
            for group in groups:
                sid = ident("ssubset", [b["id"], a, b["axes"][a], group])
                equal = len({js(sorted(fibers[v])) for v in group}) == 1
                subsets.append(
                    {
                        "id": sid,
                        "axis": b["axes"][a],
                        "values": group,
                        "size": len(group),
                        "block": b["id"],
                        "position": a,
                        "evidence": "identical_objective_profiles"
                        if equal
                        else "profile_complement",
                        "symmetry_proven": False,
                        "verification": "verified",
                    }
                )
                axis_subsets.append((sid, set(group)))
            partitions.append(axis_subsets)
        tile_count = 1
        for p in partitions:
            tile_count *= len(p)
        if tile_count > 256:
            partitions = [[(None, set(d))] for d in domains]
        for selected in product(*partitions):
            budget.tick(0)
            cells = [
                (c, value)
                for c, value in costs.items()
                if all(c[a] in group for a, (sid, group) in enumerate(selected))
            ]
            if not cells:
                continue
            p = parameter(cells, [{"axis": a} for a in range(rank)], budget)
            oid = ident("sobjective", [b["id"], [x[0] for x in selected]])
            domain = [list(c) for c, value in sorted(cells)]
            compact_domain = {"axes": [sorted(x[1]) for x in selected]}
            size = 1
            for d in compact_domain["axes"]:
                size *= len(d)
            if size != len(cells):
                compact_domain = {"coordinates": domain}
            indexed = len(js([compact_domain, p]))
            flat = len(js(cells))
            objectives.append(
                {
                    "id": oid,
                    "block": b["id"],
                    "domain": compact_domain,
                    "subsets": [x[0] for x in selected],
                    "cells": len(cells),
                    "parameter": p,
                    "verification": "verified",
                    "description_length": {
                        "indexed_bytes": indexed,
                        "flat_bytes": flat,
                    },
                    "representation": "factorized" if indexed < flat else "explicit",
                }
            )
    return views, subsets, objectives


def check_artifact(m, db, kind, candidate, blocks, budget):
    """Replay exact facts; no fingerprint alone is a proof."""
    from .structural import records, js

    views, subsets, objectives = artifacts(m, list(blocks.values()), budget)
    expected = {
        "structure_view": views,
        "structure_subset": subsets,
        "structure_objective": objectives,
    }.get(kind)
    if kind == "structure_subset" and candidate.get("evidence") == "constraint_support":
        from .structural import ident, load_family, check_family

        for f in records(db, "structure_families"):
            for t in f["ast"]["terms"]:
                for a, values in t.get("restriction", {}).items():
                    sid = ident("ssupport", [t["block"], a, values])
                    witness = {
                        "id": sid,
                        "block": t["block"],
                        "position": int(a),
                        "values": values,
                        "size": len(values),
                        "evidence": "constraint_support",
                        "verification": "verified",
                    }
                    if js(candidate) == js(witness):
                        check_family(m, load_family(db, f["id"]), blocks, budget)
                        return
        raise LPIRError("No exact support witness")
    if expected is not None:
        match = next((x for x in expected if x["id"] == candidate["id"]), None)
        if match is None or js(match) != js(candidate):
            raise LPIRError("Structural artifact differs from exact witness")
        return
    if kind == "structure_index_map":
        # Maps are certified by their owning, independently expanded families.
        from .structural import load_family, check_family

        refs = [
            f
            for f in records(db, "structure_families")
            if candidate["id"] in f.get("index_maps", {})
        ]
        if not refs:
            raise LPIRError("Unreferenced index map")
        for f in refs:
            full = load_family(db, f["id"])
            if js(full["index_maps"][candidate["id"]]) != js(candidate):
                raise LPIRError("Index map differs from family witness")
            check_family(m, full, blocks, budget)
        return
    raise LPIRError("Unknown relational validation")


def verify_artifacts(m, db, blocks, budget):
    from .structural import records, js, readmeta

    views, subsets, objectives = artifacts(m, list(blocks.values()), budget)
    for relation, expected in [
        ("structure_views", views),
        ("structure_objective", objectives),
    ]:
        if sorted(map(js, records(db, relation))) != sorted(map(js, expected)):
            raise LPIRError("Relational artifact verification failed: " + relation)
    actual_subsets = records(db, "structure_subsets")
    expected_subsets = {x["id"]: x for x in subsets}
    expected_maps = {}
    for f in records(db, "structure_families"):
        expected_maps.update(f.get("index_maps", {}))
        for term in f["ast"]["terms"]:
            for a, values in term.get("restriction", {}).items():
                from .structural import ident

                sid = ident("ssupport", [term["block"], a, values])
                expected_subsets[sid] = {
                    "id": sid,
                    "block": term["block"],
                    "position": int(a),
                    "values": values,
                    "size": len(values),
                    "evidence": "constraint_support",
                    "verification": "verified",
                }
    if sorted(map(js, actual_subsets)) != sorted(map(js, expected_subsets.values())):
        raise LPIRError("Subset evidence mismatch")
    if sorted(map(js, records(db, "structure_index_maps"))) != sorted(
        map(js, expected_maps.values())
    ):
        raise LPIRError("Index map evidence mismatch")
    constant = str(m.num(m.manifest["objective_constant"]))
    if readmeta(db, "objective_constant") != constant:
        raise LPIRError("Objective constant mismatch")
    # Tiles are exact disjoint covers of all coordinated variables; numerical
    # objective coefficients outside these entities remain explicit fallback.
    covered = {v for b in blocks.values() for v in b["coords"]}
    return {
        "status": "PASS",
        "structured_variables": len(covered),
        "fallback_variables": len(m.variables) - len(covered),
        "constant": constant,
        "tiles": len(objectives),
        "checked_variables": len(m.variables),
    }


def domain_descriptor(domain, axes):
    """Recognize equality exclusions only when they describe the entire domain."""
    domains = [sorted({c[a] for c in domain}) for a in range(len(axes))]
    size = 1
    for d in domains:
        size *= len(d)
    result = {"kind": "explicit", "axes": domains}
    if size <= max(4096, len(domain) * 4):
        excluded = [
            [a, b]
            for a in range(len(axes))
            for b in range(a + 1, len(axes))
            if axes[a] == axes[b] and all(c[a] != c[b] for c in domain)
        ]
        if set(map(tuple, domain)) == {
            c for c in product(*domains) if all(c[a] != c[b] for a, b in excluded)
        }:
            result = {
                "kind": "product_minus_equalities",
                "axes": domains,
                "not_equal": excluded,
            }
    return result


def matching_support(coords, all_coords):
    """Exact (possibly renamed) diagonal and complement, on observed support."""
    selected, universe = set(map(tuple, coords)), set(map(tuple, all_coords))
    if not selected:
        return None
    rank = len(next(iter(selected)))
    for complement, witness in [(False, selected), (True, universe - selected)]:
        for a, b in combinations(range(rank), 2):
            pairs = sorted({(c[a], c[b]) for c in witness})
            if (
                not pairs
                or len({x for x, y in pairs}) != len(pairs)
                or len({y for x, y in pairs}) != len(pairs)
            ):
                continue
            pairset = set(pairs)
            reconstructed = {
                c for c in universe if (((c[a], c[b]) in pairset) != complement)
            }
            if reconstructed == selected:
                return {
                    "kind": "matching_complement" if complement else "matching",
                    "axes": [a, b],
                    "pairs": [list(x) for x in pairs],
                }
    return None


def matches_support(c, pattern):
    if not pattern:
        return True
    a, b = pattern["axes"]
    present = [c[a], c[b]] in pattern["pairs"]
    if pattern["kind"] not in ("matching", "matching_complement"):
        raise LPIRError("Unknown support pattern")
    return not present if pattern["kind"] == "matching_complement" else present


def view_encoding(cells, block):
    """Coordinate-only alias encoding; raw variable IDs remain audit metadata."""
    source = [c for v, old, c in cells]
    target = [old for v, old, c in cells]
    bindings = []
    rank = len(block["axes"])
    for a in range(len(target[0])):
        values = [c[a] for c in target]
        choices = []
        for j in range(rank):
            inputs = [c[j] for c in source]
            table = function_table(inputs, values)
            if table is not None:
                choices.append(
                    {"input": j, "values": sorted(table.items())}
                    if inputs != values
                    else {"input": j}
                )
        if not choices:
            return {"cells": [[c, old] for v, old, c in cells]}
        from .structural import js

        bindings.append(min(choices, key=lambda x: len(js(x))))
    if {tuple(c) for c in source} == set(block["coords"].values()):
        domain = {"kind": "entity_domain"}
    else:
        domain = domain_descriptor(source, block["axes"])
        if domain["kind"] == "explicit":
            pattern = matching_support(source, block["coords"].values())
            domain = {"pattern": pattern} if pattern else {"coordinates": source}
    return {"domain": domain, "bindings": bindings}


def description_metrics(db):
    from .structural import records, js

    families = records(db, "structure_families")
    maps = {x["id"]: x for x in records(db, "structure_index_maps")}
    map_size = len(js(maps)) if maps else 0
    family_size = (
        sum(
            f["description_length"]["indexed_bytes"]
            - f["description_length"].get("map_bytes", 0)
            for f in families
        )
        + map_size
    )
    flat = sum(f["description_length"]["flat_bytes"] for f in families)
    views = {js(v.get("coordinate_map", {})) for v in records(db, "structure_views")}
    view_size = sum(len(x.encode()) for x in views)
    subsets = {
        js([x.get("axis"), x["block"], x["position"], x["values"]])
        for x in records(db, "structure_subsets")
    }
    subset_size = sum(len(x.encode()) for x in subsets)
    objective = records(db, "structure_objective")
    objective_size = sum(
        min(
            x["description_length"]["indexed_bytes"],
            x["description_length"]["flat_bytes"],
        )
        for x in objective
    )
    return {
        "indexed_bytes": family_size,
        "flat_bytes": flat,
        "shared_map_bytes": map_size,
        "view_bytes": view_size,
        "subset_bytes": subset_size,
        "objective_bytes": objective_size,
        "objective_flat_bytes": sum(
            x["description_length"]["flat_bytes"] for x in objective
        ),
        "total_structural_bytes": family_size
        + view_size
        + subset_size
        + objective_size,
        "encoding": "canonical-json-v2; maps/views deduplicated; raw identity audit excluded",
    }
