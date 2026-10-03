"""Canonical snapshots and exact, typed relational Frame contracts."""

from fractions import Fraction as Q
from decimal import Decimal
import json
import pytest
import mipframe as mf
from mipframe import col
from mipframe import DType
from mipframe import Frame
from mipframe import Ref


@pytest.fixture
def m(tmp_path):
    p = tmp_path / "model.lp"
    p.write_text("""Minimize
 obj: 0 a + 0 b + 0 c + 0 d + 0 z
Subject To
 i0: a+b=1
 i1: c+d=1
 j0: a+c=1
 j1: b+d=1
 extra: 2 a + 3 b + 5 c + 7 d <= 12
 zero: 0 z = 0
Bounds
 0 <= z <= 1
Binaries
 a b c d
End
""")
    with mf.read(p) as model:
        yield model


def ids(frame):
    return list(frame.ids)


def records(values, key=None):
    return Frame.from_records(
        values, schema={"k": DType("int", True), "v": DType("number", True)}, key=key
    )


def test_canonical_identity_and_roundtrip(m, tmp_path):
    assert m.storage_version == 3 and m.stage == "imported"
    assert set(m.vars.column("kind")) <= {"C", "I"}
    assert m.vars.by_name("a").kind == "I" and m.vars.by_name("a").is_binary
    with mf.read(m.source["path"]) as other:
        assert other.uid != m.uid and other.version == m.version
        with pytest.raises((ValueError, mf.SchemaError)):
            m.terms(vars=other.vars)
    saved = m.save(tmp_path / "native")
    with mf.load(saved) as loaded:
        assert loaded.uid == m.uid and loaded.version == m.version
        assert loaded.rows.iloc[0] == m.rows.iloc[0]
        assert len(
            m.rows.select("row").join(loaded.rows.select("row"), on="row")
        ) == len(m.rows)
    manifest = saved / "manifest.json"
    data = json.loads(manifest.read_text())
    data["schema_version"] = 2
    manifest.write_text(json.dumps(data))
    with pytest.raises(mf.MIPFrameError, match="re-import"):
        mf.load(saved)


def test_navigation_terms_and_schema(m):
    r = m.rows.iloc[0]
    x = m.vars.by_name("a")
    assert isinstance(r.vars, mf.VarFrame) and isinstance(x.rows, mf.RowFrame)
    assert len(x.rows) == len(x.terms) == 3
    term = x.terms.where(row=m.rows.head(1)).one()
    assert (
        isinstance(term, mf.Term) and term.row == r and term.var == x and term.coef == 1
    )
    assert isinstance(term.to_record()["row"], Ref)
    with pytest.raises(AttributeError):
        term.coef = 3
    with pytest.raises(TypeError):
        r["rhs"] = 3
    for f in (m.rows, m.vars):
        with pytest.raises(mf.CardinalityError):
            f.one()
        with pytest.raises(mf.CardinalityError):
            f.head(0).one()
    assert m.terms().key == ("row", "var")
    assert ids(m.rows._select((2, 0, 2))) == [2, 0]
    assert ids(r.vars | m.rows.iloc[1].vars) == [0, 1, 2, 3]
    with pytest.raises(TypeError):
        m.rows[0]
    with pytest.raises(TypeError):
        m.rows["F0"]
    with pytest.raises((TypeError, KeyError)):
        m.rows.loc["i0"]
    assert m.rows.loc[0] == r
    assert m.terms().loc[(0, 0)] == term
    assert type(m.rows.select("row", "name")) is Frame
    assert isinstance(m.rows.where(col("nnz") == 2), mf.RowFrame)
    assert isinstance(m.terms().head(1), mf.TermFrame)
    assert type(m.terms().select("row").distinct()) is Frame


def test_exact_query_and_column_pruning(m, monkeypatch):
    original = m.model.num

    def forbidden(*args):
        pytest.fail("Structural query decoded a coefficient")

    monkeypatch.setattr(m.model, "num", forbidden)
    assert len(m.terms().select("row", "var")) == 12
    assert len(m.terms().group_by("row").agg(n=mf.count())) == 5
    assert len(m.rows.select("row", "name")) == 6
    monkeypatch.setattr(m.model, "num", original)
    t = m.terms().select("var", a=col("coef") / 3)
    assert all(isinstance(r["a"], Q) for r in t.collect())
    assert t.column("a").sum() == Q(25, 3)
    with pytest.raises(mf.SchemaError):
        t.where(col("a") < 0.1)
    assert len(t.where(col("a") < Decimal("0.4"))) == 8
    assert len(m.rows.column("ub").argmax()) == 1


def test_projection_join_and_domain_checks(m):
    v = m.vars.iloc[:4]
    i = m.rows.iloc[:2]
    j = m.rows.iloc[2:4]
    p = v.project(i=i, j=j)
    assert type(p) is Frame and p.key == ("var",)
    assert p.loc[0].i == m.rows.loc[0]
    p.check("product", ("i", "j")).require()
    assert ("i", "j") in p.unique
    assert not p.head(3).check("product", ("i", "j")).ok
    assert ids(p.refs("i")) == [0, 1]
    a = m.terms(rows=m.rows.iloc[4:5], vars=v).select("var", a=col("coef"))
    assert a.key is None
    q = p.join(a, on="var", cardinality="1:1", require="both")
    g = q.group_by("i").agg(
        n=col("a").nunique(), amin=col("a").min(), amax=col("a").max()
    )
    assert [r["amin"] for r in g.collect()] == [2, 5]
    assert [r["amax"] for r in g.collect()] == [3, 7]
    assert p.check("function", key="var", value=("i", "j")).ok
    with pytest.raises(mf.CardinalityError) as exc:
        m.vars.project(i=i, j=j).head(1).collect()
    assert len(exc.value.report.violations) == 2
    counts = m.matrix.nnz(rows=m.rows, vars=v, axis="row")
    assert list(counts.column("nnz")) == [2, 2, 2, 2, 4, 0]


@pytest.mark.parametrize("budget", [128, 0.015625])
def test_spill_exact_group_sort_join(budget):
    old = mf.options.query.memory_mb
    mf.options.query.memory_mb = budget
    try:
        eps = Q(1, 10**30)
        f = records(
            [
                dict(k=2, v=Q(1) + eps),
                dict(k=1, v=Q(1)),
                dict(k=1, v=Q(1) + eps),
                dict(k=None, v=None),
            ]
        )
        assert list(f.sort_by("v").column("v")) == [Q(1), Q(1) + eps, Q(1) + eps, None]
        assert f.column("v").nunique() == 2
        assert [
            r["n"] for r in f.group_by("k").agg(n=col("v").nunique()).collect()
        ] == [2, 1, 0]
        g = f.group_by("k").agg(total=col("v").sum())
        assert len(f.join(g, on="k", how="left")) == 4
        assert f.column("v").argmax().count() == 2
        assert (
            f.sample(2, random_state=7).collect()
            == f.sample(2, random_state=7).collect()
        )
        assert f.sample(0).collect() == []
    finally:
        mf.options.query.memory_mb = old


def test_join_barrier_full_violations():
    f = records(
        [dict(k=1, v=1), dict(k=2, v=2), dict(k=2, v=3), dict(k=3, v=4), dict(k=3, v=5)]
    )
    right = Frame.from_records(
        [dict(k=1), dict(k=2)], schema={"k": DType("int", True)}, key=("k",)
    )
    with pytest.raises(mf.CardinalityError) as exc:
        f.join(right, on="k", cardinality="1:1", require="left").head(1).collect()
    assert len(exc.value.report.violations) == 4
    with pytest.raises(mf.SchemaError):
        f.join(f.select("k", other=col("v")), on="k")
    with pytest.raises(mf.SchemaError):
        f.join(f, on="k", cardinality="m:m")


def test_spec_and_exact_exports(m, tmp_path):
    q = (
        m.terms(rows=m.rows.head(2))
        .where(col("coef") > 0)
        .select("var", a=col("coef") / 3)
        .group_by("var")
        .agg(a=col("a").sum())
    )
    restored = mf.frame_from_spec(
        json.loads(json.dumps(q.to_spec())), models={m.uid: m}
    )
    assert restored.collect() == q.collect()
    path = q.write(tmp_path / "out.jsonl")
    detached = mf.read_frame(path, models=(m,))
    assert detached.collect() == q.collect()
    with pytest.raises(FileExistsError):
        q.write(path)
    with pytest.raises(mf.SchemaError):
        mf.frame_from_spec(q.to_spec(), models={})
    with pytest.raises(mf.MaterializationLimitError):
        m.terms().collect(memory_mb=0.015625) if m.nnz > 100 else Frame.from_records(
            (dict(k=n, v=Q(n, 3)) for n in range(1000)),
            schema={"k": "int", "v": "number"},
        ).collect(memory_mb=0.015625)


def test_detached_and_live_lifecycle(m):
    q = m.terms().head(1)
    row = q.one()
    snapshot = q.collect()
    batches = list(q.iter_batches(1))
    live = iter(q)
    m.close()
    assert isinstance(snapshot[0]["row"], Ref)
    assert batches[0].to_records() == snapshot
    for action in (
        lambda: row.coef,
        lambda: len(q),
        lambda: repr(q),
        lambda: next(live),
    ):
        with pytest.raises(mf.MIPFrameError):
            action()
