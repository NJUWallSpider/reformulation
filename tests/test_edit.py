"""Independent exact oracles for edit snapshots, proof closure and I/O."""

from fractions import Fraction as Q
from itertools import product
import json
import sqlite3
import pytest
import mipframe as mf


@pytest.fixture
def m(tmp_path):
    path = tmp_path / "edit.lp"
    path.write_text("""Minimize
 obj: 2 x + 3 y + 5
Subject To
 a: x + y <= 4
 b: x + y <= 5
 c: x + y <= 6
 lower: x >= 1.1
Bounds
 0 <= x <= 5
 0 <= y <= 5
 0 <= z <= 1
Generals
 x
End
""")
    with mf.read(path) as model:
        yield model


def recs(m, records, schema):
    return mf.Frame.from_records(records, schema=schema, models=(m,))


def typ(m, axis):
    return mf.DType("ref", uid=m.uid, version=m.version, axis=axis)


def row(m, n):
    return m.rows.by_name(n)


def variables(m):
    return tuple(m.vars.by_name(n) for n in ("x", "y", "z"))


def test_noop_identity_lifecycle_and_native(m, tmp_path):
    uid, version = m.uid, m.version
    e = m.edit(memory_mb=0.015625)
    n = e.build()
    assert (
        n.uid != uid
        and n.version == version
        and n.parent == dict(uid=uid, version=version)
    )
    assert n.stage == "edited" and n.source["path"] is None
    assert n.diff(m).semantic["lp"] == "SAME"
    with pytest.raises(mf.EditConflictError):
        e.build()
    saved = n.save(tmp_path / "native")
    with mf.load(saved) as loaded:
        assert loaded.uid == n.uid and loaded.version == version
        assert loaded.diff(m).semantic["mip"] == "SAME"
    m.close()
    assert n.vars.iloc[0].name == "x"
    with pytest.raises(mf.MIPFrameError):
        e.summary()
    n.close()


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_draft_exact_terms_objective_and_mapping(m, memory):
    x, y, z = variables(m)
    e = m.edit(memory_mb=memory)
    Y = e.add_vars(
        recs(m, [dict(label="a", name="aggregate")], {"label": "str", "name": "str"})
    )
    v = Y.loc["a"].var
    ref = v.ref
    c = e.add_row((x + y - v).eq(0), name="def")
    e.set_objective(v * Q(1, 3) + Q(1, 7), sense="max")
    n = e.build()
    assert n.lineage.resolve(ref).name == "aggregate"
    assert n.lineage.resolve(c).name == "def"
    assert n.objective.sense == "max" and n.objective.constant == Q(1, 7)
    assert n.vars.by_name("aggregate").obj == Q(1, 3) and n.vars.by_name("x").obj == 0
    assert [(t.var.name, t.coef) for t in n.rows.by_name("def").terms] == [
        ("x", 1),
        ("y", 1),
        ("aggregate", -1),
    ]
    assert n.diff(m).semantic["coordinates"] == "EXTENDED"
    assert n.diff(m).semantic["mip"] == "NOT_COMPARABLE"
    n.close()


def test_bulk_duplicate_cancel_and_frame_alignment(m):
    x, y, z = variables(m)
    e = m.edit()
    rows = recs(
        m,
        [dict(label="cut", ub=3), dict(label="zero", ub=1)],
        {"label": "str", "ub": "number"},
    )
    terms = recs(
        m,
        [
            dict(label="cut", var=x, coef=2),
            dict(label="cut", var=x, coef=-1),
            dict(label="cut", var=y, coef=1),
            dict(label="cut", var=y, coef=-1),
        ],
        {"label": "str", "var": typ(m, "var"), "coef": "number"},
    )
    e.add_rows(rows=rows, terms=terms)
    bounds = recs(
        m,
        [dict(var=y, value=3), dict(var=x, value=4)],
        {"var": typ(m, "var"), "value": "number"},
    )
    e.set_var_bounds(m.vars.head(2), ub=bounds)
    n = e.build()
    assert n.nnz == m.nnz + 1
    assert n.vars.by_name("x").ub == 4 and n.vars.by_name("y").ub == 3
    assert n.diff(m).semantic["lp"] == "SUBSET"
    assert len(n.diff(m).var_bounds_changed) == 2
    n.close()


def test_transaction_conflicts_no_partial_mutations(m):
    x, y, z = variables(m)
    empty = m.edit()
    with pytest.raises(mf.InvalidCertificateError, match="at least one"):
        empty.add_row(x <= 0, because=[])
    assert empty._store.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
    e = m.edit()
    e.set_var_bounds(x, ub=4)
    with pytest.raises(mf.EditConflictError):
        e.set_var_bounds(m.vars.head(2), ub=3)
    assert e._store.execute("SELECT count(*) FROM changes").fetchone()[0] == 1
    with pytest.raises(mf.EditConflictError):
        e.drop_vars(x)
    with pytest.raises(mf.SchemaError):
        e.set_var_bounds(
            m.vars.head(2),
            ub=recs(
                m, [dict(var=x, value=4)], {"var": typ(m, "var"), "value": "number"}
            ),
        )
    for bad in (True, 0.1, None, float("nan")):
        with pytest.raises((ValueError, TypeError)):
            e.set_var_bounds(y, ub=bad)
    with pytest.raises(mf.SchemaError):
        e.add_row(x <= 2, lb=0)
    e.set_var_bounds(x, ub=4)
    n = e.build()
    assert n.vars.by_name("y").ub == 5
    n.close()


def test_grounded_chain_and_self_cycle_rejected(m):
    a, b, c = (row(m, n) for n in "abc")
    pb = m.check.linear(b.ub_claim, using=[a.ub_claim])
    pc = m.check.linear(c.ub_claim, using=[b.ub_claim])
    e = m.edit()
    e.drop_rows(m.rows.iloc[1:3], because=[pb, pc])
    n = e.build()
    assert n.diff(m).semantic["lp"] == "SAME"
    n.close()
    e = m.edit()
    e.drop_rows(a, because=m.check.linear(a.ub_claim, using=[a.ub_claim]))
    with pytest.raises(mf.EditValidationError) as exc:
        e.build()
    assert len(exc.value.report.violations) > 0
    # Two identical rows can certify each other on the source, but neither is a final root.
    e = m.edit()
    e.add_row(a.ub_claim, name="a2")
    n = e.build()
    a1, a2 = row(n, "a"), row(n, "a2")
    p1 = n.check.linear(a1.ub_claim, using=[a2.ub_claim])
    p2 = n.check.linear(a2.ub_claim, using=[a1.ub_claim])
    e = n.edit()
    e.drop_rows(n.rows.where(mf.col("name").isin(["a", "a2"])), because=[p1, p2])
    with pytest.raises(mf.EditValidationError):
        e.build()
    n.close()


def test_integer_cut_and_domain_loss(m):
    x, y, z = variables(m)
    base = m.check.linear(x >= Q(11, 10), using=[row(m, "lower").lb_claim])
    rounded = m.check.round(x >= 2, from_=base)
    e = m.edit()
    e.add_row(x >= 2, because=rounded)
    n = e.build()
    d = n.diff(m)
    assert (d.semantic["lp"], d.semantic["mip"]) == ("SUBSET", "SAME")
    n.close()
    e = m.edit()
    e.add_row(x >= 2, because=rounded)
    e.set_domains(x, kind="C")
    n = e.build()
    assert n.diff(m).semantic["mip"] == "SUPERSET"
    n.close()
    conditional = m.check.linear(x <= 0, assume=[x.eq(0)], residual="bounds")
    with pytest.raises(mf.UndischargedAssumptionError):
        m.edit().add_row(x <= 0, because=conditional)
    with pytest.raises(mf.InvalidCertificateError):
        m.edit().add_row(x <= 0, because=m.vars.check("unique", ("var",)))


def test_static_infeasibility_and_drop_gc(m, tmp_path):
    x, y, z = variables(m)
    e = m.edit()
    e.fix(z, 0)
    n = e.build()
    assert n.vars.by_name("z").lb == n.vars.by_name("z").ub == 0
    n.close()
    e = m.edit()
    e.set_var_bounds(x, lb=Q(1, 10), ub=Q(9, 10))
    with pytest.raises(mf.EditValidationError):
        e.build()
    n = e.build(allow_infeasible=True)
    assert n.vars.by_name("x").lb == Q(1, 10)
    assert n.lineage.validation["directly_infeasible"] == 1
    assert n.lineage.findings.one().category == "infeasible"
    n.save(tmp_path / "infeasible")
    with mf.load(tmp_path / "infeasible") as loaded:
        assert loaded.lineage.validation["allow_infeasible"] is True
        assert loaded.lineage.findings.one().reason
    n.close()
    e = m.edit()
    e.add_row(m.linexpr(constant=1) <= 0)
    with pytest.raises(mf.EditValidationError):
        e.build()
    n = e.build(allow_infeasible=True)
    n.close()
    e = m.edit()
    e.drop_vars(x)
    with pytest.raises(mf.NonIsolatedVariableError):
        e.build()
    e = m.edit()
    e.drop_vars(z)
    n = e.build()
    assert n.shape[1] == m.shape[1] - 1
    n.close()


def test_objective_relation_and_stats(m):
    x, y, z = variables(m)
    e = m.edit()
    e.set_objective(4 * x + 6 * y + 17, sense="min")
    n = e.build()
    d = n.diff(m)
    assert d.semantic["objective"] == dict(kind="POSITIVE_AFFINE", scale=2, offset=7)
    assert d.semantic["optimal_value"] == "TRANSFORMED"
    assert d.row_nnz_stats["before"] == dict(max=2, median=2, p90=2)
    assert d.col_nnz_stats["before"]["median"] == 3
    n.close()


def batch(m, targets):
    ts = recs(
        m,
        [dict(target=k, relation="<=", rhs=row(m, k).ub) for k in targets],
        {"target": "str", "relation": "str", "rhs": "number"},
    )
    terms = recs(
        m,
        (
            dict(target=k, var=t.var, coef=t.coef)
            for k in targets
            for t in row(m, k).terms
        ),
        {"target": "str", "var": typ(m, "var"), "coef": "number"},
    )
    us = recs(
        m,
        [dict(target=k, row=row(m, "a"), side="ub", multiplier=1) for k in targets],
        {"target": "str", "row": typ(m, "row"), "side": "str", "multiplier": "number"},
    )
    return ts, terms, us


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_batch_replay_and_native(m, tmp_path, memory):
    ts, terms, us = batch(m, ["b", "c"])
    B = m.check.linear_many(targets=ts, terms=terms, using=us, memory_mb=memory)
    assert B.ok and B.for_target("c").ok
    assert len(B.evidence["results"]) == 2
    spec = B.to_spec()
    restored = mf.Report.replay(spec, models=(m,), memory_mb=memory)
    assert (
        restored.ok
        and restored.for_target("b").conclusion.expr == row(m, "b").ub_claim.expr
    )
    e = m.edit(memory_mb=memory)
    e.drop_rows(m.rows.iloc[1:3], because=B)
    n = e.build()
    saved = n.save(tmp_path / "batch")
    with mf.load(saved) as loaded:
        assert loaded.diff(m, memory_mb=memory).semantic["lp"] == "SAME"
    n.close()


def test_lp_atomic_and_roundtrip(m, tmp_path):
    out = tmp_path / "audit.lp"
    m.write_lp(out)
    assert mf.audit.roundtrip(m, out).ok
    text = out.read_text().replace(" +2 x", " +9 x", 1)
    out.write_text(text)
    assert not mf.audit.roundtrip(m, out).ok
    e = m.edit()
    e.set_objective(variables(m)[0] * Q(1, 3), sense="min")
    n = e.build()
    out.write_text("keep me")
    with pytest.raises(mf.MIPFrameError):
        n.write_lp(out)
    assert out.read_text() == "keep me"
    n.close()


def test_independent_integer_grid_oracle(m):
    x, y, z = variables(m)
    e = m.edit()
    e.set_var_bounds(y, ub=3)
    e.drop_rows(row(m, "b"))
    n = e.build()

    def feasible(model, point):
        return model.check.solution({v: point[v.name] for v in model.vars}).ok

    old = set()
    new = set()
    for xv, yv, zv in product(range(6), range(6), range(2)):
        point = dict(x=xv, y=yv, z=zv)
        key = (xv, yv, zv)
        if feasible(m, point):
            old.add(key)
        if feasible(n, point):
            new.add(key)
    # The exact certificate-free summary is conservative even when oracle sees redundancy.
    assert new <= old and n.diff(m).semantic["mip"] == "UNKNOWN"
    n.close()


def test_draft_frame_queries_and_foreign_scopes(m):
    e = m.edit()
    Y = e.add_vars(recs(m, [dict(label="u"), dict(label="v")], {"label": "str"}))
    v = Y.loc["u"].var
    assert len(Y.where(mf.col("var") == v)) == 1
    assert isinstance(Y.collect()[0]["var"], mf.DraftRef)
    assert isinstance(next(Y.iter_batches(1)).to_records()[0]["var"], mf.DraftRef)
    x = variables(m)[0]
    e.add_row(x <= v)
    e.add_row(x.eq(v))
    for call in (
        lambda: m.check.linear(v <= 0),
        lambda: m.terms(vars=Y),
        lambda: m.linexpr(v),
        lambda: m.edit().add_row(v <= 0),
    ):
        with pytest.raises((ValueError, TypeError)):
            call()
    with pytest.raises(mf.NonlinearExpressionError):
        x * v
    e.close()
    with pytest.raises(mf.MIPFrameError):
        len(Y)


def test_bound_dependency_loss_is_rejected(m):
    x, y, z = variables(m)
    # Remove a row whose only certificate uses a simultaneously relaxed bound.
    e = m.edit()
    e.add_row(y <= 5, name="boundrow")
    n = e.build()
    y = n.vars.by_name("y")
    r = row(n, "boundrow")
    proof = n.check.linear(r.ub_claim, using=[y.ub_claim])
    e = n.edit()
    e.drop_rows(r, because=proof)
    e.set_var_bounds(y, ub=6)
    with pytest.raises(mf.EditValidationError):
        e.build()
    n.close()


def test_positive_scaling_and_range_coverage(m):
    a = row(m, "a")
    p = m.check.linear(2 * a.expr <= 8, using=[(2, a.ub_claim)])
    e = m.edit()
    e.add_row(a.expr <= 5, because=p)
    n = e.build()
    assert n.diff(m).semantic["lp"] == "SAME"
    n.close()
    e = m.edit()
    e.add_row(a.expr, lb=1, ub=5, because=p)
    with pytest.raises(mf.EditValidationError):
        e.build()


def test_replace_rows_and_name_policy(m):
    a = row(m, "a")
    specs = recs(
        m,
        [dict(label="replacement", name="a", ub=3)],
        {"label": "str", "name": "str", "ub": "number"},
    )
    terms = recs(
        m,
        (dict(label="replacement", var=t.var, coef=t.coef) for t in a.terms),
        {"label": "str", "var": typ(m, "var"), "coef": "number"},
    )
    links = recs(
        m,
        [dict(old_row=a, new_label="replacement")],
        {"old_row": typ(m, "row"), "new_label": "str"},
    )
    e = m.edit()
    e.replace_rows(a, rows=specs, terms=terms, correspondence=links)
    n = e.build()
    d = n.diff(m)
    assert len(d.rows_changed) == 1 and d.rows_changed.one().row.name == "a"
    assert n.rows.by_name("a") != a and n.rows.by_name("a").ub == 3
    n.close()
    e = m.edit()
    e.add_row(a.ub_claim, name="a")
    with pytest.raises(mf.EditValidationError):
        e.build()


def test_failed_ir_and_disk_cleanup(m, monkeypatch, tmp_path):
    from mipframe.model import Model

    x = variables(m)[0]
    e = m.edit()
    e.add_row(x <= 4)
    before = m.version
    real = Model._build_ir

    def failed(*a, **k):
        raise RuntimeError("interrupted IR")

    monkeypatch.setattr(Model, "_build_ir", failed)
    with pytest.raises(RuntimeError, match="interrupted IR"):
        e.build()
    assert m.version == before and not e._sealed
    monkeypatch.setattr(Model, "_build_ir", real)
    n = e.build()
    n.close()
    with pytest.raises(mf.DiskBudgetExceeded):
        m.edit(disk_mb=0.001)


def test_batch_status_direction_and_many_targets(m):
    x = variables(m)[0]
    targets = recs(
        m,
        [dict(target=str(i), relation="<=", rhs=i) for i in range(80)],
        {"target": "str", "relation": "str", "rhs": "number"},
    )
    terms = recs(
        m,
        [dict(target=str(i), var=x, coef=1) for i in range(80)],
        {"target": "str", "var": typ(m, "var"), "coef": "number"},
    )
    b = m.check.linear_many(
        targets=targets, terms=terms, residual="bounds", memory_mb=0.015625
    )
    assert (
        b.status == "INCONCLUSIVE"
        and b.for_target("0").status == "INCONCLUSIVE"
        and b.for_target("5").ok
    )
    # Storage table count is independent of target count.
    count = b._store.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert count < 30
    with pytest.raises(mf.InvalidCertificateError):
        m.edit().add_row(x <= 5, because=b)
    e = m.edit()
    e.add_row(x <= 5, because=b.for_target("5"))
    n = e.build()
    n.close()


def test_damaged_lineage_and_saved_verdict_not_trusted(m, tmp_path):
    from mipframe.store.artifacts import sha256

    e = m.edit()
    e.add_row(variables(m)[0] <= 3)
    n = e.build()
    path = n.save(tmp_path / "tamper")
    n.close()
    db = sqlite3.connect(path / "lineage.sqlite")
    fake = dict(
        coordinates="SAME",
        lp="SAME",
        mip="SAME",
        objective={"kind": "SAME"},
        optimal_value="PRESERVED",
    )
    db.execute("UPDATE meta SET value=? WHERE key='semantic'", (json.dumps(fake),))
    db.commit()
    db.close()
    with pytest.raises(mf.MIPFrameError):
        mf.load(path)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["edit_artifact"]["sha256"] = sha256(path / "lineage.sqlite")
    (path / "manifest.json").write_text(json.dumps(manifest))
    with mf.load(path) as loaded:
        assert loaded.diff(m).semantic["mip"] == "SUBSET"


def test_export_failure_atomic_and_full_audit(m, tmp_path, monkeypatch):
    path = tmp_path / "out.lp"
    path.write_text("previous")
    import mipframe.store.exporter as exp

    real = exp._export

    def fail(model, path, **kw):
        with open(path, "w") as f:
            f.write("half")
        raise RuntimeError("writer failed")

    monkeypatch.setattr(exp, "_export", fail)
    with pytest.raises(RuntimeError):
        m.write_lp(path)
    assert path.read_text() == "previous"
    monkeypatch.setattr(exp, "_export", real)
    names = {v.id: "renamed_" + v.name for v in m.vars}
    m.write_lp(path, rename_vars=names)
    assert mf.audit.roundtrip(m, path, rename_vars=names).ok
    assert not mf.audit.roundtrip(m, path).ok


def test_field_specific_evidence_and_summary_invalidated(m):
    x, y, z = variables(m)
    proof = m.check.linear(x >= Q(11, 10), using=[row(m, "lower").lb_claim])
    e = m.edit()
    e.set_var_bounds(x, lb=Q(11, 10), because=proof)
    assert "SAME" in str(e.summary())
    e.set_var_bounds(x, ub=4)
    n = e.build()
    assert n.diff(m).semantic["mip"] == "SUBSET"
    n.close()


def test_high_degree_compile_copies_encodings(tmp_path, monkeypatch):
    import mipframe.store.model_store as storage

    path = tmp_path / "wide.lp"
    count = 1100
    path.write_text(
        "Minimize\n obj: 0 x0\nSubject To\n wide: "
        + " + ".join(f"7.125 x{i}" for i in range(count))
        + " <= 10000\nBounds\n"
        + "\n".join(f"0 <= x{i} <= 1" for i in range(count))
        + "\nEnd\n"
    )
    with mf.read(path) as m:
        cid = int(m.model.coeff_id[0])
        real = m.model.num

        def read(ident):
            assert (
                ident != cid
            ), "Unchanged source coefficient decoded during compilation"
            return real(ident)

        monkeypatch.setattr(m.model, "num", read)
        monkeypatch.setattr(
            storage.Writer,
            "term",
            lambda *a: pytest.fail("Compiler staged original coefficients as terms"),
        )
        e = m.edit(memory_mb=0.015625)
        assert "1,100" in str(e.summary())
        n = e.build()
        assert n.version == m.version and n.nnz == count
        n.close()


def test_empty_batch_and_directional_equality(m):
    x = variables(m)[0]
    targets = recs(m, [], {"target": "str", "relation": "str", "rhs": "number"})
    terms = recs(m, [], {"target": "str", "var": typ(m, "var"), "coef": "number"})
    b = m.check.linear_many(targets=targets, terms=terms)
    assert b.ok and len(b.evidence["results"]) == 0
    with pytest.raises(ValueError):
        m.check.linear_many(targets=targets, terms=terms, residual="bad")
    with pytest.raises(mf.SchemaError, match="certificate Frames"):
        m.check.linear_many(targets=targets, terms=terms, using=[object()])
    with pytest.raises(mf.SchemaError, match="string keys"):
        m.check.linear_many(targets=targets, terms=terms, reports={1: b})
    duplicate = recs(
        m,
        [dict(target="a", relation="<=", rhs=0)] * 2,
        {"target": "str", "relation": "str", "rhs": "number"},
    )
    with pytest.raises(mf.SchemaError, match="Duplicate certificate target"):
        m.check.linear_many(targets=duplicate, terms=terms)
    e = m.edit()
    e.fix(x, 2)
    n = e.build()
    x = n.vars.by_name("x")
    targets = recs(
        n,
        [dict(target="fixed", relation="==", rhs=2)],
        {"target": "str", "relation": "str", "rhs": "number"},
    )
    terms = recs(
        n,
        [dict(target="fixed", var=x, coef=1)],
        {"target": "str", "var": typ(n, "var"), "coef": "number"},
    )
    using = recs(
        n,
        [
            dict(target="fixed", var=x, side="ub", multiplier=1, direction="le"),
            dict(target="fixed", var=x, side="lb", multiplier=1, direction="ge"),
        ],
        {
            "target": "str",
            "var": typ(n, "var"),
            "side": "str",
            "multiplier": "number",
            "direction": "str",
        },
    )
    b = n.check.linear_many(targets=targets, terms=terms, using=using)
    assert b.ok and mf.Report.replay(b.to_spec(), models=(n,)).ok
    n.close()


def test_parent_report_batch_and_partial_stream(m):
    ts, terms, unused = batch(m, ["b", "c"])
    p = m.check.linear(row(m, "a").ub_claim, using=[row(m, "a").ub_claim])
    using = recs(
        m,
        [dict(target=k, proof="parent", multiplier=1) for k in ["b", "c"]],
        {"target": "str", "proof": "str", "multiplier": "number"},
    )
    b = m.check.linear_many(targets=ts, terms=terms, using=using, reports={"parent": p})
    assert mf.Report.replay(b.to_spec(), models=(m,)).for_target("c").ok
    spec = b.to_spec()
    spec["events"].pop()
    with pytest.raises(mf.SchemaError):
        mf.Report.replay(spec, models=(m,))


def test_unrelated_snapshot_and_persisted_map_identity(m, tmp_path):
    with mf.read(m.source["path"]) as other:
        with pytest.raises(mf.SchemaError):
            m.edit().drop_rows(other.rows)
    e = m.edit()
    n = e.build()
    identities = n.lineage.mapping("var").collect()
    assert (
        identities[0]["source_uid"] == m.uid
        and identities[0]["source_version"] == m.version
    )
    assert n.lineage.mapping("var", source=m).head(1).one().source_var == m.vars.iloc[0]
    it = iter(n.lineage.mapping("var"))
    next(it)
    n.close()
    with pytest.raises(mf.MIPFrameError):
        next(it)
