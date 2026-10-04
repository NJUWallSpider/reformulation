"""Independent exact algebra, certificate scope and streaming replay regressions."""

from fractions import Fraction as Q
from decimal import Decimal
from itertools import product
import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    p = tmp_path / "proof.lp"
    p.write_text("""Minimize
 obj: 2 x + 3 y + z + 5
Subject To
 cap: x + y <= 4
 eq: x - y = 0
 lower: x >= 1.1
 gate: x - 4 z >= -2
Bounds
 0 <= x <= 4
 0 <= y <= 4
 0 <= z <= 1
Generals
 x y
Binaries
 z
End
""")
    with mf.read(p) as m:
        yield m


def xyz(m):
    return tuple(m.vars.by_name(n) for n in ("x", "y", "z"))


def row(m, name):
    return m.rows.by_name(name)


def status(rep, wanted="PROVED"):
    assert rep.status == wanted
    assert rep.ok == (wanted == "PROVED")
    if wanted == "PROVED":
        assert rep.require() is rep
    else:
        with pytest.raises(mf.ValidationError) as e:
            rep.require()
        assert e.value.report is rep
    with pytest.raises(TypeError):
        bool(rep)


def test_exact_literals_and_algebra(model):
    x, y, z = xyz(model)
    assert mf.q("0.1") == Q(1, 10)
    assert mf.q("1e-90") == Q(1, 10**90)
    assert mf.q("13/17") == Q(13, 17)
    for bad in (True, 0.1, float("nan"), float("inf"), Decimal("NaN"), "NaN"):
        with pytest.raises(mf.ExactArithmeticError):
            mf.q(bad)
    e = 2 * x + 3 * x - y + 7
    assert [(r.var.id, r.coef) for r in e.terms] == [(x.id, 5), (y.id, -1)]
    assert e.constant == 7
    assert (e - e).terms.count() == 0
    assert e.evaluate({x: 2, y: 3}) == 14
    assert e.evaluate({x: 2}, missing="zero") == 17
    with pytest.raises(mf.SchemaError):
        e.evaluate({x: 2})
    assert e.substitute({x: y + 1, y: x}).evaluate({x: 2, y: 3}) == 25
    assert (x + 2 * y).substitute({x: y, y: x}) == y + 2 * x
    assert x == model.vars.loc[x.id] and hash(x) == hash(model.vars.loc[x.id])
    assert x != y
    for f in (lambda: x * y, lambda: e * x, lambda: e * e):
        with pytest.raises(mf.NonlinearExpressionError):
            f()
    for item in (e <= 10, e.eq(0)):
        with pytest.raises(TypeError):
            bool(item)
    with pytest.raises(AttributeError):
        e.constant = 9
    assert "..." in e.formula(max_terms=1)


def test_frame_construction_and_model_sources(model):
    x, y, z = xyz(model)
    frame = mf.Frame.from_records(
        [{"var": x, "a": Q(1, 3)}, {"var": x, "a": Q(2, 3)}, {"var": y, "a": 0}],
        schema={
            "var": mf.DType("ref", uid=model.uid, version=model.version, axis="var"),
            "a": "number",
        },
        models=(model,),
    )
    assert model.linexpr(frame, coef="a", constant=5) == x + 5
    assert model.linexpr(vars=model.vars, coef=Q(1, 2)) == (x + y + z) * Q(1, 2)
    cap = row(model, "cap")
    assert cap.expr == x + y
    assert cap.ub_claim.expr == x + y - 4
    assert row(model, "eq").eq_claim.relation == "=="
    with pytest.raises(mf.MissingBoundError):
        cap.eq_claim
    with pytest.raises(mf.MissingBoundError):
        cap.lb_claim
    status(model.check.identity(cap.expr, x + y))
    status(model.check.identity(x, y), "REFUTED")
    status(model.check.identity(x, y, using=[row(model, "eq").eq_claim]))
    status(
        model.check.identity(x, 2 * y, using=[row(model, "eq").eq_claim]),
        "INCONCLUSIVE",
    )


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_linear_orientations_residuals_and_certificates(model, memory):
    x, y, z = xyz(model)
    opts = {"memory_mb": memory}
    status(model.check.linear(x + y <= 5, using=[row(model, "cap").ub_claim], **opts))
    status(
        model.check.linear(x + y <= 3, using=[row(model, "cap").ub_claim], **opts),
        "INCONCLUSIVE",
    )
    status(
        model.check.linear(x >= Q(11, 10), using=[row(model, "lower").lb_claim], **opts)
    )
    status(
        model.check.linear(
            x + y <= 4,
            using=[(2, row(model, "cap").ub_claim)],
            residual="bounds",
            **opts,
        ),
        "INCONCLUSIVE",
    )
    # -x <= 0 from the original lower bound via an exact residual enclosure.
    r = model.check.linear(-x <= 0, residual="bounds", **opts)
    status(r)
    assert r.evidence["bounds"].one().side == "lb"
    status(model.check.linear((x - y).eq(0), using=[row(model, "eq").eq_claim], **opts))
    status(
        model.check.linear(
            (x - y).eq(0),
            using={
                "le": [row(model, "eq").eq_claim],
                "ge": [(-1, row(model, "eq").eq_claim)],
            },
            **opts,
        )
    )
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear(x <= 4, using=[(-1, row(model, "cap").ub_claim)])
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear(x <= 4, using=[x <= 4])
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear((x + y).eq(4), using=[row(model, "cap").ub_claim])
    duals = mf.Frame.from_records(
        [dict(row=row(model, "cap"), side="ub", multiplier=Q(1))],
        schema={
            "row": mf.DType("ref", uid=model.uid, version=model.version, axis="row"),
            "side": "str",
            "multiplier": "number",
        },
        models=(model,),
    )
    status(model.check.linear(x + y <= 4, using=duals, **opts))


def test_assumptions_domains_rounding_and_contradiction(model):
    x, y, z = xyz(model)
    conditional = model.check.linear(
        x >= 2, using=[row(model, "gate").lb_claim], assume=[z.eq(1)]
    )
    status(conditional)
    assert conditional.scope["assumptions"] == 1
    propagated = model.check.linear(x >= 1, using=[conditional])
    status(propagated)
    assert propagated.scope["assumptions"] == 1 and "assume:" in repr(propagated)
    lower = model.check.linear(x >= Q(11, 10), using=[row(model, "lower").lb_claim])
    rounded = model.check.round(x >= 2, from_=lower)
    status(rounded)
    assert rounded.scope["domain"] == "integer"
    assert model.check.linear(x >= 1, using=[rounded]).scope["domain"] == "integer"
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear(x >= 1, using=[rounded], domain="relaxation")
    status(model.check.round(x >= 3, from_=lower), "INCONCLUSIVE")
    bad = model.check.infeasible(
        using=[row(model, "lower").lb_claim, x <= 0], assume=[x <= 0], residual="bounds"
    )
    status(bad)
    assert bad.scope["assumptions"] == 1
    status(model.check.infeasible(using=[row(model, "lower").lb_claim]), "INCONCLUSIVE")
    status(model.check.infeasible(assume=[z.eq(0), z.eq(1)]))
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear(x >= 1, using=[model.check.linear(x >= 10)])
    # An arbitrary valid data assertion cannot be used as an inequality.
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear(x >= 1, using=[model.vars.check("unique", ("var",))])
    with pytest.raises(TypeError):
        model.check.linear(x >= 0, arithmetic="fast")


def test_enumeration_against_independent_oracle_and_local_scope(model):
    x, y, z = xyz(model)
    # Exact independent reference over the declared LOCAL premise, not all model rows.
    premise = row(model, "cap").ub_claim
    points = [(a, b) for a, b in product(range(5), repeat=2) if a + b <= 4]
    target = 2 * x + 3 * y <= 12
    for memory in (128, 0.015625):
        r = model.check.enumerate(
            target, vars=[y, x, x], using=[premise], memory_mb=memory
        )
        status(r)
        assert r.summary["feasible_states_observed"] == len(points)
        assert r.summary["maximum"] == max(2 * a + 3 * b - 12 for a, b in points)
        assert r.summary["minimum"] == min(2 * a + 3 * b - 12 for a, b in points)
        assert r.scope["domain"] == "integer" and r.summary["exhaustive"]
    bad = model.check.enumerate(x >= 1, vars=[x])
    status(bad, "REFUTED")
    assert bad.counterexample.one().value == 0
    # x=0 violates the original lower row, so it is only a LOCAL counterexample.
    assert not bad.summary["counterexample_is_model_solution"]
    assert "local premises" in repr(bad)
    limited = model.check.enumerate(target, vars=[x, y], using=[premise], max_states=0)
    status(limited, "INCONCLUSIVE")
    assert limited.summary["states"] == 0
    with pytest.raises(mf.UnsupportedEnumerationDomain):
        model.check.enumerate(target, vars=[x])
    status(model.check.enumerate(x >= 100, vars=[x], assume=[x <= -1]))
    # Local proof is safely reusable, with integer scope propagated.
    proved = model.check.enumerate(x <= 4, vars=[x])
    status(model.check.linear(x <= 5, using=[proved]))
    assert model.check.linear(x <= 5, using=[proved]).scope["domain"] == "integer"


def test_solution_and_all_violation_records(model):
    x, y, z = xyz(model)
    r = model.check.solution({x: 2, y: 2, z: 0})
    status(r)
    assert r.summary["objective"] == 15
    assert not r.summary["optimality_proved"]
    r = model.check.solution({x: Q(11, 2), y: -1, z: Q(1, 2)})
    status(r, "REFUTED")
    assert len(r.evidence["violating_vars"]) >= 4
    assert len(r.evidence["violating_rows"]) >= 2
    assert r.summary["max_integrality_violation"] == Q(1, 2)
    with pytest.raises(mf.SchemaError):
        model.check.solution({x: 2, y: 2})
    status(model.check.solution({x: 2, y: 2}, missing="zero"))
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear(x <= 4, using=[model.check.solution({x: 2, y: 2, z: 0})])


def test_replay_all_checkers_tampering_and_atomic_file(model, tmp_path):
    x, y, z = xyz(model)
    base = model.check.linear(x >= Q(11, 10), using=[row(model, "lower").lb_claim])
    reports = [
        base,
        model.check.round(x >= 2, from_=base),
        model.check.identity(x, y, using=[row(model, "eq").eq_claim]),
        model.check.infeasible(assume=[z.eq(0), z.eq(1)]),
        model.check.enumerate(
            x + y <= 4, vars=[x, y], using=[row(model, "cap").ub_claim]
        ),
        model.check.solution({x: 2, y: 2, z: 0}),
        model.check.linear(
            x >= 2, using=[row(model, "gate").lb_claim], assume=[z.eq(1)]
        ),
        model.vars.check("unique", ("var",)),
    ]
    for i, r in enumerate(reports):
        spec = r.to_spec()
        assert mf.Report.replay(spec, models=[model]).status == r.status
        p = tmp_path / f"certificate-{i}.jsonl"
        r.write(p)
        assert (
            mf.Report.replay(p, models=[model], memory_mb=0.015625).status == r.status
        )
    bad = model.check.linear(x >= 100).to_spec()
    for e in bad["events"]:
        if e["type"] == "node":
            e["status"] = "PROVED"
    assert mf.Report.replay(bad, models=[model]).status == "INCONCLUSIVE"
    cycle = reports[1].to_spec()
    node = next(e for e in cycle["events"] if e["type"] == "node")
    node["parents"] = [node["node"]]
    with pytest.raises(mf.InvalidCertificateError):
        mf.Report.replay(cycle, models=[model])
    forged = base.to_spec()
    source = next(
        e for e in forged["events"] if e["type"] == "claim" and e["axis"] == "row"
    )
    source["constant"] = "-999"
    with pytest.raises(mf.InvalidCertificateError):
        mf.Report.replay(forged, models=[model])
    wrong = base.to_spec()
    next(e for e in wrong["events"] if e["type"] == "node")["snapshot"][
        "version"
    ] = "wrong"
    with pytest.raises(mf.SchemaError):
        mf.Report.replay(wrong, models=[model])


def test_identity_and_lifecycle_across_native_reload(model, tmp_path):
    x, y, z = xyz(model)
    path = tmp_path / "saved"
    model.save(path)
    with mf.load(path) as other:
        xx = other.vars.loc[x.id]
        assert x == xx and hash(x) == hash(xx)
        e = x + xx
        r = model.check.identity(e, 2 * x)
        independent = e.terms.to_records()
        spec = r.to_spec()
        assert mf.Report.replay(spec, models=[other]).ok
        other.close()
        with pytest.raises(mf.MIPFrameError):
            e.constant
        with pytest.raises(mf.MIPFrameError):
            r.status
        assert independent[0]["coef"] == 2
    with mf.read("examples/data/tiny.lp") as unrelated:
        with pytest.raises(mf.SchemaError):
            x + unrelated.vars.iloc[0]


def test_source_expr_creation_does_not_decode_coefficients(model, monkeypatch):
    r = row(model, "cap")

    def fail(*args):
        raise AssertionError("unexpected numeric decode")

    monkeypatch.setattr(model.model, "num", fail)
    expr = r.expr
    assert isinstance(expr, mf.LinExpr)
    assert expr.constant == 0


def test_complete_frame_certificate_errors(model):
    x, y, z = xyz(model)
    duals = mf.Frame.from_records(
        [dict(row=row(model, "cap"), side="ub", multiplier=-i) for i in range(1, 23)],
        schema={
            "row": mf.DType("ref", uid=model.uid, version=model.version, axis="row"),
            "side": "str",
            "multiplier": "number",
        },
        models=(model,),
    )
    with pytest.raises(mf.InvalidCertificateError) as caught:
        model.check.linear(x + y <= 4, using=duals, memory_mb=0.015625)
    assert len(caught.value.violations) == 22
    assert caught.value.violations.head(1).one().record == 0


def test_noninteger_unbounded_and_exact_perturbations(tmp_path):
    p = tmp_path / "edge.lp"
    p.write_text("""Maximize
 obj: c + 7
Subject To
 u: c <= 1.000000000000000000000000000000000000000000000000001
 r: x >= -1.5
Bounds
 c free
 -2 <= x <= 1
Generals
 x
End
""")
    with mf.read(p) as m:
        c, x = m.vars.by_name("c"), m.vars.by_name("x")
        limit = Q("1.000000000000000000000000000000000000000000000000001")
        status(
            m.check.linear(c <= 1, using=[m.rows.by_name("u").ub_claim]), "INCONCLUSIVE"
        )
        status(m.check.linear(c <= limit, using=[m.rows.by_name("u").ub_claim]))
        status(m.check.linear(c <= 1, residual="bounds"), "INCONCLUSIVE")
        lb = m.check.linear(x >= Q(-3, 2), using=[m.rows.by_name("r").lb_claim])
        status(m.check.round(x >= -1, from_=lb))
        ub = m.check.linear(c <= limit, using=[m.rows.by_name("u").ub_claim])
        with pytest.raises(mf.InvalidCertificateError):
            m.check.round(c <= 1, from_=ub)
        with pytest.raises(mf.UnsupportedEnumerationDomain):
            m.check.enumerate(c <= 1, vars=[c])
        r = m.check.solution({c: Q(1), x: -1})
        status(r)
        assert r.summary["objective"] == 8 and r.summary["objective_sense"] == "max"
        status(
            m.check.infeasible(
                using=[x >= 2, m.vars.by_name("x").ub_claim],
                assume=[x >= 2],
                residual="bounds",
            )
        )


def test_closing_suspended_expression_and_report_iterators(model):
    x, y, z = xyz(model)
    e = x + y + z
    r = model.check.linear(x + y <= 4, using=[row(model, "cap").ub_claim])
    it = e._items()
    next(it)
    rit = iter(r.evidence["terms"])
    next(rit)
    detached = r.evidence["terms"].to_records()
    model.close()
    for f in (
        lambda: next(it),
        lambda: next(rit),
        lambda: r.status,
        lambda: r.explain(),
        lambda: hash(x),
    ):
        with pytest.raises(mf.MIPFrameError):
            f()
    assert detached


def test_budget_cleanup_and_atomic_publication(model, tmp_path, monkeypatch):
    from mipframe.check.certificate import ProofContext

    x, y, z = xyz(model)
    r = model.check.linear(x <= 4, residual="bounds")
    p = tmp_path / "certificate.jsonl"
    p.write_text("original")
    import mipframe.check.serialization as serial

    original = serial.events

    def interrupted(rep):
        for i, e in enumerate(original(rep)):
            if i == 2:
                raise KeyboardInterrupt()
            yield e

    monkeypatch.setattr(serial, "events", interrupted)
    with pytest.raises(KeyboardInterrupt):
        r.write(p)
    assert p.read_text() == "original"
    assert not list(tmp_path.glob(".certificate.jsonl.*"))
    target = x <= 4
    before = set(id(s) for s in model._query_stores)
    with pytest.raises(mf.DiskBudgetExceeded):
        model.check.linear(target, residual="bounds", disk_mb=0.0001)
    assert all(s.db is None for s in model._query_stores if id(s) not in before)
    assert r.ok


def test_shared_enumerator_empty_domain_and_constant_claims(model):
    e = model.linexpr(constant=0)
    status(model.check.enumerate(e.eq(0), vars=[]))
    status(model.check.enumerate(e >= 1, vars=[]), "REFUTED")
    status(model.check.infeasible(using=[e.eq(1)], assume=[e.eq(1)]))
    status(model.check.identity(e, 1), "REFUTED")
    with pytest.raises(mf.ExactArithmeticError):
        model.check.solution({xyz(model)[0]: 0.1}, missing="zero")


def test_report_replay_relational_diagnostics_and_indexed(model):
    I = model.indexed()
    r = I.verify()
    assert isinstance(r, mf.Report)
    assert mf.Report.replay(r.to_spec(), models=[model]).ok
    E = mf.Frame.from_records(
        [dict(u=0, v=1), dict(u=1, v=0)], schema={"u": "int", "v": "int"}
    )
    with pytest.raises(mf.GraphCycleError) as caught:
        mf.ops.graph.topological_order(E)
    report = caught.value.report
    assert (
        mf.Report.replay(
            report.to_spec(), models=[], relations={E.to_spec()["plan"]["relation"]: E}
        ).status
        == "REFUTED"
    )
    f = mf.Frame.from_records([dict(i=1), dict(i=1)], schema={"i": "int"})
    join = f.join(f, on="i", cardinality="1:1")
    with pytest.raises(mf.CardinalityError) as caught:
        len(join)
    assert (
        mf.Report.replay(
            caught.value.report.to_spec(),
            models=[],
            relations={f.to_spec()["plan"]["relation"]: f},
        ).status
        == "REFUTED"
    )


def test_var_only_expression_scan_avoids_decoding(model, monkeypatch):
    e = row(model, "cap").expr

    def fail(*a):
        raise AssertionError("coefficient decoded for Ref-only scan")

    monkeypatch.setattr(model.model, "num", fail)
    assert len(e.terms.select("var").to_records()) == 2
    assert len((0 * e).terms) == 0


def test_random_residual_certificates_against_box_oracle(model):
    import random

    rng = random.Random(731)
    x, y, z = xyz(model)
    for _ in range(18):
        a = Q(rng.randrange(5), 3)
        b = Q(rng.randrange(-4, 5), 7)
        c = [Q(rng.randrange(-8, 9), 11) for _ in range(3)]
        residual = [c[0] - a - b, c[1] - a + b, c[2]]
        exact_bound = 4 * a + max(
            sum(k * v for k, v in zip(residual, point))
            for point in product((0, 4), (0, 4), (0, 1))
        )
        lhs = c[0] * x + c[1] * y + c[2] * z
        sources = [(a, row(model, "cap").ub_claim), (b, row(model, "eq").eq_claim)]
        status(
            model.check.linear(
                lhs <= exact_bound, using=sources, residual="bounds", memory_mb=0.015625
            )
        )
        status(
            model.check.linear(
                lhs <= exact_bound - Q(1, 10**60),
                using=sources,
                residual="bounds",
                memory_mb=0.015625,
            ),
            "INCONCLUSIVE",
        )


def test_paged_long_expression_and_certificate(model, monkeypatch):
    x, y, z = xyz(model)
    from mipframe.frame.var import Var
    from mipframe.frame.row import Row

    def fail(*a, **kw):
        raise AssertionError("full entity record generated")

    monkeypatch.setattr(Var, "to_record", fail)
    monkeypatch.setattr(Row, "to_record", fail)
    rows = (
        dict(row=row(model, "cap"), side="ub", multiplier=Q(1, 300)) for _ in range(300)
    )
    duals = mf.Frame.from_records(
        rows,
        schema={
            "row": mf.DType("ref", uid=model.uid, version=model.version, axis="row"),
            "side": "str",
            "multiplier": "number",
        },
        models=(model,),
    )
    r = model.check.linear(x + y <= 4, using=duals, memory_mb=0.015625)
    status(r)
    assert r.evidence["sources"].count() == 300
    status(model.check.solution({x: 2, y: 2, z: 0}, memory_mb=0.015625))


def test_expression_spec_and_frozen_sources(model):
    x, y, z = xyz(model)
    e = Q(1, 3) * x - Q(2, 7) * y + Q(5, 11)
    assert mf.LinExpr.from_spec(e.to_spec(), models=[model]) == e
    c = row(model, "cap").ub_claim
    spec = c.to_spec()
    spec["source"]["side"] = "lb"
    assert c._source["side"] == "ub"
    with pytest.raises(mf.MissingBoundError):
        mf.Claim.from_spec(spec, models=[model])
    restored = mf.Claim.from_spec(c.to_spec(), models=[model])
    status(model.check.linear(x + y <= 4, using=[restored]))


def test_live_dependency_cycles_and_deadlines(model, monkeypatch):
    x, y, z = xyz(model)
    base = model.check.linear(x <= 4, residual="bounds")
    base._parents = (base,)
    with pytest.raises(mf.InvalidCertificateError):
        model.check.linear(x <= 4, using=[base])
    base._parents = ()
    from mipframe.store.search_store import SearchStore

    tick = SearchStore.tick

    def expire(ctx):
        tick(ctx)
        if ctx.operation == "enumerate" and ctx.states >= 2:
            ctx.stop("time_limit")

    monkeypatch.setattr(SearchStore, "tick", expire)
    r = model.check.enumerate(x <= 4, vars=[x], time_limit=0.5)
    status(r, "INCONCLUSIVE")
    assert r.summary["stop_reason"] == "time_limit"
    assert r.summary["extrema"] == "observed only"
    immediate = model.check.enumerate(x <= 4, vars=[x], time_limit=1e-30)
    status(immediate, "INCONCLUSIVE")
    assert immediate.summary["states"] == 0


def test_empty_and_fixed_integer_ranges(tmp_path):
    for bounds, expected in [("0.2 <= x <= 0.8", 0), ("1 <= x <= 1", 1)]:
        path = tmp_path / "range.lp"
        path.write_text(
            f"Minimize\n obj: x\nSubject To\n r: x >= 0\nBounds\n {bounds}\nGenerals\n x\nEnd\n"
        )
        with mf.read(path) as m:
            x = m.vars.by_name("x")
            r = m.check.enumerate(x <= 1, vars=[x])
            status(r)
            assert r.summary["feasible_states_observed"] == expected
            assert r.summary["vacuous"] == (expected == 0)


def test_bounds_residual_reads_only_required_side(model, monkeypatch):
    from mipframe.frame.var import Var

    x, y, z = xyz(model)
    target = x <= 4

    def fail(self):
        raise AssertionError("unused lower bound read")

    monkeypatch.setattr(Var, "lb", property(fail))
    r = model.check.linear(target, residual="bounds")
    status(r)
    assert r.dependencies.one().side == "ub"


def test_high_degree_sparse_source(tmp_path, monkeypatch):
    n = 1200
    p = tmp_path / "wide.lp"
    formula = " + ".join(f"{(i%7)+1} x{i}" for i in range(n))
    p.write_text(
        f"Minimize\n obj: x0\nSubject To\n a: {formula} <= 100000\n b: {formula} <= 200000\nEnd\n"
    )
    with mf.read(p) as m:
        r = m.rows.iloc[0]
        original = m.model.coeff_id
        reads = 0

        class Positions:
            def __getitem__(self, key):
                nonlocal reads
                assert type(key) is int and 0 <= key < n
                reads += 1
                return original[key]

        monkeypatch.setattr(m.model, "coeff_id", Positions())

        def forbidden(*args):
            raise AssertionError("full row dictionary materialized")

        monkeypatch.setattr(m.model, "row", forbidden)
        target = r.ub_claim
        assert reads == 0
        proof = m.check.linear(target, using=[target], memory_mb=0.015625).require()
        assert reads >= n
        assert len(proof.evidence["terms"]) == 2 * n
        assert len(r.expr.terms.head(3)) == 3
        monkeypatch.setattr(m.model, "coeff_id", original)


def test_public_checker_signatures_and_constant_budget(model):
    import inspect

    for method in (
        "identity",
        "linear",
        "infeasible",
        "round",
        "enumerate",
        "solution",
    ):
        signature = inspect.signature(getattr(model.check, method))
        assert "ctx" not in signature.parameters
        assert "arithmetic" not in signature.parameters
        assert signature.parameters["memory_mb"].default == 128
    with pytest.raises(ValueError):
        model.linexpr(constant=0, memory_mb=0)
    with pytest.raises(mf.NonlinearExpressionError):
        xyz(model)[0] ** 2
