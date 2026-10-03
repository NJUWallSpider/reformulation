"""Small cross-family public MPS smoke evaluation; optional independent SCIP audit."""

import argparse
import json
import hashlib
import sys
import tempfile
import time
from pathlib import Path
from evaluation_utils import saved_model
from mipframe import MIPFrameError
from evaluation_utils import equal_models
from evaluation_utils import require

ROOT = Path(__file__).resolve().parents[1]


def scip_audit(ir, source):
    try:
        from pyscipopt import Model
    except ImportError:
        return {"status": "not_run", "reason": "optional PySCIPOpt unavailable"}
    scip = Model()
    scip.hideOutput()
    scip.readProblem(str(source))
    m = ir.model
    require(
        scip.getNVars() == len(m.variables) and scip.getNConss() == len(m.rows),
        "SCIP dimensions differ",
    )
    require(
        abs(scip.getObjoffset() - float(m.num(m.manifest["objective_constant"])))
        < 1e-9,
        "SCIP objective offset",
    )
    variables = {v.name: v for v in scip.getVars()}

    def equal(a, b):
        return abs(a - b) <= 1e-9 * max(1, abs(a), abs(b))

    for v in m.variables:
        x = variables[v[1]]
        for expected, actual in [
            (m.num(v[3]), x.getLbOriginal()),
            (m.num(v[4]), x.getUbOriginal()),
        ]:
            require(
                abs(actual) >= 1e20
                if expected is None
                else equal(float(expected), actual),
                "SCIP bound",
            )
        require(equal(float(m.num(v[5])), x.getObj()), "SCIP objective")
        require((v[2] in ("B", "I")) == (x.vtype() != "CONTINUOUS"), "SCIP integrality")
    rows = {c.name: c for c in scip.getConss()}
    for r in m.rows:
        c = rows[r[1]]
        values = scip.getValsLinear(c)
        exact = {m.variables[v][1]: a for v, a in m.iter_terms(r[0])}
        require(set(values) == set(exact), "SCIP matrix support")
        require(
            all(equal(values[v], float(a)) for v, a in exact.items()),
            "SCIP matrix coefficient",
        )
        for expected, actual in [
            (m.num(r[2]), scip.getLhs(c)),
            (m.num(r[3]), scip.getRhs(c)),
        ]:
            require(
                abs(actual) >= 1e20
                if expected is None
                else equal(float(expected), actual),
                "SCIP row side",
            )
    return {
        "status": "PASS",
        "exactness": "numerical",
        "relative_tolerance": 1e-9,
        "scope": "all matrix coefficients, objective, domains and bounds",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise MIPFrameError("Use a new evaluation output directory")
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "benchmarks/public/manifest.json").read_text())
    reports = []
    for entry in manifest["instances"]:
        source = ROOT / "benchmarks/public" / entry["file"]
        require(
            hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"],
            "benchmark source changed",
        )
        start = time.perf_counter()
        ir = saved_model(source, out / entry["name"] / "ir")
        elapsed = time.perf_counter() - start
        report = {
            **entry,
            "build_seconds": elapsed,
            "ir": ir.ir.to_dict(),
            "verification": ir.verify(),
            "independent_scip_audit": scip_audit(ir, source),
        }
        names = {v[0]: f"v{v[0]}" for v in ir.model.variables}
        rows = {r[0]: f"r{r[0]}" for r in ir.model.rows}
        (out / entry["name"] / "export_name_mapping.json").write_text(
            json.dumps(
                {
                    "variables": {v[1]: names[v[0]] for v in ir.model.variables},
                    "rows": {r[1]: rows[r[0]] for r in ir.model.rows},
                },
                indent=2,
            )
        )
        ir.write_lp(
            out / entry["name"] / "reconstructed.lp",
            rename_vars=names,
            rename_rows=rows,
        )
        with tempfile.TemporaryDirectory(prefix="mipframe-public-roundtrip-") as t:
            other = saved_model(out / entry["name"] / "reconstructed.lp", Path(t) / "ir")
            equal_models(ir.model, other.model, names, rows)
            other.close()
        report["roundtrip"] = "PASS"
        reports.append(report)
        (out / entry["name"] / "summary.md").write_text(str(ir.ir.show()))
        ir.close()
        print(
            entry["name"],
            entry["split"],
            report["verification"]["status"],
            report["independent_scip_audit"]["status"],
            flush=True,
        )
    (out / "report.json").write_text(
        json.dumps(
            {
                "suite": manifest["suite"],
                "notes": manifest["notes"],
                "python": sys.version,
                "instances": reports,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
