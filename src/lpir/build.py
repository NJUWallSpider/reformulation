"""Application-level build pipeline over the immutable numerical store."""

import time
import resource
import sys
from .storage import build_numerical
from .artifact_io import dump, atomic_write
from .budget import Budget


def build(source, out, memory_mb=128, analysis_budget=None, **options):
    """Build numerical data, then give each resumable stage its own budget.

    Parsing and semantic classification are required base construction; their
    working-set policy is separate from the resumable analysis time quanta.
    """
    stage_budget = {"memory_mb": memory_mb, **(analysis_budget or {})}
    Budget(**stage_budget)  # Reject invalid options before creating artifacts.
    start = time.perf_counter()
    m = build_numerical(source, out, memory_mb=memory_mb, **options)
    try:
        return _analyze_and_describe(m, memory_mb, start, stage_budget)
    except BaseException:
        m.close()
        raise


def _analyze_and_describe(m, memory_mb, start, stage_budget):
    out = m.path
    manifest = m.manifest
    discovery_start = time.perf_counter()
    from .semantic import discover

    discover(m, memory_mb)
    from .semantic import view_digests

    manifest["view_sha256"] = view_digests(m)
    manifest["timings"]["discover"] = time.perf_counter() - discovery_start
    # The base remains verifiable even if a later optional analysis fails.
    dump(out / "manifest.json", manifest)
    from .compressed import compile_ir

    explanation_start = time.perf_counter()
    explanation = compile_ir(m, budget=stage_budget)
    manifest["timings"]["explanations"] = time.perf_counter() - explanation_start
    from .indexed import analyze as indexed_analyze

    indexed_start = time.perf_counter()
    indexed = indexed_analyze(m, budget=stage_budget)
    manifest["timings"]["indexed"] = time.perf_counter() - indexed_start
    from .structural import analyze as structure_analyze

    structure_start = time.perf_counter()
    structure = structure_analyze(m, budget=stage_budget)
    manifest["timings"]["structure"] = time.perf_counter() - structure_start
    # The default agent entry point is a bounded snapshot of persisted views.
    # Render the same document we save; rendering must not run discovery.
    from .document import document, pending_document, render_document

    summary_start = time.perf_counter()
    manifest["analysis_budget"] = stage_budget
    manifest["build_analyses"] = {
        name: {k: result.get(k) for k in ("completeness", "handle", "continuation")}
        for name, result in (
            ("explanations", explanation),
            ("operators", indexed),
            ("structure", structure),
        )
    }
    summary = (
        document(m)
        if explanation["completeness"] == "complete"
        else pending_document(m, manifest["build_analyses"])
    )
    dump(out / "summary.json", summary)
    atomic_write(out / "summary.md", render_document(summary))
    manifest["timings"]["summary"] = time.perf_counter() - summary_start
    manifest["timings"]["total"] = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    manifest["peak_rss_bytes"] = int(peak if sys.platform == "darwin" else peak * 1024)
    manifest["peak_rss_scope"] = "process high-water mark"
    manifest["artifact_bytes"] = {
        p.name: p.stat().st_size for p in out.iterdir() if p.is_file()
    }
    dump(out / "manifest.json", manifest)
    m.manifest = manifest
    return m
