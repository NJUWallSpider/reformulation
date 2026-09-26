"""Re-run current L1/L2 on an existing numerical artifact without changing it."""

import argparse
import hashlib
import json
import os
import resource
import shutil
import sys
import time
from pathlib import Path

from lpir import IR
from lpir.compressed import render_document

p = argparse.ArgumentParser()
p.add_argument("base", type=Path)
p.add_argument("--out", type=Path, required=True)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=False)
target = a.out / "ir"
target.mkdir()
for f in a.base.iterdir():
    if f.name.startswith("indexed") or not f.is_file():
        continue
    if f.name == "analysis.sqlite":
        shutil.copy2(f, target / f.name)
    else:
        os.link(f, target / f.name)
code_sha256 = hashlib.sha256(Path("src/lpir/indexed.py").read_bytes()).hexdigest()
start = time.perf_counter()
with IR(target) as ir:
    result = ir.analyze("operators")
    seconds = time.perf_counter() - start
    validation = []
    for name, kind in [
        ("index_tensors", "cartesian"),
        ("index_operators", "indexed_operator"),
    ]:
        page = ir.inspect(name, limit=1000)
        for item in page["items"]:
            v = ir.validate({"kind": kind, "id": item["id"]})
            validation.append(v)
            if v["valid"] is not True:
                raise RuntimeError(v)
    doc = ir.describe()
    md = ir.render()
    if render_document(doc) != md or len(md.encode()) > 20000:
        raise RuntimeError("summary mismatch")
    (a.out / "summary.md").write_text(md)
    (a.out / "summary.json").write_text(json.dumps(doc, indent=2))
    (a.out / "analysis.json").write_text(json.dumps(result, indent=2))
    (a.out / "validation.json").write_text(json.dumps(validation, indent=2))
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
report = {
    "analysis_seconds": seconds,
    "search_completeness": result["completeness"],
    "phase": result["checked_range"]["phase"],
    "verified_views": len(validation),
    "summary_bytes": len(md.encode()),
    "source_artifact": str(a.base),
    "code_sha256": code_sha256,
    "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
    "sidecar_bytes": sum(f.stat().st_size for f in target.glob("indexed*")),
    "verification": "PASS",
    "numeric_files": "hardlinked immutable source; numeric roundtrip reported separately",
}
(a.out / "report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
