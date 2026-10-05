"""Public model creation, lifecycle and native snapshots."""

from fractions import Fraction
from pathlib import Path
import runpy
import shutil

import pytest
import mipframe as mf

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "data"


def test_read_builds_exact_ir(tmp_path):
    with mf.read(EXAMPLE / "tiny.lp") as model:
        assert model.ir.structure['depth'] == 2
        assert model.shape == (2, 2) and model.nnz == 4
        assert model.objective.constant == Fraction(3)
        assert "constraints  : 2" in repr(model)
        assert model.vars.by_name("x").obj == Fraction(1, 10)
        assert model.vars.iloc[-1].kind == "I"
        assert [v.id for v in model.vars.iloc[::-1]] == [1, 0]
        row = model.rows.by_name("demand")
        assert row.lb == 2 and row.ub == float("inf")
        assert row.source_line > 0
        coefficients = {t.var.id: t.coef for t in row.terms}
        coefficients[0] = 999
        assert {t.var.id: t.coef for t in row.terms}[0] == 1
        assert model.objective.coefficients == {0: Fraction(1, 10), 1: Fraction(2)}
        assert not (model.model.path / "analysis.sqlite").exists()
        assert model.verify().status == "PROVED"
        model.write(tmp_path / "out.lp")
        assert not (model.model.path / "analysis.sqlite").exists()
        with pytest.raises(IndexError):
            model.vars.iloc[2]
        with pytest.raises(IndexError):
            model.vars.iloc[-3]
        with pytest.raises(KeyError):
            model.rows.by_name("missing")
        with pytest.raises(TypeError):
            model.vars.iloc[0.5]


def test_lifecycle_and_native_snapshot(tmp_path):
    input_path = tmp_path / '模型 "quoted".mps'
    shutil.copyfile(EXAMPLE / "tiny.mps", input_path)
    model = mf.read(input_path)
    source = model.model.path
    arrays = [
        model.model.data.maps[name]._mmap
        for name in ("row_ptr", "col_idx", "coeff_id", "col_ptr", "col_positions")
    ]
    rows = model.rows
    objective = model.objective
    row = rows.iloc[0]
    expected = repr(model)
    assert '模型' in expected and '\\"quoted\\"' in expected
    with pytest.raises(mf.MIPFrameError, match="outside"):
        model.save(source / "nested")
    link = tmp_path / "broken-link"
    try:
        link.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except OSError:
        pass  # Windows can require a privilege to create symlinks.
    else:
        with pytest.raises(FileExistsError):
            model.save(link)
        assert link.is_symlink()
    (source / "analysis.sqlite").write_bytes(b"retired sidecar")
    (source / "notes.txt").write_text("scratch notes")
    saved = model.save(tmp_path / "模型 #1 100%")
    assert not (saved / "analysis.sqlite").exists()
    assert not (saved / "notes.txt").exists()
    with pytest.raises(FileExistsError):
        model.save(saved)
    model.close()
    model.close()
    assert all(a.closed for a in arrays)
    assert not source.parent.exists()
    for operation in (
        lambda: model.shape,
        lambda: model.objective.constant,
        lambda: len(rows),
        lambda: {t.var.id: t.coef for t in row.terms},
        lambda: objective.constant,
    ):
        with pytest.raises(mf.MIPFrameError, match="closed"):
            operation()
    with mf.load(saved) as restored:
        assert repr(restored) == expected
        assert restored.verify().status == "PROVED"
    assert saved.exists()


def test_failures_cleanup(monkeypatch, tmp_path):
    from mipframe import io
    from tempfile import TemporaryDirectory

    created = []

    def directory(**options):
        temp = TemporaryDirectory(dir=tmp_path, **options)
        created.append(Path(temp.name))
        return temp

    monkeypatch.setattr(io, "TemporaryDirectory", directory)
    bad = tmp_path / "bad.lp"
    bad.write_text("Minimize\n o: x*x\nEnd\n")
    with pytest.raises(mf.MIPFrameError):
        mf.read(bad)
    assert created and all(not p.exists() for p in created)
    monkeypatch.undo()
    with mf.read(EXAMPLE / "tiny.lp") as model:

        def fail(*args, **kwargs):
            raise OSError("snapshot failure")

        from mipframe.store import native7
        # Exercise failure of either clone or stream-copy through the common seam.
        monkeypatch.setattr(native7, "copy_artifact", fail)
        with pytest.raises(OSError, match="snapshot failure"):
            model.save(tmp_path / "failed")
        assert not (tmp_path / "failed").exists()
        assert not list(tmp_path.glob(".mipframe-save-*"))
        assert model.verify().status == "PROVED"


def test_format_and_dimensions(tmp_path):
    path = tmp_path / "input.data"
    shutil.copyfile(EXAMPLE / "tiny.mps", path)
    with pytest.raises(mf.MIPFrameError, match="format"):
        mf.read(path)
    with mf.read(path, format="MPS") as model:
        saved = model.save(tmp_path / "模型 #1 100%")
    import numpy as np

    np.save(saved / "row_ptr.npy", np.array([0]))
    with pytest.raises(mf.MIPFrameError, match="native"):
        mf.load(saved)


@pytest.mark.parametrize("script", ["basic_read.py", "save_reload.py"])
def test_examples(script):
    runpy.run_path(str(ROOT / "examples" / script), run_name="__main__")


def test_public_surface():
    assert mf.__version__ == "1.5.0"
    assert not hasattr(mf, "IR") and not hasattr(mf.Model, "build")
    assert not hasattr(mf.Model, "summary")
    from importlib.util import find_spec

    assert find_spec("mipframe.legacy") is None
    assert not hasattr(mf, "AnalysisContext") and not hasattr(mf, "register_analysis")
    for name in ("analyze", "query", "inspect", "validate", "probe", "describe", "render",
                 "overview", "preview", "readable", "envelope", "page", "resolve"):
        assert not hasattr(mf.Model, name), name
