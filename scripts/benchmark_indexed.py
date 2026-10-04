"""Benchmark native loading with indexed discovery on a copied model."""

import argparse
import hashlib
import json
import resource
import shutil
import sys
import time
from pathlib import Path

from mipframe import load
from mipframe.ir import indexing

p = argparse.ArgumentParser()
p.add_argument("base", type=Path)
p.add_argument("--out", type=Path, required=True)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=False)
target = a.out / "model"
shutil.copytree(
    a.base, target, ignore=shutil.ignore_patterns("indexed*", "indexing.sqlite")
)
code = hashlib.sha256()
for path in sorted(Path(indexing.__file__).parent.glob("*.py")):
    code.update(path.read_bytes())
start = time.perf_counter()
with load(target) as m:
    load_seconds = time.perf_counter() - start
    start = time.perf_counter()
    result = m.indexed()
    elapsed = time.perf_counter() - start
    result.verify().require()
    (a.out / "summary.json").write_text(m.ir.to_json())
    (a.out / "summary.txt").write_text(str(m.ir.show()))
    report = {
        "load_with_indexed_seconds": load_seconds,
        "cached_access_seconds": elapsed,
        "status": "complete",
        "tensors": len(result.tensors),
        "operators": len(result.operators),
        "verification": "PASS",
        "code_sha256": code.hexdigest(),
        "sidecar_bytes": (target / "indexing.sqlite").stat().st_size,
    }
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
report["peak_rss_bytes"] = rss if sys.platform == "darwin" else rss * 1024
(a.out / "report.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
