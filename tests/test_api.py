"""Current public API, without deprecated query languages or readers."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from lpir import IR, LPIRError
from lpir.generate import generate


def test_pagination_and_handles(test1):
    first = test1.query({"table": "rows"}, limit=2)
    second = test1.query(handle=first["handle"], offset=2, limit=2)
    assert first["total"] == second["total"] == 4630
    assert first["items"] != second["items"]
    assert second["offset"] == 2 and second["truncated"]
    assert test1.inspect("row", "c0", limit=2)["total"] == 105
    assert test1.inspect("variable", "x0")["items"][0]["name"] == "x0"
    assert test1.query("neighbors", "variable:x0")["total"] >= 2
    assert test1.inspect("structure_families")["total"] == 7


@pytest.mark.parametrize("kind", ["template", "parameter"])
def test_removed_inspection_aliases(test1, kind):
    with pytest.raises(LPIRError):
        test1.inspect(kind)


@pytest.mark.parametrize("kind", ["filter", "relation"])
def test_removed_string_query_language(test1, kind):
    with pytest.raises(LPIRError, match="query AST"):
        test1.query(kind)


def test_cli_and_optimized_python(test1):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    done = subprocess.run(
        [sys.executable, "-O", "-m", "lpir", "verify", str(test1.model.path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(done.stdout)["status"] == "PASS"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "lpir",
            "inspect",
            str(test1.model.path),
            "row",
            "--key",
            "c0",
            "--limit",
            "1",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(done.stdout)["total"] == 105
    done = subprocess.run(
        [sys.executable, "-m", "lpir", "migrate", str(test1.model.path)],
        text=True,
        capture_output=True,
        env=env,
    )
    assert done.returncode != 0 and "invalid choice" in done.stderr


def test_generator_small_only(make_ir, tmp_path):
    ir = make_ir("Minimize\n o: 2x + 3\nSubject To\n r: x >= 1\nBounds\n x <= 4\nEnd\n")
    path = tmp_path / "copies.lp"
    result = generate(ir.model, path, copies=3)
    with IR.build(path, tmp_path / "copies") as other:
        assert result["copies"] == 3 and other.overview()["rows"] == 3
        assert other.overview()["objective_constant"] == "9"
    path = tmp_path / "sized.lp"
    result = generate(ir.model, path, target_mb=0.002)
    assert result["bytes"] >= int(0.002 * 1024 * 1024)
    with IR.build(path, tmp_path / "sized") as other:
        assert other.verify()["status"] == "PASS"


def test_build_writes_agent_entry_documents(make_ir):
    from lpir.compressed import render_document

    with make_ir('Minimize\n o: 2x + 3\nSubject To\n r: x >= 1\nEnd\n') as ir:
        path = ir.model.path
        doc = json.loads((path / 'summary.json').read_text())
        markdown = (path / 'summary.md').read_text()
        assert doc == ir.describe()
        assert markdown == render_document(doc) == ir.render()
        assert len(markdown.encode()) <= 20000
        assert '```json' not in markdown
        assert ir.model.manifest['artifact_bytes']['summary.md'] == len(markdown.encode())
        assert ir.model.manifest['timings']['summary'] >= 0
        assert ir.verify()['status'] == 'PASS'
