"""Rebuild each local instance under a separate wall-clock limit, without overwriting the current IRs.

Example: PYTHONPATH=src python scripts/evaluate_structure.py --out /tmp/lpir-evaluation
The parent enforces the limit over parsing, all analysis, rendering and verification.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

from lpir import IR
from lpir.compressed import render_document


INSTANCES = [
    "danoint",
    "assign1-5-8",
    "test1",
    "test2",
    "test3",
    "test4",
    "test5",
    "test6",
]


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def rss():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def worker(source, out, seconds):
    start = time.monotonic()
    result = {
        "source": str(source.resolve()),
        "limit_seconds": seconds,
        "status": "running",
    }

    def checkpoint(phase):
        result.update(
            phase=phase, elapsed_seconds=time.monotonic() - start, peak_rss_bytes=rss()
        )
        write(out / "report.json", result)

    checkpoint("build")
    with IR.build(source, out / "ir") as ir:
        result["build_seconds"] = time.monotonic() - start
        checkpoint("render")

        def render():
            doc = ir.describe()
            md = render_document(doc)
            if len(md.encode()) > 20000:
                raise RuntimeError("Summary exceeds byte budget")
            write(out / "summary.json", doc)
            (out / "summary.md").write_text(md)
            result["summary_bytes"] = len(md.encode())

        render()
        previous = None
        while time.monotonic() - start < seconds - 45:
            answer = ir.analyze(
                "operators",
                budget={
                    "seconds": min(
                        30, max(0.01, seconds - 45 - (time.monotonic() - start))
                    ),
                    "memory_mb": 128,
                    "candidates": 1000000,
                },
            )
            result["indexed_search"] = answer["completeness"]
            result["indexed_progress"] = answer.get("checked_range")
            checkpoint("indexed")
            if (
                answer["completeness"] == "complete"
                or answer.get("checked_range") == previous
            ):
                break
            previous = answer.get("checked_range")
        previous = None
        while time.monotonic() - start < seconds - 45:
            answer = ir.analyze(
                "structure",
                budget={
                    "seconds": min(
                        30, max(0.01, seconds - 45 - (time.monotonic() - start))
                    ),
                    "memory_mb": 128,
                    "candidates": 1000000,
                },
            )
            result["structure_search"] = answer["completeness"]
            result["structure_progress"] = answer["checked_range"]
            checkpoint("structure")
            if (
                answer["completeness"] == "complete"
                or answer["checked_range"] == previous
            ):
                break
            previous = answer["checked_range"]
        render()
        checkpoint("verify")
        result["verification"] = ir.verify()
        checkpoint("measure")
        blocks = ir.inspect("structure_blocks", limit=1000)
        families = ir.inspect("structure_families", limit=1000)
        result.update(
            blocks=blocks["total"],
            families=families["total"],
            structured_rows=sum(f["rows"] for f in families["items"]),
            total_rows=len(ir.model.rows),
            total_variables=len(ir.model.variables),
            residual_rows=ir.inspect("structure_residuals", limit=1)["total"],
            description_length={
                k: sum(f["description_length"][k] for f in families["items"])
                for k in ("indexed_bytes", "flat_bytes")
            },
            analysis_timings=ir.model.manifest["timings"],
        )
        from lpir.structural import connect
        from lpir.structural_relations import description_metrics

        with connect(ir.model) as structure_db:
            result["description_length"] = description_metrics(structure_db)
        result["independent_indices"] = [
            dict(rows=f["rows"], **f.get("independent_indices", {}))
            for f in families["items"]
        ]
        result["objective_verification"] = result["verification"]["structure"].get(
            "objective"
        )
        result["unstructured_rows"] = result["total_rows"] - result["structured_rows"]
        result["status"] = (
            "verified"
            if result["verification"]["structure"]["search_completeness"] == "complete"
            else "verified_partial"
        )
        checkpoint("done")


def run(source, out, seconds):
    out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    with (out / "worker.log").open("w") as log:
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--worker",
                    str(source),
                    "--out",
                    str(out),
                    "--seconds",
                    str(seconds),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=seconds,
            )
            status = "failed" if proc.returncode else None
        except subprocess.TimeoutExpired:
            status = "timeout"
    report = (
        json.loads((out / "report.json").read_text())
        if (out / "report.json").exists()
        else {}
    )
    if status:
        report.update(status=status, completed_phase=report.get("phase"))
    report.update(
        wall_seconds=time.monotonic() - start,
        limit_seconds=seconds,
        ir_available=(out / "ir" / "manifest.json").exists(),
        summary_available=(out / "summary.md").exists(),
    )
    write(out / "report.json", report)
    print(
        json.dumps({"instance": source.stem, **report}, ensure_ascii=False), flush=True
    )
    return source.stem, report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seconds", type=float, default=300)
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--instances", nargs="+", default=INSTANCES)
    p.add_argument("--worker", type=Path)
    args = p.parse_args()
    if args.worker:
        worker(args.worker, args.out, args.seconds)
        return
    if not 0 < args.seconds <= 300 or not 1 <= args.jobs <= 4:
        p.error("0 < seconds <= 300; 1 <= jobs <= 4")
    args.out.mkdir(parents=True, exist_ok=False)
    reports = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(
                run, Path("data") / (name + ".lp"), args.out / name, args.seconds
            )
            for name in args.instances
        ]
        for f in as_completed(futures):
            name, report = f.result()
            reports[name] = report
            write(args.out / "report.json", reports)
    lines = [
        "# 结构 IR 重建回归",
        "",
        "每个算例独立硬限时 300 秒，包含解析、分析、summary 生成与验证。",
        "",
        "| 算例 | 状态 | 总耗时（秒） | 结构覆盖 | 块 / 族 | 峰值内存 MiB | summary |",
        "|---|---|---:|---|---|---:|---|",
    ]
    for name in args.instances:
        r = reports[name]
        link = f"[{name}]({name}/summary.md)" if r["summary_available"] else name
        lines.append(
            f"| {link} | {r['status']} | {r['wall_seconds']:.2f} | {r.get('structured_rows', '—')} / {r.get('total_rows', '—')} | {r.get('blocks', '—')} / {r.get('families', '—')} | {r.get('peak_rss_bytes', 0) / 1048576:.1f} | {'已生成' if r['summary_available'] else '未生成'} |"
        )
    (args.out / "report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
