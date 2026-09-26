from pathlib import Path

import pytest

from lpir import IR
from lpir.compressed import render_document
from scripts.indexed_instances import model


def build(tmp_path, text, name="ir"):
    source = tmp_path / (name + ".lp")
    source.write_text(text)
    return IR.build(source, tmp_path / name)


def test_indexed_operator_and_permutation(tmp_path):
    A = [[5, 2, 0], [2, 5, 1], [0, 1, 5]]
    for permute in (False, True):
        text, truth = model(A, groups=2, permute=permute, scaled=permute, seed=17)
        with build(tmp_path, text, str(permute)) as ir:
            ts = ir.inspect("index_tensors")["items"]
            assert len(ts) == 1
            t = ts[0]
            assert t["shape"] == [3, 2]
            coords = ir.query(t["coordinates"])["items"]
            assert len(coords) == 6
            ops = ir.inspect("index_operators")["items"]
            assert len(ops) == 1
            op = ops[0]
            assert op["rows"] == 6 and op["axis_invariant"]
            stats = op["kernel_statistics"]
            assert stats["zeros"] == 2
            assert stats["alignment"]["symmetric_after_mapping"]
            assert stats["alignment"]["diagonal_constant"] == "5"
            assert ir.validate({"kind": "indexed_operator", "id": op["id"]})["valid"]
            assert ir.validate({"kind": "cartesian", "id": t["id"]})["valid"]
            assert ir.validate({"kind": "row_bundle", "id": op["id"]})["valid"]
            assert ir.validate({"kind": "axis_alignment", "id": op["id"]})["valid"]
            bindings = ir.query(op["bindings"])["items"]
            kernels = ir.query(op["kernel"])["items"]
            byvar = {ir.model.var_names[v]: p for p, v in truth["y"].items()}
            binary = {ir.model.var_names[v]: jk for jk, v in truth["X"].items()}
            for x in bindings:
                p = byvar[x["anchor"]]
                for c in coords:
                    if c["axis1"] == x["k"]:
                        j, _k = binary[c["variable"]]
                        value = next(
                            (
                                q["value"]
                                for q in kernels
                                if q["p"] == x["p"] and q["j"] == c["axis0"]
                            ),
                            "0",
                        )
                        assert int(value) == A[p][j]
            assert render_document(ir.describe()) == ir.render()
            assert len(ir.render().encode()) <= 20000
            assert ir.verify()["status"] == "PASS"


def test_zero_pattern_and_perturbation(tmp_path):
    for perturb in (None, (0, 1)):
        text, _ = model([[5, 0, 1], [1, 5, 1]], groups=3, perturb=perturb)
        with build(tmp_path, text, str(perturb)) as ir:
            ops = ir.inspect("index_operators")["items"]
            assert len(ops) == 1
            assert ops[0]["axis_invariant"] == (perturb is None)
            assert ir.validate({"kind": "indexed_operator", "id": ops[0]["id"]})[
                "valid"
            ]
            kernel = ir.query(ops[0]["kernel"], limit=1000)["items"]
            kernel[0]["value"] = "999"
            assert (
                ir.validate(
                    {"kind": "indexed_operator", "id": ops[0]["id"], "kernel": kernel}
                )["valid"]
                is False
            )
            assert (
                ir.validate(
                    {"kind": "indexed_operator", "id": ops[0]["id"], "kernel": []}
                )["valid"]
                is False
            )


def test_sparse_product_duplicate_and_external(tmp_path):
    for kwargs in ({"missing_cell": (1, 1)}, {"duplicate": True}, {"extra": True}):
        text, _ = model([[5, 1, 2], [1, 5, 2]], groups=3, **kwargs)
        with build(tmp_path, text, str(len(list(tmp_path.iterdir())))) as ir:
            t = ir.inspect("index_tensors")["items"][0]
            if "missing_cell" in kwargs:
                assert t["missing_cells"] == 1
                assert ir.validate({"kind": "cartesian", "id": t["id"]})["valid"]
            ops = ir.inspect("index_operators")["items"]
            if "duplicate" in kwargs:
                assert ops == []
            if "extra" in kwargs:
                assert ops[0]["rows"] == 5
                assert not ops[0]["complete_row_product"]


def test_reject_maps_and_keep_axes_separate(tmp_path):
    text, _ = model([[2, 2, 1], [1, 2, 2], [2, 1, 2]], groups=2)
    with build(tmp_path, text) as ir:
        op = ir.inspect("index_operators")["items"][0]
        assert "alignment" not in op["kernel_statistics"]
        t = ir.inspect("index_tensors")["items"][0]
        coords = ir.query(t["coordinates"])["items"]
        coords[0]["axis0"] = 999
        assert (
            ir.validate({"kind": "cartesian", "id": t["id"], "coordinates": coords})[
                "valid"
            ]
            is False
        )
        rows = ir.query(op["bindings"])["items"]
        rows[0]["k"] = 999
        assert (
            ir.validate({"kind": "row_bundle", "id": op["id"], "rows": rows})["valid"]
            is False
        )
        assert not ir.validate(
            {"kind": "indexed_operator", "id": op["id"], "ast": {"op": "eq"}}
        )["valid"]


def test_same_family_bipartite(tmp_path):
    text = "Minimize\n obj: a\nSubject To\n p: a+b=1\n q: c+d=1\n u: a+c=1\n v: b+d=1\nBinaries\n a b c d\nEnd\n"
    with build(tmp_path, text) as ir:
        ts = ir.inspect("index_tensors")["items"]
        assert len(ts) == 1
        assert ts[0]["shape"] == [2, 2]
        assert ir.validate({"kind": "cartesian", "id": ts[0]["id"]})["valid"]


def test_resume_and_scoped_queries(tmp_path):
    text, _ = model([[5, 1, 0], [1, 5, 2]], groups=3)
    with build(tmp_path, text) as ir:
        expected = ir.inspect("index_operators")["items"]
        # Separate scoped analysis uses public relation input and its own sidecar.
        scope = {"rows": {"op": "scan", "table": "raw_rows"}}
        phases = set()
        for i in range(100):
            out = ir.analyze("operators", scope=scope, budget={"candidates": 200})
            phases.add(out["checked_range"]["phase"])
            if out["completeness"] == "complete":
                break
        else:
            pytest.fail("resumption made no progress")
        assert out["items"][0]["rows"] == expected[0]["rows"]
        assert ir.query(out["items"][0]["kernel"])["total"] > 0
        # Explicit partition hypothesis can be refuted independently of discovery.
        assert not ir.validate({"kind": "indexed_partition", "sets": []})["valid"]


def test_assign_public_replay(tmp_path):
    source = Path("data/assign1-5-8.lp")
    if not source.exists():
        pytest.skip("local fixture absent")
    with IR.build(source, tmp_path / "ir") as ir:
        t = ir.inspect("index_tensors")["items"][0]
        assert t["shape"] == [26, 5]
        op = ir.inspect("index_operators")["items"][0]
        assert op["rows"] == 130 and op["kernel_shape"] == [26, 26]
        assert op["kernel_statistics"]["zeros"] == 10
        assert op["kernel_statistics"]["alignment"]["diagonal_constant"] == "5"
        assert (
            ir.validate({"kind": "indexed_operator", "id": op["id"]})["checked_rows"]
            == 130
        )


def test_coefficient_change_does_not_define_coordinates(tmp_path):
    text, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    text = text.replace("r0: +1 v0 +1 v1 = 1", "r0: +2 v0 +1 v1 = 1")
    with build(tmp_path, text) as ir:
        t = ir.inspect("index_tensors")["items"][0]
        assert t["variables"] == 6 and t["complete_product"]
        assert ir.validate({"kind": "cartesian", "id": t["id"]})["valid"]
        # A sum-of-variables claim is not made for the perturbed axis.
        assert any("raw row expressions" in c["formula"] for c in t["constraints"])


def test_arbitrary_hypothesis_and_small_resume(tmp_path):
    text, _ = model([[5, 1, 0], [1, 5, 2]], groups=2)
    with build(tmp_path, text) as ir:
        op = ir.inspect("index_operators")["items"][0]
        tensor = ir.inspect("index_tensors")["items"][0]
        candidate = {
            "operator": op,
            "coordinates": ir.query(tensor["coordinates"])["items"],
            "rows": ir.query(op["bindings"])["items"],
            "kernel": ir.query(op["kernel"])["items"],
        }
        assert ir.validate({"kind": "indexed_operator", "candidate": candidate})[
            "valid"
        ]
        candidate["kernel"].append({"p": 999, "k": None, "j": 0, "value": "1"})
        assert not ir.validate({"kind": "indexed_operator", "candidate": candidate})[
            "valid"
        ]
        scope = {"rows": {"op": "scan", "table": "raw_rows"}, "variable_kind": "B"}
        for i in range(300):
            out = ir.analyze("operators", scope=scope, budget={"candidates": 10})
            if out["completeness"] == "complete":
                break
        else:
            pytest.fail("small-budget analysis stalled")
        assert out["items"][0]["kernel_statistics"] == op["kernel_statistics"]


def test_projection_is_not_an_unconditional_sum_constraint(tmp_path):
    text = "Minimize\n obj: y\nSubject To\n a: y+x+z=1\n b: y+u+v=1\nBinaries\n x z u v\nEnd\n"
    with build(tmp_path, text) as ir:
        answer = ir.analyze("incidence", scope={"variable_kind": "B"})
        parts = answer["items"]
        assert len(parts) == 1 and parts[0]["partition"]
        sets = ir.query({"table": "index_sets", "analysis": answer["analysis"]})[
            "items"
        ]
        assert all(s["projected_support"] and "lower" not in s for s in sets)


def test_readable_indexed_summary_preserves_payload(tmp_path):
    text, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    with build(tmp_path, text) as ir:
        md = ir.render()
        assert "变量张量" in md
        assert "共享一个算子" in md
        assert "| 性质 | 结果 |" in md
        assert "<details>" in md
        assert "- **indexed_views**:" not in md
        assert render_document(ir.describe()) == md
        assert len(md.encode()) <= 20000
