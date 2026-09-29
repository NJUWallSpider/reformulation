"""Exact expression evaluation and compilation of whitelisted relational ASTs."""

import json
from fractions import Fraction as F
from .numbers import LPIRError
from .results import require_current_assignments
from .evidence import require_result
from .query_catalog import TABLES


def value(node, record, depth=0):
    if depth > 32 or not isinstance(node, dict):
        raise LPIRError("Invalid/deep expression")
    if set(node) == {"path"}:
        path = node["path"]
        if not isinstance(path, list) or not path or len(path) > 32:
            raise LPIRError("Expected a nonempty field path")
        result = record
        try:
            for part in path:
                if isinstance(result, dict) and isinstance(part, str):
                    result = result[part]
                elif (
                    isinstance(result, list)
                    and type(part) is int
                    and 0 <= part < len(result)
                ):
                    result = result[part]
                else:
                    raise KeyError(part)
        except (KeyError, IndexError):
            raise LPIRError(f"Missing field path {path}") from None
        return result
    if set(node) == {"field"}:
        key = node["field"]
        if key not in record:
            raise LPIRError(f"Missing field {key}")
        return record[key]
    if set(node) == {"literal"}:
        if isinstance(node["literal"], float):
            raise LPIRError("Use strings for exact decimals")
        return node["literal"]
    op = node.get("op")
    args = node.get("args")
    if set(node) != {"op", "args"} or op not in (
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "add",
        "sub",
        "and",
        "or",
        "not",
    ):
        raise LPIRError("Unsupported expression")
    if (
        not isinstance(args, list)
        or len(args) > 100
        or len(args) != (1 if op == "not" else 2)
    ):
        raise LPIRError("Invalid expression arity")
    a = [value(x, record, depth + 1) for x in args]
    if op == "not":
        return not a[0]
    if op == "and":
        return bool(a[0] and a[1])
    if op == "or":
        return bool(a[0] or a[1])

    def exact(x):
        if x is None:
            return None
        try:
            return F(x)
        except (ValueError, TypeError):
            return x

    l, r = map(exact, a)
    if op == "eq":
        return l == r
    if op == "ne":
        return l != r
    if l is None or r is None:
        raise LPIRError("Comparison/arithmetic on missing or infinite value")
    if op in ("add", "sub"):
        if not isinstance(l, F) or not isinstance(r, F):
            raise LPIRError("Arithmetic requires exact numbers")
        return str(l + r if op == "add" else l - r)
    return {
        "lt": lambda: l < r,
        "le": lambda: l <= r,
        "gt": lambda: l > r,
        "ge": lambda: l >= r,
    }[op]()


def selector(field):
    return {"field": field} if isinstance(field, str) else field


def install_functions(m, db):
    db.create_function(
        "column_degree", 1, lambda v: int(m.col_ptr[v + 1] - m.col_ptr[v])
    )
    db.create_function(
        "exact_num", 1, lambda i: str(m.num(i)) if i is not None else None
    )
    error = []

    def protect(fn):
        def call(*args):
            try:
                return fn(*args)
            except Exception as e:
                error.append(e)
                raise

        return call

    db.create_function(
        "matches",
        2,
        protect(lambda rec, pred: int(bool(value(json.loads(pred), json.loads(rec))))),
    )
    db.create_function(
        "project",
        2,
        protect(
            lambda rec, fields: json.dumps(
                {k: value(v, json.loads(rec)) for k, v in json.loads(fields).items()},
                sort_keys=True,
            )
        ),
    )

    def key(rec, fields):
        obj = json.loads(rec)
        values = []
        for f in json.loads(fields):
            v = value(selector(f), obj)
            try:
                v = {"rational": str(F(v))} if v is not None else None
            except (ValueError, TypeError):
                pass
            values.append(v)
        return json.dumps(values, sort_keys=True)

    db.create_function("group_key", 2, protect(key))
    db.create_function(
        "nonnull",
        2,
        protect(
            lambda rec, field: (
                value(selector(json.loads(field)), json.loads(rec)) is not None
            )
        ),
    )
    db.create_function(
        "merge",
        2,
        lambda a, b: json.dumps(
            {
                **{"left." + k: v for k, v in json.loads(a).items()},
                **{"right." + k: v for k, v in json.loads(b).items()},
            }
        ),
    )

    def sort_compare(a, b):
        def scalar(text):
            x = json.loads(text)
            if x is None:
                return (0, "")
            if isinstance(x, (dict, list)):
                raise LPIRError("Sort requires scalar values")
            try:
                return (1, F(x))
            except (ValueError, TypeError):
                return (2, str(x))

        a, b = scalar(a), scalar(b)
        return (a > b) - (a < b)

    db.create_function(
        "sort_value",
        2,
        protect(lambda rec, expr: json.dumps(value(json.loads(expr), json.loads(rec)))),
    )
    db.create_collation("exact_scalar", sort_compare)

    class Aggregate:
        def __init__(self):
            self.vals = {}
            self.fields = None
            self.groups = None

        def step(self, rec, group, metrics):
            r = json.loads(rec)
            metrics = json.loads(metrics)
            if self.groups is None:
                self.groups = {k: r[k] for k in json.loads(group)}
            for name, rule in metrics.items():
                op = rule.get("op")
                if op == "count":
                    self.vals[name] = self.vals.get(name, 0) + 1
                    continue
                if op not in ("sum", "min", "max"):
                    raise LPIRError("Unsupported aggregate")
                v = value(selector(rule["field"]), r)
                if v is None:
                    continue
                x = F(v)
                if name not in self.vals:
                    self.vals[name] = x
                elif op == "sum":
                    self.vals[name] += x
                else:
                    self.vals[name] = (min if op == "min" else max)(self.vals[name], x)

        def finalize(self):
            return json.dumps(
                {
                    **(self.groups or {}),
                    **{
                        k: str(v) if isinstance(v, F) else v
                        for k, v in self.vals.items()
                    },
                }
            )

    db.create_aggregate("exact_aggregate", 3, Aggregate)
    return error


def compile_query(m, db, spec):
    params = []

    def bind(x):
        params.append(json.dumps(x))
        return "?"

    def raw_bind(x):
        params.append(x)
        return "?"

    def compile(node, depth=0):
        if depth > 16 or not isinstance(node, dict):
            raise LPIRError("Invalid/deep query AST")
        op = node.get("op")
        if op == "result":
            if set(node) != {"op", "handle"}:
                raise LPIRError("Invalid result relation")
            require_result(m, db, node["handle"])
            return (
                "SELECT record rec FROM results WHERE run="
                + raw_bind(node["handle"])
                + " ORDER BY ordinal"
            )
        if op == "distinct":
            inner = compile(node["input"], depth + 1)
            fields = node.get("fields")
            if fields is None:
                return f"SELECT DISTINCT rec FROM ({inner})"
            if not isinstance(fields, list) or not fields:
                raise LPIRError("Distinct fields must be nonempty")
            p = bind({f: {"field": f} for f in fields})
            g = bind(fields)
            return f"WITH src AS ({inner}) SELECT project(rec,{p}) rec FROM src GROUP BY group_key(rec,{g})"
        if op == "scan":
            if set(node) - {"op", "table", "analysis"} or node["table"] not in TABLES:
                raise LPIRError("Unknown relation")
            if node["table"] in ("assignment_members", "assignment_edges"):
                require_current_assignments(db)
            return TABLES[node["table"]]
        if op == "filter":
            table = (
                node.get("input", {}).get("table")
                if node.get("input", {}).get("op") == "scan"
                else None
            )
            columns = {
                "explanation_bindings": {
                    "variable": "b.variable",
                    "row_id": "b.row_id",
                    "role": "b.role",
                    "family": "i.family",
                },
                "explanation_parameters": {
                    "row_id": "row_id",
                    "family": "family",
                    "name": "name",
                },
                "explanation_rows": {"row_id": "row_id", "family": "family"},
                "explanation_families": {"id": "id"},
                "variables": {"variable": "id"},
                "raw_rows": {"row_id": "id"},
                "normalized_rows": {"row_id": "row_id", "family": "family"},
                "rows": {"row_id": "row_id", "family": "family"},
            }.get(table, {})

            def constraints(pred):
                if pred.get("op") == "and":
                    for arg in pred.get("args", []):
                        yield from constraints(arg)
                if pred.get("op") == "eq" and len(pred.get("args", [])) == 2:
                    a, z = pred["args"]
                    if "literal" in a:
                        a, z = z, a
                    field = a.get("field")
                    literal = z.get("literal")
                    if field in columns and set(z) == {"literal"}:
                        if field in ("variable", "row_id"):
                            if isinstance(literal, int) and not isinstance(
                                literal, bool
                            ):
                                yield columns[field], literal
                        elif isinstance(literal, str):
                            try:
                                F(literal)
                            except ValueError:
                                yield columns[field], literal

            pushed = list(constraints(node["where"])) if columns else []
            if pushed:
                predicates = [col + "=" + raw_bind(literal) for col, literal in pushed]
                inner = TABLES[table] + " WHERE " + " AND ".join(predicates)
                p = bind(node["where"])
                return f"SELECT rec FROM ({inner}) WHERE matches(rec,{p})"
            inner = compile(node["input"], depth + 1)
            p = bind(node["where"])
            return f"SELECT rec FROM ({inner}) WHERE matches(rec,{p})"
        if op == "project":
            # Positional parameters follow lexical SQL order, hence bind projection first.
            p = bind(node["fields"])
            inner = compile(node["input"], depth + 1)
            return f"SELECT project(rec,{p}) rec FROM ({inner})"
        if op == "sort":
            rules = node.get("by")
            if not isinstance(rules, list) or not rules or len(rules) > 32:
                raise LPIRError("Sort requires 1..32 ordering rules")
            inner = compile(node["input"], depth + 1)
            ordering = []
            for rule in rules:
                if not isinstance(rule, dict) or set(rule) - {"expr", "direction"}:
                    raise LPIRError("Invalid sort rule")
                direction = rule.get("direction", "asc")
                if direction not in ("asc", "desc"):
                    raise LPIRError("Sort direction must be asc or desc")
                ordering.append(
                    f"sort_value(rec,{bind(rule['expr'])}) COLLATE exact_scalar {direction}"
                )
            return f"SELECT rec FROM ({inner}) ORDER BY " + ",".join(ordering + ["rec"])
        if op == "aggregate":
            group = node.get("group_by", [])
            metrics = node["metrics"]
            if not isinstance(group, list) or not isinstance(metrics, dict):
                raise LPIRError("Invalid aggregation")
            for rule in metrics.values():
                if rule.get("op") not in (
                    "count",
                    "sum",
                    "min",
                    "max",
                    "count_distinct",
                ):
                    raise LPIRError("Unsupported aggregate")
            inner = compile(node["input"], depth + 1)
            regular = {k: v for k, v in metrics.items() if v["op"] != "count_distinct"}
            g = bind(group)
            a = bind(regular)
            fallback = bind(
                {
                    name: 0
                    if rule["op"] == "count"
                    else "0"
                    if rule["op"] == "sum"
                    else None
                    for name, rule in regular.items()
                }
            )
            base = f"COALESCE(exact_aggregate(rec,{g},{a}),{fallback})"
            extras = []
            for name, rule in metrics.items():
                if rule["op"] != "count_distinct":
                    continue
                name_param = raw_bind(name)
                field_name = bind(rule["field"])
                field_key = bind([rule["field"]])
                extras.extend(
                    [
                        name_param,
                        f"count(DISTINCT CASE WHEN nonnull(rec,{field_name}) THEN group_key(rec,{field_key}) END)",
                    ]
                )
            output = (
                "json_patch(" + base + ",json_object(" + ",".join(extras) + "))"
                if extras
                else base
            )
            grouping = " GROUP BY group_key(rec," + bind(group) + ")" if group else ""
            return (
                f"WITH src AS MATERIALIZED ({inner}) SELECT {output} rec FROM src"
                + grouping
            )
        if op in ("join", "semi_join", "anti_join"):
            if not node.get("left_on") or len(node["left_on"]) != len(
                node.get("right_on", [])
            ):
                raise LPIRError("Join requires matching nonempty key lists")
            lk = bind(node["left_on"])
            left = compile(node["left"], depth + 1)
            rk = bind(node["right_on"])
            right = compile(node["right"], depth + 1)
            if op != "join":
                exists = "NOT EXISTS" if op == "anti_join" else "EXISTS"
                return f"WITH lhs AS MATERIALIZED (SELECT rec,group_key(rec,{lk}) k FROM ({left})), rhs AS MATERIALIZED (SELECT rec,group_key(rec,{rk}) k FROM ({right})) SELECT lhs.rec FROM lhs WHERE {exists} (SELECT 1 FROM rhs WHERE rhs.k=lhs.k)"
            return f"WITH lhs AS MATERIALIZED (SELECT rec,group_key(rec,{lk}) k FROM ({left})), rhs AS MATERIALIZED (SELECT rec,group_key(rec,{rk}) k FROM ({right})) SELECT merge(lhs.rec,rhs.rec) rec FROM lhs JOIN rhs USING(k)"
        if op in ("union", "intersection", "difference"):
            left = compile(node["left"], depth + 1)
            right = compile(node["right"], depth + 1)
            sqlop = {
                "union": "UNION",
                "intersection": "INTERSECT",
                "difference": "EXCEPT",
            }[op]
            return f"SELECT rec FROM ({left}) {sqlop} SELECT rec FROM ({right})"
        raise LPIRError(f"Unsupported query operation {op}")

    return compile(spec), params
