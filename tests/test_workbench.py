import pytest
from lpir import LPIRError


@pytest.fixture
def model(make_ir):
    return make_ir(
        "Minimize\n obj: x+y+z\nSubject To\n a: x+2 y<=3\n b: 1 x+2 y<=4\n c: x+3 y<=3\n d: x+2 z<=3\nEnd\n"
    )


def comparison(**kw):
    return dict(
        op="compare_rows",
        start=0,
        stop=1,
        left={"offset": 0, "stride": 0},
        right={"offset": 1, "stride": 1},
        **kw,
    )


def test_modes_and_resume(model):
    spec = comparison()
    page = model.query(spec, budget={"candidates": 1})
    assert page["completeness"] == "partial"
    assert page["summary"]["valid"] is None
    for _ in range(10):
        page = model.query(spec, budget={"candidates": 1})
        if page["completeness"] == "complete":
            break
    assert page["summary"]["valid"] is True
    assert page["items"][0]["bounds_equal"] is False
    assert (
        model.validate({**spec, "kind": "row_pairs", "mode": "constraint"})["valid"]
        is False
    )
    spec["right"]["offset"] = 2
    assert model.query({**spec, "mode": "support"})["summary"]["valid"] is True
    assert (
        model.query(spec)["summary"]["counterexample"]["first_difference"]["right"]
        == "3"
    )
    spec["right"]["offset"] = 3
    assert model.query({**spec, "mode": "support"})["summary"]["valid"] is False


def test_pairs_scope_and_empty(model):
    spec = comparison()
    assert model.query({**spec, "op": "row_pairs", "stop": 3})["items"][-1] == {
        "j": 2,
        "left_row": 0,
        "right_row": 3,
    }
    with pytest.raises(LPIRError, match="outside"):
        model.query({**spec, "stop": 4})
    empty = model.query({**spec, "stop": 0})
    assert empty["summary"]["empty_scope"]
    assert "没有检查" in model.preview(empty)


def test_nested_aggregate_sort_and_paging(model):
    source = {
        "op": "project",
        "input": {"op": "scan", "table": "raw_rows"},
        "fields": {"nested": {"literal": {"n": "1/3"}}, "row": {"field": "row_id"}},
    }
    stats = model.query(
        {
            "op": "aggregate",
            "input": source,
            "metrics": {
                "sum": {"op": "sum", "field": {"path": ["nested", "n"]}},
                "unique": {"op": "count_distinct", "field": {"path": ["nested", "n"]}},
            },
        }
    )
    assert stats["items"] == [{"sum": "4/3", "unique": 1}]
    spec = {
        "op": "sort",
        "input": source,
        "by": [{"expr": {"field": "row"}, "direction": "desc"}],
    }
    page = model.query(spec, limit=2)
    assert [r["row"] for r in page["items"]] == [3, 2]
    assert [
        r["row"] for r in model.query(handle=page["handle"], offset=2)["items"]
    ] == [1, 0]
    assert len(model.inspect("raw_rows")["items"]) == 4
    assert len(model.inspect("variables")["items"]) == 3


def test_comparison_aggregation_and_preview(model):
    spec = {**comparison(), "stop": 3}
    page = model.query(
        {
            "op": "aggregate",
            "input": spec,
            "group_by": ["equal"],
            "metrics": {"count": {"op": "count"}},
        }
    )
    assert {i["equal"]: i["count"] for i in page["items"]} == {True: 1, False: 2}
    page = model.query(spec, limit=1)
    text = model.preview(page)
    assert "发现反例" in text and "3 / 3" in text and "证据句柄" in text


def test_signatures_are_filters_not_proofs(model):
    spec = {"op": "row_signatures", "start": 0, "stop": 4}
    page = model.query(spec, budget={"candidates": 1})
    for _ in range(20):
        if page["completeness"] == "complete":
            break
        page = model.query(spec, budget={"candidates": 1})
    assert page["completeness"] == "complete"
    a, b, c, d = page["items"]
    assert a["signature"] == b["signature"]
    assert a["signature"] != c["signature"] != d["signature"]
    assert all(x["proof"] is False for x in page["items"])
    constrained = model.query({**spec, "mode": "constraint"})["items"]
    assert constrained[0]["signature"] != constrained[1]["signature"]
    grouped = model.query(
        {
            "op": "aggregate",
            "input": spec,
            "group_by": ["signature"],
            "metrics": {"n": {"op": "count"}},
        }
    )
    assert sorted(x["n"] for x in grouped["items"]) == [1, 1, 2]


def test_exact_sort_and_missing_paths(model):
    source = {
        "op": "project",
        "input": {"op": "scan", "table": "raw_rows"},
        "fields": {
            "n": {"op": "sub", "args": [{"literal": "1/3"}, {"field": "row_id"}]}
        },
    }
    page = model.query(
        {"op": "sort", "input": source, "by": [{"expr": {"field": "n"}}]}
    )
    assert [x["n"] for x in page["items"]] == ["-8/3", "-5/3", "-2/3", "1/3"]
    with pytest.raises(LPIRError, match="Missing field path"):
        model.query(
            {
                "op": "project",
                "input": source,
                "fields": {"n": {"path": ["missing", "n"]}},
            }
        )


def test_zero_discovery_status_and_cli_preview(model, capsys):
    from lpir.cli import main
    from lpir.markdown import render

    doc = model.describe()
    assert "structure_views" in doc
    assert "family_count" in doc["structure_views"]
    empty = {
        **doc["structure_views"],
        "family_count": 0,
        "families": [],
        "search_completeness": "complete",
    }
    text = render({**doc, "structure_views": empty})
    assert "这不代表模型没有结构" in text
    main(
        [
            "query",
            str(model.model.path),
            "--spec",
            __import__("json").dumps(comparison()),
            "--preview",
        ]
    )
    assert "全部相等" in capsys.readouterr().out


def test_sort_resume_stable_and_truncated_discovery(model):
    from lpir.markdown import render

    spec = {
        "op": "sort",
        "input": {"op": "scan", "table": "raw_rows"},
        "by": [{"expr": {"field": "row_id"}, "direction": "desc"}],
    }
    for _ in range(5):
        page = model.query(spec, budget={"candidates": 1})
        if page["completeness"] == "complete":
            break
    assert page["completeness"] == "complete"
    assert [x["row_id"] for x in page["items"]] == [3, 2, 1, 0]
    doc = model.describe()
    # Card truncation is not a zero-discovery result.
    structure = {**doc["structure_views"], "family_count": 1, "families": []}
    assert "未发现可接受" not in render({**doc, "structure_views": structure})
