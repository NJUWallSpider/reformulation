"""Regressions for API guesses observed in an actual agent run."""

from dataclasses import replace
import operator

import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "ergonomics.lp"
    path.write_text("""Minimize
 obj: x + y + z
Subject To
 ranged: 1 <= x + y <= 3
 equal: x - y = 0
Bounds
 0 <= x <= 2
 0 <= y <= 2
 -1 <= z <= 1
Generals
 x y
End
""")
    with mf.read(path) as m:
        yield m


@pytest.mark.parametrize("axis", ["rows", "vars"])
def test_ref_loc_checks_identity_selection_and_lifecycle(model, tmp_path, axis):
    frame = getattr(model, axis)
    kind = frame.key[0]
    entity = frame.iloc[0]
    ref = entity.to_record()[kind]
    assert frame.loc[ref] == frame.loc[ref.id] == ref.bind(model)
    assert frame.iloc[::-1].loc[ref] == entity
    with pytest.raises(KeyError):
        frame.iloc[1:].loc[ref]
    with pytest.raises(KeyError):
        frame.loc[replace(ref, id=len(frame))]
    for bad in (True, "0", 0.0, None):
        with pytest.raises(TypeError) as error:
            frame.loc[bad]
        assert mf.help(error.value, format="json")["results"][0]["id"] == "error.navigation"
    for bad, topic in (
        (replace(ref, kind="var" if kind == "row" else "row"), "error.navigation"),
        (replace(ref, uid="different"), "concept.identity"),
        (replace(ref, version="different"), "concept.identity"),
    ):
        with pytest.raises(mf.SchemaError) as error:
            frame.loc[bad]
        assert mf.help(error.value, format="json")["results"][0]["id"] == topic
    model.save(tmp_path / "saved")
    with mf.load(tmp_path / "saved") as loaded:
        assert getattr(loaded, axis).loc[ref] == ref.bind(loaded)
    with mf.read(tmp_path / "ergonomics.lp") as independent:
        other_ref = getattr(independent, axis).iloc[0].to_record()[kind]
        with pytest.raises(mf.SchemaError):
            frame.loc[other_ref]
    locator = frame.loc
    model.close()
    with pytest.raises(mf.MIPFrameError):
        locator[ref]


@pytest.mark.parametrize("name,compare", [("le", operator.le), ("ge", operator.ge)])
def test_inequality_methods_preserve_claim_contract(model, tmp_path, name, compare):
    x, y = model.vars.by_name("x"), model.vars.by_name("y")
    s = 2*x - y + mf.q("1/3")
    for other in (2, mf.q("4/7"), y, y + 1):
        claim = getattr(s, name)(other)
        assert claim.to_spec() == compare(s, other).to_spec()
        with pytest.raises(TypeError):
            bool(claim)
    for bad in (True, 0.5, float("inf")):
        with pytest.raises(mf.ExactArithmeticError):
            getattr(s, name)(bad)
    with mf.read(tmp_path / "ergonomics.lp") as independent:
        with pytest.raises(mf.SchemaError):
            getattr(s, name)(independent.vars.iloc[0])
    r = model.rows.by_name("ranged")
    bound, source = (r.ub, r.ub_claim) if name == "le" else (r.lb, r.lb_claim)
    model.check.linear(getattr(r.expr, name)(bound), using=[source]).require()
    assert mf.help(getattr(s, name), format="json")["results"][0]["id"] == "api.LinExpr." + name
    model.close()
    with pytest.raises(mf.MIPFrameError):
        getattr(s, name)(2)


def test_fractional_point_recovery_checks_rows_and_isolated_bounds(model):
    x, y, z = [model.vars.by_name(n) for n in ("x", "y", "z")]

    def values(z_value=0, include_z=True):
        records = [{"var": x, "value": mf.q("1/2")},
                   {"var": y, "value": mf.q("1/2")}]
        if include_z:
            records.append({"var": z, "value": z_value})
        return mf.Frame.from_records(records,
            schema={"var": model.vars.schema["var"], "value": "number"},
            key="var", models=(model,))

    point = values()
    with pytest.raises(ValueError, match="matrix.activity") as error:
        model.check.solution(point, domain="relaxation")
    assert mf.help(error.value, format="json")["results"][0]["id"] == "limitation.fractional-point"
    for domain in ("auto", "integer"):
        assert model.check.solution(point, domain=domain).status == "REFUTED"
    activity = model.matrix.activity(point).join(model.rows, on="row", cardinality="1:1", require="both")
    assert all(r.lb <= r.activity <= r.ub for r in activity)
    bounded = model.vars.join(point, on="var", cardinality="1:1", require="both")
    assert all(r.lb <= r.value <= r.ub for r in bounded)
    # Row feasibility misses an isolated variable's bound violation.
    assert list(model.matrix.activity(values(z_value=2)).column("activity")) == [1, 0]
    bad = model.vars.join(values(z_value=2), on="var", cardinality="1:1", require="both")
    assert [r.var.id for r in bad if not r.lb <= r.value <= r.ub] == [z.id]
    incomplete = values(include_z=False)
    assert model.matrix.activity(incomplete).count() == model.rows.count()
    with pytest.raises(mf.CardinalityError):
        model.vars.join(incomplete, on="var", cardinality="1:1", require="both").count()
