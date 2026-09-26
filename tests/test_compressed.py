"""Exact identities and the single-source JSON/Markdown document contract."""

import json
import sqlite3

import pytest

from lpir import IR, LPIRError
from lpir.compressed import render_document, verify


def families(ir):
    return ir.inspect("explanation", limit=100)["items"]


def test_mathematics_persisted_and_render_is_lossless(make_v2):
    ir = make_v2("""Minimize
 obj: -x-y
Subject To
 alias: x-y=0
 difference: -2u+2v-6z=0
 gate: q-100b<=0
 complement: q+100b<=100
 scaled: -3q+300b>=0
 balance: x+y-b=0
Bounds
 u free
 v free
Binaries
 x y b
Generals
 z
End
""")
    cards = families(ir)
    kinds = {f["kind"] for f in cards}
    assert {
        "variable_equality",
        "integer_controlled_difference",
        "activation_bound",
        "complemented_activation_bound",
        "binary_balance",
    } <= kinds
    assert next(f for f in cards if f["kind"] == "activation_bound")["rows"] == 2
    assert (
        "3"
        in next(f for f in cards if f["kind"] == "integer_controlled_difference")[
            "formula"
        ]
    )
    assert ir.verify()["explanations"]["expanded_rows"] == 6
    doc = ir.describe()
    assert render_document(doc) == ir.render()
    assert render_document(json.loads(json.dumps(doc))) == ir.render()
    with IR(ir.model.path) as reopened:
        assert reopened.describe() == doc
    for r in range(6):
        assert ir.validate({"kind": "explanation", "row": r})["valid"]
    wrong = {"op": "eq", "left": {"variable": "value"}, "right": {"constant": "1"}}
    result = ir.validate({"kind": "explanation", "row": 2, "ast": wrong})
    assert result["verification"] == "refuted" and result["counterexample"]["row"] == 2
    rows = ir.query({"op": "scan", "table": "explanation_rows"})
    assert rows["total"] == 6


def test_variable_length_and_exact_exception(make_v2):
    lines = [f"r{i}: u{i}-v{i}-7z=0" for i in range(21)]
    lines += ["exception: ue-ve-7z=0.000000001", "s1: b+c=1", "s2: b+c+d=1"]
    ir = make_v2(
        "Minimize\n obj: z\nSubject To\n"
        + "\n".join(lines)
        + "\nBinaries\n b c d\nGenerals\n z\nEnd\n"
    )
    cards = families(ir)
    diff = next(f for f in cards if f["kind"] == "integer_controlled_difference")
    assert diff["rows"] == 22
    d = next(p for p in diff["parameters"] if p["name"] == "d")
    assert d["usual"] == "0" and d["exception_rows"] == 1
    assert d["exceptions"] == [{"row": 21, "value": "1/1000000000"}]
    assert "d[r]" in diff["formula"]
    ones = next(f for f in cards if f["kind"] == "exactly_one")
    assert ones["rows"] == 2
    assert ones["roles"][0]["min_count"] == 2 and ones["roles"][0]["max_count"] == 3
    assert ir.verify()["status"] == "PASS"
    predicate = {"op": "ne", "args": [{"field": "value"}, {"literal": "0"}]}
    query = {
        "op": "filter",
        "input": {"op": "scan", "table": "explanation_parameters"},
        "where": {
            "op": "and",
            "args": [
                {"op": "eq", "args": [{"field": "name"}, {"literal": "d"}]},
                predicate,
            ],
        },
    }
    assert any(x["value"] == "1/1000000000" for x in ir.query(query)["items"])


def test_perturbations_and_pagination(make_v2):
    ir = make_v2("""Minimize
 obj: x
Subject To
 a: x-y=0
 b: 1.0000000001x-y=0
 c: x-y<=0
 d: x+y<=7
End
""")
    cards = families(ir)
    assert next(f for f in cards if f["kind"] == "variable_equality")["rows"] == 1
    collected = []
    offset = 0
    while True:
        doc = ir.describe(offset=offset, limit=1)
        assert render_document(doc) == ir.render(offset=offset, limit=1)
        collected += [f["id"] for f in doc["families"]]
        if not doc["page"]["truncated"]:
            break
        offset = doc["page"]["next_offset"]
    assert len(set(collected)) == len(cards)
    with pytest.raises(LPIRError):
        ir.describe(max_bytes=50)
    assert ir.verify()["status"] == "PASS"


def test_residual_explicit_and_verified(make_v2):
    terms = "+".join(f"{i + 1}x{i}" for i in range(12))
    ir = make_v2(f"Minimize\n obj: 0\nSubject To\n r: {terms}<=-123.25\nEnd\n")
    residual = ir.describe()["large_or_unstructured_rows"][0]
    assert residual["upper"] == "-493/4" and residual["positive"] == 12
    assert residual["types"] == {"C": 12}
    assert ir.verify()["explanations"]["numerical_reference_rows"] == 1
    assert ir.validate({"kind": "explanation", "row": 0})["valid"]
    with sqlite3.connect(ir.model.path / "explanations.sqlite") as db:
        db.execute("UPDATE residuals SET positive=11")
    with pytest.raises(LPIRError, match="statistics mismatch"):
        verify(ir.model, check_manifest=False)


def test_aliases_sum_signs_and_empty_rows(make_v2):
    ir = make_v2("""Maximize
 obj: b+c
Subject To
 a: -b-c=-1
 b: -b-c<=-1
 c: -b-c>=-1
 d: b+c-d=0
 e: 0 b <= 0
Binaries
 b c d
End
""")
    assert {"exactly_one", "at_least_one", "at_most_one", "binary_balance"} <= {
        f["kind"] for f in families(ir)
    }
    assert ir.verify()["explanations"]["expanded_rows"] == 5


def test_document_handles_are_executable(make_v2):
    ir = make_v2("Minimize\n obj: x\nSubject To\n r: x-y=0\nEnd\n")
    family = ir.describe()["families"][0]
    ast = ir.query(family["ast_handle"])["items"][0]
    assert ast["formula"] == family["formula"]
    assert ir.query(family["instances_handle"])["total"] == 1
    assert ir.query(family["bindings_handle"])["total"] == 2
    assert ir.query(family["parameters"][0]["handle"])["items"][0]["value"] == "0"


def test_compiler_resume_all_phases(make_v2):
    from lpir import IR

    ir = make_v2("Minimize\n obj: x+y+z\nSubject To\n a: x-y=0\n b: x+y+z<=3\nEnd\n")
    expected = ir.describe()
    path = ir.model.path
    ir.close()
    (path / "explanations.json").unlink()
    (path / "explanations.sqlite").unlink()
    phases = set()
    for _ in range(30):
        with IR(path) as reopened:
            out = reopened.analyze("explanations", budget={"candidates": 1})
            if out["completeness"] == "complete":
                assert reopened.describe() == expected
                assert reopened.verify()["explanations"]["expanded_rows"] == 2
                break
            phases.add(out["checked_range"]["phase"])
            assert out["total"] is None
            with pytest.raises(LPIRError, match="No explanation IR"):
                reopened.describe()
    else:
        pytest.fail("Compiler did not resume to completion")
    assert phases == {"rows", "families", "objective", "verify"}


def test_human_markdown_has_no_machine_payload(make_v2):
    ir = make_v2("Minimize\n obj: x\nSubject To\n c: x+y=1\nBinaries\n x y\nEnd\n")
    doc = ir.describe()
    text = ir.render()
    assert "```json" not in text
    assert "<summary>完整 indexed IR" not in text
    assert "- **indexed_views**:" not in text
    assert '"model_version"' not in text
    assert "formula1" in text
    assert render_document(doc) == text
