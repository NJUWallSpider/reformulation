"""Compose bounded documents from published analysis views; never discover."""

import json
from contextlib import closing
from .numbers import LPIRError
from .compressed import open_db, card


def pending_document(m, analyses):
    """Build-time numerical entry point when formula compilation is incomplete.

    Do not expose unverified formula records or scan the model again merely to
    produce a snapshot after the analysis budget was exhausted.
    """
    return {
        "model_version": m.manifest["model_version"],
        "variables": len(m.variables),
        "rows": len(m.rows),
        "nonzeros": len(m.col_idx),
        "scope": "numerical model; formula compilation incomplete",
        "completeness": "partial",
        "analyses": analyses,
        "families": [],
        "coverage": {"total_families": None, "numerical_reference_rows": len(m.rows)},
        "numerical_handles": [{"table": "raw_rows"}, {"table": "variables"}],
        "page": {"shown": 0, "next_offset": None, "truncated": False},
    }


def render_document(doc):
    """Human Markdown projection; complete machine data remains in describe()."""
    from .markdown import render

    return render(doc)


def document(m, offset=0, limit=20, max_bytes=20000, raw_ids=False):
    if offset < 0 or not 1 <= limit <= 1000 or max_bytes < 2000:
        raise LPIRError("Invalid explanation document budget")
    with closing(open_db(m)) as db:
        return _compose(m, db, offset, limit, max_bytes, raw_ids)


def _compose(m, db, offset, limit, max_bytes, raw_ids):
    meta = json.loads(
        db.execute("SELECT value FROM metadata WHERE key='model'").fetchone()[0]
    )
    count = db.execute("SELECT count(*) FROM families").fetchone()[0]
    residual_count = db.execute("SELECT count(*) FROM residuals").fetchone()[0]
    residuals = [
        {
            "row": r,
            "terms": n,
            "positive": p,
            "negative": q,
            "types": json.loads(t),
            "coefficient_min": a,
            "coefficient_max": b,
            "lower": lo,
            "upper": hi,
            "handle": {"table": "terms", "row_id": r},
        }
        for r, n, p, q, t, a, b, lo, hi in db.execute(
            "SELECT * FROM residuals ORDER BY nnz DESC,row_id LIMIT 3"
        )
    ]
    doc = {
        **meta,
        "scope": "all numerical rows; formula-family page",
        "numerical_handles": [{"table": "raw_rows"}, {"table": "variables"}],
        "coverage": {
            "total_families": count,
            "numerical_reference_rows": residual_count,
        },
        "large_or_unstructured_rows": residuals,
        "residuals_handle": {"table": "explanation_residuals"},
        "page": {
            "offset": offset,
            "limit": limit,
            "shown": 0,
            "next_offset": None,
            "truncated": False,
        },
        "families": [],
    }
    from .indexed import summary as indexed_summary

    from .structural_summary import summary as structure_summary

    structured = structure_summary(m, max_bytes=min(12000, max_bytes * 3 // 4))
    if structured is not None:
        doc["structure_views"] = structured
        fully_covered = structured["covered_rows"] == structured["scope_rows"]
        doc["presentation"] = {
            "primary": "structure",
            "show_indexed_diagnostics": structured["covered_rows"] == 0,
            "show_local_diagnostics": not (fully_covered or structured["covered_rows"]),
        }
    # Preserve the existing machine interface; complete structural coverage only
    # changes which view is primary in the human renderer.
    indexed = indexed_summary(m, max_bytes=min(6500, max_bytes // 2))
    if indexed is not None:
        doc["indexed_views"] = indexed
    from .references import translate

    if not raw_ids:
        doc = translate(m, doc)
        doc["reference_scope"] = (
            "artifact-local; short references are accepted by inspect/query/validate"
        )
    # Any selection happens in the IR document, never inside the Markdown renderer.
    for (fid,) in db.execute(
        "SELECT id FROM families ORDER BY rows DESC,id LIMIT ? OFFSET ?",
        (limit, offset),
    ):
        item = card(db, fid)
        if not raw_ids:
            item = translate(m, item)
        doc["families"].append(item)
        doc["page"].update(
            shown=len(doc["families"]),
            next_offset=offset + len(doc["families"]),
            truncated=True,
        )
        if len(render_document(doc).encode()) > max_bytes:
            doc["families"].pop()
            break
    shown = len(doc["families"])
    doc["page"].update(
        shown=shown,
        next_offset=offset + shown if offset + shown < count else None,
        truncated=offset + shown < count,
    )
    if not shown and offset < count:
        doc["page"]["reason"] = (
            "Next family exceeds document byte budget; inspect explanation or increase max_bytes"
        )
        doc["page"]["next_offset"] = offset
    if len(render_document(doc).encode()) > max_bytes:
        raise LPIRError("Document metadata exceeds byte budget; increase max_bytes")
    return doc
