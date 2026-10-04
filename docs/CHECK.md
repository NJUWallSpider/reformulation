# Exact expressions and verification

`LinExpr` describes an affine function. `Claim` describes a proposition. `Report`
records what an explicit verifier established, with its premises and scope.
Constructing a claim does not make it a model fact.

```python
import mipframe as mf

x = m.vars.by_name("x")
y = m.vars.by_name("y")
e = 2*x - y + mf.q("0.1")
target = e <= 10
```

All mathematical checkers in this release use exact arithmetic. Python floats, booleans,
NaN, and infinities are rejected as algebraic scalars. Use `int`, `Fraction`,
finite `Decimal`, or `mf.q("43/39")`. Exactness is relative to the current native
snapshot's saved rational values. It says nothing about an unknown model before
presolve or the original spelling of numeric tokens.

## Expressions and claims

```python
e = row.expr                       # raw coefficients, no row bounds
e = m.linexpr(T, var="var", coef="coef", constant=5)
e = m.linexpr(vars=V, coef=1)
e.terms                            # ordinary Frame(var, coef)
e.vars                             # VarFrame of nonzero coefficients
e.constant                         # Fraction
e.formula(max_terms=12)             # bounded Formula text

e2 = e.substitute({y: x + 2})
v = e.evaluate(values)             # Frame(var, value) or Var/VarRef mapping
```

Terms are sorted by original variable ID, merged exactly, and zeros removed.
`TermFrame` remains a relation of original matrix entries; an expression's terms
are an ordinary Frame because arithmetic may change their coefficients.

Substitution is simultaneous and one level deep: `{x: y, y: x}` swaps variables.
It supplies no proof of the replacement equations. Evaluation also makes no
feasibility claim. It requires all used values unless `missing="zero"` is explicit.

Addition, subtraction, negation and multiplication by exact scalars are supported.
Multiplication of variables or expressions raises `NonlinearExpressionError`.
`x == y` keeps entity identity semantics; use `e.eq(0)` or `x.eq(1)` to create an
equality Claim. Claims and Reports have no implicit Python truth value.

Original model sides are explicit:

```python
r.lb_claim
r.ub_claim
r.eq_claim                         # finite equality rows only
x.lb_claim
x.ub_claim
```

An absent finite bound raises `MissingBoundError`. These claims retain the original
row/variable identity and side. An independently written equivalent inequality
is an unproved claim until checked or declared as an assumption.

## Finite integer case discharge

`m.check.cases(target, var=y, cases={0:p0, 1:p1})` combines verified branch
Reports proving the same exact Claim under `y.eq(0)` and `y.eq(1)`. For a general
bounded integer variable, supply every integer from `ceil(y.lb)` to `floor(y.ub)`.
The domain comes from the source model, not branch assumptions. Keys use exact
scalars; floats, booleans, duplicate normalized values and empty domains fail.

Only matching single-variable equality assumptions are removed. All other
explicit and inherited assumptions remain as a union. Nested calls can discharge
additional variables. A successful result is always integer-only, and becomes
usable as `Edit.because` only when no assumptions remain. Branch targets may
involve continuous variables. Stronger or differently scaled branch conclusions
must first be aligned with ordinary linear certificates.

The result retains branch provenance, removed assumptions and the model facts
needed by every branch, including the split variable's bounds and integrality.
Replay reconstructs the proof; saved statuses never grant trust. Missing branches,
different targets or non-PROVED Reports are invalid certificates, not refutations.
Infeasibility Reports cannot stand in for target proofs.

For a complete two-stage big-M replacement example, see
[the case discharge recipe](agent/recipes/cases.md). Add the new proved row and
build first, then prove and delete the old row on that snapshot. Each direct-parent
Diff can establish `mip: SAME`; cross-generation Diff is not supported.

## Explicit certificates

```python
rep = m.check.linear(
    target,
    using=[(2, r1.ub_claim), (-1, r2.eq_claim)],
    residual="exact",
)
rep.require()
```

Unweighted sources use multiplier one. Lower sides are converted to upper-form
inequalities by negation. Inequality multipliers must be nonnegative; equality
multipliers may have either sign. Invalid inputs raise exceptions, rather than
returning a verdict about the target.

Large certificates accept a Frame with `row`, `side`, `multiplier`, or the
corresponding `var` column for variable bounds. A mixed Frame may contain nullable
`row` and `var` columns, with exactly one populated per record. Lists can combine
Frames and report sources. `InvalidCertificateError.violations`, when present,
contains the complete pageable list of malformed certificate records.

`residual="exact"` requires equal coefficients and permits a stronger source
right-hand side. `residual="bounds"` bounds the coefficient difference using the
needed original or conditional variable bounds. Its evidence shows every bound
and exact contribution. An unavailable required bound yields `INCONCLUSIVE`.

An equality target accepts one equality-only certificate, or two explicit
certificates:

```python
m.check.linear(
    (x - y).eq(0),
    using={"le": upper_certificate, "ge": lower_certificate},
).require()
```

Other rules:

| Checker | Establishes |
| --- | --- |
| `identity(e1, e2, using=...)` | Exact polynomial identity, or the specified combination of source equalities |
| `linear(target, using=..., residual=...)` | The specified linear certificate implies the target |
| `infeasible(using=..., residual=...)` | The supplied combination or conditional interval contradicts feasibility |
| `round(target, from_=report)` | Safe floor/ceiling for an integer-valued linear part |
| `enumerate(target, vars=..., using=...)` | Exhaustive finite **local** integer implication |
| `solution(values)` | A complete point satisfies the mathematical model, with its exact objective |

A failed polynomial identity is `REFUTED`. A combination that does not establish
an implication is `INCONCLUSIVE`: another certificate might work. Likewise,
failing to derive a contradiction does not establish feasibility.

Rounding requires a proved inequality, unchanged nonconstant coefficients,
integer coefficients and model-declared integer variables. It does not infer
integrality or search for a useful rescaling.

## Conditions, domains and reports

```python
conditional = m.check.linear(
    x >= 2,
    using=[gate.lb_claim],
    assume=[z.eq(1)],
)
next_report = m.check.linear(x >= 1, using=[conditional])
```

Single-variable equalities are substituted automatically. Single-variable
inequalities intersect the effective interval used by bounds verification.
General assumptions require explicit use in a certificate. All assumptions,
including inherited ones, remain in the result.

A report carries `status`, `conclusion`, `scope`, `method`, `dependencies`,
`summary`, `evidence`, `counterexample`, and `violations`. Evidence is a read-only
mapping of normalized Frames, including claims, coefficients, assumptions,
multipliers, residuals, and bounds. `explain()` shows the conclusion and immediate
dependencies; large tables stay pageable.

- `PROVED`: the declared conclusion is established.
- `REFUTED`: evidence refutes the **declared** proposition.
- `INCONCLUSIVE`: the certificate or allowed enumeration work did not decide it.

`ok` means `status == "PROVED"`; `require()` raises `ValidationError` otherwise.
`Report` is a compatibility name for the same Report implementation.
Existing Frame/ops/indexed checks retain their `summary` and `violations` APIs.
A successful relational check is not a linear inequality source.

Relaxation evidence can be used for integer conclusions. Integer-only evidence
cannot prove a relaxation claim. Use `domain="relaxation"` to require that stronger
scope; the default `"auto"` propagates dependency restrictions. A feasible-point
report does not establish a universally valid inequality or optimality.

Dependencies record original row sides, bounds, integer declarations and parent
reports. A row used to establish itself is self-implication, not a redundancy
certificate. Edit checks dependency closure against the final retained facts; a self-proof cannot justify removing its own last source.

## Local enumeration is not a MIP solver

```python
rep = m.check.enumerate(
    2*x + 3*y <= 12,
    vars=[x, y],
    using=[capacity.ub_claim],
    max_states=100_000,
)
```

Every remaining variable in the target and premises must be included in the
selection after automatic fixed-value substitution. Selected variables must be
declared integer with finite bounds. Continuous elimination and general bound
inference are not performed.

The verifier visits feasible assignments without collecting them all. It records
exact extrema when exhaustive, counts ties, and keeps the first stable minimizer
and maximizer. With a budget stop, extrema describe visited points only.
`max_states` counts shared DFS state expansions, not just feasible leaves;
`time_limit` is an optional cooperative limit on the enumeration phase after input
validation. Exhaustion without a counterexample is required for `PROVED`.
An empty feasible premise set is explicitly reported as vacuous.

A counterexample satisfies the declared local premises. It may violate an original
model row that was not included, so it is **not automatically a full-model feasible
counterexample**. The report prominently records this scope. Successful local
implications remain usable because their premises are model facts, proved facts,
or retained assumptions.

The public `mf.alg.integer.enumerate` keeps its different contract: return every
feasible assignment or raise `SearchBudgetExceeded`. Both interfaces share the
same paged exact traversal kernel.

## Point verification

`m.check.solution(values)` checks every original variable bound, integer domain,
and row side, and includes the objective constant and original sense. Missing
values are errors unless `missing="zero"` is explicit. Nonfinite values,
duplicates and wrong-snapshot references are rejected.

Violations are available in `evidence["violating_vars"]` and
`evidence["violating_rows"]`; the report also contains exact maximum violations.
No solver status, tolerance, or reported dual bound grants an exact verdict.

## Budgets, lifecycle and replay

Checkers share `memory_mb=128` and optional `disk_mb`; the minimum working-memory
budget is 16 KiB. Large intermediate relations and evidence spill to SQLite. These
budgets bound execution buffers, not process RSS. Evidence is never silently
truncated. Resource failures and cancellation raise and discard unpublished work.

All live expressions, claims, reports and Frames reject access when any model they
depend on closes. Detached records and specifications remain readable. Independent
imports have different identities; native save/load preserves identity and version.

Scalar mathematical checkers release their scratch workspace after copying the
completed certificate into shared storage for the live model owner. Keeping many
such Reports does not keep one database connection open per proof. Reports,
conclusion Claims, evidence Frames, parent proofs and staged Edits retain the
certificate nodes they need; the last dependent view releasing a node makes its
rows reclaimable. Free database pages are reused. Small expressions use bounded
exact memory; larger expressions share spill storage. Discarded temporary stores
do not rely on manual garbage collection to close their connections.

Retained evidence still consumes memory and disk. Independent batches and other
query results have their own stores, so these guarantees are not a global process
resource limit. There is no public per-Report or per-LinExpr `close()`; never close
private shared stores to release one result. `Model.close()` releases owned stores
and invalidates the corresponding live results.

```python
spec = report.to_spec()             # detached; bounded materialization
report.write("certificate.jsonl")   # streaming, atomically published

checked = mf.Report.replay(
    "certificate.jsonl",
    models=[m],
    relations={},                  # explicit bindings for external Frame inputs
)
checked.require()
```

Replay validates snapshot identity, rebuilds original sides, checks the dependency
DAG and reruns each checker. Serialized statuses are ignored. Model data is not
embedded in certificates; bind a matching native snapshot. Frame relational
checks and indexed verification also have executable recipes; indexed replay
requires the referenced completed indexed run. Frame-based diagnostics require
explicit bindings for external relations, just like ordinary Frame recipes.

`to_spec()` limits materialization to 128 MiB; `write()` streams complete records.
Certificates are not automatically attached to `m.save()`. No new checker runs
when loading a model or displaying IR.

See [the runnable example](../examples/exact_certificates.py). The test16/30/31/32/34
model files discussed in the design are not in this repository; deterministic
fixtures exercise those mathematical rules without claiming real-data validation.

## Batch conclusions and edits

For a Python loop of Claims, use the incremental builder:

```python
with m.check.linear_batch(residual="bounds") as batch:
    for key, target, sources, assumptions in checks:
        batch.add(key=key, target=target, using=sources, assume=assumptions)
    report = batch.run()

report.require()
proof = report.for_target(key)
```

Keys must be unique nonempty strings. `add` consumes and validates its inputs into
paged staging; it does not accumulate live target expressions in a Python list.
`using` accepts the same Claims, Reports, exact multipliers and directional
equality dictionaries as `linear`. `assume` is independent for each target,
including inherited assumptions. `run` verifies every target, seals the builder
and transfers storage to its returned Report. The Report and its views survive
builder context exit. Invalid inputs, resource failures or cancellation discard
unpublished work; an insufficient certificate instead produces INCONCLUSIVE.
The builder currently supports only `"linear"`; it does not discharge assumptions.

`m.check.linear_many(targets=..., terms=..., using=...)` executes multiple exact
linear certificates whose inputs already exist as Frames in shared paged storage.
This lower-level entry point has no explicit `assume` parameter. Both entrances
use the same scalar verification kernel. They return the same Report type;
`report.evidence["results"]` exposes per-target statuses and
`report.for_target(key)` returns a scalar Report without copying its coefficient
relations. Batch export/replay recomputes all conclusions. See [EDIT.md](EDIT.md)
for the normalized tables and how `because=` checks coverage and retained-fact
closure before a model edit can claim preservation.
