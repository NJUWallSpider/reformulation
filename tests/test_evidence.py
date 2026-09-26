import sqlite3

import pytest

from lpir import LPIRError


def result(page):
    return {"op": "result", "handle": page["handle"]}


def aggregate(source, metrics, groups=None):
    return {
        "op": "aggregate",
        "input": source,
        "group_by": groups or [],
        "metrics": metrics,
    }


def test_reusable_results_distinct_and_semijoins(make_v2):
    ir = make_v2(
        "Minimize\n obj: x+y+z\nSubject To\n a: x+y<=0\n b: x-z=0\nBinaries\n x y z\nEnd\n"
    )
    terms = ir.query({"op": "terms", "rows": [0, 1]})
    stats = ir.query(
        aggregate(
            result(terms),
            {
                "n": {"op": "count"},
                "unique": {"op": "count_distinct", "field": "variable"},
                "cost": {"op": "sum", "field": "coefficient"},
            },
        )
    )
    assert stats["items"] == [{"n": 4, "unique": 3, "cost": "2"}]
    byrow = ir.query(
        aggregate(
            result(terms),
            {"n": {"op": "count_distinct", "field": "variable"}},
            ["row_id"],
        )
    )
    assert byrow["items"] == [{"row_id": 0, "n": 2}, {"row_id": 1, "n": 2}]
    support = ir.query(
        {"op": "distinct", "input": result(terms), "fields": ["variable"]}
    )
    assert support["total"] == 3
    zero = ir.query({"op": "terms", "rows": [0]})
    semi = {
        "op": "semi_join",
        "left": result(support),
        "right": result(zero),
        "left_on": ["variable"],
        "right_on": ["variable"],
    }
    assert ir.query(semi)["total"] == 2
    assert ir.query({**semi, "op": "anti_join"})["total"] == 1
    assert ir.query(
        {
            "op": "difference",
            "left": result(support),
            "right": {"op": "distinct", "input": result(zero), "fields": ["variable"]},
        }
    )["items"] == [{"variable": 2}]
    with sqlite3.connect(ir.model.path / "analysis.sqlite") as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='numerical_terms'"
        ).fetchone()


def test_activity_exact_infinite_and_empty_box(make_v2):
    ir = make_v2("""Minimize
 obj: -x+2y
Subject To
 a: 2x-3y<=7
 b: z=0
 c: t<=0
Bounds
 -2<=x<=4
 1<=y<=5
 z free
 t>=3
 t<=2
End
""")
    rows = ir.query({"op": "row_activity", "rows": [0, 1, 2]})["items"]
    assert (rows[0]["minimum"], rows[0]["maximum"]) == ("-19", "5")
    assert rows[0]["objective_signs"] == {"negative": 1, "positive": 1, "zero": 0}
    assert rows[1]["minimum"] is None and rows[1]["maximum"] is None
    assert rows[2]["empty_box"] and rows[2]["minimum"] is None
    assert rows[0]["view_layer"] == "raw"


def test_fact_resume_mid_row_and_partial_inputs_rejected(make_v2):
    ir = make_v2("Minimize\n obj: x+y+z\nSubject To\n a: x+y+z<=0\nEnd\n")
    spec = {"op": "terms", "rows": [0]}
    first = ir.query(spec, budget={"candidates": 2})
    assert first["total"] is None and first["materialized_count"] == 1
    with pytest.raises(LPIRError, match="Incomplete"):
        ir.query(aggregate(result(first), {"n": {"op": "count"}}))
    last = ir.query(spec, budget={"candidates": 2})
    assert last["completeness"] == "partial"
    final = ir.query(spec, budget={"candidates": 2})
    assert final["total"] == 3 and len({x["variable"] for x in final["items"]}) == 3
    a = {"op": "row_activity", "rows": [0]}
    for _ in range(5):
        page = ir.query(a, budget={"candidates": 2})
        if page["completeness"] == "complete":
            break
    assert page["items"][0]["minimum"] == "0" and page["items"][0]["maximum"] is None


def test_graph_caller_edges_duplicates_isolates_and_resume(make_v2):
    ir = make_v2(
        "Minimize\n obj: a+b+c+d\nSubject To\n r: a-b=0\n s: b-c=0\n t: a-b=0\nEnd\n"
    )
    # Construct the input entirely with query roles, not variable names.
    bindings = ir.query({"op": "scan", "table": "explanation_bindings"})

    def side(role):
        return {
            "op": "filter",
            "input": result(bindings),
            "where": {"op": "eq", "args": [{"field": "role"}, {"literal": role}]},
        }

    edge_ast = {
        "op": "project",
        "input": {
            "op": "join",
            "left": side("v"),
            "right": side("u"),
            "left_on": ["row_id"],
            "right_on": ["row_id"],
        },
        "fields": {
            "source": {"field": "left.variable"},
            "target": {"field": "right.variable"},
            "row_id": {"field": "left.row_id"},
        },
    }
    edges = ir.query(edge_ast)
    vertices = ir.query({"op": "scan", "table": "variables"})
    scope = {"edges": result(edges), "vertices": result(vertices)}
    for _ in range(20):
        page = ir.analyze("relation_components", scope=scope, budget={"candidates": 2})
        if page["completeness"] == "complete":
            break
    assert page["total"] == 4 and page["summary"]["components"] == 2
    sizes = ir.query(
        {"op": "result", "handle": page["summary"]["outputs"]["components"]}
    )
    assert sorted(x["vertices"] for x in sizes["items"]) == [1, 3]
    evidence = ir.query({"op": "result", "handle": page["summary"]["outputs"]["edges"]})
    assert evidence["total"] == 3 and {x["evidence"] for x in evidence["items"]} == {
        0,
        1,
        2,
    }


def test_original_normalized_explanation_views_and_selective_terms(make_v2):
    ir = make_v2("Minimize\n obj: x+y\nSubject To\n r: -2x+2y=0\nBinaries\n x y\nEnd\n")
    assert (
        ir.query({"op": "scan", "table": "raw_rows"})["items"][0]["view_layer"] == "raw"
    )
    normalized = ir.query({"op": "scan", "table": "normalized_rows"})["items"][0]
    assert normalized["view_layer"] == "normalized" and "shift" in normalized
    assert (
        ir.query({"op": "scan", "table": "explanation_rows"})["items"][0]["view_layer"]
        == "explanation"
    )
    page = ir.query(
        {
            "op": "filter",
            "input": {"op": "scan", "table": "terms"},
            "where": {"op": "eq", "args": [{"field": "row_id"}, {"literal": 0}]},
        }
    )
    assert page["total"] == 2
    cols = ir.query({"op": "column_incidence", "variables": [0]})
    assert cols["items"][0]["coefficient"] == "-2"


def test_nested_selected_terms_and_exact_distinct(make_v2):
    ir = make_v2("Minimize\n obj: x+y\nSubject To\n a: x=1\n b: y=2\nEnd\n")
    selected = {
        "op": "filter",
        "input": {"op": "scan", "table": "terms"},
        "where": {"op": "eq", "args": [{"field": "row_id"}, {"literal": 1}]},
    }
    q = ir.query(
        {"op": "project", "input": selected, "fields": {"v": {"field": "variable"}}}
    )
    assert q["items"] == [{"v": 1}]
    with sqlite3.connect(ir.model.path / "analysis.sqlite") as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='numerical_terms'"
        ).fetchone()

    def const(value):
        return {
            "op": "project",
            "input": result(q),
            "fields": {"value": {"literal": value}},
        }

    union = {"op": "union", "left": const("0.5"), "right": const("1/2")}
    counts = ir.query(
        aggregate(
            union,
            {
                "n": {"op": "count"},
                "distinct": {"op": "count_distinct", "field": "value"},
            },
        )
    )
    assert counts["items"] == [{"n": 2, "distinct": 1}]
    empty = {"op": "difference", "left": result(q), "right": result(q)}
    assert ir.query(aggregate(empty, {"n": {"op": "count_distinct", "field": "v"}}))[
        "items"
    ] == [{"n": 0}]


def test_sparse_scope_handles_and_budget_not_absence(make_v2):
    ir = make_v2("Minimize\n obj: x+y\nSubject To\n a: x+y<=0\nEnd\n")
    spec = {
        "op": "aggregate",
        "input": {"op": "terms", "rows": [0]},
        "metrics": {"n": {"op": "count"}},
    }
    first = ir.query(spec, budget={"candidates": 1})
    assert first["completeness"] == "partial" and first["total"] is None
    assert first["requested_spec"] == spec
    final = ir.query(spec, budget={"candidates": 20})
    assert final["items"] == [{"n": 2}]
    with pytest.raises(LPIRError, match="Unknown or foreign"):
        ir.query({"op": "result", "handle": "unknown"})
    with pytest.raises(LPIRError, match="Unsupported fields"):
        ir.query({"table": "terms", "row_id": 0, "ignored_filter": 42})
