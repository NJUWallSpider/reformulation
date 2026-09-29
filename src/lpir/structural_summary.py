"""Bounded structural presentation derived solely from published views."""

from contextlib import closing
from fractions import Fraction as F
from .structural_store import (
    VERSION,
    ident,
    js,
    filename,
    connect,
    readmeta,
    records,
    handle,
    load_family,
)


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
            "block_count": len(blocks),
            "family_count": len(families),
            "coupling_family_count": 0,
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
            "covered_rows": len({x["row_id"] for x in records(db, "structure_rows")}),
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
            linked_blocks = sorted({t["block"] for t in full["ast"]["terms"]})
            out["coupling_family_count"] += len(linked_blocks) > 1
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
                    "blocks": [aliases[x] for x in linked_blocks],
                    "cross_block": len(linked_blocks) > 1,
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
