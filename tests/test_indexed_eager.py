"""Indexed results are ready at read/load and human display hides cache keys."""

import re

import pytest

import mipframe as mf
from mipframe.ir.indexing import discovery as discovery
from mipframe.ir.indexing import operators as operators
from scripts.indexed_instances import model


def test_read_builds_once_load_restores_and_display_is_human(tmp_path, monkeypatch):
    text, _ = model([[5, 2, 0], [2, 5, 1], [0, 1, 5]], groups=2)
    source = tmp_path / "model.lp"
    source.write_text(text)
    original = discovery.discover
    calls = []

    def track(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(discovery, "discover", track)
    with mf.read(source) as m:
        assert len(calls) == 1
        result = m.structure.indexed()
        assert result is m.structure.indexed()
        assert len(calls) == 1
        assert m.ir.indexed["status"] == "complete"
        text = str(m.ir)
        assert "1 coordinate view · 1 exact operator" in text
        assert "run_" not in text
        assert not re.search(r"[0-9a-f]{24}", text)
        assert not re.search(r"[0-9a-f]{24}", repr(result.tensors))
        assert not re.search(r"[0-9a-f]{24}", repr(result.operators))
        assert m.ir.indexed["runs"][0]["id"] == result.id
        saved = m.save(tmp_path / "saved")

    def forbidden(*args):
        pytest.fail("load of a complete saved result reran indexed discovery")

    monkeypatch.setattr(discovery, "discover", forbidden)
    monkeypatch.setattr(operators, "recover", forbidden)
    with mf.load(saved) as m:
        assert m.ir.indexed["status"] == "complete"
        assert len(m.structure.indexed().tensors) == 1
        m.structure.indexed().verify().require()


def test_load_without_indexed_artifact_computes_it(tmp_path, monkeypatch):
    source = tmp_path / "model.lp"
    source.write_text("Minimize\n obj:x\nSubject To\n r:x>=1\nEnd\n")
    with mf.read(source) as m:
        saved = m.save(tmp_path / "saved")
    (saved / "indexing.sqlite").unlink()
    original = discovery.discover
    calls = []

    def track(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(discovery, "discover", track)
    with mf.load(saved) as m:
        assert len(calls) == 1
        assert m.ir.indexed["status"] == "complete"
        assert m.structure.indexed() is m.structure.indexed()


def test_multiple_scopes_use_readable_labels(tmp_path):
    source = tmp_path / "model.lp"
    source.write_text("Minimize\n obj:x+y\nSubject To\n r:x>=1\n s:y>=1\nEnd\n")
    with mf.read(source) as m:
        m.structure.indexed(rows=m.rows.head(1))
        text = str(m.ir.show())
        assert "Full model" in text and "Selection: 1 rows" in text
        assert "run_" not in text
