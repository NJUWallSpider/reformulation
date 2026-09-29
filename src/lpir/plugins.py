"""Trusted host-side discovery extensions; query JSON cannot load or execute code."""

from dataclasses import dataclass
from .numbers import LPIRError

_REGISTRY = {}


@dataclass(frozen=True)
class AnalysisContext:
    """Public numerical/relational access; no database layout is part of this contract."""

    _ir: object

    def rows(self):
        m = self._ir.model
        for r in m.rows:
            yield {
                "row": r[0],
                "name": r[1],
                "lower": m.num(r[2]),
                "upper": m.num(r[3]),
                "source_line": r[4],
            }

    def variables(self):
        m = self._ir.model
        for v in m.variables:
            yield {
                "variable": v[0],
                "name": v[1],
                "kind": v[2],
                "lower": m.num(v[3]),
                "upper": m.num(v[4]),
                "cost": m.num(v[5]),
            }

    def terms(self, row):
        return self._ir.model.iter_terms(row)

    def incident(self, variable):
        return self._ir.model.incident(variable)

    def query(self, spec, **kw):
        return self._ir.query(spec=spec, **kw)

    def validate(self, spec):
        return self._ir.validate(spec)


def register_analysis(name, version, discover):
    """discover(context, scope, cursor) yields JSON records with optional hypotheses.

    Plugins resume at a record cursor supplied by the host. Mathematical claims are
    candidates unless a built-in validator verifies their ``hypothesis`` field.
    """
    if not name or not version or not callable(discover):
        raise LPIRError("Plugin name, version and callable required")
    _REGISTRY[name] = (str(version), discover)


def run_plugin(ir, name, scope=None, budget=None, offset=0, limit=20, handle=None):
    import hashlib, json
    from .semantic import connect
    from .budget import Budget, BudgetExceeded
    from .results import result_page

    if name not in _REGISTRY:
        raise LPIRError(f"Unregistered analysis plugin {name}")
    version, discover = _REGISTRY[name]
    context = AnalysisContext(ir)
    m = ir.model
    spec = {
        "plugin": name,
        "version": version,
        "scope": scope,
        "model": m.manifest["model_version"],
    }
    run = (
        "x_"
        + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:32]
    )
    if handle and handle != run:
        raise LPIRError("Foreign plugin continuation")
    db = connect(m)
    old = db.execute("SELECT cursor,status FROM runs WHERE id=?", (run,)).fetchone()
    cursor = old[0] if old else 0
    if old and old[1] == "complete":
        out = result_page(m, db, run, offset, limit)
        out["verification"] = "candidate"
        db.close()
        return out
    if not old:
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
            (run, "plugin:" + name, json.dumps(spec), "partial", 0, 0, "{}"),
        )
        db.commit()
    b = Budget(**(budget or {}))
    status = "complete"
    try:
        for record in discover(context, scope, cursor):
            b.tick()
            payload = dict(record)
            payload["verification"] = "candidate"
            if "hypothesis" in payload:
                payload["validation"] = context.validate(payload["hypothesis"])
                payload["verification"] = payload["validation"]["verification"]
            encoded = json.dumps(payload)
            b.require(len(encoded) * 4)
            db.execute("INSERT INTO results VALUES(?,?,?)", (run, cursor, encoded))
            cursor += 1
    except BudgetExceeded:
        status = "partial"
    db.execute(
        "UPDATE runs SET status=?,cursor=?,processed=? WHERE id=?",
        (status, cursor, cursor, run),
    )
    db.commit()
    out = result_page(m, db, run, offset, limit)
    out["verification"] = "candidate"
    db.close()
    return out
