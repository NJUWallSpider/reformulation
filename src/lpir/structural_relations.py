"""Exact relational coordinate operations; no model semantics or symmetry claims."""

from collections import defaultdict
from itertools import product

from .numbers import LPIRError


from .structural_domain import (
    reduce_blocks as reduce_blocks,
    function_table as function_table,
    bijective as bijective,
    unify_columns as unify_columns,
    support_restriction as support_restriction,
    map_value as map_value,
    domain_descriptor as domain_descriptor,
    matching_support as matching_support,
    matches_support as matches_support,
    view_encoding as view_encoding,
)


def artifacts(m, blocks, budget):
    """Build exact indexed-coordinate aliases and objective tiles from observed cells only."""
    from .structural_store import ident, js
    from .structural_algebra import parameter

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
    from .structural_store import records, js

    views, subsets, objectives = artifacts(m, list(blocks.values()), budget)
    expected = {
        "structure_view": views,
        "structure_subset": subsets,
        "structure_objective": objectives,
    }.get(kind)
    if kind == "structure_subset" and candidate.get("evidence") == "constraint_support":
        from .structural_store import ident, load_family
        from .structural_algebra import check_family

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
        from .structural_store import load_family
        from .structural_algebra import check_family

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
    from .structural_store import records, js, readmeta

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
                from .structural_store import ident

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


def description_metrics(db):
    from .structural_store import records, js

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
