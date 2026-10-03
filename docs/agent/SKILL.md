---
name: mipframe
description: Inspect linear LP/MPS models with mipframe, compose exact sparse queries, verify explicit claims, and build independent edited snapshots. Use for model analysis and verified transformations, not as an optimization solver.
---

# Working with mipframe

This guide targets MIPFrame 0.8.0 and Python 3.11 or newer. Check the active
interpreter and `mf.__version__` before using unfamiliar syntax. A system `python3`
may differ from the environment's `python`. Use the documentation shipped with the
installed package when an older copied guide disagrees.

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

Do not guess methods such as `detect_network`, `find_symmetry`, `auto_reformulate`
or `write_mps`. Search the intended capability; a limitation topic may be the
correct answer. Inspect a public signature or docstring when the argument contract
is uncertain. Avoid rebuilding parsers or sparse indexing already supplied by the
library, while keeping ordinary Python available for small custom calculations.

## Observe, hypothesize, query, verify, act, audit

Read the IR after importing a model. `read()` and `load()` prepare default IR and
indexed results before returning, so loading is real work. Displaying IR does not
start a second search. Use samples to choose hypotheses and full declared scopes
to establish them. A small example is not a proof about every family member.

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

## Rows, Vars, Terms and identity

The canonical tables are Rows `(row,name,lb,ub)`, Vars
`(var,name,kind,lb,ub,obj)` and Terms `(row,var,coef)`. Finite numeric facts are
exact rationals. Terms exclude the objective, bounds and structural zeros.
Canonical variable kind is C or I; binary status is derived from effective integer
bounds and includes variables fixed at zero or one.

Use `m.rows.iloc[0]` for a position, `.loc[key]` for a declared key,
`.by_name("name")` for explicit name lookup and `.family("F0@2")` for a family
selection in that bound configuration. Frame `[]` is not a mixed-purpose index.
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
but Diff and proof navigation can still depend on the parent remaining open.

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

For many linear checks, use the scalar-style batch builder. It accepts the same
`using` and `assume` arguments as `linear`, without constructing schemas:

```python
with m.check.batch("linear", residual="bounds") as batch:
    for key, claim, sources, assumptions in checks:
        batch.add(key=key, target=claim, using=sources, assume=assumptions)
    report = batch.run()
report.require()
proof = report.for_target(key)
```

Keys are unique nonempty strings. `add` consumes each input immediately; `run`
verifies and publishes the complete batch. Results survive the builder's context
exit. Every target retains its own explicit and inherited assumptions; a batch
does not merge branches or discharge conditions. If targets, terms and sources
already exist as Frames, use `linear_many`. See `mf.help("Checker.batch")`.

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

Stage edits against one source snapshot. Setters use exact scalars or key-aligned
Frames, not positional arrays. Omitted bounds preserve the old side; None is not
infinity. Changing coefficients requires a new row identity. Dropping a variable
requires no final row or objective references; it is not variable elimination.

An omitted because permits an unverified modification without asserting equivalence.
A supplied because is strict: proved, exact, unconditional, matching source and
covering the modification. Deletion proofs must close to retained facts; self-proof
and ungrounded cycles fail. New rows become usable premises only after an
intermediate build and a new check on that snapshot.
For a case-proved replacement, first add the new row with `because`, build,
then prove and delete the old row on the new snapshot. Check both direct-parent
Diffs; comparing across multiple edits is not supported.

Inspect separate LP/MIP semantics and objective relations in Diff. A valid cut need
not improve an LP objective or solver time. Native save is lossless; LP export rejects
unrepresentable exact data. `mf.audit.roundtrip` checks supported LP serialization,
not mathematical implication. There is no MPS writer or automatic elimination.

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

Scalar mathematical Reports share retained certificate storage; keeping thousands
does not require one persistent database connection per proof. Small expressions
use bounded exact memory and larger expressions share spill storage. Evidence
Frames, Claims, dependent Reports and Edits keep the data they still need alive.
Release unneeded containers to reclaim that data; routine `gc.collect()` is not
required. Export replayable certificates with `Report.write` or `to_spec` when
they must outlive the model. `Model.close` invalidates live dependent results.
There is no public `Report.close` or `LinExpr.close`; see
`mf.help("concept.report-resources")` for ownership and resource scope.

Use an external solver for optimization and an explicit mapping for solver data.
Use source text when provenance or serialization itself is the question. The goal
is a justified result with its actual scope, not forcing every computation into a
mipframe primitive or inferring domain semantics from an attractive pattern.
