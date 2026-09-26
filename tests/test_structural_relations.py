"""Exact relationships, rather than model-specific pattern recognition."""

import copy
import json
import random
import sqlite3
from pathlib import Path

import pytest

from lpir import IR
from lpir.jobs import Budget
from lpir.structural import connect, load_family, records
from lpir.structural_relations import reduce_blocks, unify_columns, support_restriction


@pytest.fixture(scope="module")
def test1(tmp_path_factory):
    source = Path("data/test1.lp")
    if not source.exists():
        pytest.skip("local fixture absent")
    with IR.build(source, tmp_path_factory.mktemp("relations") / "ir") as ir:
        yield ir


def test_test1_exact_closed_relations(test1):
    ir = test1
    bs = ir.inspect("structure_blocks", limit=100)["items"]
    assert sorted((b["variables"], b["shape"]) for b in bs) == [
        (65, [65]),
        (11025, [105, 105]),
    ]
    fs = ir.inspect("structure_families", limit=100)["items"]
    assert sum(f["rows"] for f in fs) == 4630
    pair = next(f for f in fs if f["rows"] == 4160)
    assert len(pair["row_axes"]) == 2
    assert pair["symbolic_domain"]["not_equal"] == [[0, 1]]
    assert len(pair["index_maps"]) == 2
    subset = next(f for f in fs if any(t.get("restriction") for t in f["ast"]["terms"]))
    assert subset["rows"] == 65 and len(subset["row_axes"]) == 1
    costs = ir.inspect("structure_objective", limit=100)["items"]
    assert sorted(x["cells"] for x in costs) == [65, 1600, 2600, 2600, 4225]
    assert sorted(len(x["parameter"]["inputs"]) for x in costs) == [0, 0, 1, 1, 2]
    assert ir.verify()["structure"]["objective"]["fallback_variables"] == 0
    md = ir.render()
    assert "4630 / 4630" in md and "目标系数分块" in md
    assert "| X2 | 65 |" in md and "714025" not in md
    assert len(md.encode()) < 15000
    for relation, kind in [
        ("structure_views", "structure_view"),
        ("structure_subsets", "structure_subset"),
        ("structure_index_maps", "structure_index_map"),
        ("structure_objective", "structure_objective"),
    ]:
        first = ir.query({"table": relation}, limit=1)
        assert first["total"] > 0
        assert ir.validate({"kind": kind, "id": first["items"][0]["id"]})["valid"]
        if first["next_offset"]:
            second = ir.query({"table": relation}, limit=1, offset=first["next_offset"])
            assert second["items"][0]["id"] != first["items"][0]["id"]


def test_corrupted_relations_are_refuted(test1):
    ir = test1
    with connect(ir.model) as db:
        f = load_family(
            db,
            next(
                x["id"] for x in records(db, "structure_families") if x["rows"] == 4160
            ),
        )
        cost = next(x for x in records(db, "structure_objective") if x["cells"] == 1600)
        view = records(db, "structure_views")[0]
        support = next(
            x
            for x in records(db, "structure_subsets")
            if x["evidence"] == "constraint_support"
        )
    for change in ("map", "extra_map", "domain", "coordinates"):
        x = copy.deepcopy(f)
        if change == "map":
            next(iter(x["index_maps"].values()))["values"][0][1] = 999
        elif change == "extra_map":
            x["index_maps"]["unused"] = next(iter(x["index_maps"].values()))
        elif change == "domain":
            x["symbolic_domain"]["not_equal"] = []
        else:
            x["instances"][0]["coordinates"][0] = 999
        assert (
            ir.validate({"kind": "structure_family", "id": f["id"], "candidate": x})[
                "valid"
            ]
            is False
        )
    cost["parameter"]["values"][0][1] = "1"
    view["cells"][0][2] = [999, 999]
    support["values"].append(999)
    for kind, x in [
        ("structure_objective", cost),
        ("structure_view", view),
        ("structure_subset", support),
    ]:
        assert ir.validate({"kind": kind, "candidate": x})["valid"] is False


def test_maps_nonaffine_and_no_arbitrary_flattening():
    blocks = [
        {
            "id": "b",
            "axes": ["a", "b"],
            "coords": {i: (i, [7, 1, 9, 2][i]) for i in range(4)},
            "origin": {},
        }
    ]
    assert len(reduce_blocks(blocks)[0]["axes"]) == 1
    grid = [
        {
            "id": "g",
            "axes": ["a", "b"],
            "coords": {i * 3 + j: (i, j) for i in range(2) for j in range(3)},
            "origin": {},
        }
    ]
    assert len(reduce_blocks(grid)[0]["axes"]) == 2
    rows = [(i, j) for i in range(7) for j in range(7) if i != j]
    perm = [9, 2, 12, 4, 8, 7, 1]
    columns = [
        (0, 0, "a", tuple(perm[i] for i, j in rows)),
        (0, 1, "b", tuple(j for i, j in rows)),
        (1, 0, "t", tuple(i for i, j in rows)),
        (1, 1, "t", tuple(j for i, j in rows)),
    ]
    keep, bindings, counts = unify_columns(columns, Budget())
    assert counts["after"] == 2
    for col, (slot, pairs) in bindings.items():
        for r in range(len(rows)):
            v = columns[keep[slot]][3][r]
            assert (dict(pairs)[v] if pairs is not None else v) == columns[col][3][r]
    assert support_restriction([(1, 4), (3, 4)], [(i, 4) for i in range(6)], [1]) == {
        "0": [1, 3]
    }
    assert (
        support_restriction(
            [(0, 0), (1, 1)], [(i, j) for i in range(2) for j in range(2)], []
        )
        is None
    )


def small_model(seed=0, perturb=False, missing=False):
    rng = random.Random(seed)
    n = 7
    k = 4
    A = [0, 2, 5]
    B = [1, 3, 4, 6]
    vars = [f"x{i}_{j}" for i in range(n) for j in range(n)] + [
        f"t{i}" for i in range(k)
    ]
    shuffled = list(vars)
    rng.shuffle(shuffled)
    names = dict(zip(vars, shuffled))

    # Names can even exchange old binary/continuous spellings; types follow identity.
    def emit(terms):
        terms = list(terms)
        rng.shuffle(terms)
        return " ".join(f"{c:+} {names[v]}" for v, c in terms)

    rows = []
    for i in range(n):
        rows.append(([(f"x{i}_{j}", 1) for j in range(n)], "=", 1))
        rows.append(([(f"x{j}_{i}", 1) for j in range(n)], "=", 1))
    for i in range(k):
        rows += [([(f"t{i}", 1)], ">=", i + 2), ([(f"t{i}", 1)], "<=", i + 20)]
        rows.append(
            (
                [(f"t{i}", 1)]
                + [
                    (f"x{a}_{B[i]}", i + 3)
                    for a in A
                    if not (missing and i == 0 and a == 0)
                ],
                ">=",
                0,
            )
        )
        for j in range(k):
            if i != j:
                rows.append(
                    (
                        [
                            (f"t{j}", 1),
                            (f"t{i}", -1),
                            (f"x{B[i]}_{B[i]}", i + j + 2),
                            (f"x{B[i]}_{B[j]}", -100),
                        ],
                        ">=",
                        i + j + 2 - 100,
                    )
                )
    obj = []
    for i in range(n):
        for j in range(n):
            c = (
                0
                if i in A and j in A
                else j + 1
                if i in A
                else i + 1
                if j in A
                else i + j + 3
            )
            if perturb and i == A[0] and j == B[0]:
                c += 1
            if c:
                obj.append((f"x{i}_{j}", c))
    rng.shuffle(rows)
    lines = []
    for i, (ts, sense, rhs) in enumerate(rows):
        scale = rng.choice([-3, -1, 1, 2])
        if scale < 0:
            sense = {">=": "<=", "<=": ">=", "=": "="}[sense]
        lines.append(
            f"r{i}: "
            + emit([(v, c * scale) for v, c in ts])
            + f" {sense} {rhs * scale}"
        )
    return "\n".join(
        [
            "Minimize",
            "obj: " + emit(obj),
            "Subject To",
            *lines,
            "Binaries",
            " ".join(names[v] for v in vars if v.startswith("x")),
            "End",
        ]
    )


@pytest.mark.parametrize("seed", [3, 19])
def test_renamed_permuted_scaled_model(tmp_path, seed):
    p = tmp_path / "input.lp"
    p.write_text(small_model(seed))
    with IR.build(p, tmp_path / "ir") as ir:
        result = ir.verify()["structure"]
        assert result["checked_rows"] + result["residual_rows"] == 38
        assert result["checked_rows"] >= 26
        costs = ir.inspect("structure_objective", limit=100)["items"]
        assert any(
            x["cells"] == 9 and x["parameter"]["values"] == [[[], "0"]] for x in costs
        )
        fs = ir.inspect("structure_families", limit=100)["items"]
        assert any(f["rows"] == 12 and len(f["row_axes"]) == 2 for f in fs)


def test_objective_perturbation_and_support_split(tmp_path):
    counts = []
    for case, perturb, missing in [
        ("base", False, False),
        ("cost", True, False),
        ("support", False, True),
    ]:
        p = tmp_path / (case + ".lp")
        p.write_text(small_model(3, perturb, missing))
        with IR.build(p, tmp_path / case) as ir:
            assert ir.verify()["structure"]["status"] == "PASS"
            subsets = ir.inspect("structure_subsets", limit=100)["items"]
            counts.append(
                sum(
                    s["size"] == 3 and s["evidence"] == "identical_objective_profiles"
                    for s in subsets
                )
            )
            if missing:
                assert ir.inspect("structure_residuals")["total"] > 0
    assert counts[1] < counts[0]


def test_obsolete_structure_sidecar_is_rejected(tmp_path):
    from lpir import LPIRError

    p = tmp_path / "m.lp"
    p.write_text(small_model())
    with IR.build(p, tmp_path / "ir") as ir:
        dbpath = ir.model.path / "structure.sqlite"
        with sqlite3.connect(dbpath) as db:
            model = json.loads(
                db.execute("select value from meta where key='model'").fetchone()[0]
            )
            model[1] = "1.0"
            db.execute(
                "update meta set value=? where key='model'", (json.dumps(model),)
            )
        before = dbpath.read_bytes()
        for action in (
            lambda: ir.inspect("structure_blocks"),
            lambda: ir.analyze("structure"),
        ):
            with pytest.raises(LPIRError, match="version mismatch"):
                action()
        assert dbpath.read_bytes() == before
        assert not list(dbpath.parent.glob("*.v1.0.*"))


def test_matching_support_patterns():
    from lpir.structural_relations import matching_support, matches_support

    universe = [(a, b) for a in range(4) for b in range(6)]
    diagonal = [(0, 4), (1, 1), (2, 5), (3, 0)]
    for selected in [diagonal, [c for c in universe if c not in diagonal]]:
        pattern = matching_support(selected, universe)
        assert pattern is not None
        assert {c for c in universe if matches_support(c, pattern)} == set(selected)
    irregular = [(0, 0), (0, 1), (1, 0), (2, 3)]
    assert matching_support(irregular, universe) is None


def test_scoped_relational_queries_resume(tmp_path):
    p = tmp_path / "m.lp"
    p.write_text(small_model())
    with IR.build(p, tmp_path / "ir") as ir:
        scope = {"rows": list(range(10))}
        for _ in range(100):
            out = ir.analyze("structure", scope=scope, budget={"candidates": 1})
            if out["completeness"] == "complete":
                break
        assert out["completeness"] == "complete"
        for relation in ["structure_views", "structure_subsets", "structure_objective"]:
            first = ir.query({"table": relation, "analysis": out["analysis"]}, limit=1)
            assert first["total"] > 0
            assert first["items"][0]["verification"] == "verified"
        item = ir.inspect(
            "structure_objective", {"analysis": out["analysis"]}, limit=1
        )["items"][0]
        assert ir.validate(
            {
                "kind": "structure_objective",
                "id": item["id"],
                "analysis": out["analysis"],
            }
        )["valid"]
