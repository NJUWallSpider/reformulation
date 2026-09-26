"""Public-tool replay and bounded L1/L2 regression; preserves existing artifacts."""

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from lpir import IR
from lpir.compressed import render_document


def evaluate(source, out):
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite {out}")
    out.mkdir(parents=True)
    start = time.perf_counter()
    ir = IR.build(source, out / "ir")
    build_seconds = time.perf_counter() - start
    trace = []

    def call(name, fn, *args, **kw):
        t = time.perf_counter()
        response = fn(*args, **kw)
        trace.append(
            {
                "operation": name,
                "args": args,
                "kwargs": kw,
                "seconds": time.perf_counter() - t,
                "response": response,
            }
        )
        return response

    with ir:
        tensors = call("inspect", ir.inspect, "index_tensors", limit=20)
        operators = call("inspect", ir.inspect, "index_operators", limit=20)
        validations = []
        for t in tensors["items"]:
            validations.append(
                call("validate", ir.validate, {"kind": "cartesian", "id": t["id"]})
            )
        for op in operators["items"]:
            validations.append(
                call(
                    "validate",
                    ir.validate,
                    {"kind": "indexed_operator", "id": op["id"]},
                )
            )
            if "alignment" in op["kernel_statistics"]:
                validations.append(
                    call(
                        "validate",
                        ir.validate,
                        {"kind": "axis_alignment", "id": op["id"]},
                    )
                )
        doc = call("describe", ir.describe)
        md = ir.render()
        if render_document(doc) != md:
            raise RuntimeError("Summary codec changed content")
        if len(md.encode()) > 20000:
            raise RuntimeError("Summary too large")
        if any(x["valid"] is not True for x in validations):
            raise RuntimeError("View verification failed")
        checked = call("verify", ir.verify)
        if checked["status"] != "PASS":
            raise RuntimeError("Numerical verification failed")
        ir.export(out / "roundtrip.lp")
        with IR.build(out / "roundtrip.lp", out / "roundtrip_ir") as reread:
            if len(reread.model.rows) != len(ir.model.rows) or len(
                reread.model.variables
            ) != len(ir.model.variables):
                raise RuntimeError("Roundtrip shape changed")
            # Numerical IDs may change with exported declaration order: compare by names.
            orig = ir.model
            copy = reread.model
            for v in orig.variables:
                w = copy.variables[copy.var_names[v[1]]]
                if v[2] != w[2] or tuple(orig.num(v[k]) for k in (3, 4, 5)) != tuple(
                    copy.num(w[k]) for k in (3, 4, 5)
                ):
                    raise RuntimeError("Roundtrip domain/cost changed")
            for r in orig.rows:
                q = copy.rows[copy.row_names[r[1]]]
                if tuple(orig.num(r[k]) for k in (2, 3)) != tuple(
                    copy.num(q[k]) for k in (2, 3)
                ):
                    raise RuntimeError("Roundtrip bounds changed")
                a = {orig.variables[v][1]: c for v, c in orig.iter_terms(r[0])}
                b = {copy.variables[v][1]: c for v, c in copy.iter_terms(q[0])}
                if a != b:
                    raise RuntimeError("Roundtrip coefficients changed")
            if orig.manifest["sense"] != copy.manifest["sense"] or orig.num(
                orig.manifest["objective_constant"]
            ) != copy.num(copy.manifest["objective_constant"]):
                raise RuntimeError("Roundtrip objective changed")
        (out / "summary.md").write_text(md)
        (out / "summary.json").write_text(json.dumps(doc, indent=2))
    (out / "trace.json").write_text(json.dumps(trace, indent=2))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        "source": str(source),
        "build_seconds": build_seconds,
        "total_seconds": time.perf_counter() - start,
        "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "peak_rss_scope": "process high-water mark",
        "public_calls": len(trace),
        "returned_bytes": sum(len(json.dumps(x["response"]).encode()) for x in trace),
        "summary_bytes": len(md.encode()),
        "tensors": tensors["total"],
        "operators": operators["total"],
        "search_completeness": doc["indexed_views"]["search_completeness"],
        "numeric_roundtrip": "PASS",
        "view_verification": "PASS",
        "artifact_bytes": {
            p.name: p.stat().st_size for p in (out / "ir").iterdir() if p.is_file()
        },
    }
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    evaluate(a.input, a.out)
