"""Relational safety contracts independent of signature labels and heuristics."""

from fractions import Fraction as Q
import json
import pytest
import mipframe as mf
from mipframe import col


def frame(values, *, key=None):
    return mf.Frame.from_records(
        values,
        schema={"a": mf.DType("int", True), "b": mf.DType("number", True)},
        key=key,
    )


def test_join_cardinality_and_stable_order():
    budget = 0.015625
    for (cardinality, left, right, passes) in [
        ('1:1', [1, 2], [1, 2], True),
        ('1:1', [1, 1], [1, 2], False),
        ('1:m', [1, 2], [1, 1], True),
        ('1:m', [1, 1], [1, 2], False),
        ('m:1', [1, 1], [1, 2], True),
        ('m:1', [1, 2], [1, 1], False),
        ('m:m', [1, 1], [1, 1], True),
    ]:
        a = mf.Frame.from_records(
            (dict(k=k, l=n) for n, k in enumerate(left)), schema={"k": "int", "l": "int"}
        )
        b = mf.Frame.from_records(
            (dict(k=k, r=n) for n, k in enumerate(right)), schema={"k": "int", "r": "int"}
        )
        old = mf.options.query.memory_mb
        mf.options.query.memory_mb = budget
        try:
            joined = a.join(b, on="k", cardinality=cardinality)
            if not passes:
                with pytest.raises(mf.CardinalityError):
                    joined.head(0).collect()
            else:
                assert joined.collect() == [
                    dict(k=k, l=i, r=j)
                    for i, k in enumerate(left)
                    for j, v in enumerate(right)
                    if k == v
                ]
        finally:
            mf.options.query.memory_mb = old


def test_match_requirements_are_independent():
    a = mf.Frame.from_records([dict(k=1), dict(k=2)], schema={"k": "int"}, key=("k",))
    b = mf.Frame.from_records([dict(k=1), dict(k=3)], schema={"k": "int"}, key=("k",))
    for require in [None, 'left', 'right', 'both']:
        query = a.join(b, on="k", how="left", require=require)
        if require is None:
            assert query.collect() == [dict(k=1), dict(k=2)]
        else:
            with pytest.raises(mf.CardinalityError) as exc:
                query.head(1).collect()
            assert len(exc.value.report.violations) == (2 if require == "both" else 1)


def test_null_exact_composite_and_renamed_keys():
    a = frame([dict(a=1, b=Q(1)), dict(a=None, b=None)], key=("a", "b"))
    b = a.select(i=col("a"), v=col("b"), hit=1)
    q = a.join(b, on={"a": "i", "b": "v"}, how="left", cardinality="1:1")
    assert list(q.column("hit")) == [1, None]
    assert q.loc[(1, Q(1))].hit == 1
    assert q.loc[(None, None)].hit is None
    assert list(a.where(col("a") != None)) == []
    assert len(a.where(col("a").is_null())) == 1
    assert a.column("b").count() == 1
    assert a.head(0).column("b").sum() == 0
    assert a.head(0).group_by().agg(
        s=col("b").sum(), n=mf.count(), lo=col("b").min()
    ).collect() == [dict(s=0, n=0, lo=None)]


def test_uniqueness_is_local_and_no_false_propagation():
    f = frame([dict(a=1, b=1), dict(a=1, b=2)])
    g = f.head(1)
    g.check("unique", "a").require()
    assert ("a",) in g.unique and ("a",) not in f.unique
    h = frame([dict(a=1, b=1), dict(a=2, b=2)], key=("a",))
    assert h.select(x=col("a")).key == ("x",)
    assert h.select(x=col("a") + 1).key is None
    assert f.distinct().key == ("a", "b")
    with pytest.raises(mf.SchemaError):
        f.loc[1]
    with pytest.raises(mf.SchemaError):
        h.select("*", a=col("a"))


def test_schema_and_spec_do_not_accept_claimed_proofs():
    f = frame([dict(a=1, b=1), dict(a=1, b=2)])
    spec = json.loads(json.dumps(f.to_spec()))
    spec["plan"]["unique"] = [["a"]]
    restored = mf.frame_from_spec(
        spec, models={}, relations={spec["plan"]["relation"]: f}
    )
    assert restored.key is None and not restored.unique
    with pytest.raises(mf.SchemaError):
        mf.frame_from_spec(spec, models={}, relations={})
    wrong = mf.Frame.from_records([dict(a="x")], schema={"a": "str"})
    with pytest.raises(mf.SchemaError):
        mf.frame_from_spec(spec, models={}, relations={spec["plan"]["relation"]: wrong})
    with pytest.raises(mf.SchemaError):
        mf.DType("object")
    with pytest.raises(mf.SchemaError):
        mf.Ref("uid", "version", "var", True)
    enum = mf.DType("enum", values=("C", "I"))
    e = mf.Frame.from_records([{"kind": "I"}], schema={"kind": enum})
    assert len(e.where(col("kind") == "I")) == 1
    with pytest.raises(mf.SchemaError):
        mf.Frame.from_records([{"kind": "B"}], schema={"kind": enum})


def test_sampling_stable_batches_and_export_failures(tmp_path, monkeypatch):
    f = frame([dict(a=i, b=Q(i, 7)) for i in range(30)])
    sample = f.sample(7)
    assert sample.collect() == sample.collect()
    assert list(sample.column("a")) == sorted(sample.column("a"))
    batches = list(f.iter_batches(3, memory_mb=0.015625))
    assert [r for b in batches for r in b.to_records()] == f.collect()
    with pytest.raises(AttributeError):
        batches[0].schema = {}
    from mipframe.frame.schema import encode
    import mipframe.frame.serialization as serialization

    def fail(value):
        if value == 3:
            raise RuntimeError("injected export failure")
        return encode(value)

    monkeypatch.setattr(serialization, "encode", fail)
    with pytest.raises(RuntimeError):
        f.write(tmp_path / "failed.jsonl")
    assert list(tmp_path.iterdir()) == []


def test_public_analysis_relations_and_roundtrip(tmp_path):
    p = tmp_path / "roles.lp"
    p.write_text(
        "Minimize\n o:0x+0y\nSubject To\n a:2x<=4\n b:3y<=9\nBinaries\n x y\nEnd\n"
    )
    with mf.read(p) as m:
        sig = m.structure.signatures(depth=1)
        family = sig.family(sig.row_families().one().id)
        params = family.params
        assert type(params) is mf.Frame
        assert all(t.kind not in ("object", "list") for t in params.schema.values())
        assert len(params.relations["histogram"]) > 0
        assert len(params.where(col("varying"))) > 0
        assert type(m.rows.signature()) is mf.Frame
        assert type(m.rows.families()) is mf.Frame
        assert len(m.rows.families().relations["members"]) == 2
        assert list(sig.rows.column("row").ids) == [0, 1]
        refs = m.terms().refs("var")
        spec = json.loads(json.dumps(refs.to_spec()))
        restored = mf.frame_from_spec(spec, models={m.uid: m})
        assert isinstance(restored, mf.VarFrame) and list(restored.ids) == [0, 1]
        exported = params.collect()
    assert exported[0]["basis"] == "canonical"
    with pytest.raises(mf.MIPFrameError):
        repr(params)
