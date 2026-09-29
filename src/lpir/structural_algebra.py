"""Structural affine representation, exact expansion and family verification."""

from collections import defaultdict
from fractions import Fraction as F
from itertools import combinations
from .numbers import LPIRError
from .structural_store import js


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
    from .structural_domain import map_value, matches_support

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
        from .structural_domain import domain_descriptor

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
