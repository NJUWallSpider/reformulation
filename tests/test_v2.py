from fractions import Fraction as F
from pathlib import Path
import json, sqlite3, subprocess, sys, os
import pytest
from lpir import IR, LPIRError

LP = """Minimize
 cost: -x-y-z + 3.25
Subject To
 small: x+y=1
 large: x+y+z=1
 same: 2x+2y=2
 graph: 7x+y+z<=8
Bounds
Binaries
 x y z
End
"""


def test_v2_families_queries_proofs(make_v2, tmp_path):
    ir = make_v2(LP)
    o = ir.overview()
    assert o["schema_version"] == 2
    fam = next(f for f in o["families"] if f["kind"] == "set_partitioning")
    assert fam["rows"] == 3
    assert ir.inspect("family", fam["id"])["items"][0]["roles"][0]["coefficient"] == "1"
    assert ir.verify()["views_verified"] == 4
    subsets = ir.analyze("subsets")
    assert subsets["total"] == 1 and subsets["summary"]["variables"] == 1
    dup = ir.analyze("proportional_rows")
    assert dup["total"] == 1
    assert ir.validate({"kind": "formula", "row": 3})["valid"]
    assert ir.validate({"kind": "subset", "left": 0, "right": 1})["valid"]
    assert (
        ir.validate({"kind": "clique", "variables": [0, 1]})["verification"]
        == "refuted"
    )
    p = tmp_path / "out.lp"
    ir.export(p)
    other = IR.build(p, tmp_path / "again")
    assert other.verify()["status"] == "PASS"
    assert other.model.row(0) == ir.model.row(0)
    assert len(ir.render().encode()) <= 20000 and "center" in ir.render()


def test_relational_ast(make_v2):
    ir = make_v2(LP)
    scan = {"op": "scan", "table": "rows"}
    pred = {"op": "eq", "args": [{"field": "class"}, {"literal": "set_partitioning"}]}
    filt = {"op": "filter", "input": scan, "where": pred}
    grouped = {
        "op": "aggregate",
        "input": filt,
        "group_by": ["class"],
        "metrics": {"n": {"op": "count"}, "nnz": {"op": "sum", "field": "nnz"}},
    }
    assert ir.query(grouped)["items"] == [
        {"class": "set_partitioning", "n": 3, "nnz": "7"}
    ]
    proj = {
        "op": "project",
        "input": filt,
        "fields": {
            "row": {"field": "row_id"},
            "delta": {"op": "sub", "args": [{"field": "upper"}, {"literal": "0.1"}]},
        },
    }
    assert ir.query(proj)["items"][0]["delta"] == "9/10"
    join = {
        "op": "join",
        "left": scan,
        "right": {"op": "scan", "table": "roles"},
        "left_on": ["row_id"],
        "right_on": ["row_id"],
    }
    assert ir.query(join)["total"] == 5
    assert ir.query({"op": "difference", "left": scan, "right": filt})["total"] == 1
    result = ir.query(scan, limit=1)
    assert (
        ir.query(handle=result["handle"], offset=1, limit=1)["items"][0]["row_id"] == 1
    )
    with pytest.raises(LPIRError):
        ir.query({"op": "scan", "table": "rows; DROP TABLE rows"})
    with pytest.raises(LPIRError):
        ir.query({"op": "filter", "input": scan, "where": {"op": "eval", "args": []}})


def test_partial_resume_and_metadata_paging(make_v2):
    ir = make_v2(LP)
    r = ir.analyze("duplicates", budget={"candidates": 1})
    assert r["completeness"] == "partial" and r["total"] is None and r["continuation"]
    s = ir.analyze("duplicates", handle=r["handle"])
    assert s["completeness"] == "complete"
    assert not isinstance(ir.model.variables, list) and not isinstance(
        ir.model.var_names, dict
    )
    q = ir.query({"op": "scan", "table": "rows"}, budget={"candidates": 1})
    assert q["materialized_count"] == 1 and q["total"] is None
    q = ir.query({"op": "scan", "table": "rows"})
    assert q["total"] == 4
    r = ir.analyze("components", budget={"memory_mb": 0.00001})
    assert r["completeness"] == "partial"


def test_corruption_checked_without_asserts(make_v2):
    ir = make_v2(LP)
    db = sqlite3.connect(ir.model.path / "analysis.sqlite")
    db.execute("UPDATE roles SET coefficient='2' WHERE row_id=0")
    db.commit()
    db.close()
    with pytest.raises(LPIRError, match="mismatch"):
        ir.verify()


def test_formula_ast_corruption_and_nonlinearity(make_v2):
    ir = make_v2(LP)
    fid = ir.overview()["families"][0]["id"]
    db = sqlite3.connect(ir.model.path / "analysis.sqlite")
    ast = json.loads(
        db.execute("SELECT ast FROM families WHERE id=?", (fid,)).fetchone()[0]
    )
    ast["expression"] = {"constant": "0"}
    db.execute("UPDATE families SET ast=? WHERE id=?", (json.dumps(ast), fid))
    db.commit()
    db.close()
    with pytest.raises(LPIRError, match="mismatch"):
        ir.verify()


def test_normalization_complement_and_counterexample(make_v2):
    ir = make_v2("""Minimize
 o: x+y
Subject To
 a: 2x-2y=0
 b: -3x+3y=0
 c: 2x-2y=0.00000000001
Binaries
 x y
End
""")
    assert ir.verify()["views_verified"] == 3
    assert ir.overview()["classes"]["set_partitioning"] == 2
    assert ir.analyze("proportional_rows")["total"] == 1
    q = ir.query({"op": "scan", "table": "bindings"})
    assert any(x["complement"] for x in q["items"])


def test_v2_streaming_syntax(make_v2):
    # Re-run the complete parser compatibility suite against the new streaming front end.
    import test_parser

    for fn in [
        test_parser.test_defaults_cancellation_and_zero_row,
        test_parser.test_empty_constraint_set_and_bound_only_variable,
        test_parser.test_multiline_rhs_and_reverse_range,
        test_parser.test_infinite_bound_orientations,
    ]:
        fn(make_v2)


def test_long_physical_line_and_objective(make_v2):
    terms = " + ".join("x" for _ in range(25000))
    ir = make_v2(
        f"Minimize\n o: {terms}\nSubject To\n r: {terms} = 25000\nBinaries\n x\nEnd\n"
    )
    assert (
        ir.model.row(0) == {0: F(25000)}
        and ir.model.num(ir.model.variables[0][5]) == 25000
    )
    assert ir.verify()["status"] == "PASS"


def test_components_wl_and_lp(make_v2):
    ir = make_v2(LP)
    assert ir.analyze("components")["summary"]["blocks"] == 1
    assert ir.analyze("wl")["summary"]["symmetry_certificate"] is False
    res = ir.probe()
    assert res["exactness"] == "numerical" and res["summary"][
        "objective"
    ] == pytest.approx(2.25)
    assert ir.query(handle=res["handle"])["exactness"] == "numerical"
    assert ir.probe(memory_mb=0.00001)["completeness"] == "not_run"


def test_old_numerical_schema_is_rejected(tmp_path):
    p = tmp_path / 'manifest.json'
    p.write_text(json.dumps({'schema_version': 1}))
    with pytest.raises(LPIRError, match='Expected IR v2'):
        IR(tmp_path)


def test_cli_json_ast_and_optimized_checker(make_v2):
    ir = make_v2(LP)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    r = subprocess.run(
        [sys.executable, "-O", "-m", "lpir", "verify", str(ir.model.path)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(r.stdout)["status"] == "PASS"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "lpir",
            "query",
            str(ir.model.path),
            "--spec",
            json.dumps({"op": "scan", "table": "rows"}),
            "--limit",
            "1",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(r.stdout)["total"] == 4


def test_terms_index_and_empty_aggregate(make_v2):
    ir = make_v2(LP)
    terms = ir.query({"op": "scan", "table": "terms"}, limit=1000)
    assert terms["total"] == 10
    assert next(x for x in terms["items"] if x["row_id"] == 2)["coefficient"] == "2"
    empty = {
        "op": "filter",
        "input": {"op": "scan", "table": "rows"},
        "where": {"op": "lt", "args": [{"field": "row_id"}, {"literal": 0}]},
    }
    q = ir.query(
        {
            "op": "aggregate",
            "input": empty,
            "metrics": {"n": {"op": "count"}, "nnz": {"op": "sum", "field": "nnz"}},
        }
    )
    assert q["items"] == [{"n": 0, "nnz": "0"}]


def test_all_complemented_literals(make_v2):
    ir = make_v2(
        "Minimize\n o: a+b+c\nSubject To\n r: -2a-2b-2c=-4\nBinaries\n a b c\nEnd\n"
    )
    assert (
        ir.overview()["classes"]["set_partitioning"] == 1
        and ir.verify()["status"] == "PASS"
    )


def test_validate_joined_exact_relation(make_v2):
    ir = make_v2(LP)
    relation = {
        "op": "join",
        "left": {"op": "scan", "table": "rows"},
        "right": {"op": "scan", "table": "roles"},
        "left_on": ["row_id"],
        "right_on": ["row_id"],
    }
    r = ir.validate(
        {
            "kind": "relation",
            "query": relation,
            "predicate": {
                "op": "eq",
                "args": [{"field": "left.row_id"}, {"field": "right.row_id"}],
            },
        }
    )
    assert r["valid"] and r["checked"] == 5
    r = ir.validate(
        {"kind": "clique", "variables": [0, 1, 2]}, budget={"candidates": 1}
    )
    assert r["verification"] in ("refuted", "candidate")


def test_external_materialized_and_objective_coupled(make_v2):
    ir = make_v2(LP)
    family = next(
        f["id"] for f in ir.overview()["families"] if f["kind"] == "set_partitioning"
    )
    r = ir.query("external", family)
    assert r["total"] == 1 and r["objective_variable_count"] == 3
    assert ir.query(handle=r["handle"])["items"] == r["items"]


def test_zero_objective_copy_generator(make_v2, tmp_path):
    from lpir.generate import generate

    ir = make_v2("Minimize\n o: 0\nSubject To\n r: x <= 1\nEnd\n")
    p = tmp_path / "zero.lp"
    generate(ir.model, p, copies=2)
    other = IR.build(p, tmp_path / "zero_ir")
    assert other.overview()["objective_nonzeros"] == 0


def test_discovery_abstains_on_large_single_row_but_retains_all_terms(make_v2):
    names = [f"x{i}" for i in range(50001)]
    ir = make_v2("Minimize\n o: 0\nSubject To\n r: " + "+".join(names) + "<=1\nEnd\n")
    assert ir.overview()["unclassified_working_set_rows"] == 1
    assert ir.inspect("row", 0, offset=50000, limit=1)["total"] == 50001
    assert ir.verify()["rows_verified"] == 1 and ir.verify()["views_verified"] == 0


def test_probe_constants_and_float_overflow(make_v2):
    pytest.importorskip("scipy")
    ir = make_v2("Minimize\n obj: 7\nSubject To\n c: 0 <= 1\nEnd\n")
    assert ir.probe()["summary"]["objective"] == 7
    ir = make_v2("Minimize\n obj: 0\nSubject To\n c: 0 >= 1\nEnd\n")
    assert ir.probe()["summary"]["solver_status"] == 2
    ir = make_v2("Minimize\n obj: 1e500 x\nSubject To\nEnd\n")
    with pytest.raises(LPIRError, match="finite float64"):
        ir.probe()


def test_excessive_literal_precision_is_located(make_v2):
    with pytest.raises(LPIRError, match="[Ll]ine|precision|number"):
        make_v2("Minimize\n obj: 1e999999999 x\nEnd\n")


def test_proportional_columns_use_exact_matrix(make_v2):
    ir = make_v2(
        "Minimize\n obj: x+5y+z\nSubject To\n a: x-2y+z<=2\n b: 3x-6y+4z<=9\nEnd\n"
    )
    result = ir.analyze("proportional_columns")
    assert result["total"] == 1
    assert result["items"][0]["scale"] == "-2"
    assert list(ir.model.iter_column(1)) == [(0, F(-2)), (1, F(-6))]
    assert ir.analyze("duplicate_columns")["total"] == 0


def test_positive_clique_bound_and_budget(make_v2):
    ir = make_v2(
        "Minimize\n obj: -x-y-z\nSubject To\n a: 7x+y+z<=8\n b: x+7y+z<=8\n c: x+y+7z<=8\nBinaries\n x y z\nEnd\n"
    )
    spec = {"kind": "clique", "variables": [0, 1, 2], "cardinality_bound": 2}
    assert ir.validate(spec)["valid"]
    assert len(ir.validate(spec)["evidence"]["source_rows"]) == 3
    partial = ir.validate(spec, budget={"candidates": 2})
    assert partial["completeness"] == "partial" and partial["valid"] is None


def test_render_many_roles_stays_bounded(make_v2):
    terms = "+".join(f"{i + 1} x{i}" for i in range(150))
    ir = make_v2(f"Minimize\n obj: {terms}\nSubject To\n many: {terms}<=200\nEnd\n")
    text = ir.render()
    assert len(text.encode()) <= 20000 and "numerical_row_reference" in text
    assert ir.describe()["large_or_unstructured_rows"][0]["terms"] == 150
    ir.analyze("duplicates")
    assert ir.inspect("analysis")["total"] == 1


def test_complement_aliases_are_not_assignments(make_v2):
    ir = make_v2(
        "Minimize\n obj: x+y+z\nSubject To\n a: x-y=0\n b: x-z=0\nBinaries\n x y z\nEnd\n"
    )
    result = ir.analyze("assignments")
    assert result["total"] == 0
    assert result["summary"]["excluded_complemented_rows"] == 2
    assert ir.verify()["status"] == "PASS"


def test_obsolete_assignment_cache_is_rejected_and_recomputed(make_v2):
    ir = make_v2(
        "Minimize\n obj: x+y+z\nSubject To\n a: x-y=0\n b: x-z=0\nBinaries\n x y z\nEnd\n"
    )
    import hashlib

    m = ir.model
    spec = {
        "kind": "assignments",
        "scope": None,
        "version": "2.0",
        "model": m.manifest["model_version"],
    }
    handle = (
        "a_"
        + hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:32]
    )
    db = sqlite3.connect(m.path / "analysis.sqlite")
    db.execute(
        "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
        (handle, "assignments", json.dumps(spec), "complete", 2, 2, "{}"),
    )
    db.execute(
        "INSERT INTO results VALUES(?,?,?)",
        (handle, 0, json.dumps({"id": "assignment_0", "shape": [1, 1]})),
    )
    db.execute(
        "INSERT INTO assignment_members VALUES(?,?,?,?)", ("assignment_0", 0, 0, 0)
    )
    db.commit()
    db.close()
    with pytest.raises(LPIRError, match="Obsolete"):
        ir.query(handle=handle)
    with pytest.raises(LPIRError, match="Obsolete"):
        ir.query({"op": "scan", "table": "assignment_members"})
    with pytest.raises(LPIRError, match="Obsolete"):
        ir.inspect("assignment", "assignment_0")
    assert "Verified assignment incidence" not in ir.render()
    assert ir.analyze("assignments")["total"] == 0
    assert ir.query({"op": "scan", "table": "assignment_members"})["total"] == 0
    with pytest.raises(LPIRError, match="Obsolete"):
        ir.query(handle=handle)


def test_assignment_relation_requires_completed_discovery(make_v2):
    ir = make_v2(LP)
    with pytest.raises(LPIRError, match="has not run"):
        ir.query({"op": "scan", "table": "assignment_members"})
    ir.analyze("assignments", budget={"candidates": 1})
    with pytest.raises(LPIRError, match="incomplete"):
        ir.query({"op": "scan", "table": "assignment_members"})
