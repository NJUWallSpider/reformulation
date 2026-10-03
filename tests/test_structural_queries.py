"""Python-first queries: ID-aligned comparisons, NumPy masks and raw CSR access."""

import builtins
from fractions import Fraction

import numpy as np
import pytest

import mipframe as mf


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "participation.lp"
    path.write_text(
        "Minimize\n obj: 0h + "
        + " + ".join(f"0x{i}" for i in range(12))
        + " + 0w + 0q + 100unused\nSubject To\n"
        + "".join(f" r{i}: h + x{i // 2} <= 1\n" for i in range(24))
        + " outside: 0.5w - q <= 2\nEnd\n"
    )
    with mf.read(path) as model:
        yield model


def forbidden(*args, **kwargs):
    raise AssertionError("A structural query should only read indices")


def test_raw_csr_and_name_indices(model, monkeypatch):
    sparse = pytest.importorskip("scipy.sparse")
    with monkeypatch.context() as patch:
        patch.setattr(sparse.csr_matrix, "toarray", forbidden)
        a = model.A
    assert isinstance(a, sparse.csr_matrix)
    assert a.dtype == np.float64 and a.shape == (25, 16) and a.nnz == 50
    assert a[model.row_index["outside"], model.var_index["w"]] == 0.5
    assert a[model.row_index["outside"], model.var_index["q"]] == -1
    assert a[:, model.var_index["unused"]].nnz == 0  # Objective is not a matrix row.
    assert isinstance(model.row_index, mf.NameIndex)
    assert isinstance(model.var_index, mf.NameIndex)
    assert list(model.row_index) == [f"r{i}" for i in range(24)] + ["outside"]
    assert model.row_index.to_dict() == {r.name: r.id for r in model.constraints}
    assert model.var_index.to_dict() == {v.name: v.id for v in model.variables}
    assert len(model.row_index) == a.shape[0] and len(model.var_index) == a.shape[1]
    a.data[:] = 7
    fresh = model.A
    assert fresh[24, 13] == 0.5
    assert not np.shares_memory(a.data, fresh.data)
    assert model.rows.by_name("outside").terms.iloc[0]["coef"] == Fraction(1, 2)
    row_snapshot = model.row_index.to_dict()
    model.close()
    assert a[row_snapshot["outside"], 13] == 7
    assert fresh[24, 13] == 0.5


def test_name_indices_are_lazy_readonly_and_bounded(model, monkeypatch):
    monkeypatch.setattr(model.model, "num", forbidden)
    monkeypatch.setattr(mf.Model, "A", property(forbidden))
    index = model.row_index
    assert index["r17"] == 17
    with pytest.raises(KeyError):
        index["missing"]
    with pytest.raises(TypeError):
        index[17]
    with pytest.raises(TypeError):
        index["r17"] = 2
    monkeypatch.setattr(mf.options.display, "max_rows", 3)
    text = repr(index)
    assert "r0" in text and "r1" in text and "outside" in text and "..." in text
    assert "r17" not in text
    pretty = pytest.importorskip("IPython.lib.pretty").pretty
    assert pretty(index) == text
    mask = model.rows.head(24).incidence_counts().where(mf.col("nnz") <= 2)
    assert pretty(mask) == repr(mask)
    assert "Frame" in repr(mask)


def test_raw_access_requires_scipy_only_when_requested(model, monkeypatch):
    original = builtins.__import__

    def no_scipy(name, *args, **kwargs):
        if name == "scipy" or name.startswith("scipy."):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_scipy)
    assert model.row_index["r0"] == 0 and model.var_index["h"] == 0
    rows = model.rows.head(24)
    assert len(rows.incidence_counts().where(mf.col("nnz") <= 2)) == 12
    with pytest.raises(mf.MIPFrameError, match=r"mipframe\[scipy\]"):
        model.A


def test_new_views_guard_close(model):
    counts = model.rows.head(24).incidence_counts()
    filtered = counts.where(mf.col("nnz") <= 2)
    index = model.row_index
    stream = iter(filtered)
    next(stream)
    model.close()
    for action in (
        lambda: model.A,
        lambda: len(counts),
        lambda: repr(filtered),
        lambda: filtered.collect(),
        lambda: next(stream),
        lambda: index["r0"],
    ):
        with pytest.raises(mf.MIPFrameError):
            action()
