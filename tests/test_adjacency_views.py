"""Exact, bounded incidence browsing and local family patterns."""

from fractions import Fraction

import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "adjacency.lp"
    path.write_text(
        "Minimize\n obj: 0x0 + 0x1 + 0z0 + 0z1\nSubject To\n"
        " a0: x0 + x1 <= 0\n a1: x0 + x1 = 1\n"
        " b0: -x0 + z0 = 0\n b1: -x1 + z0 = 0\n"
        " c0: x0 + z1 <= 0\n c1: x1 + z1 <= 0\n"
        "Binaries\n x0 x1\nEnd\n"
    )
    with mf.read(path) as m:
        yield m


def test_incidence_navigation_families_and_readonly_sets(model, monkeypatch, tmp_path):
    x = model.vars.by_name("x0")
    assert x.nnz == 4 and len(x.rows) == 4
    terms = x.terms
    assert {t.row.name: t.coef for t in terms} == dict(
        a0=Fraction(1), a1=Fraction(1), b0=Fraction(-1), c0=Fraction(1)
    )
    assert len(terms.head(2)) == 2 and len(terms.sample(2, random_state=3)) == 2
    label = (
        x.rows.family_membership().one().family
        if len(x.rows) == 1
        else x.rows.family_membership().head(1).one().family
    )
    selected = x.rows.family(label)
    assert len(selected) > 0
    assert set(selected.ids) <= set(x.rows.ids)
    with pytest.raises(ValueError):
        x.rows.family("V0")

    members = model.var_families.relations["members"]
    label = members.loc[model.vars.by_name("x0").id].family
    selected = model.vars.family(label)
    assert set(selected.column("name")) == {"x0", "x1"}
    patterns = selected.pattern_counts()
    assert list(patterns.column("count")) == [2]
    assert set(patterns.relations["members"].column("var").ids) == set(selected.ids)
    assert len(patterns.relations["items"]) > 0
    row = model.rows.by_name("b0")
    assert {t.var.name: t.coef for t in row.terms} == {
        "x0": Fraction(-1),
        "z0": Fraction(1),
    }
    assert isinstance(row.vars, mf.VarFrame)

    a = model.rows.by_name("a0").vars
    b = model.rows.by_name("b0").vars
    expected_a = set(a.ids)
    expected_b = set(b.ids)
    monkeypatch.setattr(
        model.model, "num", lambda *args: pytest.fail("Support decoded coefficients")
    )
    assert set((a & b).ids) == expected_a & expected_b
    assert set((a | b).ids) == expected_a | expected_b
    assert set((a - b).ids) == expected_a - expected_b
    with pytest.raises(TypeError):
        a & model.rows
    path = tmp_path / "other.lp"
    path.write_text("Minimize\n o:0\nSubject To\n r:x<=1\nEnd\n")
    with mf.read(path) as other:
        with pytest.raises(mf.SchemaError):
            a & other.vars


def test_views_check_closed_model(model):
    rows = model.vars.by_name("x0").rows
    terms = model.vars.by_name("x0").terms
    model.close()
    for action in (
        lambda: rows.iloc[0],
        lambda: rows.head(),
        lambda: rows.family("F0"),
        lambda: terms.head(),
        lambda: iter(terms),
    ):
        with pytest.raises(mf.MIPFrameError):
            action()
