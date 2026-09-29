"""Exact relational coordinate operations; no model semantics or symmetry claims."""

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
    from .structural_store import js

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
        from .structural_store import js

        bindings.append(min(choices, key=lambda x: len(js(x))))
    if {tuple(c) for c in source} == set(block["coords"].values()):
        domain = {"kind": "entity_domain"}
    else:
        domain = domain_descriptor(source, block["axes"])
        if domain["kind"] == "explicit":
            pattern = matching_support(source, block["coords"].values())
            domain = {"pattern": pattern} if pattern else {"coordinates": source}
    return {"domain": domain, "bindings": bindings}
