"""Replay mathematical IR compilation against copied immutable v2 assets.

Does not change old artifacts or parse source LPs again. Each invocation evaluates
one model so peak RSS is per process. Numerical arrays are hard-linked read-only.
"""

import argparse
import json
import os
import resource
import shutil
import time
from pathlib import Path

from lpir import IR
from lpir.compressed import render_document


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model", type=int, choices=range(1, 7))
    p.add_argument("--out", default="/tmp/lpir-explanations")
    a = p.parse_args()
    src = Path("evaluation") / f"test{a.model}" / "ir"
    out = Path(a.out) / f"test{a.model}"
    dst = out / "ir"
    dst.mkdir(parents=True, exist_ok=False)
    for f in src.iterdir():
        if f.suffix == ".npy" or f.name == "metadata.sqlite":
            os.link(f, dst / f.name)
        elif f.name in ("manifest.json", "analysis.sqlite"):
            shutil.copy2(f, dst / f.name)
    with IR(dst) as ir:
        start = time.perf_counter()
        compiled = ir.analyze("explanations")
        elapsed = time.perf_counter() - start
        start = time.perf_counter()
        verified = ir.verify()
        verification = time.perf_counter() - start
        doc = ir.describe()
        markdown = ir.render()
        if render_document(doc) != markdown:
            raise RuntimeError("Markdown changed document content")
        (out / "summary.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        )
        (out / "summary.md").write_text(markdown)
        families = []
        offset = 0
        while True:
            page = ir.inspect("explanation", offset=offset, limit=20)
            families.extend(page["items"])
            if not page["truncated"]:
                break
            offset += len(page["items"])
        report = {
            "model": a.model,
            "compile_seconds_including_expansion": elapsed,
            "verification_seconds": verification,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "sidecar_bytes": (dst / "explanations.sqlite").stat().st_size,
            "markdown_bytes": len(markdown.encode()),
            "markdown_matches_renderer": True,
            "machine_document": "summary.json",
            "compilation": compiled,
            "verification": verified,
            "families": families,
        }
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k not in ("families", "compilation", "verification")
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
