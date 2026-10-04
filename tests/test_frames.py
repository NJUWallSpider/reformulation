"""Lazy numerical frames, selection, exact statistics and display limits."""

from math import inf

import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "frames.lp"
    path.write_text(
        "Minimize\n o: x - x - y + 17z + 0w\nSubject To\n"
        " c0: -7x + 12y <= 1\n c1: x - y = 0\n c2: x + 2z <= 0\n"
        " c3: y = 1\n c4: -3x + 5w >= 17\n"
        " ranged: -2 <= x + z <= 3\n empty: x - x = 0\n"
        "Bounds\n w free\nBinaries\n x y\nGenerals\n w\nEnd\n"
    )
    with mf.read(path) as m:
        yield m


def test_canonical_records_and_exact_statistics(model):
    assert model.rows.key == ("row",) and model.vars.key == ("var",)
    assert model.rows.shape == (7, len(model.rows.schema))
    r = model.rows.iloc[0]
    assert (
        r.lb == -inf
        and r.ub == 1
        and r.nnz == 2
        and r.coef_min == -7
        and r.coef_max == 12
    )
    assert r.to_record()["row"] == mf.Ref(model.uid, model.version, "row", 0)
    x = model.vars.iloc[0]
    assert x.kind == "I" and x.is_binary and x.type == "B"
    assert list(model.vars.column("nnz")) == [5, 3, 2, 1]
    assert model.rows.iloc[-1].coef_min is None
    record = r.to_record()
    record["ub"] = 999
    assert r.ub == 1

    assert model.vars.column("obj").sum() == 16
    assert model.vars.column("obj").min() == -1
    assert model.vars.column("ub").max() == inf
    assert list(model.vars.column("ub").argmax().ids) == [2, 3]
    counts = model.vars.column("type").value_counts()
    assert {r.type: r.count for r in counts} == {"B": 2, "C": 1, "I": 1}
    assert {
        r.relation: r.count for r in model.rows.column("relation").value_counts()
    } == {"<=": 2, "=": 3, ">=": 1, "range": 1}
    assert model.rows.column("coef_min").min() == -7
    assert model.rows.column("coef_max").max() == 12
    with pytest.raises(mf.SchemaError):
        model.vars.column("absent")


@pytest.mark.parametrize("axis", ["rows", "vars"])
def test_selection_order_identity_and_validation(model, axis):
    f = getattr(model, axis)
    assert list(f.head(2).ids) == [0, 1]
    assert list(f.tail(2).ids) == [len(f) - 2, len(f) - 1]
    assert len(f.head(0)) == len(f.tail(0)) == 0
    assert list(f.iloc[::-1].ids) == list(reversed(range(len(f))))
    a = f.sample(3, random_state=42)
    b = f.sample(3, random_state=42)
    assert list(a.ids) == list(b.ids) == sorted(a.ids)
    assert isinstance(a, type(f))
    for method in (f.head, f.tail, f.sample):
        with pytest.raises(ValueError):
            method(-1)
        with pytest.raises(TypeError):
            method(True)
    with pytest.raises(ValueError):
        len(f.sample(len(f) + 1))
    with pytest.raises(IndexError):
        f.iloc[len(f)]
    with pytest.raises(TypeError):
        f[0]

    base = getattr(model, axis)
    f = base._select((2, 0, 2))
    assert list(f.ids) == [2, 0]
    assert f.iloc[-1] == base.loc[0] == f.by_name(base.loc[0].name)
    assert list(f.iloc[::-1].ids) == [0, 2]
    assert list(f.iloc[:0].ids) == []
    with pytest.raises(KeyError):
        f.loc[1]
    with pytest.raises(TypeError):
        f.iloc[True]
    with pytest.raises(TypeError):
        f.loc[base.loc[0].name]
    locator = f.iloc
    model.close()
    with pytest.raises(mf.MIPFrameError):
        locator[0]


def test_bounded_display_prunes_columns_and_never_counts(model, monkeypatch, capsys):
    monkeypatch.setattr(mf.options.display, "max_rows", 3)
    monkeypatch.setattr(mf.options.display, "max_cols", 3)
    monkeypatch.setattr(
        model.model,
        "iter_terms",
        lambda *args: pytest.fail("Display scanned coefficients"),
    )
    monkeypatch.setattr(
        mf.Frame, "__len__", lambda *args: pytest.fail("Display counted query")
    )
    text = repr(model.rows.where(mf.col("nnz") > 0))
    assert "total not computed" in text and "..." in text
    assert "coef_min" not in text and "coef_max" not in text
    assert capsys.readouterr().out == ""
    pretty = pytest.importorskip("IPython.lib.pretty").pretty
    assert pretty(model.rows) == repr(model.rows)


def test_name_and_family_namespaces(tmp_path):
    p = tmp_path / "names.lp"
    p.write_text("Minimize\n o:0V0+0y\nSubject To\n F0:V0<=1\n foo:y<=2\nEnd\n")
    with mf.read(p) as m:
        assert m.rows.by_name("F0").name == "F0"
        assert m.vars.by_name("V0").name == "V0"
        assert len(m.family("F0@2").rows) == 2
        with pytest.raises(TypeError):
            m.rows["F0"]


def test_display_setting_validation():
    for setting in ['max_rows', 'max_cols']:
        for value in (0, -1):
            with pytest.raises(ValueError):
                setattr(mf.options.display, setting, value)
        for value in (False, 1.5, "12", None):
            with pytest.raises(TypeError):
                setattr(mf.options.display, setting, value)
