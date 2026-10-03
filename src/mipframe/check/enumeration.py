"""Stream a finite local implication check; local witnesses are not MIP points."""

from fractions import Fraction
from math import ceil, floor
from time import monotonic
from mipframe.check.certificate import (
    UnsupportedEnumerationDomain,
    InvalidCertificateError,
    satisfied,
)
from mipframe.linear.expr import var_id, attach
from mipframe.frame.base import Frame
from mipframe.frame.schema import ref_type, DType
from mipframe.store.search_store import SearchStopped, nonnegative
from mipframe.ops._kernels.integer import walk


def enumerate_claim(ctx, target, variables, using, assume, max_states, time_limit):
    nonnegative(max_states, "max_states", optional=True)
    # Validate time options even for an immediately inconsistent domain.
    ctx.configure(candidate=True, max_states=max_states, time_limit=time_limit)
    ctx.searching = False
    if ctx.requested_domain == "relaxation":
        raise InvalidCertificateError("Enumeration proves integer conclusions only")
    ctx.proof_domain = "integer"
    target_id = ctx.add_claim(target)
    ctx.assume(assume).ingest(using, constraints=True)
    ctx.prepare_assumptions()
    selected = ctx.raw_table("v INTEGER PRIMARY KEY")
    if isinstance(variables, Frame):
        if getattr(variables, "_entity_axis", None) != "var":
            raise UnsupportedEnumerationDomain(
                "vars requires a VarFrame or Var sequence"
            )
        attach(ctx, variables._models)
        iterator = (r["var"] for r in variables._raw(("var",), ctx))
    else:

        def ids():
            for v in variables:
                if hasattr(v, "_owner"):
                    attach(ctx, (v._owner,))
                yield var_id(ctx.owner, v)

        iterator = ids()
    for v in iterator:
        ctx.execute(f"INSERT OR IGNORE INTO {selected} VALUES(?)", (v,))
    mapping = ctx.raw_table("v INTEGER PRIMARY KEY,original INTEGER UNIQUE")
    bounds = ctx.raw_table("v INTEGER PRIMARY KEY,lo TEXT,hi TEXT")
    for i, (v,) in enumerate(ctx.execute(f"SELECT v FROM {selected} ORDER BY v"), 1):
        var = ctx.owner.vars.by_id(v)
        if var.kind != "I":
            raise UnsupportedEnumerationDomain(
                "Enumeration variables must be declared integer"
            )
        lo, hi = ctx.interval(v)
        if isinstance(lo, float) or isinstance(hi, float):
            raise UnsupportedEnumerationDomain(
                "Enumeration requires finite variable bounds"
            )
        ctx.dependency("integer", v, "kind")
        ctx.execute(f"INSERT INTO {mapping} VALUES(?,?)", (i, v))
        ctx.execute(
            f"INSERT INTO {bounds} VALUES(?,?,?)", (i, str(ceil(lo)), str(floor(hi)))
        )
    constraints = ctx.raw_table("r INTEGER PRIMARY KEY,lo TEXT,hi TEXT")
    coefficients = ctx.raw_table("r INTEGER,v INTEGER,c TEXT,PRIMARY KEY(r,v)")
    premise_ids = ctx.raw_table("id INTEGER PRIMARY KEY")
    ctx.execute(f"INSERT OR IGNORE INTO {premise_ids} SELECT claim FROM {ctx.sources}")
    ctx.execute(
        f"INSERT OR IGNORE INTO {premise_ids} SELECT claim FROM {ctx.assumptions}"
    )

    def reduced(claim):
        t, c = ctx.accumulate(((1, claim.expr),))
        for (v,) in ctx.execute(f"SELECT v FROM {t} ORDER BY v"):
            if (
                ctx.execute(
                    f"SELECT 1 FROM {mapping} WHERE original=?", (v,)
                ).fetchone()
                is None
            ):
                raise UnsupportedEnumerationDomain(
                    f"Variable ID {v} remains outside the finite integer selection"
                )
        return t, c

    for (r,) in ctx.execute(f"SELECT id FROM {premise_ids} ORDER BY id"):
        cl = ctx.claim(r)
        t, c = reduced(cl)
        lo = str(-c) if cl.relation in (">=", "==") else "-inf"
        hi = str(-c) if cl.relation in ("<=", "==") else "inf"
        ctx.execute(f"INSERT INTO {constraints} VALUES(?,?,?)", (r, lo, hi))
        ctx.execute(
            f"INSERT INTO {coefficients} SELECT ?,m.v,t.c FROM {t} t JOIN {mapping} m ON m.original=t.v",
            (r,),
        )
    target_table, constant = reduced(ctx.claim(target_id))
    witness = ctx.raw_table("kind TEXT,v INTEGER,x TEXT,PRIMARY KEY(kind,v)")
    low = high = None
    nlow = nhigh = feasible = 0
    status = "PROVED"
    exhaustive = False
    stopped = None

    def copy_point(kind, assignment):
        ctx.execute(f"DELETE FROM {witness} WHERE kind=?", (kind,))
        ctx.execute(
            f"INSERT INTO {witness} SELECT ?,m.original,a.x FROM {assignment} a JOIN {mapping} m ON m.v=a.v",
            (kind,),
        )

    def visit(assignment):
        nonlocal low, high, nlow, nhigh, feasible, status
        total = constant
        for c, x in ctx.execute(
            f"SELECT t.c,a.x FROM {target_table} t JOIN {mapping} m ON m.original=t.v JOIN {assignment} a ON a.v=m.v"
        ):
            total += Fraction(c) * int(x)
            ctx.check_size(total)
        # A completed point's statistics and witnesses are one atomic observation.
        ctx.clock()
        ctx.searching = False
        try:
            feasible += 1
            if low is None or total < low:
                low, nlow = total, 1
                copy_point("min", assignment)
            elif total == low:
                nlow += 1
            if high is None or total > high:
                high, nhigh = total, 1
                copy_point("max", assignment)
            elif total == high:
                nhigh += 1
            if not satisfied(target.relation, total):
                status = "REFUTED"
                copy_point("counterexample", assignment)
                return False
        finally:
            ctx.searching = True

    ctx.scope_info = "local integer implication; not full-model feasibility"
    ctx.started = monotonic()
    ctx.configure(candidate=True, max_states=max_states, time_limit=time_limit)
    # Input validation has completed; even a deadline before the first expansion
    # is a legitimate INCONCLUSIVE enumeration result, not a preparation error.
    ctx.prepared = True
    try:
        ctx.clock()
        if ctx.contradiction:
            exhaustive = True
        else:
            exhaustive = walk(ctx, bounds, constraints, coefficients, visit)
    except SearchStopped as exc:
        status = "INCONCLUSIVE"
        stopped = str(exc)
    ctx.searching = False
    vs = ctx.publish(
        {"var": ref_type(ctx.owner, "var")},
        (
            dict(var=v)
            for v, in ctx.execute(f"SELECT original FROM {mapping} ORDER BY original")
        ),
        key=("var",),
    )
    schema = {"var": ref_type(ctx.owner, "var"), "value": DType("number")}

    def point(kind):
        return ctx.publish(
            schema,
            (
                dict(var=v, value=Fraction(x))
                for v, x in ctx.execute(
                    f"SELECT v,x FROM {witness} WHERE kind=? ORDER BY v", (kind,)
                )
            ),
            key=("var",),
        )

    minimum, maximum, counterexample = (
        point("min"),
        point("max"),
        point("counterexample"),
    )
    summary = dict(
        exhaustive=exhaustive,
        states=ctx.states,
        feasible_states_observed=feasible,
        vacuous=exhaustive and feasible == 0,
        minimum=low,
        maximum=high,
        minimum_count=nlow,
        maximum_count=nhigh,
        extrema="exact" if exhaustive else "observed only",
        stop_reason=stopped or ("exhausted" if exhaustive else "local counterexample"),
        counterexample_is_model_solution=False,
        algorithm_version=1,
    )
    return ctx.finish_report(
        status,
        ctx.claim(target_id),
        summary,
        local=True,
        complete=status != "INCONCLUSIVE",
        evidence={"variables": vs, "minimizer": minimum, "maximizer": maximum},
        counterexample=counterexample,
        recipe=dict(
            kind="enumerate",
            target=target_id,
            max_states=max_states,
            time_limit=time_limit,
        ),
    )
