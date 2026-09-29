"""Replay published structural witnesses against the immutable numerical model."""

from contextlib import closing
from fractions import Fraction as F
import json
from .budget import Budget, BudgetExceeded
from .numbers import LPIRError
from .structural_store import (
    VERSION,
    js,
    filename,
    connect,
    readmeta,
    records,
    block_card,
    load_blocks,
    load_family,
)
from .structural_algebra import check_family
from .structural_discovery import Discovery


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
