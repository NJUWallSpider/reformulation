"""Indexed verification and exact numerical roundtrip regression; preserves existing artifacts."""

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from evaluation_utils import saved_model, equal_models


def evaluate(source, out):
    if out.exists():
        raise RuntimeError(f"Refusing to overwrite {out}")
    out.mkdir(parents=True)
    start = time.perf_counter()
    ir = saved_model(source, out / "ir")
    build_seconds = time.perf_counter() - start
    trace = []

    def call(name, fn, *args, **kw):
        t = time.perf_counter()
        response = fn(*args, **kw)
        if hasattr(response, "ok"):
            response = {"valid": response.ok, "summary": response.summary}
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
        indexed = ir.indexed()
        tensors = dict(items=indexed.tensors.head(20).to_records(), total=len(indexed.tensors))
        operators = dict(items=indexed.operators.head(20).to_records(), total=len(indexed.operators))
        validations = []
        for t in tensors["items"]:
            validations.append(
                call("validate", indexed.validate, {"kind": "cartesian", "id": t["id"]})
            )
        for op in operators["items"]:
            validations.append(
                call(
                    "validate",
                    indexed.validate,
                    {"kind": "indexed_operator", "id": op["id"]},
                )
            )
            if indexed.operator(op["id"]).related("alignment").count():
                validations.append(
                    call(
                        "validate",
                        indexed.validate,
                        {"kind": "axis_alignment", "id": op["id"]},
                    )
                )
        search_status = indexed.to_record()["status"]
        doc = call("ir.to_dict", ir.ir.to_dict)
        md = str(ir.ir.show())
        if len(md.encode()) > 20000:
            raise RuntimeError("Summary too large")
        if any(x["valid"] is not True for x in validations):
            raise RuntimeError("View verification failed")
        checked = call("verify", ir.verify)
        if checked["status"] != "PASS":
            raise RuntimeError("Numerical verification failed")
        ir.write_lp(out / "roundtrip.lp")
        with saved_model(out / "roundtrip.lp", out / "roundtrip_ir") as reread:
            equal_models(ir.model, reread.model)
        (out / "summary.md").write_text(md)
        (out / "summary.json").write_text(json.dumps(doc, indent=2, default=str))
    (out / "trace.json").write_text(json.dumps(trace, indent=2, default=str))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        "source": str(source),
        "build_seconds": build_seconds,
        "total_seconds": time.perf_counter() - start,
        "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "peak_rss_scope": "process high-water mark",
        "public_calls": len(trace),
        "returned_bytes": sum(len(json.dumps(x["response"], default=str).encode()) for x in trace),
        "summary_bytes": len(md.encode()),
        "tensors": tensors["total"],
        "operators": operators["total"],
        "search_completeness": search_status,
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
