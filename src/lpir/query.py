"""Restricted relational JSON AST; SQLite executes joins/aggregation with exact UDFs."""

from fractions import Fraction as F
import hashlib, json, sqlite3
from .numbers import LPIRError
from .semantic import connect
from .jobs import (
    Budget,
    BudgetExceeded,
    result_page,
    assignment_dependency,
    require_current_assignments,
    ASSIGNMENT_VERSION,
)

QUERY_VERSION = "3.3"

TABLES = {
    "rows": "SELECT json_object('row_id',row_id,'class',class,'nnz',nnz,'sense',sense,'lower',lower,'upper',upper,'family',family,'scale',scale,'shift',shift,'view_layer','normalized') rec FROM features",
    "raw_rows": "SELECT json_object('row_id',id,'name',name,'lower',exact_num(lo),'upper',exact_num(hi),'source_line',line,'view_layer','raw') rec FROM numerical.rows",
    "normalized_rows": "SELECT json_object('row_id',row_id,'class',class,'nnz',nnz,'lower',lower,'upper',upper,'scale',scale,'shift',shift,'family',family,'view_layer','normalized') rec FROM features",
    "roles": "SELECT json_object('row_id',row_id,'role',role,'coefficient',coefficient,'kind',kind,'count',count,'view_layer','normalized') rec FROM roles",
    "bindings": "SELECT json_object('row_id',row_id,'role',role,'variable',variable,'complement',complement,'view_layer','normalized') rec FROM bindings",
    "temporal": "SELECT json_object('row_id',row_id,'source',source,'target',target,'switch',switch,'arc',arc,'q',q,'M',M) rec FROM temporal",
    "assignment_edges": "SELECT json_object('block',block,'variable',variable,'left_index',left_idx,'right_index',right_idx) rec FROM assignment_edges",
    "assignment_members": "SELECT json_object('block',block,'side',side,'index',idx,'row_id',row_id) rec FROM assignment_members",
    "time_bounds": "SELECT json_object('variable',variable,'lower',lower,'upper',upper) rec FROM time_bounds",
    "facts": "SELECT json_object('kind',kind,'row_id',row_id,'variable',variable,'lower',lower,'upper',upper,'source',source) rec FROM facts",
    "edges": "SELECT json_object('source',source,'target',target) rec FROM edges",
    "variables": "SELECT json_object('variable',id,'name',name,'kind',kind,'lower',exact_num(lo),'upper',exact_num(hi),'cost',exact_num(obj),'degree',column_degree(id),'view_layer','raw') rec FROM numerical.variables",
    "terms": "SELECT json_object('row_id',row_id,'variable',variable,'coefficient',coefficient,'view_layer','raw') rec FROM numerical_terms",
    "explanation_families": "SELECT json_object('id',id,'kind',kind,'ast',json(ast),'formula',formula,'roles',json(roles),'parameters',json(parameters),'rows',rows) rec FROM explained.families",
    "explanation_rows": "SELECT json_object('row_id',row_id,'family',family,'scale',scale,'source_family',source_family,'view_layer','explanation') rec FROM explained.instances",
    "explanation_parameters": "SELECT json_object('family',family,'row_id',row_id,'name',name,'value',value) rec FROM explained.parameters",
    "explanation_bindings": "SELECT json_object('family',i.family,'row_id',b.row_id,'role',b.role,'variable',b.variable,'view_layer','explanation') rec FROM explained.bindings b JOIN explained.instances i USING(row_id)",
    "explanation_residuals": "SELECT json_object('row_id',row_id,'nnz',nnz,'positive',positive,'negative',negative,'types',json(types),'min_coefficient',min_coefficient,'max_coefficient',max_coefficient,'lower',lower_bound,'upper',upper_bound) rec FROM explained.residuals",
}


from .indexed import RELATIONS as INDEXED_RELATIONS
from .structural import RELATIONS as STRUCTURE_RELATIONS

for _relation in STRUCTURE_RELATIONS:
    TABLES[_relation] = (
        "SELECT record rec FROM structured.records WHERE relation='" + _relation + "'"
    )

for _relation in INDEXED_RELATIONS:
    TABLES[_relation] = (
        "SELECT record rec FROM indexed.records WHERE relation='" + _relation + "'"
    )

TABLES["index_operators"] += " AND json_extract(record,'$.verification')='verified'"
for _relation in ("index_rows", "index_kernel", "index_alignment"):
    TABLES[_relation] += (
        " AND EXISTS(SELECT 1 FROM indexed.records p WHERE p.relation='index_operators' AND p.key=json_extract(records.record,'$.id') AND json_extract(p.record,'$.verification')='verified')"
    )


def value(node, record, depth=0):
    if depth > 32 or not isinstance(node, dict):
        raise LPIRError("Invalid/deep expression")
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
            if node.get("op") in FACT_OPS:
                page = fact_query(m, node, budget, limit=1)
                if page["completeness"] != "complete":
                    raise DependencyPending(page)
                return {"op": "result", "handle": page["handle"]}
            return {k: prepare(v) for k, v in node.items()}
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
    db = connect(m)
    db.execute("ATTACH DATABASE ? AS numerical", (str(m.path / "metadata.sqlite"),))
    if "explanation_" in json.dumps(spec):
        from .compressed import open_db

        checked = open_db(m)
        checked.close()
        db.execute(
            "ATTACH DATABASE ? AS explained",
            (f"file:{(m.path / 'explanations.sqlite').resolve()}?mode=ro",),
        )
    indexed_manifest = None
    structure_manifest = None
    if "structure_" in json.dumps(spec):
        from .structural import connect as structure_connect, filename as structure_filename

        def structure_scopes(node):
            if isinstance(node, dict):
                if node.get("table") in STRUCTURE_RELATIONS:
                    yield node.get("analysis")
                for child in node.values():
                    yield from structure_scopes(child)
            elif isinstance(node, list):
                for child in node:
                    yield from structure_scopes(child)

        scopes_selected = set(structure_scopes(spec))
        if len(scopes_selected) > 1:
            raise LPIRError("Materialize different structure scopes separately before joining")
        structure_analysis = next(iter(scopes_selected), None)
        checked = structure_connect(m, structure_analysis)
        checked.close()
        structure_manifest = m.path / structure_filename(structure_analysis, ".json")
        db.execute("ATTACH DATABASE ? AS structured",
                   (f"file:{(m.path / structure_filename(structure_analysis)).resolve()}?mode=ro",))
    if "index_" in json.dumps(spec):
        from .indexed import connect as indexed_connect, ScopedModel, filename

        def scopes(node):
            if isinstance(node, dict):
                if "analysis" in node:
                    yield node["analysis"]
                for v in node.values():
                    yield from scopes(v)
            elif isinstance(node, list):
                for v in node:
                    yield from scopes(v)

        selected = set(scopes(spec))
        if len(selected) > 1:
            raise LPIRError(
                "Materialize scoped queries separately before joining their result handles"
            )
        indexed_model = ScopedModel(m, next(iter(selected))) if selected else m
        indexed_manifest = m.path / filename(indexed_model, ".json")

        checked = indexed_connect(indexed_model, True)
        checked.close()
        db.execute(
            "ATTACH DATABASE ? AS indexed",
            (
                f"file:{(m.path / filename(indexed_model, '.sqlite')).resolve()}?mode=ro",
            ),
        )
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
            if f not in obj:
                raise LPIRError(f"Missing key field {f}")
            v = obj[f]
            try:
                v = {"rational": str(F(v))} if v is not None else None
            except (ValueError, TypeError):
                pass
            values.append(v)
        return json.dumps(values, sort_keys=True)

    db.create_function("group_key", 2, protect(key))
    db.create_function(
        "nonnull", 2, protect(lambda rec, field: json.loads(rec)[field] is not None)
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
                v = r[rule["field"]]
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
                field_name = raw_bind(rule["field"])
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

    sql = compile(spec)
    version = QUERY_VERSION + (
        ASSIGNMENT_VERSION if assignment_dependency(spec) else ""
    )
    if "explanation_" in json.dumps(spec):
        version += (
            ":" + json.loads((m.path / "explanations.json").read_text())["sha256"]
        )
    if "index_" in json.dumps(spec):
        version += ":" + indexed_manifest.read_text()
    if structure_manifest is not None:
        version += ":" + structure_manifest.read_text()
    run = (
        "q2_"
        + hashlib.sha256(
            (
                m.manifest["model_version"] + json.dumps(spec, sort_keys=True) + version
            ).encode()
        ).hexdigest()[:32]
    )
    row = db.execute("SELECT status,cursor FROM runs WHERE id=?", (run,)).fetchone()
    if row and row[0] == "complete":
        out = result_page(m, db, run, offset, limit)
        db.close()
        return out
    cursor = row[1] if row else 0
    if not row:
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
            (
                run,
                "query",
                json.dumps(spec),
                "partial",
                0,
                0,
                json.dumps(
                    {
                        "query_version": QUERY_VERSION,
                        **(
                            {
                                "indexed_source": json.loads(
                                    indexed_manifest.read_text()
                                ),
                                "scope_semantics": "materialized snapshot of published facts; search may be partial",
                            }
                            if indexed_manifest is not None
                            else {}
                        ),
                    }
                ),
            ),
        )
        db.commit()
    b = Budget(**(budget or {}))
    status = "complete"
    if "numerical_terms" in sql:
        db.execute(
            "CREATE TABLE IF NOT EXISTS numerical_terms(row_id INTEGER,variable INTEGER,coefficient TEXT,PRIMARY KEY(row_id,variable))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS numerical_term_progress(id INTEGER PRIMARY KEY,cursor INTEGER)"
        )
        db.execute("INSERT OR IGNORE INTO numerical_term_progress VALUES(0,0)")
        pos = db.execute(
            "SELECT cursor FROM numerical_term_progress WHERE id=0"
        ).fetchone()[0]
        import numpy as np

        try:
            for start in range(pos, len(m.col_idx), 1024):
                b.tick()
                end = min(start + 1024, len(m.col_idx))
                for p in range(start, end):
                    r = int(np.searchsorted(m.row_ptr, p, side="right") - 1)
                    db.execute(
                        "INSERT OR REPLACE INTO numerical_terms VALUES(?,?,?)",
                        (r, int(m.col_idx[p]), str(m.num(int(m.coeff_id[p])))),
                    )
                db.execute(
                    "UPDATE numerical_term_progress SET cursor=? WHERE id=0", (end,)
                )
            db.commit()
        except BudgetExceeded:
            db.commit()
            out = result_page(m, db, run, offset, limit)
            out["summary"] = {"reason": "building paged numerical term relation"}
            db.close()
            return out

    # SQLite temp data spills to disk; interrupt expensive joins/sorts before output too.
    db.set_progress_handler(lambda: int(time_expired(b)), 10000)
    try:
        for (record,) in db.execute(
            f"SELECT rec FROM ({sql}) LIMIT -1 OFFSET ?", (*params, cursor)
        ):
            b.tick()
            b.require(len(record.encode()) * 4)
            db.execute("INSERT INTO results VALUES(?,?,?)", (run, cursor, record))
            cursor += 1
    except BudgetExceeded:
        status = "partial"
    except sqlite3.OperationalError as e:
        if error:
            raise LPIRError(str(error[0])) from e
        if "interrupted" in str(e):
            status = "partial"
        else:
            raise LPIRError(str(e)) from e
    finally:
        db.set_progress_handler(None, 0)
    db.execute(
        "UPDATE runs SET status=?,cursor=?,processed=? WHERE id=?",
        (status, cursor, cursor, run),
    )
    db.commit()
    out = result_page(m, db, run, offset, limit)
    db.close()
    return out


def time_expired(b):
    import time

    return time.monotonic() > b.deadline


class DependencyPending(Exception):
    def __init__(self, page):
        self.page = page
