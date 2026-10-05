# Exact expressions and compact certificates

`LinExpr` describes an affine function; `Claim` describes a proposition.
`BatchProof` is an immutable checked result with compact target, certificate,
status and dependency columns. `ProofRef` is a scalar view into that batch.
Frame/indexed diagnostics retain `Report`; full-point checks use `PointReport`.
These result types have separate serialization contracts.

## Expressions and Claims

```python
x = m.vars.by_name("x")
y = m.vars.by_name("y")
e = 2*x - y + mf.q("1/3")
claim = e <= 4
identity = (x-y).eq(0)
```

Use exact integers, Fractions, finite Decimal values or `mf.q(...)`. Approximate
floating values do not establish exact proofs. Entity `x == y` compares identity;
mathematical equality uses `.eq()`. Expression substitution is exact and does not
change the model. A Claim is not evidence merely because Python constructed it.
Rows expose `lb_claim`, `ub_claim`, and `eq_claim` where the corresponding side
exists. Model variables expose their bounds as Claims.

## Explicit linear checking

```python
row = m.rows.by_name("capacity")
p = m.check.linear(row.ub_claim, using=[row.ub_claim]).require()
```

Supply source Claims or checked scalar proofs, optionally weighted by exact
multipliers. Inequality multipliers must be nonnegative. Equality sources allow
signed multipliers. Foreign-snapshot entities and proofs are rejected.

`residual="exact"` requires the exact combination to establish the target.
`residual="bounds"` may discharge remaining coefficients using valid variable
bounds. Those precise bound dependencies remain part of the proof. The checker
borrows CSR/CSC and the exact number dictionary; it does not query SQL per term.
Large residuals can use bounded exact external accumulation.

For a scalar equality with separate directional witnesses:

```python
p = m.check.linear(x.eq(y), using={"le": le_sources, "ge": ge_sources})
p.require()
```

`identity(left, right)` checks exact affine identity, optionally using explicitly
supplied equality sources. `infeasible(using=...)` checks an explicit contradiction.
Neither operation runs a solver or searches for missing multipliers.

## Batch checking

When targets and sources are model rows, pass IDs and ragged witness arrays:

```python
certificate = mf.CertificateBatch.paths([[0, 1], [3, 4, 5]])
proof = m.check.linear_rows(targets=[2, 6], witnesses=certificate, side="ub")
proof.require()
selected = proof.for_target(2)
```

This example assumes the indicated source paths actually imply the targets.
`CertificateBatch(offsets, sources, multipliers, sides, kinds)` handles general
ragged sources. Offsets have one more entry than targets. `Targets` supports row,
variable and explicit affine targets. `m.check.linear_batch()` stages multiple
keyed scalar requests in typed buffers; `.add(key=key, target=claim, using=...)` and `.run()`
produce one batch. Closing the builder releases unpublished staging storage.

`linear_many(targets=..., terms=..., using=...)` accepts Frames:

- targets: `target`, `relation`, `rhs`;
- terms: `target`, typed `var`, exact `coef`;
- using: `target`, `multiplier`, and exactly one of `row`, `var`, `proof`, with
  `side` for model facts. Proof keys are resolved through `reports={key: proof}`.

Frame inputs compile once to ragged arrays. Duplicate affine coefficients coalesce
exactly. There is no Claim or Report allocation per target. Frame certificates use
`direction="main"`; separate directional equality witnesses use scalar `linear`.

All target statuses are retained. `.status`, `.ok`, `.summary`, `.require()`,
`.target_ids`, `.for_target(key)` and `.select(ids)` do not change proof authority.
Missing evidence yields INCONCLUSIVE; malformed certificates raise. `REFUTED` is
used only when the declared check establishes a refutation. Proof objects have no
implicit truth value: use `.ok` or `.require()` explicitly.

## Scope, assumptions and integer reasoning

`assume=[...]` records conditional premises. Parent-proof assumptions propagate;
a conditional result cannot silently become an unconditional edit justification.
Relaxation reasoning and integer-only conclusions have distinct scope.

`round(target, from_=proof)` rounds a proved bound on an integer-valued expression.
Integer coefficients alone are insufficient: the variables' domains must justify
integrality. `BatchProof.round_many()` handles proved variable bounds, and
`affine_integrality(vars=..., rows=...)` checks explicit defining rows.

`cases(target, var=v, cases={value: proof, ...})` requires exhaustive coverage of a
finite integer domain and a matching case assumption in every branch. All branch
conclusions must match. It discharges only the corresponding split assumption.

`one_hot_affine(groups=...)` validates one-hot rows and affine definitions.
`.bound_exclusions()` derives impossible options from exact bounds; its compact
batch can justify tightening and fixed-variable elimination. This primitive does
not infer arbitrary one-hot semantics from names.

## Finite enumeration and points

`enumerate(target, vars=..., using=..., assume=..., max_states=..., time_limit=...)`
checks the selected finite integer domain. Variables outside the selection must
be fixed or absent from the local premises and target. An exhaustive local proof
is conditional on its stated premises. A local counterexample is not automatically
a feasible solution of the complete model. State/time exhaustion is inconclusive.

`solution(values, missing="error")` validates bounds, integrality, every row and the
objective of an exact complete point. A mapping or typed variable/value Frame is
accepted; the Frame input streams into exact sparse arrays. `missing="zero"` assigns
zero to omitted live variables. Duplicate or foreign variables are errors.
`domain="relaxation"` is not a solution-check mode; use matrix activity for fractional
point calculations. A feasible point does not establish optimality.

```python
r = m.check.solution(point_frame, memory_mb=128)
r.require()
r.summary
r.evidence["violating_vars"]
r.evidence["violating_rows"]
r.close()
```

Point evidence is distinct from linear implication evidence and cannot justify
arbitrary row deletion.

## Persistence and lifecycle

```python
# A scalar ProofRef shares its complete parent batch.
p.batch.write("certificate.json")
replayed = mf.BatchProof.replay("certificate.json", model=m)
replayed.require()
```

Replay checks snapshot identity/version and recomputes the mathematical result.
A serialized status cannot issue a trusted proof. Scalar `ProofRef.write()` has a
separate wrapper containing its batch and selected index; replay the wrapper's
`batch` with `BatchProof.replay`, then select its original target. Old numerical
`Report.replay` recipes require rebuilding compact certificates.

Proofs and overlays retain shared immutable snapshot data. Public views of a closed
Model still fail lifecycle guards; a separately live child retains the base arrays
it needs. Numerical input, computation workspace and lazy presentation have distinct
ownership. Native v6 includes the base and required proof/delta artifacts, but a
loaded bundle needs `audit(source=...)` before its historical semantic labels are
trusted.

## Resources and limitations

Exact sparse residuals, point vectors and typed array workspaces support memory
budgets and disk spill; budgets describe workspace, not total RSS. A single exact
value can exceed the minimum workspace and be rejected explicitly. SQL is not a
numeric overflow backend. Diagnostic views preserve complete failures and may
expand them on demand.

Uniform resource accounting is still incomplete in some Frame, finite-case,
search and IR paths. Do not assume every accepted historical resource option bounds
all intermediate containers. See [implementation status](RUNTIME_V5_STATUS.md) and
[API migration](API_MIGRATION.md). Runnable scalar and edit examples are
[`exact_certificates.py`](../examples/exact_certificates.py) and
[`exact_edits.py`](../examples/exact_edits.py).
