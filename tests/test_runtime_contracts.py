"""Behavioral contracts for orchestration, dependency resolution and recovery."""

import ast
from contextlib import closing
import json
from pathlib import Path

import pytest

from lpir import IR, LPIRError
from lpir import artifact_io, structural
from lpir.structural_store import connect, readmeta, records


SMALL = "Minimize\n o: x+y\nSubject To\n a: x>=1\n b: y>=2\nEnd\n"


@pytest.mark.parametrize(
    "literal",
    [
        "structure_hello",
        "index_hello",
        "explanation_hello",
        {"op": "scan", "table": "structure_families", "analysis": "not-a-scope"},
        {"op": "terms", "rows": [1000000]},
        {"op": "result", "handle": "not-a-result"},
        {"op": "scan", "table": "assignment_members"},
    ],
)
def test_query_literals_do_not_create_dependencies(make_ir, literal):
    ir = make_ir(SMALL)
    for prefix in ("structure", "indexed", "explanations"):
        for path in ir.model.path.glob(prefix + ".*"):
            path.unlink()
    result = ir.query(
        {
            "op": "project",
            "input": {"op": "scan", "table": "rows"},
            "fields": {"label": {"literal": literal}},
        }
    )
    assert result["items"] == [{"label": literal}] * 2
    assert result["completeness"] == "complete"
    assert not (ir.model.path / "structure.sqlite").exists()


def test_structural_failure_rolls_back_to_last_checkpoint(make_ir, monkeypatch):
    ir = make_ir(SMALL)
    scope = {"rows": [0, 1]}
    partial = ir.analyze("structure", scope=scope, budget={"candidates": 1})
    assert partial["completeness"] == "partial"
    analysis = partial["analysis"]
    with closing(connect(ir.model, analysis)) as db:
        previous = readmeta(db, "state")
        revision = readmeta(db, "revision")
        published = records(db, "structure_residuals")

    original_put = structural.put

    def fail_after_write(db, relation, key, value):
        original_put(db, relation, key, value)
        raise RuntimeError("injected publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(structural, "put", fail_after_write)
        with pytest.raises(RuntimeError, match="injected"):
            ir.analyze("structure", scope=scope, handle=partial["handle"])
    with closing(connect(ir.model, analysis)) as db:
        assert readmeta(db, "state") == previous
        assert readmeta(db, "revision") == revision
        assert records(db, "structure_residuals") == published

    complete = ir.analyze("structure", scope=scope, handle=partial["handle"])
    assert complete["completeness"] == "complete"
    assert (
        ir.query({"table": "structure_residuals", "analysis": analysis})["total"] == 2
    )


def test_manifest_failure_does_not_hide_committed_checkpoint(make_ir, monkeypatch):
    ir = make_ir(SMALL)
    scope = {"rows": [0, 1]}
    partial = ir.analyze("structure", scope=scope, budget={"candidates": 1})
    spec = {"table": "structure_residuals", "analysis": partial["analysis"]}
    before = ir.query(spec)
    assert before["total"] < 2
    manifest = ir.model.path / structural.filename(partial["analysis"], ".json")
    old = manifest.read_bytes()

    def fail_snapshot(*args):
        raise OSError("injected snapshot failure")

    with monkeypatch.context() as patch:
        patch.setattr(structural, "atomic_write", fail_snapshot)
        with pytest.raises(OSError, match="snapshot"):
            ir.analyze("structure", scope=scope)
    assert manifest.read_bytes() == old
    after = ir.query(spec)
    assert after["total"] == 2
    assert after["handle"] != before["handle"]
    assert ir.query(handle=before["handle"])["total"] == before["total"]
    assert ir.analyze("structure", scope=scope)["completeness"] == "complete"
    assert json.loads(manifest.read_text())["state"]["phase"] == "done"


def test_atomic_snapshot_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    artifact_io.dump(path, {"revision": 1})

    def fail_replace(*args):
        raise OSError("injected replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(artifact_io.os, "replace", fail_replace)
        with pytest.raises(OSError):
            artifact_io.dump(path, {"revision": 2})
    assert json.loads(path.read_text()) == {"revision": 1}
    assert list(tmp_path.iterdir()) == [path]
    artifact_io.dump(path, {"revision": 2})
    assert json.loads(path.read_text()) == {"revision": 2}


def test_build_budget_is_per_stage_and_partial_build_is_usable(tmp_path):
    source = tmp_path / "small.lp"
    source.write_text(SMALL)
    with IR.build(
        source, tmp_path / "ir", memory_mb=64, analysis_budget={"candidates": 1}
    ) as ir:
        manifest = ir.model.manifest
        assert manifest["analysis_budget"] == {"memory_mb": 64, "candidates": 1}
        assert all(
            x["completeness"] == "partial" for x in manifest["build_analyses"].values()
        )
        summary = json.loads((ir.model.path / "summary.json").read_text())
        assert summary["completeness"] == "partial"
        assert summary["families"] == []
        assert "公式编译尚未完成" in (ir.model.path / "summary.md").read_text()
        assert ir.query({"table": "raw_rows"})["total"] == 2
        assert ir.verify()["status"] == "PASS"
        for kind in ("explanations", "operators", "structure"):
            assert ir.analyze(kind)["completeness"] == "complete"
        assert ir.describe()["rows"] == 2
        assert ir.verify()["explanations"]["expanded_rows"] == 2
    with IR.build(source, tmp_path / "full") as full, IR(tmp_path / "ir") as resumed:
        actual, expected = resumed.describe(raw_ids=True), full.describe(raw_ids=True)
        actual["structure_views"].pop("elapsed_seconds")
        expected["structure_views"].pop("elapsed_seconds")
        assert actual == expected


def test_invalid_build_budget_does_not_leave_an_artifact(tmp_path):
    target = tmp_path / "ir"
    with pytest.raises(LPIRError, match="positive"):
        IR.build(tmp_path / "missing.lp", target, analysis_budget={"seconds": 0})
    assert not target.exists()


def test_formula_memory_budget_is_enforced_and_resumable(tmp_path):
    source = tmp_path / "small.lp"
    source.write_text(SMALL)
    with IR.build(source, tmp_path / "ir", analysis_budget={"memory_mb": 0.0001}) as ir:
        assert (
            ir.model.manifest["build_analyses"]["explanations"]["completeness"]
            == "partial"
        )
        pending = ir.analyze("explanations", budget={"memory_mb": 0.0001})
        assert pending["checked_range"]["rows_compiled"] == 0
        assert ir.analyze("explanations")["completeness"] == "complete"
        assert ir.verify()["explanations"]["expanded_rows"] == 2


def test_indexed_committed_state_survives_missing_snapshot(make_ir):
    ir = make_ir(SMALL)
    before = ir.describe(raw_ids=True)["indexed_views"]
    (ir.model.path / "indexed.json").unlink()
    assert ir.describe(raw_ids=True)["indexed_views"] == before
    assert ir.verify()["indexed"]["status"] == "PASS"
    assert ir.analyze("structure", scope={"rows": [0]})["completeness"] == "complete"


def test_scopes_are_resolved_per_relation_provider(make_ir):
    ir = make_ir(SMALL)
    indexed = ir.analyze("operators", scope={"rows": {"table": "raw_rows"}})
    structured = ir.analyze("structure", scope={"rows": [0, 1]})
    assert indexed["analysis"] != structured["analysis"]
    spec = {
        "op": "union",
        "left": {
            "op": "project",
            "input": {
                "op": "scan",
                "table": "index_tensors",
                "analysis": indexed["analysis"],
            },
            "fields": {"provider": {"literal": "indexed"}},
        },
        "right": {
            "op": "project",
            "input": {
                "op": "scan",
                "table": "structure_residuals",
                "analysis": structured["analysis"],
            },
            "fields": {"provider": {"literal": "structure"}},
        },
    }
    result = ir.query(spec)
    assert {x["provider"] for x in result["items"]} == {"indexed", "structure"}


def test_cli_build_analysis_budget(tmp_path, capsys):
    from lpir.cli import main

    source, target = tmp_path / "small.lp", tmp_path / "ir"
    source.write_text(SMALL)
    main(
        [
            "build",
            str(source),
            "--out",
            str(target),
            "--analysis-candidates",
            "1",
            "--analysis-seconds",
            "5",
            "--memory-mb",
            "64",
        ]
    )
    assert json.loads(capsys.readouterr().out)["rows"] == 2
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["analysis_budget"] == {
        "seconds": 5,
        "memory_mb": 64,
        "candidates": 1,
    }
    assert (
        json.loads((target / "summary.json").read_text())["completeness"] == "partial"
    )


def test_storage_and_analysis_do_not_import_public_facade():
    root = Path(__file__).resolve().parents[1] / "src" / "lpir"
    for path in root.glob("*.py"):
        if path.stem in {"api", "cli", "__init__", "__main__"}:
            continue
        imports = [
            n.module
            for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.ImportFrom) and n.level == 1
        ]
        assert "api" not in imports, path.name
        if path.stem == "storage":
            assert not set(imports) & {
                "compressed",
                "indexed",
                "structural",
                "document",
                "build",
            }
