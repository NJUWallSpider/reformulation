"""Public API for the current numerical and structural IR."""

from collections import Counter
import json
from .numbers import LPIRError, decimal
from .storage import Model, build
from .semantic import connect
from .jobs import analyze, result_page, require_current_assignments, ASSIGNMENT_VERSION


class IR:
    def __init__(self, path):
        self.model = Model(path)

    def close(self):
        self.model.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @classmethod
    def build(cls, source, out, **options):
        m = build(source, out, **options)
        m.close()
        return cls(out)

    @staticmethod
    def page(offset, limit):
        if offset < 0 or not 1 <= limit <= 1000:
            raise LPIRError("offset >= 0 and 1 <= limit <= 1000 required")

    def envelope(self, items, total, offset=0, limit=20, scope=None, **extra):
        return {
            "model_version": self.model.manifest["model_version"],
            "analysis_version": "2.0",
            "exactness": "exact",
            "verification": "verified",
            "completeness": "complete",
            "scope": scope,
            "total": total,
            "offset": offset,
            "limit": limit,
            "truncated": offset + len(items) < total,
            "next_offset": offset + len(items) if offset + len(items) < total else None,
            "items": items,
            "provenance": {"artifact": "numerical IR / analysis.sqlite"},
            **extra,
        }

    def resolve(self, kind, key):
        m = self.model
        table = m.variables if kind == "variable" else m.rows
        names = m.var_names if kind == "variable" else m.row_names
        if isinstance(key, str) and key in names:
            return names[key]
        try:
            i = int(key)
        except (ValueError, TypeError) as e:
            raise LPIRError(f"Unknown {kind}: {key}") from e
        if not 0 <= i < len(table):
            raise LPIRError(f"Invalid {kind}: {i}")
        return i

    def overview(self):
        m = self.model
        db = connect(m, True)
        classes = dict(db.execute("SELECT class,count(*) FROM features GROUP BY class"))
        families = [
            {"id": fid, "kind": kind, "formula": formula, "rows": count}
            for fid, kind, formula, count in db.execute(
                "SELECT f.id,f.kind,f.formula,count(*) FROM families f JOIN features r ON r.family=f.id GROUP BY f.id ORDER BY count(*) DESC,f.id LIMIT 20"
            )
        ]
        family_count = db.execute("SELECT count(*) FROM families").fetchone()[0]
        types = Counter()
        low = high = None
        positive = negative = zero = 0
        for v in m.variables:
            types[v[2]] += 1
            c = m.num(v[5])
            low = c if low is None else min(low, c)
            high = c if high is None else max(high, c)
            positive += c > 0
            negative += c < 0
            zero += c == 0
            # Distinct costs aggregate on disk below; avoid a model-sized Counter.
        objective = {
            "sense": m.manifest["sense"],
            "constant": decimal(m.num(m.manifest["objective_constant"])),
            "nonzeros": len(m.variables) - zero,
            "positive": positive,
            "negative": negative,
            "zero": zero,
            "min": str(low) if low is not None else None,
            "max": str(high) if high is not None else None,
            "uniform": low == high,
            "interpretation": None,
        }
        if low == high and low is not None and low != 0:
            objective["interpretation"] = (
                "maximum_cardinality"
                if (m.manifest["sense"] == "min" and low < 0)
                or (m.manifest["sense"] == "max" and low > 0)
                else "minimum_cardinality"
            )
            objective["interpretation"] = (
                "weighted_uniform_sum"
                if types.get("B", 0) != len(m.variables)
                else objective["interpretation"]
            )
        runs = [
            {"handle": i, "kind": k, "completeness": s, "summary": json.loads(x)}
            for i, k, s, x in db.execute(
                "SELECT id,kind,CASE WHEN kind='assignments' AND json_extract(spec,'$.version')<>'2.0.1' THEN 'obsolete' ELSE status END,summary FROM runs ORDER BY kind,id LIMIT 20"
            )
        ]
        run_count = db.execute("SELECT count(*) FROM runs").fetchone()[0]
        tc = db.execute("SELECT count(*) FROM temporal").fetchone()[0]
        tv = db.execute("SELECT count(*) FROM time_bounds").fetchone()[0]
        omitted = db.execute("SELECT count(*) FROM omitted").fetchone()[0]
        db.close()
        return {
            "model_version": m.manifest["model_version"],
            "schema_version": 2,
            "analysis_version": "2.0",
            "exactness": "exact",
            "variables": len(m.variables),
            "rows": len(m.rows),
            "nonzeros": len(m.col_idx),
            "variable_types": dict(types),
            "objective": objective,
            "objective_nonzeros": objective["nonzeros"],
            "objective_constant": objective["constant"],
            "classes": classes,
            "families": families,
            "families_view": "coefficient groups; mathematical explanation document available through describe()",
            "explanation_ir_available": (m.path / "explanations.json").exists(),
            "family_count": family_count,
            "family_list_truncated": family_count > 20,
            "temporal_rows": tc,
            "time_variables": tv,
            "unclassified_working_set_rows": omitted,
            "numerical_coverage": 1.0,
            "semantic_coverage_claim": None,
            "analyses": runs,
            "analysis_count": run_count,
            "analysis_list_truncated": run_count > 20,
            "metrics": {
                k: m.manifest.get(k)
                for k in ("timings", "peak_rss_bytes", "artifact_bytes")
            },
        }

    def inspect(self, kind, key=None, offset=0, limit=20):
        self.page(offset, limit)
        from .references import translate

        if kind.startswith("structure_"):
            from .structural import page

            key = translate(
                self.model, key, decode=True, reference=isinstance(key, str)
            )
            return page(self.model, kind, key, offset, limit)

        if kind.startswith("index_") or kind in (
            "explanation",
            "family",
            "parameter",
            "assignment",
        ):
            key = translate(
                self.model, key, decode=True, reference=isinstance(key, str)
            )
        if kind.startswith("index_"):
            from .indexed import page, ScopedModel, qualify

            if isinstance(key, dict) and "analysis" in key:
                model = ScopedModel(self.model, key["analysis"])
                return qualify(
                    page(model, kind, key.get("id"), offset, limit), key["analysis"]
                )
            return page(self.model, kind, key, offset, limit)
        m = self.model
        db = connect(m, True)
        try:
            if kind == "explanation":
                from .compressed import inspect as inspect_explanation

                return inspect_explanation(m, key, offset, limit)
            if kind == "analysis":
                total = db.execute("SELECT count(*) FROM runs").fetchone()[0]
                items = [
                    {
                        "handle": i,
                        "kind": k,
                        "completeness": status,
                        "summary": json.loads(summary),
                    }
                    for i, k, status, summary in db.execute(
                        "SELECT id,kind,CASE WHEN kind='assignments' AND json_extract(spec,'$.version')<>'2.0.1' THEN 'obsolete' ELSE status END,summary FROM runs ORDER BY kind,id LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                ]
                return self.envelope(items, total, offset, limit, "analyses")
            if kind == "row":
                r = self.resolve("row", key)
                meta = m.rows[r]
                lo = int(m.row_ptr[r])
                total = int(m.row_ptr[r + 1]) - lo
                items = [
                    {
                        "variable": int(m.col_idx[p]),
                        "name": m.variables[int(m.col_idx[p])][1],
                        "coefficient": decimal(m.num(int(m.coeff_id[p]))),
                    }
                    for p in range(
                        lo + min(offset, total), lo + min(total, offset + limit)
                    )
                ]
                return self.envelope(
                    items,
                    total,
                    offset,
                    limit,
                    {"row": r},
                    name=meta[1],
                    lower=decimal(m.num(meta[2])) if meta[2] is not None else None,
                    upper=decimal(m.num(meta[3])) if meta[3] is not None else None,
                    source_line=meta[4],
                )
            if kind == "variable":
                v = m.variables[self.resolve(kind, key)]
                fmt = lambda i: decimal(m.num(i)) if i is not None else None
                return self.envelope(
                    [
                        {
                            "id": v[0],
                            "name": v[1],
                            "kind": v[2],
                            "lower": fmt(v[3]),
                            "upper": fmt(v[4]),
                            "objective": fmt(v[5]),
                            "source_line": v[6],
                        }
                    ],
                    1,
                    scope={"variable": v[0]},
                )
            if kind == "family":
                if key is None:
                    total = db.execute("SELECT count(*) FROM families").fetchone()[0]
                    items = [
                        {"id": i, "kind": k, "formula": f}
                        for i, k, f in db.execute(
                            "SELECT id,kind,formula FROM families ORDER BY id LIMIT ? OFFSET ?",
                            (limit, offset),
                        )
                    ]
                    return self.envelope(items, total, offset, limit, "families")
                family = db.execute(
                    "SELECT kind,formula,ast FROM families WHERE id=?", (key,)
                ).fetchone()
                if not family:
                    raise LPIRError("Unknown family")
                items = []
                total = db.execute(
                    "SELECT count(*) FROM features WHERE family=?", (key,)
                ).fetchone()[0]
                for r, lo, hi, scale, shift in db.execute(
                    "SELECT row_id,lower,upper,scale,shift FROM features WHERE family=? ORDER BY row_id LIMIT ? OFFSET ?",
                    (key, limit, offset),
                ):
                    roles = []
                    for role, c, k, n in db.execute(
                        "SELECT role,coefficient,kind,count FROM roles WHERE row_id=? ORDER BY role",
                        (r,),
                    ):
                        example = [
                            {"variable": v, "complement": bool(b)}
                            for v, b in db.execute(
                                "SELECT variable,complement FROM bindings WHERE row_id=? AND role=? ORDER BY variable LIMIT 3",
                                (r, role),
                            )
                        ]
                        roles.append(
                            {
                                "role": role,
                                "coefficient": c,
                                "type": k,
                                "count": n,
                                "examples": example,
                                "truncated": n > 3,
                            }
                        )
                    items.append(
                        {
                            "row": r,
                            "lower": lo,
                            "upper": hi,
                            "scale": scale,
                            "shift": shift,
                            "roles": roles,
                        }
                    )
                return self.envelope(
                    items,
                    total,
                    offset,
                    limit,
                    {"family": key},
                    formula=family[1],
                    ast=json.loads(family[2]),
                )
            if kind == "assignment":
                if key is None:
                    return self.analyze("assignments", offset=offset, limit=limit)
                require_current_assignments(db)
                total = db.execute(
                    "SELECT count(*) FROM assignment_edges WHERE block=?", (key,)
                ).fetchone()[0]
                items = [
                    {"variable": v, "left_index": i, "right_index": j}
                    for v, i, j in db.execute(
                        "SELECT variable,left_idx,right_idx FROM assignment_edges WHERE block=? ORDER BY variable LIMIT ? OFFSET ?",
                        (key, limit, offset),
                    )
                ]
                return self.envelope(items, total, offset, limit, {"assignment": key})
            if kind in ("residual", "exceptions"):
                total = db.execute("SELECT count(*) FROM omitted").fetchone()[0]
                items = [
                    {"row": r, "reason": why}
                    for r, why in db.execute(
                        "SELECT * FROM omitted ORDER BY row_id LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                ]
                return self.envelope(
                    items, total, offset, limit, "unclassified working sets"
                )
            raise LPIRError(f"Unknown inspection kind {kind}")
        finally:
            db.close()

    def analyze(self, kind, scope=None, budget=None, handle=None, offset=0, limit=20):
        from .references import translate

        if kind == "structure":
            from .structural import analyze as structure_analyze

            return structure_analyze(
                self.model,
                translate(self.model, scope, decode=True),
                budget,
                handle,
                offset,
                limit,
            )

        scope = translate(self.model, scope, decode=True)
        handle = translate(self.model, handle, decode=True, reference=True)
        if kind in ("incidence", "coordinates", "operators"):
            from .indexed import analyze as indexed_analyze

            return indexed_analyze(
                self.model, kind, scope, budget, handle, offset, limit
            )
        if kind == "relation_components":
            from .relation_graph import components

            return components(self.model, scope, budget, handle, offset, limit)
        if kind == "explanations":
            if scope is not None:
                raise LPIRError("Explanation compilation requires full-model scope")
            from .compressed import compile_ir

            return compile_ir(self.model, budget=budget, handle=handle)
        if kind.startswith("plugin:"):
            from .plugins import run_plugin

            return run_plugin(self, kind[7:], scope, budget, offset, limit, handle)
        if scope is not None and kind not in (
            "components",
            "wl",
            "decomposition",
            "network",
        ):
            raise LPIRError("This analysis currently supports model scope only")
        return analyze(self.model, kind, scope, budget, handle, offset, limit)

    def query(
        self,
        kind=None,
        key=None,
        offset=0,
        limit=20,
        handle=None,
        spec=None,
        budget=None,
    ):
        self.page(offset, limit)
        m = self.model
        from .references import translate

        handle = translate(m, handle, decode=True, reference=True)
        if isinstance(kind, dict):
            spec = kind
        if spec is not None:
            from .query import run_query
            from .references import translate

            return run_query(m, translate(m, spec, decode=True), budget, offset, limit)
        db = connect(m)
        try:
            if handle:
                out = result_page(m, db, handle, offset, limit)
                if out["scope"].startswith("plugin:"):
                    out["verification"] = "candidate"
                if out["scope"] == "probe":
                    out.update(
                        exactness="numerical",
                        verification="candidate",
                        continuation=None,
                    )
                return out
            if kind == "neighbors":
                obj, ident = key.split(":", 1)
                i = self.resolve(obj, ident)
                if obj == "row":
                    total = int(m.row_ptr[i + 1] - m.row_ptr[i])
                    start = int(m.row_ptr[i]) + offset
                    items = [
                        {"variable": int(v)}
                        for v in m.col_idx[
                            start : min(int(m.row_ptr[i + 1]), start + limit)
                        ]
                    ]
                else:
                    total = int(m.col_ptr[i + 1] - m.col_ptr[i])
                    start = int(m.col_ptr[i]) + offset
                    import numpy as np

                    items = [
                        {"row": int(np.searchsorted(m.row_ptr, p, side="right") - 1)}
                        for p in m.col_positions[
                            start : min(int(m.col_ptr[i + 1]), start + limit)
                        ]
                    ]
                return self.envelope(items, total, offset, limit, {"neighbors": key})
            if kind == "external":
                import hashlib

                assignment = str(key).startswith("assignment_")
                if assignment:
                    require_current_assignments(db)
                    if not db.execute(
                        "SELECT 1 FROM assignment_members WHERE block=?", (key,)
                    ).fetchone():
                        raise LPIRError("Unknown assignment block")

                run = (
                    "e_"
                    + hashlib.sha256(
                        (
                            m.manifest["model_version"]
                            + str(key)
                            + (ASSIGNMENT_VERSION if assignment else "")
                        ).encode()
                    ).hexdigest()[:32]
                )
                if db.execute("SELECT 1 FROM runs WHERE id=?", (run,)).fetchone():
                    return result_page(m, db, run, offset, limit)
                db.execute("CREATE TEMP TABLE chosen(row_id INTEGER PRIMARY KEY)")
                db.execute(
                    "INSERT OR IGNORE INTO chosen SELECT row_id FROM features WHERE family=?",
                    (key,),
                )
                db.execute(
                    "INSERT OR IGNORE INTO chosen SELECT row_id FROM assignment_members WHERE block=?",
                    (key,),
                )
                if not db.execute("SELECT 1 FROM chosen LIMIT 1").fetchone():
                    raise LPIRError("Unknown family/block")
                db.execute("CREATE TEMP TABLE boundary(row_id INTEGER PRIMARY KEY)")
                db.execute("CREATE TEMP TABLE boundary_vars(v INTEGER PRIMARY KEY)")
                for (r,) in db.execute("SELECT row_id FROM chosen"):
                    for v, c in m.iter_terms(r):
                        db.execute(
                            "INSERT OR IGNORE INTO boundary_vars VALUES(?)", (v,)
                        )
                for (v,) in db.execute("SELECT v FROM boundary_vars"):
                    for r in m.incident(v):
                        db.execute("INSERT OR IGNORE INTO boundary VALUES(?)", (r,))
                total = 0
                for (r,) in db.execute(
                    "SELECT row_id FROM boundary WHERE row_id NOT IN (SELECT row_id FROM chosen) ORDER BY row_id"
                ):
                    db.execute(
                        "INSERT INTO results VALUES(?,?,?)",
                        (run, total, json.dumps({"row": r, "name": m.rows[r][1]})),
                    )
                    total += 1
                objective = sum(
                    bool(m.num(m.variables[v][5]))
                    for (v,) in db.execute("SELECT v FROM boundary_vars")
                )
                summary = {"objective_variable_count": objective}
                db.execute(
                    "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                    (
                        run,
                        "external",
                        json.dumps({"key": key}),
                        "complete",
                        total,
                        total,
                        json.dumps(summary),
                    ),
                )
                db.commit()
                return {**result_page(m, db, run, offset, limit), **summary}
            raise LPIRError("Use a query AST, neighbors, external, or a result handle")
        finally:
            db.close()

    def validate(self, spec, budget=None):
        from .references import translate

        if spec.get("kind", "").startswith("structure_"):
            from .structural import validate as structure_validate

            return structure_validate(
                self.model, translate(self.model, spec, decode=True), budget
            )

        spec = translate(self.model, spec, decode=True)
        if spec.get("kind") in (
            "indexed_operator",
            "indexed_partition",
            "cartesian",
            "row_bundle",
            "axis_alignment",
        ):
            from .indexed import validate as indexed_validate

            return indexed_validate(self.model, spec, budget)
        from .validation import validate

        return validate(self.model, spec, budget)

    def probe(self, **options):
        from .probe import probe

        return probe(self.model, **options)

    def verify(self):
        from .validation import verify
        from .indexed import verify as verify_indexed

        result = verify(self.model)
        result["indexed"] = verify_indexed(self.model)
        from .structural import verify as verify_structure

        result["structure"] = verify_structure(self.model)
        return result

    def export(self, path, **options):
        from .exporter import export

        return export(self.model, path, **options)

    def readable(self, result):
        """Convert response references without changing the underlying numerical facts."""
        from .references import translate

        result = translate(self.model, result)
        if isinstance(result, dict):
            result = {**result, "reference_scope": "artifact-local"}
        return result

    def describe(self, offset=0, limit=20, max_bytes=20000, raw_ids=False):
        """The exact structured document consumed by agents and by render()."""
        from .compressed import document

        return document(self.model, offset, limit, max_bytes, raw_ids=raw_ids)

    def render(self, **options):
        from .compressed import render_document

        return render_document(self.describe(**options))
