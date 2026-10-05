"""Indexed discovery is explicit, cached, and absent from default scan rendering."""

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
        assert not calls
        scan = str(m.ir)
        result = m.indexed()
        assert result is m.indexed()
        assert len(calls) == 1
        assert m.ir.indexed["status"] == "complete"
        text = str(m.ir)
        assert text == scan
        assert m.ir.indexed["runs"][0]["total_tensors"] == 1
        assert m.ir.indexed["runs"][0]["total_operators"] == 1
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
        assert len(m.indexed().tensors) == 1
        m.indexed().verify().require()


def test_load_without_indexed_artifact_computes_it(tmp_path, monkeypatch):
    source = tmp_path / "model.lp"
    source.write_text("Minimize\n obj:x\nSubject To\n r:x>=1\nEnd\n")
    with mf.read(source) as m:
        saved = m.save(tmp_path / "saved")
    assert not (saved / "indexing.sqlite").exists()
    original = discovery.discover
    calls = []

    def track(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(discovery, "discover", track)
    with mf.load(saved) as m:
        assert not calls
        assert m.ir.indexed["status"] == "complete"
        assert len(calls) == 1
        assert m.indexed() is m.indexed()


def test_multiple_scopes_use_readable_labels(tmp_path):
    source = tmp_path / "model.lp"
    source.write_text("Minimize\n obj:x+y\nSubject To\n r:x>=1\n s:y>=1\nEnd\n")
    with mf.read(source) as m:
        scan = str(m.ir.show())
        m.indexed(rows=m.rows.head(1))
        assert m.ir.indexed["total_runs"] == 2
        text = str(m.ir.show())
        assert text == scan
        assert "run_" not in text
