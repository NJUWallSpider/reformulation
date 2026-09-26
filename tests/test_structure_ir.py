"""Cross-family coordinates are recovered from anonymous matrix incidence."""

import copy
import random
from collections import Counter
from pathlib import Path

import pytest

from lpir import IR
from lpir.structural import connect, load_family

DANOINT = Path("data/danoint.lp")


@pytest.fixture(scope="module")
def danoint(tmp_path_factory):
    if not DANOINT.exists():
        pytest.skip("local fixture absent")
    with IR.build(DANOINT, tmp_path_factory.mktemp("structure") / "ir") as ir:
        yield ir


def test_danoint_exact_structure(danoint):
    ir = danoint
    bs = ir.inspect("structure_blocks", limit=100)["items"]
    assert Counter((b["variables"], len(b["shape"])) for b in bs) == Counter(
        {(56, 2): 2, (392, 3): 1, (8, 1): 2, (1, 0): 1}
    )
    flow = next(b for b in bs if b["variables"] == 392)
    assert flow["domain"]["kind"] == "inequalities"
    assert len(flow["domain"]["not_equal"]) == 2
    fs = ir.inspect("structure_families", limit=100)["items"]
    assert sorted(f["rows"] for f in fs) == [8] * 6 + [56] * 4 + [392]
    assert sorted(f["rows"] for f in fs if f["definition"]) == [8, 8, 56]
    assert ir.inspect("structure_residuals")["total"] == 0
    activation = next(f for f in fs if f["rows"] == 392)
    ps = ir.query(activation["parameters_handle"], limit=100)["items"]
    p = next(p for p in ps if len(p["values"]) > 1)
    assert len(p["inputs"]) == 2 and len(p["values"]) == 64 and p["invariant_inputs"]
    assert all(
        f["description_length"]["indexed_bytes"] < f["description_length"]["flat_bytes"]
        for f in fs
    )
    assert ir.verify()["structure"]["checked_rows"] == 664
    doc = ir.describe()
    assert doc["structure_views"]["covered_rows"] == 664
    assert doc["indexed_views"]["operators"]  # Existing machine interface survives.
    assert "400 条约束共享一个算子" not in ir.render()
    assert len(ir.render().encode()) <= 20000
    # Old diagnostics and local syntax remain available.
    assert ir.inspect("index_operators")["total"] == 2
    assert ir.query({"table": "explanation_rows"}, limit=1)["total"] == 664


def test_exact_queries_and_reject_corruption(danoint):
    ir = danoint
    f = next(
        f
        for f in ir.inspect("structure_families", limit=100)["items"]
        if f["rows"] == 392
    )
    with connect(ir.model) as db:
        original = load_family(db, f["id"])
    assert ir.validate({"kind": "structure_family", "id": f["id"]})["valid"]
    for change in (
        "coefficient",
        "domain",
        "alignment",
        "missing_row",
        "extra_parameter_cell",
    ):
        x = copy.deepcopy(original)
        if change == "coefficient":
            x["parameters"]["P0"]["values"][0][1] = "999"
        elif change == "domain":
            x["domain"][0] = x["domain"][1]
        elif change == "alignment":
            x["row_axes"][0] = "foreign_axis"
        elif change == "missing_row":
            x["instances"].pop()
            x["domain"].pop()
            x["rows"] -= 1
        else:
            x["parameters"]["P0"]["values"].append([[999, 999], "1"])
        assert (
            ir.validate({"kind": "structure_family", "id": f["id"], "candidate": x})[
                "valid"
            ]
            is False
        ), change
    block = next(
        b
        for b in ir.inspect("structure_blocks", limit=100)["items"]
        if b["variables"] == 392
    )
    cs = ir.query(block["domain"]["coordinates"], limit=1000)["items"]
    cs[0]["coordinates"] = cs[1]["coordinates"]
    assert not ir.validate(
        {"kind": "structure_block", "id": block["id"], "coordinates": cs}
    )["valid"]
    assert (
        ir.query(
            {
                "op": "filter",
                "input": {"op": "scan", "table": "structure_links"},
                "where": {
                    "op": "eq",
                    "args": [{"field": "relation"}, {"literal": "definition"}],
                },
            },
            limit=100,
        )["total"]
        == 3
    )


def anonymous_model(m, seed):
    rng = random.Random(seed)
    ids = list(range(len(m.variables)))
    rng.shuffle(ids)
    names = {v: "u" + str(ids[v]) for v in range(len(ids))}

    # Integer scales clear all source denominators.
    def expr(ts, scale):
        ts = list(ts)
        rng.shuffle(ts)
        return " ".join(f"{int(c * scale):+} {names[v]}" for v, c in ts)

    objective = expr(
        [(v, m.num(m.variables[v][5])) for v in ids if m.num(m.variables[v][5])], 100
    )
    rows = []
    for r in range(len(m.rows)):
        lo, hi = m.num(m.rows[r][2]), m.num(m.rows[r][3])
        scale = rng.choice([-300, -100, 100, 200])
        if lo == hi:
            sense = "="
            rhs = lo
        elif lo is None:
            sense = "<="
            rhs = hi
        else:
            sense = ">="
            rhs = lo
        if scale < 0 and sense != "=":
            sense = {">=": "<=", "<=": ">="}[sense]
        rows.append(
            expr(m.iter_terms(r), scale) + " " + sense + " " + str(int(rhs * scale))
        )
    rng.shuffle(rows)
    binary = [names[v] for v in ids if m.variables[v][2] == "B"]
    return "\n".join(
        [
            "Minimize",
            " obj: " + objective,
            "Subject To",
            *[f"r{j}: {r}" for j, r in enumerate(rows)],
            "Binaries",
            " ".join(binary),
            "End",
        ]
    )


@pytest.mark.parametrize("seed", [7, 31])
def test_danoint_permutation_scaling_names(tmp_path, danoint, seed):
    source = tmp_path / "anonymous.lp"
    source.write_text(anonymous_model(danoint.model, seed))
    with IR.build(source, tmp_path / "ir") as ir:
        bs = ir.inspect("structure_blocks", limit=100)["items"]
        assert sorted(b["variables"] for b in bs) == [1, 8, 8, 56, 56, 392]
        assert sorted(
            f["rows"] for f in ir.inspect("structure_families", limit=100)["items"]
        ) == [8] * 6 + [56] * 4 + [392]
        assert ir.verify()["structure"]["checked_rows"] == 664


def grid_model(n=4, m=5, layers=3, perturb=False, extra=False):
    # Rectangular repeated copies linked by signed differences, with no network names.
    cells = [(i, j) for i in range(n) for j in range(m) if (i + j) % 4 != 0]
    xs = {(i, j): f"x{i}_{j}" for i, j in cells}
    fs = {(k, i, j): f"f{k}_{i}_{j}" for k in range(layers) for i, j in cells}
    rows = [" + ".join(xs[i, j] for a, j in cells if a == i) + " = 2" for i in range(n)]
    rows += [
        " + ".join(xs[i, j] for i, b in cells if b == j) + " = 2" for j in range(m)
    ]
    for k in range(layers):
        for a, (i, j) in enumerate(cells):
            p = 20 + k + i + (1 if perturb and k == 0 and a == 0 else 0)
            rows.append(
                f"{fs[k, i, j]} - {p} {xs[i, j]}"
                + (" + stray" if extra and k == 0 and a == 0 else "")
                + " <= 0"
            )
        for (i, j), (a, b) in zip(cells, cells[1:]):
            rows.append(f"{fs[k, i, j]} - {fs[k, a, b]} = 0")
    for i, j in cells:
        rows.append(
            f"z{i}_{j} - " + " - ".join(fs[k, i, j] for k in range(layers)) + " = 0"
        )
    return "\n".join(
        [
            "Minimize",
            " obj: " + next(iter(xs.values())),
            "Subject To",
            *[f"r{j}: {r}" for j, r in enumerate(rows)],
            "Binaries",
            " ".join(xs.values()),
            "End",
        ]
    )


@pytest.mark.parametrize("n,m,layers", [(4, 5, 3), (5, 7, 2)])
def test_rectangular_masked_copies(tmp_path, n, m, layers):
    p = tmp_path / "grid.lp"
    p.write_text(grid_model(n, m, layers))
    with IR.build(p, tmp_path / "ir") as ir:
        bs = ir.inspect("structure_blocks", limit=100)["items"]
        lifted = [b for b in bs if b["origin"]["kind"] == "incidence_lift"]
        assert len(lifted) == 1
        assert sorted(lifted[0]["shape"]) == sorted([layers, n, m])
        assert lifted[0]["domain"]["kind"] == "explicit"
        assert ir.verify()["structure"]["status"] == "PASS"


def test_parameter_perturbation_and_extra_term(tmp_path):
    input_counts = []
    for name, perturb, extra in [
        ("base", False, False),
        ("perturb", True, False),
        ("extra", False, True),
    ]:
        p = tmp_path / (name + ".lp")
        p.write_text(grid_model(perturb=perturb, extra=extra))
        with IR.build(p, tmp_path / name) as ir:
            fs = ir.inspect("structure_families", limit=100)["items"]
            ps = [
                p
                for f in fs
                for p in ir.query(f["parameters_handle"], limit=100)["items"]
                if p["name"].startswith("P") and len(p["values"]) > 1
            ]
            input_counts.append(max([len(p["inputs"]) for p in ps], default=0))
            assert ir.verify()["structure"]["status"] == "PASS"
            if extra:
                assert ir.inspect("structure_residuals")["total"] > 0
    assert input_counts[1] > input_counts[0]


def test_scope_resume_and_read_compatibility(tmp_path, danoint):
    ir = danoint
    scope = {"rows": list(range(16))}
    phases = set()
    for _ in range(150):
        out = ir.analyze("structure", scope=scope, budget={"candidates": 1})
        phases.add(out["checked_range"]["phase"])
        if out["completeness"] == "complete":
            break
    else:
        pytest.fail("resume did not progress")
    assert {"scan", "discover", "draft", "families", "residuals"} <= phases
    fs = ir.query(
        {"table": "structure_families", "analysis": out["analysis"]}, limit=100
    )["items"]
    assert sum(f["rows"] for f in fs) == 16
    assert ir.inspect("structure_families", {"analysis": out["analysis"]}, limit=100)[
        "total"
    ] == len(fs)
    out2 = ir.analyze("structure", scope={"rows": {"op": "scan", "table": "raw_rows"}})
    assert out2["completeness"] == "complete"
    assert (
        sum(
            f["rows"]
            for f in ir.query(
                {"table": "structure_families", "analysis": out2["analysis"]}, limit=100
            )["items"]
        )
        == 664
    )
    with pytest.raises(Exception, match="Foreign structure"):
        ir.analyze("structure", handle="foreign")
    # A read of a historical artifact does not create the new sidecar.
    old = tmp_path / "old"
    import shutil

    shutil.copytree(ir.model.path, old)
    for p in old.glob("structure*"):
        p.unlink()
    with IR(old) as historical:
        assert "structure_views" not in historical.describe()
        assert not (old / "structure.sqlite").exists()
        assert historical.verify()["structure"]["status"] == "not_run"


def test_unambiguous_definition_and_no_seed_completion(tmp_path):
    source = tmp_path / "ambiguous.lp"
    source.write_text(
        "Minimize\n obj: x + y\nSubject To\n a: x - y = 0\n b: x + y = 2\nEnd\n"
    )
    with IR.build(source, tmp_path / "ir") as ir:
        assert ir.inspect("structure_blocks")["total"] == 0
        assert ir.inspect("structure_residuals")["total"] == 2
        assert ir.verify()["structure"]["search_completeness"] == "complete"


def test_undermerged_local_formulas_are_one_structural_family(danoint):
    ir = danoint
    families = ir.inspect("structure_families", limit=100)["items"]
    combined = []
    for family in families:
        if family["rows"] == 8:
            rows = ir.query(family["rows_handle"], limit=100)["items"]
            if len({r["source_family"] for r in rows}) == 7:
                combined.append(family)
    assert len(combined) == 1
    doc = ir.describe()
    for block in doc["structure_views"]["blocks"]:
        assert ir.query(block["handle"])["total"] == 1
    assert "## 本地约束公式" not in ir.render()
    assert doc["families"]  # Diagnostic machine payload stays available.
