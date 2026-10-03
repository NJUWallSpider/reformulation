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
        arithmetic="exact",
        **kwargs,
    ):
        self._owner.model
        ctx = ProofContext.__new__(ProofContext)
        try:
            ctx.__init__(
                self._owner,
                method.__name__,
                memory_mb=memory_mb,
                disk_mb=disk_mb,
                domain=domain,
                arithmetic=arithmetic,
            )
            report = method(self, ctx, *args, **kwargs)
            from mipframe.check.batch import retain_report

            result = retain_report(report)
            ctx.close()
            return result
        except BaseException as exc:
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
                ("arithmetic", "exact"),
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
    Checker [api.Checker]

    Exact certificate verification through m.check.

    Checker [type]

    When to use: Exact certificate verification through m.check.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.proofs
    MIPFRAME HELP END
    """
    def __init__(self, owner):
        self._owner = owner

    def batch(self, method="linear", *, residual="exact", domain="auto", memory_mb=128, disk_mb=None):
        """
        Incrementally stage linear checks with scalar arguments and per-target assumptions.

        MIPFRAME HELP BEGIN
        Checker.batch [api.Checker.batch]

        Build a batch of linear certificates with scalar arguments and per-target assumptions.

        Checker.batch(method='linear', *, residual='exact', domain='auto', memory_mb=128, disk_mb=None)

        When to use: Build a batch of linear certificates with scalar arguments and per-target assumptions.

        Inputs: method="linear"; residual="exact" or "bounds"; domain="auto", "relaxation" or "integer"; memory_mb and disk_mb apply to the whole batch.

        Returns: LinearBatch context manager. add stages each check; run verifies all targets and returns a Report.

        Establishes: Consumes inputs incrementally in shared storage. No caller-authored schemas or reports mapping are required.

        Does not establish: No multiplier search, condition discharge, enumeration batch or model edit.

        Failures: Unsupported methods or options raise. Invalid inputs, cancellation and resource failures abort unpublished work.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Illustrative example (requires caller-provided inputs):
        with m.check.batch("linear", residual="bounds") as batch:
            for row in m.rows:
                batch.add(key=str(row.id), target=row.ub_claim, using=[row.ub_claim])
            report = batch.run()
        report.require()

        The example requires finite upper bounds on the selected rows. Replace targets and sources with the actual propositions and certificates to verify.
        Prefer this builder for ordinary Python loops. If targets/terms/using are already Frames, use linear_many instead.
        The returned report remains usable after leaving the with block. Use report.for_target(key) for one conclusion, including its explicit and inherited assumptions.

        See also: api.LinearBatch.add, api.LinearBatch.run, api.LinearBatch.close, api.Checker.linear_many, concept.report-resources, concept.conditions
        MIPFRAME HELP END
        """
        from mipframe.check.batch import LinearBatch

        if method != "linear":
            raise ValueError('batch currently supports method="linear"')
        return LinearBatch(self, residual=residual, domain=domain,
                           memory_mb=memory_mb, disk_mb=disk_mb)

    def linear_many(self, *, targets, terms, using=(), reports=None, residual="exact", domain="auto", memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Checker.linear_many [api.Checker.linear_many]

        Batch explicit linear certificates in shared paged working storage.

        Checker.linear_many(*, targets, terms, using=(), reports=None, residual='exact', domain='auto', memory_mb=128, disk_mb=None)

        When to use: Batch explicit linear certificates in shared paged working storage.

        Inputs: Exact claims, original source sides or previously verified Reports; see signature.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: Targets keyed by strings; for_target returns one checked conclusion. Overall PROVED requires all targets proved.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        targets has target/relation/rhs, with unique string target IDs; terms has target/var/coef.
        using has target and exactly one source field row/var/proof per record, plus side and multiplier (direction for equality target sides).
        Reference columns use m.rows.schema["row"] or m.vars.schema["var"]. A proof source is a string key in the explicit reports mapping.
        Use residual="bounds" for bound compensation. This Frame-native entry point has no assume parameter; use check.batch("linear").add(..., assume=...) for per-target assumptions and ordinary Claim/Report inputs.
        The result shares paged storage across targets; inspect evidence and use for_target(target_id) for one conclusion. require() on the batch requires every target to be PROVED.

        See also: concept.proofs, concept.conditions, api.Checker.batch, api.Frame.schema, api.Report.for_target, concept.report-resources
        MIPFRAME HELP END
        """
        from mipframe.check.batch import linear_many
        return linear_many(self, targets=targets, terms=terms, using=using, reports=reports, residual=residual, domain=domain, memory_mb=memory_mb, disk_mb=disk_mb)

    @checked
    def cases(self, ctx, target, *, var, cases):
        """
        Discharge an exhaustive finite integer split over one variable.

        MIPFRAME HELP BEGIN
        Checker.cases [api.Checker.cases]

        Discharge an exhaustive finite integer split over one variable.

        Checker.cases(target, *, var, cases, memory_mb=128, disk_mb=None, domain='auto', arithmetic='exact')

        When to use: Discharge an exhaustive finite integer split over one variable.

        Inputs: Exact claims, original source sides or previously verified Reports; see signature.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: Every branch proves the same exact target; only its matching equality assumption is removed. Remaining assumptions are unioned. The result is integer-only.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        var is a same-snapshot Var or VarRef with integer kind and finite model bounds. cases maps exact integer values to verified exact PROVED Reports with the same target relation, constant and coefficients.
        Keys must exactly cover ceil(lb)..floor(ub); duplicates after normalization, empty domains, floats and booleans are rejected. Coverage never uses branch assumptions.
        Each branch must assume a*y+b==0 for this variable and value. Remove matching equalities only; preserve the union of every other explicit or inherited assumption. Nested calls can discharge multiple variables.
        domain="auto" and "integer" produce integer proofs; "relaxation" is rejected. Missing/invalid branches raise, not REFUTED. Infeasible Reports cannot substitute for target proofs.
        Use ordinary linear checks to align stronger or differently scaled conclusions first. Branches may contain continuous variables. Results support Edit.because only when no assumptions remain; all model fact dependencies and integer-domain coverage bounds remain auditable.
        The cases and discharged evidence Frames record branch parent indices and removed assumptions. Replay rechecks all branches and coverage.

        See also: concept.proofs, concept.conditions, recipe.cases
        MIPFRAME HELP END
        """
        from mipframe.check.cases import discharge_cases

        return discharge_cases(ctx, target, var, cases)

    @checked
    def identity(self, ctx, left, right, *, using=(), assume=()):
        """
        MIPFRAME HELP BEGIN
        Checker.identity [api.Checker.identity]

        Check an exact affine identity or explicit equality combination.

        Checker.identity(left, right, *, using=(), assume=(), memory_mb=128, disk_mb=None, domain='auto', arithmetic='exact')

        When to use: Check an exact affine identity or explicit equality combination.

        Inputs: Exact claims, original source sides or previously verified Reports; see signature.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: No-premise coefficient mismatch refutes identity; insufficient specified premises yield INCONCLUSIVE.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Without premises, compare the exact affine expressions. With using, verify the supplied equality combination.

        See also: concept.proofs, concept.conditions
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
        Checker.linear [api.Checker.linear]

        Verify that supplied sources imply the target inequality or equality.

        Checker.linear(target, *, using=(), residual='exact', assume=(), memory_mb=128, disk_mb=None, domain='auto', arithmetic='exact')

        When to use: Verify that supplied sources imply the target inequality or equality.

        Inputs: Exact claims, original source sides or previously verified Reports; see signature.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: Inequality multipliers nonnegative; equality multipliers may have either sign. bounds compensates exact residuals using required bounds.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Equality targets use equality-only sources, or explicit using={"le": ..., "ge": ...} certificates. residual="bounds" compensates coefficient residuals with original variable bounds.

        See also: concept.proofs, concept.conditions
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

    @checked
    def infeasible(self, ctx, *, using=(), residual="exact", assume=()):
        """
        MIPFRAME HELP BEGIN
        Checker.infeasible [api.Checker.infeasible]

        Verify an explicit contradiction certificate.

        Checker.infeasible(*, using=(), residual='exact', assume=(), memory_mb=128, disk_mb=None, domain='auto', arithmetic='exact')

        When to use: Verify an explicit contradiction certificate.

        Inputs: Exact claims, original source sides or previously verified Reports; see signature.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: PROVED means contradiction in the recorded scope; failure to find contradiction is INCONCLUSIVE, never feasible.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Supply a contradiction combination explicitly. Single-variable assumptions tighten bounds or substitute values and remain recorded in the conditional scope.

        See also: concept.proofs, concept.conditions
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
        Checker.round [api.Checker.round]

        Round a proved bound on a model-integer-valued expression.

        Checker.round(target, *, from_, memory_mb=128, disk_mb=None, domain='auto', arithmetic='exact')

        When to use: Round a proved bound on a model-integer-valued expression.

        Inputs: Exact claims, original source sides or previously verified Reports; see signature.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: Same nonconstant integer coefficients and integer variables required; floor upper / ceil lower; scope becomes integer.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        The target must keep the same nonconstant integer-valued expression as the proved source bound. Round upper bounds down and lower bounds up.

        See also: concept.proofs, concept.conditions
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
            if c.denominator != 1 or ctx.owner.vars.by_id(v).kind != "I":
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
        Checker.solution [api.Checker.solution]

        Validate a complete exact point against the mathematical integer model, including integrality, original bounds and both row sides.

        Checker.solution(values, *, value='value', missing='error', memory_mb=128, disk_mb=None, domain='auto', arithmetic='exact')

        When to use: Validate a complete exact point against the mathematical integer model, including integrality, original bounds and both row sides.

        Inputs: Exact Frame(var,value) or Var/VarRef mapping from this snapshot. missing="error" requires every variable; missing="zero" explicitly fills absent values.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: Returns all violations or feasibility with exact objective. No optimality conclusion.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        missing="error" requires all model variables; missing="zero" is explicit. Inputs from a solver are not silently rounded.
        domain="auto" and domain="integer" both enforce the original integer domains; domain="relaxation" is rejected. For fractional LP points, use m.matrix.activity(values) plus exact checks of both row sides and every variable bound. Verify complete variable coverage, including variables absent from all rows; see limitation.fractional-point.

        See also: concept.proofs, concept.conditions, limitation.fractional-point
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
                var = ctx.owner.vars.by_id(v)
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
                row = ctx.owner.rows.by_id(r)
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
        Checker.enumerate [api.Checker.enumerate]

        Verify a claim on a finite local integer domain.

        Checker.enumerate(target, *, vars, using=(), assume=(), max_states=100000, time_limit=None, memory_mb=128, disk_mb=None, domain='auto', arithmetic='exact')

        When to use: Verify a claim on a finite local integer domain.

        Inputs: Exact claims, original source sides or previously verified Reports; see signature.

        Returns: Report with complete scope, dependencies and evidence.

        Establishes: Exhaustion proves; a local counterexample refutes only this local proposition; budget exhaustion yields INCONCLUSIVE.

        Does not establish: No solver call, multiplier search, automatic condition discharge or model edit.

        Failures: Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        vars must contain every remaining variable after substitutions; continuous or unbounded integer domains are unsupported.

        See also: concept.proofs, concept.conditions
        MIPFRAME HELP END
        """
        from mipframe.check.enumeration import enumerate_claim

        return enumerate_claim(ctx, target, vars, using, assume, max_states, time_limit)
