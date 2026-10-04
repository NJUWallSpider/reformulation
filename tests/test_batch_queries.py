"""Source-preserving graph queries: exactness, navigation and bounded execution."""

import pytest
import mipframe as mf


@pytest.fixture
def m(tmp_path):
    path = tmp_path / "grid.lp"
    path.write_text("""Minimize
 obj: 0x00 + 0x01 + 0x10 + 0x11 + 0twin + 0alone
Subject To
 i0: 2x00 + x01 + twin = 1
 i1: x10 + x11 = 1
 j0: x00 + x10 + twin <= 2
 j1: x01 + x11 <= 2
 empty: 0x00 <= 3
End
""")
    with mf.read(path) as model:
        yield model


def forbidden(*args, **kwargs):
    raise AssertionError("Query read names or coefficients")


def domains(m):
    return m.vars.head(4), m.rows.head(2), m.rows.iloc[2:4]


def test_projection_counts_integer_only(m, monkeypatch):
    budget = 0.015625
    V, I, J = domains(m)
    monkeypatch.setattr(m.model, "num", forbidden)
    monkeypatch.setattr(m.model, "iter_terms", forbidden)
    monkeypatch.setattr(type(m.model.rows), "__getitem__", forbidden)
    P = V.iloc[::-1].project(i=I, j=J, memory_mb=budget)
    assert list(P.column("var").ids) == [3, 2, 1, 0]
    assert [(r.var.id, r.i.id, r.j.id) for r in P] == [
        (3, 1, 3),
        (2, 1, 2),
        (1, 0, 3),
        (0, 0, 2),
    ]
    P.check("product", ("i", "j"), memory_mb=budget).require()
    assert P.loc[0].i.id == 0
    C = m.matrix.nnz(rows=I, vars=m.vars, axis="var")
    assert list(C.column("nnz")) == [1, 1, 1, 1, 1, 0]
    assert list(C.where(mf.col("nnz") == 0).refs("var").ids) == [5]


def test_projection_checks_and_complete_violation_relations(m):
    with pytest.raises(mf.CardinalityError) as exc:
        m.vars.project(i=m.rows, absent=m.rows.head(0)).head(0).to_records()
    report = exc.value.report
    assert not report.ok and len(report.violations) == 12
    assert report.violations.schema["source"].axis == "var"
    matches = report.violations.relations["matches"]
    assert len(matches) == m.nnz
    assert len(matches.where(source=m.vars.head(1))) == 2
    assert len(matches.where(column="absent")) == 0
    assert report.violations.head(1).one().source == m.vars.iloc[0]
    with pytest.raises(mf.ValidationError):
        report.require()

    V, I, J = domains(m)
    P = V.project(i=I, j=J)
    assert P.check("unique", ("i", "j")).ok
    assert not P.check("function", key="i", value="j").ok
    report = P.head(1).check("function", key="i", value="j")
    assert report.ok and report.summary["uncovered_targets"] == {"i": 1, "j": 1}
    missing = P.head(3).check("product", ("i", "j"))
    assert not missing.ok and missing.summary["missing_coordinates"] == 1
    collision = m.vars.head(5).project(i=I, j=J).check("product", ("i", "j"))
    assert not collision.ok and collision.summary["duplicate_records"] == 1
    empty = V.head(0).project(i=I, j=J)
    assert empty.check("unique", ("i", "j")).ok
    assert not empty.check("product", ("i", "j")).ok
    assert V.head(0).project(i=I.head(0), j=J).check("product", ("i", "j")).ok
    with pytest.raises(mf.CardinalityError):
        V.project(i=m.rows.head(0)).to_records()
    assert list(V.project(i=I, also_i=I).column("i").ids) == [0, 0, 1, 1]
    with pytest.raises(ValueError):
        V.project()
    with pytest.raises(TypeError):
        V.project(i=I, exactly_one=False)


def test_patterns_normalized_relations_no_values(m, monkeypatch):
    budget = 0.015625
    monkeypatch.setattr(m.model, "num", forbidden)
    monkeypatch.setattr(type(m.model.rows), "__getitem__", forbidden)
    P = m.vars.pattern_counts(memory_mb=budget)
    assert isinstance(P, mf.Frame) and sum(P.column("count")) == 6
    members = P.related("members")
    items = P.relations["items"]
    assert list(members.column("var").ids) == list(m.vars.ids)
    empty = members.loc[5].pattern
    assert len(items.where(pattern=empty)) == 0
    assert list(P.column("count")) == sorted(P.column("count"), reverse=True)
    assert all(t.kind in ("str", "int") for t in items.schema.values())


def test_budget_validation(m):
    for budget in [0, -1, True, float('inf'), float('nan'), 0.001]:
        with pytest.raises(ValueError):
            m.vars.project(i=m.rows, memory_mb=budget)
        with pytest.raises(ValueError):
            m.vars.pattern_counts(memory_mb=budget)


def test_family_depth_and_cross_snapshot(m, tmp_path):
    membership = m.rows.family_membership(depth=1)
    for item in membership:
        assert item.row.id in m.family(item.family).rows.ids
    spec = membership.to_spec()
    assert mf.frame_from_spec(spec, models={m.uid: m}).to_records() == membership.to_records()
    families = mf.analysis.families(m.rows,method='exact_structural',depth=3)
    family = families.head(1)
    assert len(family.rows) == family.one().count
    with pytest.raises((ValueError, TypeError)):
        m.vars.project(i=m.vars)
    with mf.read(m.source["path"]) as other:
        with pytest.raises(ValueError):
            m.vars.project(i=other.rows)
    with mf.load(m.save(tmp_path / "saved")) as other:
        terms = m.terms(rows=other.rows)
        assert len(terms) == m.nnz
        other.close()
        with pytest.raises(mf.MIPFrameError):
            len(terms)


def test_read_dependencies_and_suspended_lifecycle(m, monkeypatch):
    monkeypatch.setattr(m.model, "iter_terms", forbidden)
    assert m.rows.column("ub").max() == 3
    assert list(m.rows.column("ub").argmax().ids) == [4]
    stream = iter(m.vars.head(4).project(i=m.rows.head(2), j=m.rows.iloc[2:4]))
    detached = next(stream).to_record()
    m.close()
    assert detached["var"].id == 0
    with pytest.raises(mf.MIPFrameError):
        next(stream)


def test_high_degree_disk_projection_cleanup(tmp_path, monkeypatch):
    n = 400
    path = tmp_path / "wide.lp"
    path.write_text(
        "Minimize\n o:0\nSubject To\n r:"
        + "+".join(f"x{i}" for i in range(n))
        + "=1\nEnd\n"
    )
    with mf.read(path) as model:
        monkeypatch.setattr(model.model, "num", forbidden)
        p = model.vars.project(r=model.rows, memory_mb=0.015625)
        assert len(p) == n and p.column("r").nunique() == 1
        report = model.rows.project(v=model.vars, memory_mb=0.015625)
        with pytest.raises(mf.CardinalityError) as exc:
            report.head(1).to_records()
        assert exc.value.report.violations.one().count == n
        assert len(exc.value.report.violations.relations["matches"]) == n
        stores = list(model._query_stores)
    assert all(store.db is None for store in stores)
