---
name: mipframe
description: Inspect linear LP/MPS models with mipframe, compose exact sparse queries, verify explicit claims, and build independent edited snapshots. Use for model analysis and verified transformations, not as an optimization solver.
---

# Working with mipframe

This guide targets MIPFrame 1.4.0 and Python 3.11 or newer. Check the configured interpreter and `mf.__version__`.
If the installed version matches this skill, use the current interpreter.
Do not search for or switch to another Python environment.

```python
import sys
import mipframe as mf
print(sys.executable, sys.version)
print(mf.__version__)
mf.help()
```

MIPFrame represents a linear MIP as immutable rows, variables, sparse terms and
model metadata. Compressed IR suggests structural hypotheses. Frames and `mf.ops`
compute exact structural facts. `mf.alg` searches graph or finite integer objects.
`LinExpr`, `Claim` and `m.check` express and verify mathematical propositions.
An `Edit` builds a different snapshot; it never mutates its source.

## Prefer batch execution

For repeated checks, use `m.check.batch()` first. Do not write a Python `for`
loop, comprehension, or `map` that executes `m.check.linear`, `cases`, `solution`
or another scalar checker once per item. Do not rebuild model-wide validation
with handwritten per-row or per-variable loops when a library checker exists.
Use Frame operations for structural queries and register related checks together.
A loop is appropriate only to **register** tasks with `batch.linear(...)`, etc.;
call `batch.run()` once after registration. Construct shared expressions inside
the batch context so their common parts remain shared. Scalar checks are for
isolated checks, not the default strategy for a family of targets.

## Run one complete example

This self-contained example creates temporary files and removes them when done.
It selects two known rows, verifies a supplied witness, commits the deletion and
checks the exported MPS. For a real model, choose target rows and retained witnesses
from the actual model; no checker discovers them automatically.

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import mipframe as mf

with TemporaryDirectory() as directory:
    source = Path(directory) / "input.lp"
    source.write_text("\n".join([
        "Minimize", " obj: x + y", "Subject To",
        " keep: x + y <= 4", " redundant: x + y <= 4",
        "Bounds", " 0 <= x <= 5", " 0 <= y <= 5", "End",
    ]))
    with mf.read(source) as m:
        kept = m.rows.by_name("keep")
        dropped = m.rows.by_name("redundant")
        proof = m.check.linear_rows(
            targets=[dropped.id], witnesses=[[kept.id]],
        ).require()
        with m.edit() as edit:
            edit.drop_rows([dropped.id], because=proof)
            with edit.commit() as final:
                assert final.shape == (1, 2)
                receipt = final.write(Path(directory) / "output.mps", audit="readback")
                assert receipt.verified
                print(receipt.writer, receipt.verified)
```

The same executable example is in `mf.help("recipe.quickstart", detail="full")`.

## Find the capability before inventing syntax

Query `mf.help("recover coordinates")`, `mf.help("linear certificate")`, or another
English task description. An exact public name such as `mf.help("VarFrame.project")`
opens its contract. `mf.help(mf.ops.sets.pairs)` accepts actual registered callables.
Read a full topic with `detail="full"`; use `format="json"` for structured results.
The same documentation is available through `python -m mipframe help "query"`.
Search is local, versioned and deterministic. It does not execute an analysis.

Start from these six actions:

- Observe: `m.ir`, `m.rows`, `m.vars`, family summaries and bounded examples.
- Navigate: `.rows` and `.vars` for entities, `.terms` for original coefficients.
- Relate: Frame filters, joins, grouping and strict coordinate projection.
- Compute or search: exact sets/graphs/matrix operations, then explicit alg searches.
- Verify: an explicit Claim and the appropriate checker, including its scope.
- Modify: stage an Edit, inspect its summary, build and audit the new snapshot.

Do not guess methods such as `detect_network`, `find_symmetry` or `auto_reformulate`.
Use `Model.write` or `Edit.write` for supported LP/MPS output. Search the intended
capability; a limitation topic may be the correct answer. Inspect a public signature
or docstring when the argument contract
is uncertain. Avoid rebuilding parsers or sparse indexing already supplied by the
library, while keeping ordinary Python available for small custom calculations.

## Unified entity and indexed navigation

Use `m.vars` / `m.rows`, `by_name(name)`, `loc[id_or_ref]`, and `iloc[position]`.
Bounds are exact finite values or infinities; `source_line` is one-based or `None`.
`to_records()` explicitly materializes detached records within a budget.
Export a numerical SciPy copy with `m.matrix.view().to_scipy()`.

By default `m.indexed()` skips partitions with only one row set; these cannot
contribute tensor coordinates. Use `m.indexed(include_singletons=True)` only
when those potentially numerous partitions are needed.

Indexed directories follow the family Frame model: `I = m.indexed()`, then
`O = I.operators.where(...)`, `O.rows`, and `O.related("kernel")` or
`O.related("bindings")`. `I.operator(id)` returns a one-row Frame, so metadata
uses `O.one().row_count` or `O.one().formula`. Tensor members use `T.vars`;
its axes use `T.related("axes").where(axis=0).rows`. Frame.shape is the table
shape; indexed dimensions are in `related("dimensions")`.
Keep `indexed_key` when projecting to preserve related navigation. All relations
follow the current selection and share storage. Use `inspect(max_items=...)` for
bounded previews, including ordinary Frames. Indexed views additionally support
`verbose`; family-specific display options are rejected on other providers.

`m.verify()` and `I.verify(selection)` return Reports with integrity/indexed scope.
Inconsistency returns REFUTED; use `.require()` to raise. Neither is a linear
certificate or an Edit justification. `summary["checked"]` and `summary["failures"]`
count primary checks; `evidence["checks"]` lists these checks and
`evidence["details"]` adds subordinate diagnostics. A skipped or inconclusive check
prevents an overall PROVED result. Use mathematical checkers for those claims.
All mathematical checkers use exact arithmetic; no tolerance or arithmetic switch
changes their comparison rules. See `mf.help("concept.indexed", detail="full")`.

## Observe, hypothesize, query, verify, act, audit

Read the IR after importing a model. `read()` and `load()` cache a cheap scan:
size, domains, bounds, degrees, numeric fingerprints, local row shapes and lexical
name hypotheses. Display and default exports never start structural analysis.
High-cardinality counts may carry error bounds; candidate fingerprints and name
patterns are not exact structural evidence. Use `m.ir.row_shapes()` or
`m.ir.name_templates("row")` for full exact navigable directories. Families,
`m.ir.topology`, role support and `m.indexed()` compute only on explicit access.
Use samples to choose hypotheses and full declared scopes to establish them.
A small example is not a proof about every family member.

```python
m = mf.read("model.mps")
m.ir
m.rows.head()
mf.help("recipe.coordinates", detail="full")
```

Keep explanations separate from verified facts. Anonymous coefficients can support
a precise statement about incidence without identifying the original application.
Names may suggest a useful coordinate map, but the map still requires coverage,
uniqueness and numerical checks. Family labels belong to a snapshot, configuration
and refinement depth; their numbers are not portable across models or versions.

Structural signatures are normalized observations, not original formulas. Inspect
original terms and bounds before reasoning about a row. Equal support does not
imply equal coefficients, equal bounds, redundancy or symmetry. Comparing complete
local templates still omits any external coupling that was not in the payload.

## Structural families

Start with `m.row_families` or `m.var_families`: both are template structural
Frames at depth 2. `m.family("F0")` is a one-row Frame with `.rows`; variable
families have `.vars`. Filter several families and use the same navigation.
`m.rows.head(100).families()` restricts members, preserving global family IDs.
`count` is selected coverage; `total_count` is the full-model population.

Use `f.related("pattern")` for exact structural tokens, `f.related("parameters")`
for numeric summaries, and `f.related("histogram")` for complete distributions.
Filter `basis="canonical"`, `"raw"` or `"scale_normalized"` explicitly; retain
`orientation` when comparing zero-RHS equalities. `f.inspect()` is bounded.
Keep `family_key` when selecting columns if you need later relation navigation.
A depth-d row pattern references depth-(d-1) variable roles, not necessarily the
families in the default variable directory.

Advanced algebraic, typed and exact structural rules live in
`mf.analysis.families/signatures(..., method=...)`, always returning Frames.
Use `m.refinement(...)` for parent/child transitions and `m.indexed(...)` for
indexed discovery. There is no `m.structure` or public Family/Signature object.

## Rows, Vars, Terms and identity

The canonical tables are Rows `(row,name,lb,ub)`, Vars
`(var,name,kind,lb,ub,obj,source_line)` and Terms `(row,var,coef)`. Finite numeric facts are
exact rationals. Terms exclude the objective, bounds and structural zeros.
Canonical variable kind is C or I; binary status is derived from effective integer
bounds and includes variables fixed at zero or one.

Use `m.rows.iloc[0]` for a position, `.loc[key]` for a declared key,
`.by_name("name")` for explicit name lookup. Use `m.family("F0@2").rows`
or `m.family("V0@2").vars` for family members. Frame `[]` is not a mixed-purpose index.
Use `.ids` for original IDs and typed Ref columns for relationships.
`m.rows.loc[ref]` and `m.vars.loc[ref]` accept typed Refs with matching snapshot
uid/version and entity axis, and require membership in the selected Frame.
`ref.bind(m)` navigates the full matching model. `.loc[ref.id]` uses only the
integer ID and cannot validate the Ref's snapshot or axis; prefer `.loc[ref]`.

`r.vars` and `x.rows` are deduplicated entity sets. A coefficient belongs to
`r.terms` or `x.terms`; `term.row` and `term.var` navigate its endpoints.
`one()` requires exactly one output, never an arbitrary representative.

```python
x = m.vars.iloc[0]
term = x.terms.head(1).one()  # requires a nonzero column
term.row, term.var, term.coef
```

Independent imports have different snapshot identities. Save/load preserves uid
and version. Same spelling does not authorize cross-model joins. Presolved names
do not supply a postsolve map. Keep every model referenced by a live result open;
export detached records if data must outlive it. New Edit snapshots are independent
and child snapshots remain usable after the parent closes.

## Compose relations with explicit contracts

Use `mf.col` for serializable table expressions, `&`, `|`, `~` for predicates.
Do not use Python `and/or` or lambda callbacks in the core query language.
`select` and `join` return ordinary Frames; filtering and slicing retain entity
type. Direct key retention can preserve uniqueness; choosing a non-key column
cannot create it. `Terms.select("var")` is usually not unique.

For joins, specify the intended cardinality when existing facts do not establish
it. `cardinality="1:1"` and `require="left"` are separate requests: the first
checks uniqueness, the second checks coverage. Unknown uniqueness is not a finding
of duplicates. Use `m:m` only for an intended expansion. Rename conflicting fields
explicitly with select. `head(1)` cannot bypass whole-input validation.

A strict `V.project(i=A,j=B)` requires one adjacent target in each domain for every
source variable. Then `P.check("product", ("i","j")).require()` checks the full
declared Cartesian product. Filtering or head does not shrink those target domains.
Inspect complete violations on zero/multiple matches instead of choosing the first.
For participation counts including isolated members use `m.matrix.nnz`; ordinary
Terms group-by emits observed groups only.

## Choose exact computation or candidate search

Use sets.pairs/members for support relationships and witnesses; sets.components for
hypergraph blocks; graph reachability for directed closure. Closure union resource
sums must count shared members once: use sets.pair_aggregate. A directed path is a
graph witness; deletion of its corresponding model rows still requires a linear
certificate from retained row sides.

`mf.ops` completes or raises. `mf.alg` distinguishes candidate APIs from exhaustive
searches. Read the search metadata and selected scope. A greedy top-k clique search
does not prove a maximum clique. Graph cliques and odd cycles do not prove that the
original MIP edges are conflicts. Supply and check those sources independently.

Use `MatrixView.preview()` only when a small grid helps. The default is 8 by 12;
most_active ranks columns over all selected rows, not just the row prefix.
`to_scipy()` explicitly converts to independent float64 CSR. Exact reasoning stays
on the rational path; conversion can fail on overflow or nonzero underflow.

## Verify mathematical statements

Use `row.expr` for raw coefficients, not for row bounds. Use `row.lb_claim`,
`row.ub_claim`, `row.eq_claim` or variable bound claims as original fact sources.
`x == y` compares entity identities; `.eq()` constructs a mathematical equality.
For inequalities use `s <= U` or `s >= L`; `LinExpr.le(U)` and `LinExpr.ge(L)`
are equivalent method forms. Constructing a Claim neither proves it nor adds a row.
Claims and Reports have no implicit bool. Check `status` or call `require()`.

Scalar mathematical checkers return `ProofRef`; bulk checkers return `BatchProof`.
`check.solution` returns `PointReport`, with point violations rather than an
implication certificate. Frame/integrity checks return diagnostic `Report`.
These types have different methods: use their own help, not the Report API by
analogy. In particular, ProofRef has no `explain()`; inspect its conclusion,
scope and evidence. Finish consuming PointReport evidence before calling its
`close()` to release retained point storage.

Use `mf.q("9.6")` or `mf.q("43/39")`, not a Python float. Converting approximate
solver multipliers to rationals is only a proposal; replay the full certificate
with residual bound compensation. Exactness is relative to this saved snapshot,
not an unknown pre-presolve model or unsaved original token spelling.

`identity` checks algebra; `linear` checks supplied multipliers; `infeasible`
checks contradiction; `round` uses integer-valued expressions; `enumerate` checks
a finite local integer implication; `solution` validates a complete exact point
against the mathematical integer model, including integrality. It rejects
`domain="relaxation"`. For a fractional LP point use `m.matrix.activity(values)`
plus exact checks of both row sides and every variable bound. Check complete
variable coverage, including variables absent from all rows; activity alone only
requires participating variables. See `mf.help("fractional point feasibility")`.
No checker searches for multipliers.

Use one explicit batch for dependent proofs and mixed check types:

```python
x = m.vars.iloc[0]  # example requires an integer variable with bounds [0, 1]
with m.check.batch() as batch:
    target = x >= 0
    p0 = batch.linear(target, using=[x == 0], assume=[x == 0])
    p1 = batch.linear(target, using=[x == 1], assume=[x == 1])
    batch.cases(target, var=x, cases={0: p0, 1: p1}, result_key="bound")
    batch.model_verify(result_key="integrity")
    results = batch.run()
results.require()
proof = results["bound"]
```

Registration returns a `CheckHandle`, not a proof: it has no `.ok` or `.status`
and cannot authorize edits. Handles may supply dependencies to later tasks in
the same batch. `result_key` names an exported result and must be unique; unnamed
tasks are internal dependencies. `run()` checks the complete dependency closure
of named results and seals the plan. Context exit never runs it implicitly.
`results.require()` requires all exported checks to succeed. Mathematical results
are `ProofRef`/`BatchProof`, points are `PointReport`, and diagnostics are `Report`;
point and diagnostic reports never grant mathematical or edit authority.
Conditions belong to each target, including inherited conditions; only a valid
`cases` step discharges its matching branch conditions. Different snapshots need
separate batches and cannot share proof authority.

Defaults are 30 seconds, 1,000,000 logical tasks, 4,096 compiled execution nodes,
100,000,000 work units and 128 MiB working memory. Budget exhaustion stops with
`ProofBudgetExceeded`, never a successful or mathematically refuted result.
Use explicit finite overrides on `m.check.batch(...)` or `m.check.budget(...)`
when justified; time and work limits cannot be disabled.

Existing compact inputs can use `linear_rows(targets=..., witnesses=...)` or
`linear_many(targets=mf.Targets(...), witnesses=mf.CertificateBatch(...))` as
adapters to the common engine. Prefer `check.batch()` for new multi-rule work;
do not add a new `*_many` interface for every checker or mathematical structure.

`BatchProof` stores statuses and arrays; do not call `for_target()` in a large loop.
Bind the batch or `proof.select(ids)` directly to an edit. `round_many(ub=0)` or
`round_many(lb=...)` rounds a whole batch of variable bounds.

For shared one-hot affine structure use `m.check.one_hot_affine(groups=...)`, where
each group is `(onehot_row_id, [(definition_row_id, defined_variable_id), ...])`.
`bound_exclusions()` verifies option exclusions after checking all common structure.
`affine_integrality(vars=..., rows=...)` checks normalized integer definitions; its
proof supports relaxing those variables to continuous without relying on their own
integer declarations. These APIs require discovered structure, not name conventions.

An insufficient certificate is INCONCLUSIVE, not a proof that the target is false.
A local enumeration counterexample need not extend to a full-model feasible point.
A feasible point does not prove optimality. Integer-only evidence cannot prove an
LP claim. Assumptions remain in the report, including those inherited from parents.
Use `m.check.cases(target, var=y, cases={0:p0, 1:p1})` to explicitly discharge
an exhaustive split over a finite model-integer variable. Each branch must prove
the same target and assume its matching value. Only those equalities disappear;
other assumptions remain. The result is integer-only and supports `because`
only when unconditional. Branches may involve continuous activity variables.
A contradiction under z=1 does not automatically discharge the condition to create
an unconditional z=0 certificate. General continuous disjunction merging and full
MIP symmetry certification are not implemented.

## Modify and audit deliberately

An Edit records a source-relative delta. `summary()` reports counts and known
semantics without global matrix analysis. `build()` allows experimental unproved
changes and marks semantic preservation UNKNOWN. `commit()` strictly requires
MIP equivalence, preserved objective values, covered operations and grounded
certificate dependencies. Conditional, foreign and unproved evidence is rejected.
Deletion witnesses must ultimately use retained facts; cycles cannot justify edits.

Use cheap intermediate snapshots when a later proof needs newly established facts.
For example, prove bounds, commit the tightening, eliminate fixed variables with
`substitute_fixed(vars=...)`, then check implications on the reduced snapshot.
Nonzero fixed values shift row bounds and the objective constant exactly. The delta
retains a lifting map. `set_domains(...,kind="C",because=integrality_proof)` supports
proved integrality relaxation. General unproved replacements remain UNKNOWN.

Snapshots share numerical storage with their parent. Stable entity IDs may have
gaps; use `.iloc` for positions and `.loc` for IDs. Closing the parent does not
invalidate a child. `child.diff(ancestor)` composes stored deltas; unrelated models
receive a numerical comparison with no inferred mathematical equivalence.

Write one final artifact with `write(path,audit="readback")`. It writes a
temporary file, checks the actual numerical output once, then publishes it. It does
not rerun mathematical proofs. With an indexed MPS source whose hash still matches,
patch output supports row deletion, fixed substitution, row/variable bounds and
integrality relaxation. `strategy="auto"` falls back to canonical output when
patching is unsupported; `strategy="patch"` requires patch support. Inspect
`patch_plan()` for the selected mode and reasons. Native v7 `save()`
stores flattened numerical facts plus historical delta/certificates. Loading never
turns saved PROVED flags into live trust. `loaded.audit(source=original)` explicitly
rechecks saved evidence and numerical output. Independent external verifiers remain
useful for final research audits; ordinary summary/diff/save do not invoke them.

## Recover and control output

When a call fails, inspect its original diagnostics and complete pageable report.
Use `mf.help(exc)` or its help_topic to find a correction. Do not discard violations
or weaken a contract merely to continue with an unsupported conclusion.

Avoid full record dumps. repr is bounded but upstream joins, sorts or aggregations
can still require complete work. Query memory defaults to 128 MiB with a 16 KiB
minimum; parsing has a separate 32 MiB minimum. Budgets do not bound process RSS.
Use iter_batches/write for large exports. Output limits fail complete ops rather
than silently truncating them. Search and enumeration budget statuses have different
contracts; consult their exact topics.

Mathematical BatchProof results use immutable exact arrays and lightweight ProofRefs.
Only explicit evidence access builds Frames or symbolic claims. Keep model owners
open for entity/Frame navigation. Export certificates with `write()` or `to_spec()`;
replay always runs the checker again. Never treat a status copied from disk as proof.

Use an external solver for optimization and an explicit mapping for solver data.
Use source text when provenance or serialization itself is the question. The goal
is a justified result with its actual scope, not forcing every computation into a
mipframe primitive or inferring domain semantics from an attractive pattern.
