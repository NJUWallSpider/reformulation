"""Read-only checks returning witnesses/counterexamples; no model transformations."""

from fractions import Fraction as F
from .numbers import LPIRError
from .semantic import connect, verify_views
from .formula import expand
from .artifact_io import sha256


def verify(m):
    for name, digest in m.manifest["base_sha256"].items():
        if sha256(m.path / name) != digest:
            raise LPIRError(f"Base artifact checksum mismatch: {name}")
    if len(m.row_ptr) != len(m.rows) + 1 or int(m.row_ptr[-1]) != len(m.col_idx):
        raise LPIRError("Invalid CSR dimensions")
    from .semantic import view_digests

    if m.manifest.get("view_sha256") != view_digests(m):
        raise LPIRError("Semantic view checksum mismatch")
    checked = verify_views(m)
    db = connect(m, True)
    for r, fid in db.execute(
        "SELECT row_id,family FROM features WHERE family IS NOT NULL"
    ):
        a, lo, hi = expand(db, r)
        meta = m.rows[r]
        if a != m.row(r) or lo != m.num(meta[2]) or hi != m.num(meta[3]):
            raise LPIRError(f"Formula AST mismatch at row {r}")
    db.close()
    explanation = {}
    if (m.path / "explanations.json").exists():
        from .compressed import verify as verify_explanations

        explanation = {"explanations": verify_explanations(m)}
    return {
        **explanation,
        "status": "PASS",
        "exactness": "exact",
        "model_version": m.manifest["model_version"],
        "rows_verified": len(m.rows),
        "nonzeros_verified": len(m.col_idx),
        "coverage": 1.0,
        "objective_and_domains": "PASS",
        **checked,
    }


def validate(m, spec, budget=None):
    if not isinstance(spec, dict):
        raise LPIRError("Validation requires an object")
    from .budget import Budget, BudgetExceeded

    work_budget = Budget(**(budget or {}))
    kind = spec.get("kind")
    db = connect(m, True)
    result = {
        "model_version": m.manifest["model_version"],
        "analysis_version": "2.0",
        "exactness": "exact",
        "verification": "verified",
        "completeness": "complete",
        "scope": spec,
        "provenance": {"artifact": "numerical IR and analysis.sqlite"},
        "valid": True,
    }
    try:
        if kind == "explanation":
            from .compressed import validate_row, VERSION

            r = int(spec["row"])
            if not 0 <= r < len(m.rows):
                raise LPIRError("Invalid explanation row")
            work_budget.require(int(m.row_ptr[r + 1] - m.row_ptr[r]) * 512)
            result["valid"] = validate_row(m, r, spec.get("ast"))
            result["analysis_version"] = VERSION
            result["provenance"] = {"artifact": "numerical IR and explanations.sqlite"}
            result["evidence"] = {"row": r, "source_line": m.rows[r][4]}
            if not result["valid"]:
                result["counterexample"] = {
                    "row": r,
                    "reason": "exact expansion differs from original coefficients or bounds",
                }
        elif kind == "formula":
            r = int(spec["row"])
            if not 0 <= r < len(m.rows):
                raise LPIRError("Invalid formula row")
            work_budget.require(int(m.row_ptr[r + 1] - m.row_ptr[r]) * 512)
            a, lo, hi = expand(db, r, spec.get("ast"))
            work_budget.tick()
            meta = m.rows[r]
            result["valid"] = (
                a == m.row(r) and lo == m.num(meta[2]) and hi == m.num(meta[3])
            )
            result["evidence"] = {"row": r, "source_line": meta[4]}
        elif kind in ("subset", "equal_support", "disjoint"):
            indices = [int(spec["left"]), int(spec["right"])]
            if any(r < 0 or r >= len(m.rows) for r in indices):
                raise LPIRError("Invalid support row")
            work_budget.require(
                sum(int(m.row_ptr[r + 1] - m.row_ptr[r]) for r in indices) * 256
            )
            work_budget.tick()
            a = set(m.row(int(spec["left"])))
            b = set(m.row(int(spec["right"])))
            result["valid"] = (
                a < b
                if kind == "subset"
                else a == b
                if kind == "equal_support"
                else a.isdisjoint(b)
            )
            result["difference"] = sorted(b - a)
            result["intersection_size"] = len(a & b)
            if not result["valid"]:
                result["counterexample"] = next(
                    iter(
                        a - b
                        if kind == "subset"
                        else a ^ b
                        if kind == "equal_support"
                        else a & b
                    ),
                    None,
                )
        elif kind == "clique":
            work_budget.require(len(spec["variables"]) * 256)
            vertices = list(dict.fromkeys(spec["variables"]))
            if any(
                type(v) is not int or not 0 <= v < len(m.variables) for v in vertices
            ):
                raise LPIRError("Invalid clique vertex")
            for i, v in enumerate(vertices):
                for u in vertices[i + 1 :]:
                    work_budget.tick()
                    if (
                        not db.execute(
                            "SELECT 1 FROM edges WHERE source=? AND target=?", (v, u)
                        ).fetchone()
                        or not db.execute(
                            "SELECT 1 FROM edges WHERE source=? AND target=?", (u, v)
                        ).fetchone()
                    ):
                        result.update(
                            valid=False, counterexample={"missing_edge": [v, u]}
                        )
                        return finish(result)
            result["evidence"] = {
                "vertices": vertices,
                "verified_pairs": len(vertices) * (len(vertices) - 1) // 2,
            }
            if "cardinality_bound" in spec:
                k = spec["cardinality_bound"]
                if type(k) is not int or k < 0:
                    raise LPIRError("Cardinality bound must be nonnegative integer")
                witnesses = []
                for v in vertices:
                    work_budget.tick()
                    found = None
                    for r, a, b, rhs in db.execute(
                        "SELECT row_id,coefficient,neighbor_coefficient,upper FROM graphs WHERE center=?",
                        (v,),
                    ):
                        nb = {
                            u
                            for (u,) in db.execute(
                                "SELECT variable FROM bindings WHERE row_id=? AND role='neighbors'",
                                (r,),
                            )
                        }
                        if (
                            set(vertices) - {v} <= nb
                            and F(a) > 0
                            and F(b) > 0
                            and F(a) + k * F(b) > F(rhs)
                        ):
                            found = r
                            break
                    if found is None:
                        result.update(
                            valid=False,
                            counterexample={
                                "center": v,
                                "reason": "no individual source row proves proposed cardinality bound",
                            },
                        )
                        return finish(result)
                    witnesses.append(found)
                result["evidence"]["source_rows"] = witnesses
                result["evidence"]["argument"] = (
                    "if k+1 clique variables selected, each selected center violates its nonnegative source row"
                )
                result["applied"] = False
        elif kind == "mapping":
            # Explicitly proposed relation is checked in both directions, never inferred from names.
            if spec.get("relation") != "queen_attack":
                raise LPIRError("Supported mapping relation: queen_attack")
            mapping = {int(v): tuple(xy) for v, xy in spec["mapping"].items()}
            if len(set(mapping.values())) != len(mapping) or any(
                len(xy) != 2 or any(type(x) is not int for x in xy)
                for xy in mapping.values()
            ):
                raise LPIRError("Mapping must have distinct integer coordinate pairs")
            actual = {v for (v,) in db.execute("SELECT DISTINCT center FROM graphs")}
            if set(mapping) != actual:
                result.update(
                    valid=False,
                    counterexample={
                        "reason": "mapping does not cover precisely all graph centers"
                    },
                )
                return finish(result)
            work_budget.require(len(mapping) * 512)
            for v, (x, y) in mapping.items():
                work_budget.tick(len(mapping))
                expected = {
                    u
                    for u, (a, b) in mapping.items()
                    if u != v and (x == a or y == b or abs(x - a) == abs(y - b))
                }
                observed = {
                    u
                    for (u,) in db.execute(
                        "SELECT target FROM edges WHERE source=?", (v,)
                    )
                }
                if observed != expected:
                    result.update(
                        valid=False,
                        counterexample={
                            "center": v,
                            "difference": sorted(observed ^ expected)[:20],
                        },
                    )
                    return finish(result)
                for (row_id,) in db.execute(
                    "SELECT row_id FROM graphs WHERE center=?", (v,)
                ):
                    individual = {
                        u
                        for (u,) in db.execute(
                            "SELECT variable FROM bindings WHERE row_id=? AND role='neighbors'",
                            (row_id,),
                        )
                    }
                    if individual != expected:
                        result.update(
                            valid=False,
                            counterexample={
                                "row": row_id,
                                "reason": "individual neighborhood differs from proposed geometry",
                            },
                        )
                        return finish(result)
            result["evidence"] = {
                "centers_checked": len(mapping),
                "all_neighbors_checked": True,
            }
        elif kind == "decomposition":
            blocks = spec["blocks"]
            border = set(spec.get("border_rows", []))
            row_map = {}
            col_map = {}
            for i, block in enumerate(blocks):
                for r in block["rows"]:
                    if r in row_map or r in border:
                        raise LPIRError("Overlapping row blocks/border")
                    row_map[r] = i
                for v in block["variables"]:
                    if v in col_map:
                        raise LPIRError("Overlapping column blocks")
                    col_map[v] = i
            scope = set(spec.get("rows", range(len(m.rows))))
            if set(row_map) | border != scope:
                result.update(
                    valid=False,
                    counterexample={"reason": "row coverage differs from scope"},
                )
                return finish(result)
            if scope == set(range(len(m.rows))) and set(col_map) != set(
                range(len(m.variables))
            ):
                result.update(
                    valid=False,
                    counterexample={
                        "reason": "full-model decomposition must cover isolated columns too"
                    },
                )
                return finish(result)
            for r in scope:
                work_budget.tick()
                for v, c in m.iter_terms(r):
                    if v not in col_map or (
                        r not in border and row_map[r] != col_map[v]
                    ):
                        result.update(
                            valid=False, counterexample={"row": r, "variable": v}
                        )
                        return finish(result)
            result["evidence"] = {
                "rows_checked": len(scope),
                "all_incidence_checked": True,
            }
        elif kind == "relation":
            from .query import value, TABLES, run_query
            import json

            if "query" in spec:
                page = run_query(m, spec["query"], budget=budget, limit=1000)
                result["query_handle"] = page["handle"]
                if page["completeness"] != "complete":
                    return {
                        **result,
                        "valid": None,
                        "verification": "candidate",
                        "completeness": "partial",
                        "continuation": page["handle"],
                    }
                records = (
                    json.loads(rec)
                    for (rec,) in db.execute(
                        "SELECT record FROM results WHERE run=? ORDER BY ordinal",
                        (page["handle"],),
                    )
                )
            else:
                relation = spec["table"]
                if (
                    relation not in TABLES
                    or relation in ("variables", "terms", "raw_rows")
                    or relation.startswith("explanation_")
                ):
                    raise LPIRError(
                        "Use a query AST for numerical variable/term relations"
                    )
                if relation in ("assignment_members", "assignment_edges"):
                    from .results import require_current_assignments

                    require_current_assignments(db)
                records = (json.loads(rec) for (rec,) in db.execute(TABLES[relation]))
            checked = 0
            for record in records:
                work_budget.tick()
                checked += 1
                if not value(spec["predicate"], record):
                    result.update(valid=False, counterexample=record, checked=checked)
                    return finish(result)
            result["checked"] = checked
        else:
            raise LPIRError(f"Unknown validation kind {kind}")
        return finish(result)
    except BudgetExceeded:
        return {
            **result,
            "valid": None,
            "verification": "candidate",
            "completeness": "partial",
            "continuation": {"spec": spec},
            "reason": "validation budget exhausted; retry with a larger budget",
        }
    finally:
        db.close()


def finish(result):
    if not result["valid"]:
        result["verification"] = "refuted"
    return result
