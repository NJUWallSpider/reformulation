"""Optional continuous relaxation probe. All solver outputs remain numerical."""

import hashlib, json, time, math
import numpy as np
from .numbers import LPIRError
from .semantic import connect


def probe(m, seconds=30, memory_mb=256, offset=0, limit=20):
    if seconds <= 0 or memory_mb <= 0 or offset < 0 or not 1 <= limit <= 1000:
        raise LPIRError("Invalid probe budget/page")
    estimate = len(m.col_idx) * 96 + (len(m.variables) + len(m.rows)) * 256
    base = {
        "model_version": m.manifest["model_version"],
        "analysis_version": "2.0",
        "exactness": "numerical",
        "verification": "candidate",
        "scope": "continuous LP relaxation",
        "applied": False,
    }
    if estimate > memory_mb * 1024**2:
        return {
            **base,
            "completeness": "not_run",
            "reason": "estimated solver working set exceeds memory budget",
            "estimated_bytes": estimate,
        }
    try:
        from scipy.optimize import linprog
        from scipy.sparse import csr_matrix, vstack
    except ImportError as e:
        raise LPIRError("LP probe requires lpir[probe]") from e
    t = time.perf_counter()
    n = len(m.variables)

    def finite(value):
        try:
            result = float(value)
        except (OverflowError, ValueError) as exc:
            raise LPIRError(
                "LP probe cannot represent exact input in finite float64"
            ) from exc
        if not math.isfinite(result):
            raise LPIRError("LP probe cannot represent exact input in finite float64")
        return result

    objective_constant = finite(m.num(m.manifest["objective_constant"]))
    if n == 0:
        feasible = all(
            (m.num(r[2]) is None or m.num(r[2]) <= 0)
            and (m.num(r[3]) is None or m.num(r[3]) >= 0)
            for r in m.rows
        )
        return {
            **base,
            "completeness": "complete",
            "total": 0,
            "items": [],
            "continuation": None,
            "summary": {
                "solver": "constant model check",
                "solver_status": 0 if feasible else 2,
                "objective": objective_constant if feasible else None,
                "seconds": time.perf_counter() - t,
                "estimated_bytes": estimate,
            },
        }
    data = np.fromiter(
        (finite(m.num(int(i))) for i in m.coeff_id), float, len(m.coeff_id)
    )
    c = np.fromiter((finite(m.num(v[5])) for v in m.variables), float, n)
    if not np.isfinite(data).all() or not np.isfinite(c).all():
        raise LPIRError("LP probe cannot represent exact input in finite float64")
    sign = 1 if m.manifest["sense"] == "min" else -1
    a = csr_matrix((data, m.col_idx, m.row_ptr), shape=(len(m.rows), n))
    eq = []
    be = []
    ub = []
    bu = []
    ids = []
    for r in m.rows:
        lo, hi = m.num(r[2]), m.num(r[3])
        if lo is not None and lo == hi:
            eq.append(a[r[0]])
            be.append(finite(lo))
            ids.append(("equality", r[0], 1))
        else:
            if hi is not None:
                ub.append(a[r[0]])
                bu.append(finite(hi))
                ids.append(("upper", r[0], 1))
            if lo is not None:
                ub.append(-a[r[0]])
                bu.append(finite(-lo))
                ids.append(("lower", r[0], -1))
    bounds = [
        (
            finite(m.num(v[3])) if v[3] is not None else None,
            finite(m.num(v[4])) if v[4] is not None else None,
        )
        for v in m.variables
    ]
    remaining = seconds - (time.perf_counter() - t)
    if remaining <= 0:
        return {
            **base,
            "completeness": "not_run",
            "reason": "assembly consumed time budget",
        }
    res = linprog(
        sign * c,
        A_ub=vstack(ub, format="csr") if ub else None,
        b_ub=bu or None,
        A_eq=vstack(eq, format="csr") if eq else None,
        b_eq=be or None,
        bounds=bounds,
        method="highs",
        options={"time_limit": remaining},
    )
    run = (
        "p_"
        + hashlib.sha256(
            json.dumps(
                {
                    "model": m.manifest["model_version"],
                    "seconds": seconds,
                    "memory": memory_mb,
                }
            ).encode()
        ).hexdigest()[:32]
    )
    db = connect(m)
    db.execute("DELETE FROM results WHERE run=?", (run,))
    count = 0
    if res.x is not None:
        for v in m.variables:
            i = v[0]
            x = float(res.x[i])
            rec = {
                "variable": i,
                "value": x,
                "fractional": v[2] != "C" and abs(x - round(x)) > 1e-7,
                "lower_marginal": sign * float(res.lower.marginals[i]),
                "upper_marginal": sign * float(res.upper.marginals[i]),
            }
            db.execute(
                "INSERT INTO results VALUES(?,?,?)", (run, count, json.dumps(rec))
            )
            count += 1
        ie = iu = 0
        for typ, r, side_sign in ids:
            group = res.eqlin if typ == "equality" else res.ineqlin
            j = ie if typ == "equality" else iu
            rec = {
                "row": r,
                "side": typ,
                "slack": float(group.residual[j]),
                "dual": sign * side_sign * float(group.marginals[j]),
            }
            db.execute(
                "INSERT INTO results VALUES(?,?,?)", (run, count, json.dumps(rec))
            )
            count += 1
            if typ == "equality":
                ie += 1
            else:
                iu += 1
    summary = {
        "solver": "SciPy/HiGHS",
        "solver_status": int(res.status),
        "message": res.message,
        "objective": sign * float(res.fun) + objective_constant
        if res.fun is not None
        else None,
        "seconds": time.perf_counter() - t,
        "estimated_bytes": estimate,
    }
    completeness = "complete" if res.status in (0, 2, 3) else "partial"
    db.execute(
        "INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?)",
        (run, "probe", "{}", completeness, count, count, json.dumps(summary)),
    )
    db.commit()
    from .jobs import result_page

    result = result_page(m, db, run, offset, limit)
    db.close()
    return {**result, **base, "summary": summary, "continuation": None}
