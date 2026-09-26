"""Small exact affine AST interpreter, shared by view expansion and validation."""

from fractions import Fraction as F
from .numbers import LPIRError


def affine(node, parameters, bindings, depth=0):
    if depth > 32 or not isinstance(node, dict):
        raise LPIRError("Invalid/deep formula AST")
    if set(node) == {"parameter"}:
        if node["parameter"] not in parameters:
            raise LPIRError("Unknown formula parameter")
        return {}, F(parameters[node["parameter"]])
    if set(node) == {"constant"}:
        if type(node["constant"]) not in (int, str):
            raise LPIRError("Exact constants must be integers or rational strings")
        return {}, F(node["constant"])
    op = node.get("op")
    if op == "sum":
        if set(node) != {"op", "relation", "value"} or node["value"] != {
            "op": "literal"
        }:
            raise LPIRError("Sum requires bound literals")
        if node["relation"] not in bindings:
            raise LPIRError("Unknown binding relation")
        a = {}
        c = F(0)
        for v, comp in bindings[node["relation"]]:
            a[v] = a.get(v, F(0)) + (-1 if comp else 1)
            c += bool(comp)
        return a, c
    if op not in ("add", "sub", "mul") or set(node) != {"op", "args"}:
        raise LPIRError("Unsupported formula operation")
    args = node["args"]
    if (
        not isinstance(args, list)
        or len(args) > 1000
        or (op in ("sub", "mul") and len(args) != 2)
    ):
        raise LPIRError("Invalid affine argument count")
    values = [affine(x, parameters, bindings, depth + 1) for x in args]
    if op == "mul":
        (a, c), (b, d) = values
        if a and b:
            raise LPIRError("Nonlinear formula rejected")
        return (
            ({v: x * d for v, x in a.items()}, c * d)
            if a
            else ({v: x * c for v, x in b.items()}, c * d)
        )
    out = {}
    constant = F(0)
    for i, (a, c) in enumerate(values):
        sign = -1 if op == "sub" and i else 1
        constant += sign * c
        for v, x in a.items():
            out[v] = out.get(v, F(0)) + sign * x
    return {v: c for v, c in out.items() if c}, constant


def expand(db, r, ast=None):
    row = db.execute(
        "SELECT lower,upper,scale,shift,family FROM features WHERE row_id=?", (r,)
    ).fetchone()
    if row is None or row[4] is None:
        raise LPIRError("No formula for row")
    lo, hi, scale, shift, fid = row
    if ast is None:
        import json

        ast = json.loads(
            db.execute("SELECT ast FROM families WHERE id=?", (fid,)).fetchone()[0]
        )
    if ast.get("op") != "range" or set(ast) != {"op", "lower", "upper", "expression"}:
        raise LPIRError("Formula must be a range")
    params = {
        role: c
        for role, c in db.execute(
            "SELECT role,coefficient FROM roles WHERE row_id=?", (r,)
        )
    }
    params.update(lower=lo, upper=hi)
    bindings = {
        role: list(
            db.execute(
                "SELECT variable,complement FROM bindings WHERE row_id=? AND role=? ORDER BY variable",
                (r, role),
            )
        )
        for role in params
        if role not in ("lower", "upper")
    }
    a, c = affine(ast["expression"], params, bindings)

    def endpoint(n):
        if n.get("parameter") in ("lower", "upper") and params[n["parameter"]] is None:
            return None
        x, k = affine(n, params, bindings)
        if x:
            raise LPIRError("Range endpoint cannot contain variables")
        return k

    lower, upper = endpoint(ast["lower"]), endpoint(ast["upper"])
    scale = F(scale)
    lower = (lower - c) * scale if lower is not None else None
    upper = (upper - c) * scale if upper is not None else None
    if scale < 0:
        lower, upper = upper, lower
    return {v: x * scale for v, x in a.items()}, lower, upper
