"""Restricted relational query entry point and dependency staging."""

import json
from .numbers import LPIRError
from .semantic import connect
from .results import result_page
from .query_catalog import TABLES
from .query_sql import value as value
from .query_catalog import QUERY_VERSION as QUERY_VERSION


def run_query(m, spec, budget=None, offset=0, limit=20):
    from .evidence import FACT_OPS, fact_query, require_result

    if not isinstance(spec, dict):
        raise LPIRError("Query AST must be an object")
    if len(json.dumps(spec)) > 65536:
        raise LPIRError("Query AST exceeds 64KiB")

    def check_depth(node, depth=0):
        if depth > 48:
            raise LPIRError("Query AST is too deep")
        if isinstance(node, dict):
            for v in node.values():
                check_depth(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                check_depth(v, depth + 1)

    check_depth(spec)
    from .row_relations import OPS, query as row_query

    if spec.get("op") in OPS:
        return row_query(m, spec, budget, offset, limit)
    if spec.get("op") in FACT_OPS:
        return fact_query(m, spec, budget, offset, limit)
    if spec.get("op") == "result":
        if set(spec) != {"op", "handle"}:
            raise LPIRError("Invalid result relation")
        db = connect(m)
        try:
            require_result(m, db, spec["handle"])
            return result_page(m, db, spec["handle"], offset, limit)
        finally:
            db.close()
    # Push explicit single-row term selections to CSR before SQL/materialization.
    if spec.get("table") == "terms" and "row_id" in spec and "op" not in spec:
        if set(spec) != {"table", "row_id"}:
            raise LPIRError("Unsupported fields in row-term handle")
        return fact_query(
            m, {"op": "terms", "rows": [spec["row_id"]]}, budget, offset, limit
        )
    if spec.get("op") == "filter" and spec.get("input") == {
        "op": "scan",
        "table": "terms",
    }:
        pred = spec.get("where", {})
        if pred.get("op") == "eq":
            args = pred.get("args", [])
            for left, right in (args, args[::-1]) if len(args) == 2 else ():
                if (
                    left == {"field": "row_id"}
                    and isinstance(right, dict)
                    and set(right) == {"literal"}
                    and type(right["literal"]) is int
                ):
                    return fact_query(
                        m,
                        {"op": "terms", "rows": [right["literal"]]},
                        budget,
                        offset,
                        limit,
                    )

    # Nested evidence operators are staged once; consumers only see complete tables.
    def selected_row(predicate):
        if not isinstance(predicate, dict):
            return None
        args = predicate.get("args", [])
        if predicate.get("op") == "and":
            for arg in args:
                found = selected_row(arg)
                if found is not None:
                    return found
        if predicate.get("op") == "eq" and len(args) == 2:
            for a, z in (args, args[::-1]):
                if (
                    a == {"field": "row_id"}
                    and isinstance(z, dict)
                    and type(z.get("literal")) is int
                ):
                    return z["literal"]
        return None

    def prepare(node):
        if isinstance(node, dict):
            if node.get("op") == "filter" and node.get("input") == {
                "op": "scan",
                "table": "terms",
            }:
                r = selected_row(node.get("where"))
                if r is not None:
                    page = fact_query(m, {"op": "terms", "rows": [r]}, budget, limit=1)
                    if page["completeness"] != "complete":
                        raise DependencyPending(page)
                    return {**node, "input": {"op": "result", "handle": page["handle"]}}
            if node.get("op") in OPS:
                page = row_query(m, node, budget, limit=1)
                if page["completeness"] != "complete":
                    raise DependencyPending(page)
                return {"op": "result", "handle": page["handle"]}
            if node.get("op") in FACT_OPS:
                page = fact_query(m, node, budget, limit=1)
                if page["completeness"] != "complete":
                    raise DependencyPending(page)
                return {"op": "result", "handle": page["handle"]}
            return {
                k: prepare(v) if k in ("input", "left", "right") else v
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [prepare(v) for v in node]
        return node

    try:
        spec = prepare(spec)
    except DependencyPending as e:
        return {
            **e.page,
            "requested_spec": spec,
            "continuation": {"spec": spec},
            "reason": "input relation is incomplete",
        }
    # Public document handles are short, declarative selections of the same AST.
    if isinstance(spec, dict) and "op" not in spec and "table" in spec:
        allowed = {"table", "id", "family", "name", "row_id", "not_equal", "analysis"}
        if set(spec) - allowed or spec["table"] not in TABLES:
            raise LPIRError("Invalid document relation handle")
        predicates = [
            {
                "op": "ne" if key == "not_equal" else "eq",
                "args": [
                    {"field": "value" if key == "not_equal" else key},
                    {"literal": val},
                ],
            }
            for key, val in spec.items()
            if key not in ("table", "analysis")
        ]
        scan = {
            "op": "scan",
            "table": spec["table"],
            **({"analysis": spec["analysis"]} if "analysis" in spec else {}),
        }
        predicate = predicates[0] if predicates else None
        for following in predicates[1:]:
            predicate = {"op": "and", "args": [predicate, following]}
        spec = (
            {"op": "filter", "input": scan, "where": predicate} if predicates else scan
        )
    if len(json.dumps(spec)) > 65536:
        raise LPIRError("Query AST exceeds 64KiB")
    from .query_executor import execute_query

    return execute_query(m, spec, budget, offset, limit)


class DependencyPending(Exception):
    def __init__(self, page):
        self.page = page
