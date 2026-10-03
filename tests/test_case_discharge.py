"""Finite case proofs, scoped dependency closure and executable certificates."""

from copy import deepcopy
from fractions import Fraction as Q
import gc
import weakref

import pytest
import mipframe as mf


@pytest.fixture
def m(tmp_path):
    path = tmp_path / "cases.lp"
    path.write_text("""Minimize
 obj: x
Subject To
 cap: x <= 6
 old: x + 1197 y <= 1200
Bounds
 0 <= x <= 10
 0 <= y <= 1
 0 <= z <= 1
Generals
 y z
End
""")
    with mf.read(path) as model:
        yield model


def branches(m, **opts):
    x, y = (m.vars.by_name(k) for k in ("x", "y"))
    target = x + 3*y <= 6
    reports = {
        k: m.check.linear(target, using=[m.rows.by_name(name).ub_claim],
                          assume=[y.eq(k)], residual="bounds", **opts)
        for k, name in ((0, "cap"), (1, "old"))
    }
    assert all(p.ok for p in reports.values())
    return target, y, reports


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_two_stage_edit_replay_and_native(m, tmp_path, memory):
    t, y, ps = branches(m, memory_mb=memory)
    p = m.check.cases(t, var=mf.Ref(m.uid, m.version, "var", y.id), cases=ps, memory_mb=memory)
    assert (p.status, p.method, p.scope["domain"], p.scope["assumptions"]) == (
        "PROVED", "cases", "integer", 0)
    assert len(p.evidence["cases"]) == len(p.evidence["discharged"]) == 2
    deps = {(d.kind, d.side) for d in p.dependencies}
    assert {("integer", "kind"), ("var", "lb"), ("var", "ub")} <= deps
    for package in (p.to_spec(), p.write(tmp_path / "proof.jsonl")):
        replayed = mf.Report.replay(package, models=[m], memory_mb=memory)
        assert replayed.ok and replayed.scope["assumptions"] == 0
        assert replayed.conclusion.expr == t.expr
    e = m.edit(memory_mb=memory)
    e.add_row(t, name="tight", because=p)
    with e.build() as middle:
        d = middle.diff(m).semantic
        assert (d["lp"], d["mip"], d["optimal_value"]) == ("SUBSET", "SAME", "PRESERVED")
        with mf.load(middle.save(tmp_path / "middle.native")) as saved:
            assert saved.diff(m).semantic["mip"] == "SAME"
        yy = middle.vars.by_name("y")
        old = middle.rows.by_name("old")
        reverse = middle.check.linear(old.ub_claim, using=[middle.rows.by_name("tight").ub_claim, (1194, yy.ub_claim)])
        edit = middle.edit()
        edit.drop_rows(old, because=reverse)
        with edit.build() as final:
            d = final.diff(middle).semantic
            assert (d["lp"], d["mip"], d["optimal_value"]) == ("SAME", "SAME", "PRESERVED")
            def feasible_rows(model, xvalue, yvalue):
                point = {v: {"x": xvalue, "y": yvalue, "z": 0}[v.name] for v in model.vars}
                return all(r.lb <= r.expr.evaluate(point) <= r.ub for r in model.rows)

            for yy in (0, 1):
                for numerator in range(21):
                    xx = Q(numerator, 2)
                    expected = xx <= (6 if yy == 0 else 3)
                    assert feasible_rows(m, xx, yy) == feasible_rows(final, xx, yy) == expected
            assert feasible_rows(m, Q(6), Q(1, 2))
            assert not feasible_rows(final, Q(6), Q(1, 2))
            with pytest.raises(mf.SchemaError, match="direct parent"):
                final.diff(m)


@pytest.mark.parametrize("lb,ub", [(0, 2), (-3, -1), (Q(-5, 2), Q(3, 2)), (1, 1)])
@pytest.mark.parametrize("equality", [False, True])
def test_integer_intervals_and_equalities(m, lb, ub, equality):
    from math import ceil, floor
    edit = m.edit()
    edit.set_var_bounds(m.vars.by_name("y"), lb=lb, ub=ub)
    with edit.build() as n:
        y = n.vars.by_name("y")
        t = (0*y).eq(0) if equality else 0*y <= 1
        ps = {k: n.check.linear(t, assume=[(2*y).eq(2*k), (-3*y).eq(-3*k)]) for k in range(ceil(lb), floor(ub)+1)}
        p = n.check.cases(t, var=y, cases=ps)
        assert p.ok and p.scope["assumptions"] == 0
        assert len(p.evidence["discharged"]) == 2 * len(ps)


def test_residual_assumptions_inherited_union_and_nested(m):
    x, y, z = (m.vars.by_name(k) for k in ("x", "y", "z"))
    t = x <= 4
    inner = {}
    for k in (0, 1):
        ps = {}
        for j in (0, 1):
            base = m.check.linear(t, assume=[x <= 4, y.eq(j), z.eq(k)], residual="bounds")
            ps[j] = m.check.linear(t, using=[base])
        inner[k] = m.check.cases(t, var=y, cases=ps)
        assert inner[k].scope["assumptions"] == 2
    p = m.check.cases(t, var=z, cases=inner)
    assert p.ok and p.scope["assumptions"] == 1
    assert mf.Report.replay(p.to_spec(), models=[m]).scope["assumptions"] == 1
    with pytest.raises(mf.UndischargedAssumptionError):
        m.edit().add_row(t, because=p)
    # Different residual assumptions are retained as a union, never intersected.
    ps = {j: m.check.linear(t, assume=[y.eq(j), x <= 3+j], residual="bounds") for j in (0, 1)}
    assert m.check.cases(t, var=y, cases=ps).scope["assumptions"] == 2
    # Fully discharge two splits; conditional descendants are audit-only.
    t = m.rows.by_name("cap").ub_claim
    ps = {k: m.check.cases(t, var=y, cases={j: m.check.linear(t, using=[t], assume=[y.eq(j), z.eq(k)])
          for j in (0, 1)}) for k in (0, 1)}
    p = m.check.cases(t, var=z, cases=ps)
    assert p.scope["assumptions"] == 0
    e = m.edit()
    e.add_row(t, because=p)
    with e.build() as n:
        assert n.diff(m).semantic["mip"] == "SAME"


def test_unconditional_frontier_and_no_conditional_leak(m):
    t, y, ps = branches(m)
    cap = m.check.linear(m.rows.by_name("cap").ub_claim, using=[m.rows.by_name("cap").ub_claim])
    ps[0] = m.check.linear(t, using=[cap], assume=[y.eq(0)], residual="bounds")
    p = m.check.cases(t, var=y, cases=ps)
    report_deps = [d for d in p.dependencies if d.kind == "report"]
    assert report_deps and all(p._parents[d.parent].scope["assumptions"] == 0 for d in report_deps)
    # A branch-only intermediate conclusion cannot certify an unrelated addition.
    x = m.vars.by_name("x")
    local = m.check.linear(x <= 3, using=[m.rows.by_name("old").ub_claim], assume=[y.eq(1)], residual="bounds")
    ps[1] = m.check.linear(t, using=[local], residual="bounds")
    p = m.check.cases(t, var=y, cases=ps)
    e = m.edit()
    e.add_row(t, because=p)
    e.add_row(x <= 3)
    with e.build() as n:
        assert n.diff(m).semantic["mip"] != "SAME"


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "wrong_assumption", "wrong_target", "inconclusive", "refuted", "infeasible", "unverified", "relation", "mapping", "relaxation"])
def test_invalid_cases(m, change):
    t, y, ps = branches(m)
    opts = {}
    if change == "missing": del ps[1]
    if change == "extra": ps[2] = ps[1]
    if change == "duplicate": ps["0"] = ps[0]
    if change == "wrong_assumption": ps[1] = ps[0]
    if change == "wrong_target": ps[1] = m.check.linear(y <= 1, assume=[y.eq(1)], residual="bounds")
    if change == "inconclusive": ps[1] = m.check.linear(t)
    if change == "refuted": ps[1] = m.check.identity(y, y+1)
    if change == "infeasible": ps[1] = m.check.infeasible(assume=[y.eq(0), y.eq(1)])
    if change == "unverified": ps[1]._verified = False
    if change == "relation": ps[1] = m.vars.check("unique", ("var",))
    if change == "mapping": ps = list(ps.values())
    if change == "relaxation": opts["domain"] = "relaxation"
    with pytest.raises(mf.InvalidCertificateError):
        m.check.cases(t, var=y, cases=ps, **opts)


@pytest.mark.parametrize("key", [False, 0.0, Q(1, 2)])
def test_invalid_keys(m, key):
    t, y, ps = branches(m)
    with pytest.raises((mf.InvalidCertificateError, mf.ExactArithmeticError)):
        m.check.cases(t, var=y, cases={key: ps[0], 1: ps[1]})


def test_cross_snapshot_and_unsupported_domains(m):
    t, y, ps = branches(m)
    with m.edit().build() as other:
        with pytest.raises((mf.InvalidCertificateError, mf.SchemaError)):
            other.check.cases(t, var=other.vars.by_name("y"), cases=ps)
        with pytest.raises(mf.SchemaError):
            m.check.cases(t, var=other.vars.by_name("y"), cases=ps)
    for lb, ub, kind in [(0, 1, "C"), (0, float("inf"), "I"), (Q(1, 4), Q(3, 4), "I")]:
        e = m.edit()
        e.set_var_bounds(y, lb=lb, ub=ub)
        e.set_domains(y, kind=kind)
        with e.build(allow_infeasible=True) as n:
            yy = n.vars.by_name("y")
            with pytest.raises(mf.InvalidCertificateError):
                n.check.cases(yy <= 3, var=yy, cases={})


def test_deletion_and_domain_dependency_safety(m):
    t, y, ps = branches(m)
    p = m.check.cases(t, var=y, cases=ps)
    # Source-relative addition remains sound, but removing integer restrictions
    # or widening the coverage interval cannot be labelled equivalent.
    for change in ("domain", "bounds"):
        e = m.edit()
        e.add_row(t, because=p)
        if change == "domain": e.set_domains(y, kind="C")
        else: e.set_var_bounds(y, ub=2)
        with e.build() as n:
            assert n.diff(m).semantic["mip"] != "SAME"
    old = m.rows.by_name("old")
    self_ps = {k: m.check.linear(old.ub_claim, using=[old.ub_claim], assume=[y.eq(k)]) for k in (0, 1)}
    self_proof = m.check.cases(old.ub_claim, var=y, cases=self_ps)
    e = m.edit()
    e.drop_rows(old, because=self_proof)
    with pytest.raises(mf.EditValidationError): e.build()
    # Deletion through cases still requires all branch facts to survive.
    e = m.edit()
    e.add_row(t, because=p)
    e.drop_rows(old, because=m.check.linear(old.ub_claim, using=[p, (1194, y.ub_claim)]))
    with pytest.raises(mf.EditValidationError): e.build()


def test_case_deletion_closure_and_mutual_cycle(m):
    cap = m.rows.by_name("cap")
    e = m.edit()
    e.add_row(cap.ub_claim, name="copy")
    with e.build() as n:
        y = n.vars.by_name("y")
        a, b = (n.rows.by_name(k) for k in ("cap", "copy"))

        def prove(target, source):
            return n.check.cases(target.ub_claim, var=y, cases={
                k: n.check.linear(target.ub_claim, using=[source.ub_claim], assume=[y.eq(k)])
                for k in (0, 1)})

        pa, pb = prove(a, b), prove(b, a)
        e = n.edit()
        e.drop_rows(b, because=pb)
        with e.build() as final:
            assert final.diff(n).semantic["mip"] == "SAME"
        e = n.edit()
        e.drop_rows(a, because=pa)
        e.drop_rows(b, because=pb)
        with pytest.raises(mf.EditValidationError):
            e.build()


def test_coverage_bounds_cannot_be_lost_during_deletion(m):
    y = m.vars.by_name("y")
    e = m.edit()
    e.add_row(y <= 1, name="upper")
    with e.build() as n:
        y = n.vars.by_name("y")
        row = n.rows.by_name("upper")
        p = n.check.cases(row.ub_claim, var=y, cases={k: n.check.linear(
            row.ub_claim, assume=[y.eq(k)], residual="bounds") for k in (0, 1)})
        e = n.edit()
        e.drop_rows(row, because=p)
        e.set_var_bounds(y, ub=2)
        with pytest.raises(mf.EditValidationError):
            e.build()


@pytest.mark.parametrize("tamper", ["missing", "value", "parent", "duplicate", "variable", "branch_target", "branch_assumption", "verdict"])
def test_replay_rechecks_case_certificates(m, tamper):
    t, y, ps = branches(m)
    p = m.check.cases(t, var=y, cases=ps)
    spec = deepcopy(p.to_spec())
    events = spec["events"]
    case = next(e for e in events if e["type"] == "case")
    root = next(e for e in events if e["type"] == "node" and e["recipe"]["kind"] == "cases")
    if tamper == "missing": events.remove(case)
    if tamper == "value": case["value"] = "2"
    if tamper == "parent": case["parent"] = 999
    if tamper == "duplicate": events.append(dict(case))
    if tamper == "variable": root["recipe"]["var"] = -1
    if tamper in ("branch_target", "branch_assumption"):
        branch = next(e for e in events if e["type"] == "node" and e["recipe"]["kind"] == "linear")
        cid = branch["recipe"]["target"] if tamper == "branch_target" else next(
            e["claim"] for e in events if e["type"] == "assumption" and e["node"] == branch["node"])
        claim = next(e for e in events if e["type"] == "claim" and e["node"] == branch["node"] and e["id"] == cid)
        claim["constant"] = "-99"
    if tamper == "verdict":
        root["status"] = "REFUTED"
        assert mf.Report.replay(spec, models=[m]).ok
    else:
        with pytest.raises(mf.InvalidCertificateError): mf.Report.replay(spec, models=[m])


def test_batch_sources_and_reuse(m):
    t, y, ps = branches(m)
    batch = m.check.batch(residual="bounds")
    for k, name in ((0, "cap"), (1, "old")):
        batch.add(key=str(k), target=t, using=[m.rows.by_name(name).ub_claim], assume=[y.eq(k)])
    b = batch.run()
    p = m.check.cases(t, var=y, cases={k: b.for_target(str(k)) for k in (0, 1)})
    assert mf.Report.replay(p.to_spec(), models=[m]).ok
    batch = m.check.batch()
    batch.add(key="combined", target=t, using=[p])
    b = batch.run()
    replayed = mf.Report.replay(b.to_spec(), models=[m])
    assert replayed.for_target("combined").scope["assumptions"] == 0


def test_2016_merges_share_archive_and_release(m):
    t, y, ps = branches(m)
    reports = [m.check.cases(t, var=y, cases=ps) for _ in range(2016)]
    archive = reports[0]._store._shared
    assert all(p._store._shared is archive for p in reports)
    table_count = archive.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    assert table_count < 30
    ref = weakref.ref(reports[0])
    del reports
    gc.collect()
    assert ref() is None
    # The two branch Reports survive; all 2016 merged nodes are reclaimed.
    result_table = next(iter(ps.values()))._store._lease.archive.result
    assert archive.execute(f"SELECT count(*) FROM {result_table}").fetchone()[0] == 2


def test_lifecycle_and_cycles(m):
    t, y, ps = branches(m)
    p = m.check.cases(t, var=y, cases=ps)
    parent = ps[0]
    old = parent._parents
    parent._parents = (parent,)
    try:
        with pytest.raises(mf.InvalidCertificateError, match="Cyclic"):
            m.check.cases(t, var=y, cases=ps)
    finally:
        parent._parents = old
    m.close()
    with pytest.raises(mf.MIPFrameError, match="closed"):
        _ = p.status
