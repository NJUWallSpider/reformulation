"""Exhaustive finite pure-integer linear feasibility; no continuous projection."""

from fractions import Fraction
from math import ceil, floor
from mipframe.ops._runtime import operation
from mipframe.store.algorithm_store import KeyIndex
from mipframe.frame.schema import DType, SchemaError
from mipframe.frame.executor import unpack


def validate_numbers(ctx, table, schema, fields, *, finite):
    for name in fields:
        if schema[name].kind not in ("int", "number"):
            raise SchemaError(f"{name} must contain exact numbers")
    columns = tuple(schema)
    condition = " OR ".join(
        f"c{columns.index(n)} IS NULL"
        + (f" OR c{columns.index(n)} IN ('inf','-inf')" if finite else "")
        for n in fields
    )
    query = f"SELECT {','.join('c'+str(i) for i in range(len(schema)))} FROM {table} WHERE {condition} ORDER BY ordinal"
    ctx.invalid(
        "Expected nonmissing " + ("finite " if finite else "") + "exact values",
        schema,
        ctx.rows(query, schema),
    )


@operation("alg.integer.enumerate", search=True)
def enumerate(ctx, variables, terms, rows, *, max_states=None, time_limit=None):
    """
    All feasible assignments of a bounded integer linear system, or an error.

    MIPFRAME HELP BEGIN
    alg.integer.enumerate [api.alg.integer.enumerate]

    Enumerate the complete feasible assignments of an explicit finite integer system.

    alg.integer.enumerate(variables, terms, rows, *, max_states=None, time_limit=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Enumerate the complete feasible assignments of an explicit finite integer system.

    Inputs: variables(var,lb,ub); terms(row,var,coef); rows(row,lb,ub), with compatible endpoint keys.

    Returns: Frame(assignment,var,value); relations["summary"] contains every assignment, including the empty one.

    Establishes: Explicit pure-integer domains ceil(lb)..floor(ub). Exact leaf checks; exhaustive or SearchBudgetExceeded, with no partial successful result.

    Does not establish: No continuous projection, objective optimization, implicit model integrality or implication Report.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.enumeration, api.Checker.enumerate
    MIPFRAME HELP END
    """
    vt, vs = ctx.stage_checked(
        variables, ("var", "lb", "ub"), unique=("var",), nonnull=("var",)
    )
    rt, rs = ctx.stage_checked(
        rows, ("row", "lb", "ub"), unique=("row",), nonnull=("row",)
    )
    tt, ts = ctx.stage_checked(
        terms, ("row", "var", "coef"), unique=("row", "var"), nonnull=("row", "var")
    )
    if not vs["var"].compatible(ts["var"]) or not rs["row"].compatible(ts["row"]):
        raise SchemaError(
            "Term endpoints must match the declared row/variable identities"
        )
    validate_numbers(ctx, vt, vs, ("lb", "ub"), finite=True)
    validate_numbers(ctx, rt, rs, ("lb", "ub"), finite=False)
    validate_numbers(ctx, tt, ts, ("coef",), finite=True)
    vi = KeyIndex(ctx, vs["var"], f"SELECT c0 AS value FROM {vt}")
    ri = KeyIndex(ctx, rs["row"], f"SELECT c0 AS value FROM {rt}")
    vi.validate(f"SELECT c1 AS value FROM {tt}", "var")
    ri.validate(f"SELECT c0 AS value FROM {tt}", "row")
    bounds = ctx.raw_table("v INTEGER PRIMARY KEY,lo TEXT,hi TEXT")
    for v, lo, hi in ctx.execute(
        f"SELECT d.ordinal,t.c1,t.c2 FROM {vt} t JOIN {vi.table} d ON d.c0=t.c0 ORDER BY d.ordinal"
    ):
        ctx.execute(
            f"INSERT INTO {bounds} VALUES(?,?,?)",
            (v, str(ceil(Fraction(lo))), str(floor(Fraction(hi)))),
        )
    constraints = ctx.raw_table("r INTEGER PRIMARY KEY,lo TEXT,hi TEXT")
    ctx.execute(
        f"INSERT INTO {constraints} SELECT d.ordinal,t.c1,t.c2 FROM {rt} t JOIN {ri.table} d ON d.c0=t.c0"
    )
    coefficients = ctx.raw_table("r INTEGER,v INTEGER,c TEXT,PRIMARY KEY(r,v)")
    ctx.execute(
        f"INSERT INTO {coefficients} SELECT r.ordinal,v.ordinal,t.c2 FROM {tt} t JOIN {ri.table} r ON r.c0=t.c0 JOIN {vi.table} v ON v.c0=t.c1"
    )
    summary = ctx.raw_table("id INTEGER PRIMARY KEY")
    values = ctx.raw_table("id INTEGER,v INTEGER,x TEXT,PRIMARY KEY(id,v)")
    ctx.scope_info = f"finite integer system: {vi.n} variables, {ri.n} rows"
    ctx.paths.add(
        "paged integer DFS + exact interval pruning and complete leaf verification"
    )
    ctx.ready()
    found = 0

    def visit(assignment):
        nonlocal found
        found += 1
        ctx.enforce(found * (vi.n + 1))
        ctx.execute(f"INSERT INTO {summary} VALUES(?)", (found,))
        ctx.execute(f"INSERT INTO {values} SELECT ?,v,x FROM {assignment}", (found,))

    from mipframe.ops._kernels.integer import walk

    walk(ctx, bounds, constraints, coefficients, visit)
    status = ctx.finish(
        found=found,
        retained=found,
        details="all feasible integer assignments; no objective or continuous variables",
    )
    ids = ctx.publish(
        {"assignment": DType("int")},
        (
            {"assignment": i}
            for (i,) in ctx.execute(f"SELECT id FROM {summary} ORDER BY id")
        ),
        key=("assignment",),
        relations={"search": status},
    )
    return ctx.publish(
        {"assignment": DType("int"), "var": vs["var"], "value": DType("int")},
        (
            {"assignment": i, "var": vi.value(v), "value": int(x)}
            for i, v, x in ctx.execute(f"SELECT id,v,x FROM {values} ORDER BY id,v")
        ),
        key=("assignment", "var"),
        domains={"var": vi.as_frame("var")},
        relations={"summary": ids, "search": status},
    )
