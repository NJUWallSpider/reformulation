"""Explicit finite integer case discharge; branch conclusions stay scoped."""

from collections.abc import Mapping
from fractions import Fraction
from math import ceil, floor

from mipframe.check.certificate import InvalidCertificateError
from mipframe.frame.schema import DType
from mipframe.linear.expr import var_id
from mipframe.linear.scalar import q


def discharge_cases(ctx, target, variable, cases):
    if ctx.requested_domain == "relaxation":
        raise InvalidCertificateError("Case discharge proves integer conclusions only")
    ctx.proof_domain = "integer"
    v = var_id(ctx.owner, variable)
    var = ctx.owner.vars.loc[v]
    if var.kind != "I" or isinstance(var.lb, float) or isinstance(var.ub, float):
        raise InvalidCertificateError("Case variable must be integer with finite model bounds")
    lo, hi = ceil(var.lb), floor(var.ub)
    if lo > hi:
        raise InvalidCertificateError("Empty integer domain requires an infeasibility proof")
    if not isinstance(cases, Mapping):
        raise InvalidCertificateError("cases requires a mapping from exact integer values to Reports")
    target_id = ctx.add_claim(target)
    branches = ctx.raw_table("value TEXT PRIMARY KEY,parent INTEGER")
    discharged = ctx.raw_table("value TEXT,claim INTEGER")
    ctx.dependency("integer", v, "kind")
    ctx.dependency("var", v, "lb")
    ctx.dependency("var", v, "ub")

    # A conditional conclusion must never become an Edit fact provider. Expand
    # its semantic dependencies only; unconditional reports remain ordinary
    # proof dependencies so existing retained-fact closure still applies.
    visited = set()

    def prerequisites(root):
        pending = [root]
        while pending:
            report = pending.pop()
            if id(report) in visited:
                continue
            visited.add(id(report))
            if not report.scope.get("assumptions", 0):
                ctx.parent(report)
                continue
            # These are the canonical dependencies of a verified proof. Reading
            # them directly avoids allocating a Frame execution DB per branch.
            for kind, entity, side, parent in report._store.execute(
                f"SELECT kind,entity,side,parent FROM {report._store.deps} ORDER BY id"
            ):
                if kind == "report":
                    pending.append(report._parents[parent])
                elif kind in ("row", "var", "integer"):
                    ctx.dependency(kind, entity, side)
                else:
                    raise InvalidCertificateError("Unsupported case proof dependency")

    for key, report in cases.items():
        value = q(key)
        if value.denominator != 1 or not lo <= value <= hi:
            raise InvalidCertificateError("Case value is outside the finite integer domain")
        value_text = str(value)
        if ctx.execute(f"SELECT 1 FROM {branches} WHERE value=?", (value_text,)).fetchone():
            raise InvalidCertificateError("Duplicate normalized case value")
        parent = ctx.register_parent(report)
        conclusion = ctx.add_claim(report.conclusion)
        if not ctx.equal_claim(target_id, conclusion):
            raise InvalidCertificateError("Every case must prove the same exact target")
        matched = False
        for assumption in report._assumption_reader():
            terms = iter(assumption.expr._items(ctx))
            first = next(terms, None)
            if (assumption.relation == "==" and first is not None
                    and first[0] == v and first[1] != 0
                    and next(terms, None) is None
                    and -assumption.expr.constant / first[1] == value):
                matched = True
                ident = ctx.add_claim(assumption)
                ctx.execute(f"INSERT INTO {discharged} VALUES(?,?)", (value_text, ident))
            else:
                ctx.assume((assumption,))
        if not matched:
            raise InvalidCertificateError("Case report is missing its matching equality assumption")
        ctx.execute(f"INSERT INTO {branches} VALUES(?,?)", (value_text, parent))
        prerequisites(report)
    count = ctx.execute(f"SELECT count(*) FROM {branches}").fetchone()[0]
    if count != hi - lo + 1:
        raise InvalidCertificateError("Cases must exactly cover the finite integer domain")
    evidence = {
        "cases": ctx.publish(
            {"value": DType("number"), "parent": DType("int")},
            (dict(value=Fraction(k), parent=p) for k, p in
             ctx.execute(f"SELECT value,parent FROM {branches} ORDER BY rowid")),
            key=("value",),
        ),
        "discharged": ctx.publish(
            {"value": DType("number"), "claim": DType("int")},
            (dict(value=Fraction(k), claim=c) for k, c in
             ctx.execute(f"SELECT value,claim FROM {discharged} ORDER BY rowid")),
        ),
    }
    return ctx.finish_report(
        "PROVED", ctx.claim(target_id),
        dict(cases=count, lower=lo, upper=hi, exhaustive=True),
        evidence=evidence, recipe=dict(kind="cases", target=target_id, var=v),
    )
