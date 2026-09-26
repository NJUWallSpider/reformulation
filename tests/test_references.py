import re

from lpir import IR
from lpir.compressed import render_document
from lpir.references import translate
from scripts.indexed_instances import model


def test_short_references_are_stable_queryable_and_reversible(tmp_path):
    text, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    lp = tmp_path / "model.lp"
    lp.write_text(text)
    path = tmp_path / "ir"
    with IR.build(lp, path) as ir:
        doc = ir.describe()
        assert doc["model_version"] == "model1"
        tensor = doc["indexed_views"]["tensors"][0]
        op = doc["indexed_views"]["operators"][0]
        assert tensor["id"] == "X1"
        assert op["id"] == "operator1"
        assert not re.search(r"[0-9a-f]{20,}", ir.render())
        assert render_document(doc) == ir.render()
        assert ir.query(op["kernel"])["total"] > 0
        assert ir.inspect("index_operators", "operator1")["total"] == 1
        assert ir.validate({"kind": "indexed_operator", "id": "operator1"})["valid"]
        assert ir.validate({"kind": "cartesian", "id": "X1"})["valid"]
        family = doc["families"][0]
        assert family["id"].startswith("formula")
        assert ir.inspect("explanation", family["id"])["items"]
        query = {
            "op": "filter",
            "input": {"op": "scan", "table": "index_kernel"},
            "where": {"op": "eq", "args": [{"field": "id"}, {"literal": "operator1"}]},
        }
        assert ir.query(query)["total"] == ir.query(op["kernel"])["total"]
        decoded = translate(ir.model, doc, decode=True)
        assert decoded["model_version"] == ir.model.manifest["model_version"]
        assert decoded["indexed_views"] == ir.describe(raw_ids=True)["indexed_views"]
        assert translate(
            ir.model, {"name": "X1", "value": "operator1"}, decode=True
        ) == {"name": "X1", "value": "operator1"}
        # Pagination neither determines nor changes already issued references.
        ir.describe(offset=1, limit=1)
        assert ir.describe() == doc
    with IR(path) as ir:
        assert ir.describe() == doc
        assert ir.verify()["status"] == "PASS"
