"""Explicit exact certificate replay. No multiplier or proof search."""

from fractions import Fraction
from functools import wraps
from math import ceil, floor
from mipframe.linear.expr import coerce, LinExpr, vector
from mipframe.linear.claim import Claim
from mipframe.linear.scalar import q
from mipframe.frame.schema import DType, ref_type, SchemaError
from mipframe.check.certificate import (
    ProofContext,
    InvalidCertificateError,
    UnsupportedEnumerationDomain,
    satisfied,
    number,
)


def checked(method):
    @wraps(method)
    def call(
        self,
        *args,
        memory_mb=128,
        disk_mb=None,
        domain="auto",
        **kwargs,
    ):
        self._owner.model
        runtime = None
        if method.__name__ == "cases" and disk_mb is None:
            from mipframe.check.workspace import ProofWorkspace
            from mipframe.store.query_store import memory_bytes
            amount = memory_bytes(memory_mb)
            runtime = getattr(self._owner, "_case_workspace", None)
            if runtime is None or runtime.db is None or runtime.memory != amount:
                if runtime is not None:
                    runtime.close()
                runtime = ProofWorkspace((self._owner,), memory_mb=memory_mb, max_output_rows=None)
                self._owner._case_workspace = runtime
            runtime.begin()
        ctx = ProofContext.__new__(ProofContext)
        try:
            ctx.__init__(
                self._owner,
                method.__name__,
                memory_mb=memory_mb,
                disk_mb=disk_mb,
                domain=domain,
                runtime=runtime,
            )
            report = method(self, ctx, *args, **kwargs)
            from mipframe.check.batch import retain_report

            result = retain_report(report)
            ctx.close()
            if runtime is not None:
                runtime.reset()
            return result
        except BaseException as exc:
            if runtime is not None:
                runtime.close()
            if isinstance(exc, InvalidCertificateError) and exc.violations is not None:
                ctx.searching = False
            else:
                ctx.close()
            raise

    from inspect import signature, Parameter

    call.__signature__ = signature(method).replace(
        parameters=[
            p for name, p in signature(method).parameters.items() if name != "ctx"
        ]
        + [
            Parameter(name, Parameter.KEYWORD_ONLY, default=value)
            for name, value in (
                ("memory_mb", 128),
                ("disk_mb", None),
                ("domain", "auto"),
            )
        ]
    )
    return call


def empty_vector(ctx, table):
    return ctx.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None


def evidence_vector(ctx, table):
    return ctx.publish(
        {"var": ref_type(ctx.owner, "var"), "coef": DType("number")},
        (
            dict(var=v, coef=Fraction(c))
            for v, c in ctx.execute(f"SELECT v,c FROM {table} ORDER BY v")
        ),
    )


def target_claim(ctx, target):
    ident = ctx.add_claim(target)
    return ident, ctx.claim(ident)


def direction_check(ctx, target, direction, residual, sign):
    from itertools import chain

    terms = chain(
        ((sign, target.expr),), ((-a, e) for a, e in ctx.source_terms(direction))
    )
    table, c = ctx.accumulate(terms)
    if residual == "exact":
        ok = empty_vector(ctx, table) and c <= 0
        upper = c if empty_vector(ctx, table) else None
    else:
        upper = ctx.bound(table, c, direction)
        ok = upper is not None and upper <= 0
    return ok, table, c, upper


class Checker:
    """
    MIPFRAME HELP BEGIN
    Exact certificate verification through m.check.

    Full contract: mf.help("api.Checker")
    MIPFRAME HELP END
    """
    def __init__(self, owner):
        self._owner = owner

    def linear_batch(self, *, residual="exact", domain="auto", memory_mb=128, disk_mb=None):
        """
        Incrementally stage linear checks with scalar arguments and per-target assumptions.

        MIPFRAME HELP BEGIN
        Build a batch of linear certificates with scalar arguments and per-target assumptions.

        Full contract: mf.help("api.Checker.linear_batch")
        MIPFRAME HELP END
        """
        from mipframe.check.batch import LinearBatch

        return LinearBatch(self, residual=residual, domain=domain,
                           memory_mb=memory_mb, disk_mb=disk_mb)

    def linear_many(self, *, targets, terms, using=(), reports=None, residual="exact", domain="auto", memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Batch explicit linear certificates in shared paged working storage.

        Full contract: mf.help("api.Checker.linear_many")
        MIPFRAME HELP END
        """
        from mipframe.check.batch import linear_many
        return linear_many(self, targets=targets, terms=terms, using=using, reports=reports, residual=residual, domain=domain, memory_mb=memory_mb, disk_mb=disk_mb)

    @checked
    def cases(self, ctx, target, *, var, cases):
        """
        Discharge an exhaustive finite integer split over one variable.

        MIPFRAME HELP BEGIN
        Discharge an exhaustive finite integer split over one variable.

        Full contract: mf.help("api.Checker.cases")
        MIPFRAME HELP END
        """
        from mipframe.check.cases import discharge_cases

        return discharge_cases(ctx, target, var, cases)

    @checked
    def identity(self, ctx, left, right, *, using=(), assume=()):
        """
        MIPFRAME HELP BEGIN
        Check an exact affine identity or explicit equality combination.

        Full contract: mf.help("api.Checker.identity")
        MIPFRAME HELP END
        """
        left, right = coerce(self._owner, left), coerce(self._owner, right)
        target_table, c = ctx.accumulate(((1, left), (-1, right)))
        target = Claim(ctx.expr(target_table, c), "==")
        target_id = ctx.add_claim(target)
        ctx.assume(assume).ingest(using)
        if ctx.execute(
            f"SELECT 1 FROM {ctx.sources} s JOIN {ctx.claims} c ON c.id=s.claim WHERE c.relation!='==' LIMIT 1"
        ).fetchone():
            raise InvalidCertificateError(
                "Identity certificates accept equalities only"
            )
        ctx.prepare_assumptions()
        from itertools import chain

        table, c = ctx.accumulate(
            chain(
                ((1, target.expr),),
                ((-a, e) for a, e in ctx.source_terms(oriented=False)),
            )
        )
        ok = empty_vector(ctx, table) and c == 0
        has_premises = bool(
            ctx.execute(
                f"SELECT 1 FROM {ctx.sources} UNION ALL SELECT 1 FROM {ctx.assumptions} LIMIT 1"
            ).fetchone()
        )
        status = "PROVED" if ok else "INCONCLUSIVE" if has_premises else "REFUTED"
        difference = evidence_vector(ctx, table)
        return ctx.finish_report(
            status,
            ctx.claim(target_id),
            dict(residual_constant=c, coefficient_match=empty_vector(ctx, table)),
            kind="identity",
            evidence={"difference": difference},
            counterexample=difference if status == "REFUTED" else None,
            recipe=dict(kind="identity", target=target_id),
        )

    @checked
    def linear(self, ctx, target, *, using=(), residual="exact", assume=()):
        """
        MIPFRAME HELP BEGIN
        Verify that supplied sources imply the target inequality or equality.

        Full contract: mf.help("api.Checker.linear")
        MIPFRAME HELP END
        """
        if residual not in ("exact", "bounds"):
            raise ValueError("residual must be exact or bounds")
        ident, target = target_claim(ctx, target)
        ctx.assume(assume)
        double = isinstance(using, dict)
        if double:
            if target.relation != "==" or set(using) != {"le", "ge"}:
                raise InvalidCertificateError(
                    "Directional certificates require an equality target and le/ge keys"
                )
            for d in ("le", "ge"):
                ctx.ingest(using[d], d)
        else:
            ctx.ingest(using)
        return verify_linear(ctx, ident, residual=residual, double=double)

    @checked
    def infeasible(self, ctx, *, using=(), residual="exact", assume=()):
        """
        MIPFRAME HELP BEGIN
        Verify an explicit contradiction certificate.

        Full contract: mf.help("api.Checker.infeasible")
        MIPFRAME HELP END
        """
        if residual not in ("exact", "bounds"):
            raise ValueError("residual must be exact or bounds")
        ctx.assume(assume).ingest(using)
        ctx.prepare_assumptions()
        table, c = ctx.accumulate(ctx.source_terms())
        equalities = (
            ctx.execute(
                f"SELECT 1 FROM {ctx.sources} s JOIN {ctx.claims} c ON c.id=s.claim WHERE c.relation!='==' LIMIT 1"
            ).fetchone()
            is None
        )
        low = high = c if empty_vector(ctx, table) else None
        if residual == "bounds":
            low = ctx.bound(table, c, "lower", upper=False)
            high = ctx.bound(table, c, "upper") if equalities else None
        ok = low is not None and low > 0 or equalities and high is not None and high < 0
        if ctx.contradiction:
            ok = True
        contradiction = ctx.publish(
            {
                "reason": DType("str"),
                "lower": DType("number", True),
                "upper": DType("number", True),
            },
            (
                [
                    dict(
                        reason=ctx.contradiction or "weighted sources contradict zero",
                        lower=low,
                        upper=high,
                    )
                ]
                if ok
                else []
            ),
        )
        target = Claim(LinExpr(ctx.owner, 1), "<=")
        return ctx.finish_report(
            "PROVED" if ok else "INCONCLUSIVE",
            target,
            dict(
                residual=residual,
                derived_lower=low,
                derived_upper=high,
                contradiction=ctx.contradiction,
            ),
            kind="infeasibility",
            evidence={
                "combination": evidence_vector(ctx, table),
                "contradiction": contradiction,
            },
            recipe=dict(kind="infeasible", residual=residual),
        )

    @checked
    def round(self, ctx, target, *, from_):
        """
        MIPFRAME HELP BEGIN
        Round a proved bound on a model-integer-valued expression.

        Full contract: mf.help("api.Checker.round")
        MIPFRAME HELP END
        """
        ident, target = target_claim(ctx, target)
        ctx.parent(from_)
        src = from_.conclusion
        if target.relation not in ("<=", ">=") or src.relation != target.relation:
            raise InvalidCertificateError(
                "Rounding requires inequalities with the same direction"
            )
        if ctx.requested_domain == "relaxation":
            raise InvalidCertificateError(
                "Integer rounding cannot produce a relaxation certificate"
            )
        ctx.proof_domain = "integer"
        from itertools import zip_longest

        if any(
            a != b
            for a, b in zip_longest(target.expr._items(ctx), src.expr._items(ctx))
        ):
            raise InvalidCertificateError(
                "Rounding requires identical nonconstant coefficients"
            )
        for v, c in src.expr._items(ctx):
            if c.denominator != 1 or ctx.owner.vars.loc[v].kind != "I":
                raise InvalidCertificateError(
                    "Rounding requires integer coefficients and declared integer variables"
                )
            ctx.dependency("integer", v, "kind")
        threshold = -src.expr.constant
        rounded = floor(threshold) if src.relation == "<=" else ceil(threshold)
        requested = -target.expr.constant
        ok = rounded <= requested if target.relation == "<=" else rounded >= requested
        return ctx.finish_report(
            "PROVED" if ok else "INCONCLUSIVE",
            target,
            dict(source_bound=threshold, rounded_bound=rounded, target_bound=requested),
            recipe=dict(kind="round", target=ident, parent=0),
        )

    @checked
    def solution(self, ctx, values, *, value="value", missing="error"):
        """
        MIPFRAME HELP BEGIN
        Validate a complete exact point against the mathematical integer model, including integrality, original bounds and both row sides.

        Full contract: mf.help("api.Checker.solution")
        MIPFRAME HELP END
        """
        if missing not in ("error", "zero"):
            raise ValueError("missing must be error or zero")
        ctx.proof_domain = "integer"
        if ctx.requested_domain == "relaxation":
            from mipframe._help.diagnostics import hint

            raise hint(ValueError(
                "solution checks the complete mathematical integer model, including integrality; "
                'domain="relaxation" is not supported. For a fractional LP point, use '
                "m.matrix.activity(values) plus exact checks of both row sides, "
                "all variable bounds and complete variable coverage."
            ), "limitation.fractional-point")
        table = vector(ctx, ctx.owner, values, value)
        nv = ctx.owner.shape[1]
        for v in range(nv):
            if ctx.execute(f"SELECT 1 FROM {table} WHERE v=?", (v,)).fetchone() is None:
                if missing == "error":
                    raise SchemaError(f"Missing solution variable ID {v}")
                ctx.execute(f"INSERT INTO {table} VALUES(?,?)", (v, "0"))
        vschema = {
            "var": ref_type(ctx.owner, "var"),
            "kind": DType("str"),
            "value": DType("number"),
            "bound": DType("number"),
            "violation": DType("number"),
        }
        rschema = {
            "row": ref_type(ctx.owner, "row"),
            "side": DType("str"),
            "activity": DType("number"),
            "bound": DType("number"),
            "violation": DType("number"),
        }
        maximum = {"bound": Fraction(0), "integrality": Fraction(0), "row": Fraction(0)}
        objective = ctx.owner.objective.constant

        def variable_errors():
            nonlocal objective
            for v, x in ctx.execute(f"SELECT v,c FROM {table} ORDER BY v"):
                x = Fraction(x)
                var = ctx.owner.vars.loc[v]
                objective += var.obj * x
                ctx.check_size(objective)
                for side, b in (("lb", var.lb), ("ub", var.ub)):
                    violation = b - x if side == "lb" else x - b
                    if violation > 0:
                        maximum["bound"] = max(maximum["bound"], violation)
                        yield dict(
                            var=v, kind=side, value=x, bound=b, violation=violation
                        )
                if var.kind == "I" and x.denominator != 1:
                    delta = min(x - floor(x), ceil(x) - x)
                    maximum["integrality"] = max(maximum["integrality"], delta)
                    yield dict(
                        var=v,
                        kind="integrality",
                        value=x,
                        bound=Fraction(round(x)),
                        violation=delta,
                    )

        var_errors = ctx.publish(vschema, variable_errors())

        def row_errors():
            m = ctx.owner.model
            for r in range(ctx.owner.shape[0]):
                total = Fraction(0)
                for ids, positions in m.sparse.blocks(
                    "row", r, max(1, ctx.memory // 128)
                ):
                    for v, p in zip(ids, positions):
                        x = Fraction(
                            ctx.execute(
                                f"SELECT c FROM {table} WHERE v=?", (int(v),)
                            ).fetchone()[0]
                        )
                        if x:
                            total += x * m.num(int(m.coeff_id[int(p)]))
                            ctx.check_size(total)
                row = ctx.owner.rows.loc[r]
                for side, b in (("lb", row.lb), ("ub", row.ub)):
                    delta = b - total if side == "lb" else total - b
                    if delta > 0:
                        maximum["row"] = max(maximum["row"], delta)
                        yield dict(
                            row=r, side=side, activity=total, bound=b, violation=delta
                        )

        row_errors = ctx.publish(rschema, row_errors())
        point = ctx.publish(
            {"var": ref_type(ctx.owner, "var"), "value": DType("number")},
            (
                dict(var=v, value=Fraction(x))
                for v, x in ctx.execute(f"SELECT v,c FROM {table} ORDER BY v")
            ),
            key=("var",),
        )
        ok = len(var_errors) == 0 and len(row_errors) == 0
        summary = dict(
            objective=objective,
            objective_sense=ctx.owner.objective.sense,
            max_bound_violation=maximum["bound"],
            max_integrality_violation=maximum["integrality"],
            max_row_violation=maximum["row"],
            violating_vars=len(var_errors),
            violating_rows=len(row_errors),
            optimality_proved=False,
        )
        result = ctx.finish_report(
            "PROVED" if ok else "REFUTED",
            dict(kind="point_feasibility"),
            summary,
            kind="solution",
            evidence={
                "values": point,
                "violating_vars": var_errors,
                "violating_rows": row_errors,
            },
            counterexample=point if not ok else None,
            recipe=dict(kind="solution", missing=missing),
        )
        result._violations = ctx.publish(
            {"axis": DType("str"), "id": DType("int"), "reason": DType("str")},
            (
                dict(
                    axis=axis,
                    id=r[name].id,
                    reason=r["kind" if axis == "var" else "side"],
                )
                for axis, f, name in (
                    ("var", var_errors, "var"),
                    ("row", row_errors, "row"),
                )
                for r in f
            ),
        )
        return result

    @checked
    def enumerate(
        self,
        ctx,
        target,
        *,
        vars,
        using=(),
        assume=(),
        max_states=100_000,
        time_limit=None,
    ):
        """
        MIPFRAME HELP BEGIN
        Verify a claim on a finite local integer domain.

        Full contract: mf.help("api.Checker.enumerate")
        MIPFRAME HELP END
        """
        from mipframe.check.enumeration import enumerate_claim

        return enumerate_claim(ctx, target, vars, using, assume, max_states, time_limit)


def verify_linear(ctx, ident, *, residual, double):
    """Verify already normalized inputs; shared by scalar, batch and replay."""
    target = ctx.claim(ident)
    ctx.prepare_assumptions()
    evidence = {}
    details = {}
    if target.relation == "==" and not double:
        if ctx.execute(
            f"SELECT 1 FROM {ctx.sources} s JOIN {ctx.claims} c ON c.id=s.claim WHERE c.relation!='==' LIMIT 1"
        ).fetchone():
            raise InvalidCertificateError(
                "Equality requires equality sources or explicit le/ge certificates"
            )
        from itertools import chain

        table, c = ctx.accumulate(
            chain(
                ((1, target.expr),),
                ((-a, e) for a, e in ctx.source_terms(oriented=False)),
            )
        )
        ok = empty_vector(ctx, table) and c == 0
        evidence["residual"] = evidence_vector(ctx, table)
        details["residual_constant"] = c
    else:
        ok = True
        for d, sign in (
            (("le", 1), ("ge", -1))
            if double
            else (("main", -1 if target.relation == ">=" else 1),)
        ):
            passed, table, c, upper = direction_check(
                ctx, target, d, residual, sign
            )
            ok = ok and passed
            evidence["residual_" + d] = evidence_vector(ctx, table)
            details[d + "_residual_constant"] = c
            details[d + "_upper"] = upper
    if ctx.contradiction:
        ok = True
        details["contradiction"] = ctx.contradiction
    return ctx.finish_report(
        "PROVED" if ok else "INCONCLUSIVE",
        target,
        dict(residual=residual, **details),
        evidence=evidence,
        recipe=dict(kind="linear", target=ident, residual=residual, double=double),
    )
