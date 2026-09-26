"""Fixtures are generated from mathematical definitions, independently of discovery."""

import json, random
from pathlib import Path
import pytest
from lpir import IR, register_analysis


def model(objective, rows, binary=(), bounds=()):
    return "\n".join(
        ["Minimize", " o: " + objective, "Subject To"]
        + [f" r{i}: {row}" for i, row in enumerate(rows)]
        + ["Bounds"]
        + list(bounds)
        + (["Binaries", " ".join(binary)] if binary else [])
        + ["End", ""]
    )


@pytest.mark.parametrize("seed", [7, 19, 61])
def test_set_family_shuffling_scaling_complements(make_v2, seed):
    rng = random.Random(seed)
    names = [f"z{k}" for k in rng.sample(range(1000), 12)]
    supports = [rng.sample(range(12), k) for k in (2, 3, 4, 5, 6, 7)]
    rows = []
    # Complement the same global subset, with exact RHS updates and varying row scale.
    complemented = {1, 5, 7}
    for support in supports:
        scale = rng.choice([2, 3, 7])
        rhs = scale * (1 - len(set(support) & complemented))
        terms = []
        for v in support:
            terms.append(f"{-scale if v in complemented else scale:+} {names[v]}")
        rng.shuffle(terms)
        rows.append(" ".join(terms) + f" = {rhs}")
    rng.shuffle(rows)
    ir = make_v2(model(" + ".join(names), rows, names))
    require_class = ir.overview()["classes"]
    assert require_class.get("set_partitioning", 0) == 6
    assert ir.verify()["status"] == "PASS"
    assert all(
        x["roles"][0]["coefficient"] == "1"
        for x in ir.inspect("family", ir.overview()["families"][0]["id"])["items"]
    )


@pytest.mark.parametrize("n", [4, 7])
def test_assignment_difference_and_boundary(make_v2, n):
    xs = [f"z{i}_{j}" for i in range(n) for j in range(n)]
    rows = ["+".join(f"z{i}_{j}" for j in range(n)) + "=1" for i in range(n)] + [
        "+".join(f"z{i}_{j}" for i in range(n)) + "=1" for j in range(n)
    ]
    rows += ["2z0_0+3z1_1<=4", "-2t+2u+14z0_0-200z0_1 >= -186"]
    ir = make_v2(model("+".join(xs), rows, xs, ["t free", "u free"]))
    a = ir.analyze("assignments")
    assert a["items"][0]["shape"] == [n, n]
    assert ir.overview()["temporal_rows"] == 1 and ir.overview()["time_variables"] == 2
    assert ir.query("external", a["items"][0]["id"])["total"] == 2
    assert ir.verify()["status"] == "PASS"


def test_network_and_fixed_charge_classification(make_v2):
    ir = make_v2(
        model(
            "3a+2b+z", ["a=2", "-a+b=0", "-b=-2", "b-8z<=0"], ["z"], ["a<=10", "b<=10"]
        )
    )
    net = ir.analyze("network", scope={"rows": [0, 1, 2]})
    assert net["summary"]["verified_arcs"] == 2
    assert ir.overview()["classes"]["variable_bound"] == 1
    assert ir.validate(
        {
            "kind": "decomposition",
            "blocks": [{"rows": [0, 1, 2, 3], "variables": [0, 1, 2]}],
            "border_rows": [],
        }
    )["valid"]


def test_sparse_assignment_rejection_is_not_lost_structure(make_v2):
    ir = make_v2(model("x+y+z", ["x+y=1", "y+z=1", "z+x=1"], ["x", "y", "z"]))
    assert ir.overview()["classes"]["set_partitioning"] == 3
    assert ir.analyze("assignments")["total"] == 0


def test_center_neighbor_graph_permutation_and_modified_coefficient(make_v2):
    rng = random.Random(13)
    labels = [f"a{i}" for i in rng.sample(range(1000), 8)]
    edges = {(i, (i + 1) % 8) for i in range(8)}
    edges |= {(j, i) for i, j in list(edges)}
    rows = []
    for i in range(8):
        terms = [f"7 {labels[i]}"] + [labels[j] for a, j in edges if a == i]
        rng.shuffle(terms)
        rows.append("+".join(terms) + "<=8")
    rng.shuffle(rows)
    ir = make_v2(model("-" + "-".join(labels), rows, labels))
    assert ir.analyze("graph")["summary"]["undirected_edges"] == 8
    assert ir.overview()["family_count"] == 1
    changed = make_v2(
        model(
            "-" + "-".join(labels),
            [rows[0].replace("7 ", "7.00000000001 ", 1)] + rows[1:],
            labels,
        )
    )
    assert changed.verify()["status"] == "PASS"
    original = [ir.model.row(i) for i in range(8)]
    new = [changed.model.row(i) for i in range(8)]
    assert original != new


def test_optional_community_decomposition(make_v2):
    pytest.importorskip("igraph")
    ir = make_v2(model("x+y+z+w", ["x+y<=1", "z+w<=1", "y+z<=1"], ["x", "y", "z", "w"]))
    q = ir.analyze("decomposition")
    assert 1 <= q["total"] <= 8
    for rec in q["items"]:
        assert ir.validate(
            {
                "kind": "decomposition",
                "blocks": rec["blocks"],
                "border_rows": rec["border_rows"],
            }
        )["valid"]
        assert rec["score_status"] == "heuristic"


def test_public_plugin_contract(make_v2):
    def discover(context, scope, cursor):
        for row in context.rows():
            if row["row"] >= cursor:
                yield {
                    "row": row["row"],
                    "hypothesis": {"kind": "formula", "row": row["row"]},
                }

    register_analysis("formula_audit", "1", discover)
    ir = make_v2(model("x+y", ["x+y=1"], ["x", "y"]))
    r = ir.analyze("plugin:formula_audit")
    assert r["items"][0]["verification"] == "verified"
    assert (
        r["verification"] == "candidate"
    )  # plugin envelope is not itself a mathematical theorem


def test_public_corpus(make_v2, tmp_path):
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1] / "benchmarks/public/manifest.json"
        ).read_text()
    )
    root = Path(__file__).resolve().parents[1] / "benchmarks/public"
    expected = {
        "p0033": 2520.5717391304347,
        "stein27": 13,
        "flugpl": 1167185.7255923206,
        "vpm1": 15.416666666666666,
    }
    for entry in manifest["instances"]:
        ir = IR.build(root / entry["file"], tmp_path / entry["name"])
        assert ir.verify()["status"] == "PASS"
        assert ir.probe()["summary"]["objective"] == pytest.approx(
            expected[entry["name"]], rel=1e-9
        )
