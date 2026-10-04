# Exact edits of immutable snapshots

`m.edit()` stages changes to one source snapshot. `build()` produces an independent
Model with a new UUID and an explicit parent; it never mutates the source. All
operations in an Edit take effect together. A proof that needs a newly added row
must be checked after building an intermediate snapshot.

For a finite integer case split, `m.check.cases(target, var=y, cases=branches)`
can produce unconditional integer evidence for `because`. For a big-M replacement,
first add the new row with that proof, build, then prove and delete the old row
on the new snapshot. See the executable [case discharge recipe](agent/recipes/cases.md).
Both direct-parent Diffs can certify `mip: SAME` and `optimal_value: PRESERVED`;
integer-only evidence does not assert LP equivalence or compute an optimal value.

```python
import mipframe as mf

with mf.read("model.lp") as m:
    r = m.rows.by_name("capacity")
    proof = m.check.linear(r.ub_claim, using=[r.ub_claim])
    edit = m.edit(memory_mb=128, disk_mb=None)
    edit.add_row(r.ub_claim, name="capacity_copy", because=proof)
    edit.summary()                  # Formula text, no model construction or IR
    candidate = edit.build()
    diff = candidate.diff(m)
    assert diff.semantic["lp"] == "SAME"
    candidate.save("candidate.native")
```

The candidate remains usable after the source closes. Diff and live proofs that
reference the source do not. Edited snapshots defer default IR/indexed preparation until requested;
use `build(prepare="indexed")` for eager preparation. A no-op build still receives a new UUID; its mathematical content version
can equal its parent's version. A successful Edit is sealed and cannot be built
again. Failed builds do not publish a candidate; unsealed edits can be retried.
Use `Edit.close()` or a context manager to release its scratch resources.

## Operations and alignment

- `add_row(claim, name=..., because=...)`: move the Claim's constant into its bound.
- `add_row(expr, lb=..., ub=...)`: create a ranged row. Do not mix Claim and bounds.
- `add_rows(rows=..., terms=..., because=...)`: add a complete batch.
- `drop_rows(R, because=...)`: delete source rows.
- `set_row_bounds(R, lb=..., ub=..., because=...)`: preserve coefficients.
- `set_var_bounds(V, lb=..., ub=..., because=...)` and `fix(V, value, because=...)`.
- `set_domains(V, kind="C")`: use only canonical `C/I`, not `B`.
- `add_vars(specs)` and `drop_vars(V)`.
- `set_objective(expr, sense="min")`: replace the entire objective and constant.
- `replace_rows(old, rows=..., terms=..., correspondence=..., because=...)`:
  explicit add/drop sugar; the correspondence has `old_row, new_label` columns.
  Its `because` is a mapping with optional `add` and `drop` Report pools.

Selectors are source Row/Var entities or their entity Frames. Separate loaded
instances of the same uid/version are compatible. Names and untyped integers are
not selectors. Repeated deletes are idempotent; conflicting writes to the same
field, or deleting and modifying the same entity, raise `EditConflictError`.
Each API call is transactional: a failed batch leaves earlier declarations intact.

Bounds accept exact scalars, or a `Frame(row/var, value)` whose keys match the
selection exactly in both directions. Input row order is irrelevant. Omitted
arguments preserve the original side; `None` is not an infinity or omission marker.
Use `-math.inf` for an unbounded lower side and `math.inf` for an unbounded upper
side. Finite floats, bools and NaNs are rejected. `kind` can also use a keyed
`Frame(var, value)` with `C/I` values.

Bulk row specs have `label` and optional `name, lb, ub`; each omitted side is
unbounded. Terms have `label, var, coef`. Labels are nonempty strings unique within
a batch. Duplicate terms are summed exactly and cancellation removes zeros.
A label with no terms still creates an empty row. Names must be unique on the final
axis; an explicitly replaced row may reuse its deleted predecessor's name.

`drop_vars` is garbage collection, not projection or substitution. Every removed
variable must be absent from surviving rows and have zero final objective cost.
Otherwise `NonIsolatedVariableError.report.violations` explains the failure.

## Draft variables

Variable specs have `label` and optional `name, kind, lb, ub, obj`; defaults are
`C, 0, +inf, 0`. Generated names avoid collisions. Their return value is an ordinary
Frame, and its `var` column contains Edit-local references:

```python
specs = mf.Frame.from_records(
    [{"label": "total", "name": "total"}],
    schema={"label": "str", "name": "str"},
)
edit = m.edit()
Y = edit.add_vars(specs)
y = Y.loc["total"].var
x1, x2 = m.vars.iloc[0], m.vars.iloc[1]
new_row = edit.add_row((y - x1 - x2).eq(0), name="define_total")
edit.set_objective(y + 3, sense="min")
candidate = edit.build()
actual_var = candidate.lineage.resolve(y.ref)
actual_row = candidate.lineage.resolve(new_row)
```

Draft expressions support affine arithmetic, comparisons and `.formula()`. They
are construction objects, not model facts: `m.check`, `m.terms`, and `m.linexpr`
reject them. Different Edits cannot exchange draft identities. Source-first
arithmetic such as `x1 + y` is supported as well. Draft references have a distinct
Frame scalar type; they never become source VarRefs by integer-ID coincidence.

Bulk terms involving both types use nullable `var` and `draft_var` columns, with
exactly one endpoint per record. Obtain the draft dtype from `Y.schema["var"]`;
use `.optional()` for nullable endpoint columns. After build, lineage maps draft
identities to real target entities. Detached draft identities remain identities;
their live navigation requires the Edit to remain open.

`add_vars.obj` contributes to the inherited objective only when there is no
explicit objective replacement. `set_objective` always defines the entire final
objective, regardless of call order. Domain and bound setters operate on source
entities; new variable specifications are supplied when adding them.

## Evidence and batch checking

Omitting `because` is allowed: the library reports the inclusion that is justified
by the edit itself, without claiming equivalence. Supplying evidence is a strict
contract. A Report must have a verified, exact, unconditional PROVED linear
conclusion on the source snapshot. Structural, solution, and candidate-search
reports are not linear evidence.

`because` accepts a scalar Report, a PROVED batch Report, or an iterable of Reports.
Finite sides are matched by exact mathematical content, including positive scaling
and a stronger bound for the same linear part. No matching by names or position
occurs. Both sides of ranged rows require coverage. Equalities can cover both sides.

For many conclusions, use one paged batch:

```python
B = m.check.linear_many(
    targets=targets,       # target:str, relation: <=/>=/==, rhs:exact
    terms=target_terms,    # target, var:VarRef, coef:exact
    using=multipliers,
    residual="exact",     # or "bounds"
    memory_mb=128,
)
B.evidence["results"]     # target, status, domain
B.for_target("cut_17")    # scalar Report sharing batch storage
```

`using` is a Frame or iterable of Frames with `target, multiplier` and one source:
`row + side`, `var + side`, or `proof`. Row sides are `lb/ub/eq`; variable sides are
`lb/ub`. `proof` keys bind scalar reports through `reports={key: report}`. Missing
endpoint columns may be omitted; nullable mixed-source columns are allowed.
Inequality multipliers must be nonnegative. Equality targets use equality sources
or two explicit `direction="le"/"ge"` certificates; ordinary sources default to
`direction="main"`. Exact duplicate target coefficients are merged.

All targets are checked. Insufficient certificates yield INCONCLUSIVE, with each
target's status retained; invalid inputs raise. `B.for_target(key)` makes any
selection of a proven target explicit. The normal Report spec/write/replay APIs
also support batches. Restored verdicts are recomputed, never trusted from JSON.

Removing or weakening a fact requires more than a source proof. Its dependencies
must ultimately reach facts still present in the final snapshot. This includes
parent reports, particular row sides, particular variable bounds and integer
domains. Self-proofs and ungrounded mutually dependent deletions fail. Explicit
proof chains may pass through deleted facts if they close at retained facts.
Conditional reports are refused; build a branch snapshot and check there first.

## Semantics and validation

`diff.semantic` separately describes LP and MIP feasible sets relative to the
source: `SAME`, `SUBSET`, `SUPERSET`, or `UNKNOWN`. Subset/superset are non-strict.
The implementation proves the two inclusion directions separately; it does not
assume that an unverified restriction and relaxation cancel each other.

An integer-valid cut may yield `lp=SUBSET, mip=SAME`. Removing integrality can
invalidate reverse-direction dependencies even when a source integer proof is
still valid for the forward direction. Adding/removing variables yields an
`EXTENDED/REDUCED/CHANGED` coordinate relation and `NOT_COMPARABLE` feasible sets;
no projection equivalence is inferred.

Objectives are classified as `SAME`, `SHIFT`, `POSITIVE_AFFINE`, or `CHANGED`.
Known affine transformations carry exact `scale` and `offset`. Optimal-value
preservation/transformation additionally requires MIP feasible-set equality.
Symmetry and general transformation certificates are outside this initial API.

`build()` checks all references, name collisions, isolated-variable deletion and
certificate obligations. It rejects directly visible infeasibility: reversed
bounds, empty integer intervals, or empty rows excluding zero. These checks include
inherited facts. `allow_infeasible=True` permits those mathematical conditions but
never permits invalid references or evidence. Passing the static checks does not
establish general feasibility. Failures carry a pageable violations Report.

## Persistence, diffs and I/O

`candidate.diff(source)` supports a direct parent or the same snapshot. It exposes
`rows_added`, `rows_dropped`, `vars_added`, `vars_dropped`, `row_bounds_changed`,
`var_bounds_changed`, `domains_changed`, and `rows_changed` Frames. Replacement
rows have before/after nnz. `row_nnz_stats` and `col_nnz_stats` include zero-degree
entities, exact medians and nearest-rank p90. The total nnz alone can hide fill-in.

Native v3 keeps an independently versioned, checksummed lineage attachment with
operations, mappings and only the explicitly attached proof packages. Parent model
data is not embedded. The target works without a live parent; historical proof
statuses are recorded data, not reusable Reports. `candidate.lineage.proof(id,
models=(source,))` replays evidence, and `diff(source)` rechecks evidence and the
actual target facts. Saved semantic labels are not accepted as proof.

`lineage.operations`, `lineage.changes`, and `lineage.evidence` are normal Frames.
`lineage.validation` records the build-time static check summary, and
`lineage.findings` preserves every explicitly permitted infeasibility finding.
`lineage.mapping("var")` preserves qualified source identity alongside target refs;
`mapping("var", source=source)` offers bound entity navigation on both sides.

`write_lp()` publishes atomically. It rejects non-terminating rational decimals,
free rows and incompatible names unless supported explicit renaming is supplied.
It does not silently round or drop facts. Native save remains the exact outlet for
arbitrary Fraction values. `mf.audit.roundtrip(candidate, "candidate.lp")` re-parses
numerical facts without IR search and checks all coefficients, bounds, domains and
the complete objective. Optional `rename_vars/rename_rows` mappings are explicit.
The audit Report establishes I/O consistency only, not a mathematical implication.

No solver, new core dependency, MPS writer, automatic rewrite recipe or global
transformation proof is introduced. The query/edit memory minimum is 16 KiB;
LP parsing, including the auditor's parser, retains its existing 32 MiB minimum.

Run `python examples/exact_edits.py` for the small end-to-end example. The larger
acceptance flow is reproducible with
`python examples/edit_real_models.py data/assign1-5-8.lp data/test10.mps`; it includes
independent indexed verification, native reloading, and the test10 190,000-variable
coordinate product check. This flow builds three snapshots per input model.

## Logical builds and verification reuse

`edit.build()` defaults to `prepare="logical"`: it validates the complete edit,
exact proof obligations and LP/MIP relations and produces an independent native
snapshot. Numerical rows, variables, terms, matrices, lineage, `repr` and `save`
are immediately available without constructing structural analyses. Accessing
`m.ir` prepares the default IR/indexed results once; `m.indexed()` and family
queries prepare their own required analyses. Use `edit.build(prepare="indexed")`
to prepare everything before returning. `read()` and `load()` remain eager.
A failed lazy analysis leaves numerical facts usable and can be retried.

`child.diff(parent, verify="auto")` reuses a process-local receipt from a
successful build or full verification when the exact live source and target
stores still match. This does not retain the Edit or its proof graph, and
closing the Edit does not invalidate its child. Loaded artifacts have no such
receipt: their first diff replays lineage and compares the complete target.
`verify="replay"` always forces that full audit. There is no unchecked mode.
Diff Frames and row/column degree statistics are materialized only when read.
Both snapshots must remain open while using the diff.
