# Concepts and capability boundaries

## Rows, variables and sparse terms

Rows, variables and sparse terms [concept.frame]

Rows and Vars are entity sets; Terms are original nonzero (row, var, coef) relationships.

Inputs: A live Model. Rows: row,name,lb,ub. Vars: var,name,kind,lb,ub,obj. Terms: row,var,coef.

Establishes: Row/Var IDs are typed, snapshot-qualified identities; names are display and explicit lookup data.

Does not establish: An entity neighborhood does not retain one edge per source. A Row or Var has no coef field.

Illustrative example (requires caller-provided inputs):
r = m.rows.iloc[0]
x = m.vars.by_name("x")
T = x.terms
edge = T.head(1).one()
edge.row, edge.var, edge.coef

Use r.vars and x.rows for deduplicated neighborhoods; use r.terms, x.terms or m.terms(rows=R, vars=V) for coefficients.
Use .iloc for positions, .loc for declared keys, .by_name for names and .family for a bound IR family.
RowFrame.loc and VarFrame.loc accept integer IDs or same-snapshot, same-axis Refs within the selection.
select/join return ordinary Frames. where/head/sort preserve entity type. Entity sets support &, | and -.
Ref columns retain their snapshot and axis. .refs("var") returns the corresponding entity set.
Terms group-by observes only present groups. m.matrix.nnz retains zero-degree members.
select does not invent uniqueness for a projected non-key. joins enforce their declared cardinality before yielding even head(1).

See also: api.Frame.join, api.VarFrame.project, concept.identity

## Snapshot identity and lifecycle

Snapshot identity and lifecycle [concept.identity]

Independent imports have independent identities; native save/load retains uid and content version.

Establishes: Alignment uses uid, version, entity axis and ID. Canonical kind is C/I; binary is derived from integer bounds.

Does not establish: Names do not establish an original-to-presolved mapping. Exact saved values do not preserve unsaved source tokens.

Model is immutable. Keep all models referenced by a query open while using live results or suspended iterators.
Exported records contain detached Ref identity values. Bind them explicitly to the matching snapshot to navigate entities.
Use m.rows.loc[ref] or m.vars.loc[ref] for same-snapshot, same-axis references; selections still enforce membership.
ref.bind(m) uses the full matching model. loc[ref.id] discards the Ref snapshot and axis checks.
Same uid with conflicting versions is rejected. New Edit snapshots have new uid and explicit parent/lineage.
read/load prepare default IR and indexed results synchronously. Help and repr do not start an additional discovery run.
Only native v3 is accepted; reimport LP/MPS for older native formats.

See also: api.Ref.bind, api.Model.save, recipe.edits

## Claims, reports and proof scope

Claims, reports and proof scope [concept.proofs]

A Claim states a proposition; only the matching completed checker establishes its stated conclusion.

Establishes: Report status is PROVED, REFUTED or INCONCLUSIVE. require() succeeds only for PROVED.

Does not establish: Structural checks, feasible points, solver status and graph candidates cannot serve as linear proofs.

identity compares algebra, linear checks supplied multipliers, infeasible checks a contradiction, round uses model integrality,
enumerate checks a finite local integer domain, and solution checks a complete point against the mathematical integer model, including integrality.
For a fractional LP point, use matrix.activity plus exact comparisons of both row sides and all variable bounds, with complete variable coverage.
solution rejects domain="relaxation". linear_many batches explicit certificates.
These checkers do not search for multipliers or solve the MIP. A failed certificate is INCONCLUSIVE, not a proof of the opposite claim.
Assumptions propagate and remain conditional. A local enumeration counterexample need not extend to a feasible point of the full model.
Relaxation-valid evidence can support integer claims, but integer-only evidence cannot establish a relaxation claim.
Use original row/variable side claims, not a newly typed unproved equivalent Claim, as trusted sources.
Proof replay rechecks sources and dependencies; a saved PROVED string carries no authority.

See also: concept.conditions, api.Checker.linear, api.Report.require

## Conditions and explicit finite case discharge

Conditions and explicit finite case discharge [concept.conditions]

Ordinary checkers retain assumptions; check.cases explicitly discharges exhaustive finite integer branches.

Establishes: A conditional report records every explicit and inherited assumption.

Does not establish: Contradiction under z=1 is not automatically converted to an unconditional z=0 Report usable by Edit. Continuous disjunctions and infeasible-branch elimination are unsupported.

General linear assumptions require explicit multipliers. Assumptions are not searched or silently removed.
When possible, form an unconditional linear bound from original row sides, compensate the residual with bounds, then round an integer expression.
For a finite model-integer variable y, check.cases(target, var=y, cases={0:p0,1:p1}) checks exhaustive coverage and removes only the matching y=value assumptions.
Every branch must prove the same exact target. Other explicit and inherited assumptions remain as a union, so only an empty residual assumption set gives unconditional evidence.
Coverage uses ceil(model lb)..floor(model ub), not assumption-tightened bounds. The result is integer-only, even when all branches are relaxation-valid.

See also: recipe.conditions, recipe.cases, api.Checker.cases, recipe.round, limitation.disjunction

## Tighten a big-M bound with explicit certificates

Tighten a big-M bound with explicit certificates [concept.big-m]

Derive a candidate bound from original row sides, then verify the proposed gated inequality.

Inputs: An explicit gated row, switch domain, candidate M and original source sides.

Establishes: check.linear verifies supplied unconditional combinations; check.enumerate can verify a finite pure-integer local implication.

Does not establish: No automatic big-M optimizer or general branch-proof merger is provided. A resource bound alone need not imply the gated inequality.

Use check.linear(target, using=..., residual="bounds") for an explicit unconditional certificate.
A bound s<=U does not by itself prove s<=U*z. Account for the switch and both branches.
check.infeasible or check.linear with assume=[z.eq(0)] or assume=[z.eq(1)] retains the assumption.
Use check.cases(target, var=z, cases={0:p0,1:p1}) to discharge both branches of a model binary; other assumptions remain.
check.enumerate only accepts a finite integer domain after substitutions; it cannot enumerate continuous activity variables.
For row replacement, first add the proved new row and build, then prove the old row from retained facts on that new snapshot and delete it. Diff compares direct parent snapshots only.
Only a supported unconditional proof can justify Edit.add_row(..., because=proof).

See also: recipe.linear, concept.conditions, concept.resource-cover, api.Checker.enumerate, api.Edit.add_row

## Set covers and resource bounds

Set covers and resource bounds [concept.resource-cover]

Inspect coverage using set relations; verify a chosen resource-row combination with exact linear certificates.

Inputs: Selected original resource rows, target expression, explicit multipliers and any required variable bounds.

Establishes: check.linear checks the actual coefficients and residual bounds, including overlaps and variables outside the target support.

Does not establish: There is no minimum set-cover solver or automatic cover-cut generator. Support coverage alone is not a linear proof.

Build membership from original terms with set=row and member=var. Use ops.sets.pairs and ops.sets.members for containment, overlap and uncovered members.
Choose a cover and its multipliers explicitly or with an external algorithm. Verify target <= U with check.linear and residual="bounds" when original bounds control the coefficient residual.
For many targets, use check.linear_many. A smaller cover is a candidate improvement, not evidence until verification succeeds.
For closure-union consumption, use recipe.closure; that is distinct from choosing resource rows to cover a target.
Cover inequalities for a knapsack need their own integer-valid certificate; they do not follow from set membership alone.

See also: api.ops.sets.pairs, api.ops.sets.members, recipe.linear, api.Checker.linear_many, recipe.closure

## Graph matching algorithms are not provided

Graph matching algorithms are not provided [limitation.graph-matching]

Matching edges with disjoint endpoints is a different task from graph isomorphism or table joins.

Does not establish: No maximum-cardinality or weighted matching solver is provided. A chosen matching is not a proof of optimality or a MIP inequality.

Construct a candidate matching externally and check that its edges exist and its endpoints are disjoint using Frames.
If selected edges encode proved inequalities, combine their original source sides with check.linear to verify an explicit target bound.
Use alg.graph.isomorphism_classes only for label-preserving bijections between graph instances, not for disjoint-edge selection.

See also: concept.search, api.Frame.check, recipe.linear, api.alg.graph.isomorphism_classes

## Check fractional point feasibility in the relaxation

Check fractional point feasibility in the relaxation [limitation.fractional-point]

Use exact matrix activity and bound comparisons to inspect an LP relaxation point.

Inputs: A complete exact Frame(var,value), uniquely keyed by var and bound to the model snapshot.

Establishes: m.matrix.activity computes exact row activities; compare both row sides and all variable bounds explicitly.

Does not establish: check.solution checks the integer model and rejects domain="relaxation". Fractional values on integer variables fail its integrality check.

Use m.matrix.activity(values), with the default missing="error", then join the result to m.rows on row and compare activity against lb and ub.
Independently check full variable coverage and both variable bounds, including variables absent from every row.
A point that passes these comparisons is feasible for the linear relaxation; it need not satisfy integrality.
LinExpr.evaluate only evaluates one expression; it is not a feasibility check. A feasible point is not an optimality or implication certificate.
Use check.solution for a complete point that must also satisfy the original integer domains.

See also: api.MatrixAccessor.activity, api.Frame.join, api.Checker.solution, concept.exact

## Report resource lifecycle

Report resource lifecycle [concept.report-resources]

Mathematical Reports share paged certificate storage; evidence views and proof dependencies retain the data they need.

Establishes: Scalar mathematical checkers release their temporary workspace after publication and share retained evidence per live model owner. Batch targets share one store; for_target does not open another database.

Does not establish: Report and LinExpr have no public close() method. Retained evidence still uses memory and disk. Unrelated queries and separately retained batches have their own storage; memory_mb is not a global process budget.

Use check.batch("linear") for a loop of explicit claims, sources and per-target assumptions. Use check.linear_many when the inputs already exist as Frames.
Retaining thousands of scalar mathematical Reports does not allocate one persistent SQLite connection per Report. Releasing the last Report, Claim, evidence Frame and dependent proof for a node makes its stored rows reclaimable; free database pages can be reused.
Small expressions use bounded exact in-memory terms; larger materialized expressions share spill storage. Temporary SQLite callbacks do not keep discarded workspaces alive; manual garbage collection is not part of the API contract.
Keep source models and proof dependencies alive until verification and any Edit that consumes them are complete.
Export needed audit data with Report.to_spec or Report.write; exported status alone is not a live proof and must be replayed before reuse.
Release unneeded reports, evidence Frames, expressions and containers holding them. Dependent reports and staged edits may still retain references, so deleting one name need not free storage immediately.
Model.close releases owned query resources and invalidates dependent live results. Close a model only when its live results are no longer needed.
Do not call private report stores to close shared evidence. This release offers no per-report deterministic close API.

See also: api.Checker.batch, api.Checker.linear_many, api.Report.for_target, api.Report.write, api.Report.replay, api.Model.close, concept.resources

## Exact arithmetic and external numerical values

Exact arithmetic and external numerical values [concept.exact]

Use integer, Fraction, finite Decimal or mf.q text for mathematical scalars.

Establishes: m.check and LinExpr operate on the exact rational values of the bound snapshot.

Does not establish: Converting a solver float to a rational does not certify feasibility, optimality or a dual bound.

Illustrative example (requires caller-provided inputs):
a = mf.q("9.6")
b = mf.q("43/39")

Booleans, NaN, infinity and Python floats are rejected as algebraic scalars. Infinity is allowed only in documented bound fields.
If an external approximate value is useful for candidate ranking, explicitly choose and record its conversion outside mipframe.
For proof multipliers, replay the resulting rational certificate including residual bound compensation.
Exact solution checking may reject a rounded solver point. Do not silently add a tolerance or label it exact.
SciPy export is an explicit float64 conversion; overflow, nonfinite results and nonzero underflow are errors.

See also: api.q, api.Checker.linear, api.MatrixView.to_scipy

## Execution budgets and bounded display

Execution budgets and bounded display [concept.resources]

Preview size, materialization size, algorithm output and search completeness are different limits.

Establishes: Complete ops finish or raise; no truncated mathematical result is returned.

Does not establish: head() cannot bypass a join validation barrier, sort or aggregation. repr does not promise constant-time upstream work.

Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. Parsing has a separate 32 MiB minimum.
collect/to_records explicitly materialize and can raise MaterializationLimitError; use iter_batches/write for large outputs.
Most ops default to 1,000,000 output records. Set max_output_rows=None only when full output is intended.
alg *_candidates may stop early and record scope/stop reason. Exhaustive algorithms raise SearchBudgetExceeded when incomplete.
check.enumerate returns INCONCLUSIVE when its enumeration budget is exhausted; this is different from alg.integer.enumerate.
MatrixView repr reports statistics without dense conversion. preview defaults to an 8 by 12 grid, independent of global display options.

See also: api.Frame.iter_batches, concept.search, api.MatrixView.preview

## Candidate search versus exhaustive algorithms

Candidate search versus exhaustive algorithms [concept.search]

Use alg for potentially expensive searches; inspect its declared domain and completeness.

Establishes: Candidates are verified graph objects in the selected graph. Exhaustive searches either finish or raise.

Does not establish: A top-k candidate ranking is not a global optimum or a MIP cut proof.

clique_candidates and odd_cycle_candidates return ordinary Frames with normalized membership and search information in relations.
maximal_cliques means inclusion-maximal, not maximum-cardinality. simple_cycles and isomorphism_classes are explicit exhaustive searches.
vertices declares the graph domain; scope induces the search subgraph. Do not pass a subset as the full domain while retaining out-of-domain edges.
Structural isomorphism only respects the supplied graph labels; it does not prove an automorphism of the whole optimization model.

See also: api.alg.graph.clique_candidates, api.alg.graph.maximal_cliques, limitation.symmetry

## Recover from a failed query

Recover from a failed query [concept.errors]

Read the original error and its complete report, then query its linked help topic.

Establishes: Suggestions name real current APIs; exceptions retain their original types and evidence.

Does not establish: A resource error or malformed input is not a mathematical counterexample.

Use mf.help(exc) inside an exception handler. If exc.report exists, inspect its summary and pageable violations.
Do not fix a strict projection by taking the first neighbor. Inspect zero/multiple matches or refine the target selection explicitly.
Do not silence cardinality errors with m:m unless that relationship is intended. Do not bypass proof errors by omitting because and claiming equivalence.

See also: error.join, error.navigation, concept.proofs

## Join uniqueness and complete matching

Join uniqueness and complete matching [error.join]

Join cardinality and match coverage are separate contracts.

Establishes: Explicit 1:1, 1:m or m:1 validates the required uniqueness over the entire input.

Does not establish: Unknown uniqueness does not mean duplicates were found. select("var") on Terms does not make var unique.

Illustrative example (requires caller-provided inputs):
Q = A.join(B, on="var", cardinality="1:1", require="left")

auto needs an existing uniqueness fact on at least one side. Otherwise specify the intended cardinality explicitly.
Use m:m only for an intended expansion. require=left/right/both verifies complete matching independently.
Rename conflicting non-key fields with select; there are no implicit suffixes. head(1) cannot suppress invalid later input.
A failed check retains all duplicate or unmatched records in its report.

See also: api.Frame.join, api.Frame.check

## Navigate entities and terms explicitly

Navigate entities and terms explicitly [error.navigation]

Coefficients belong to Terms, not Row or Var entities.

Establishes: one() requires exactly one item; it never silently selects a representative.

Does not establish: Neighborhood .rows/.vars forgets source-to-target edge multiplicity.

Illustrative example (requires caller-provided inputs):
term = x.terms.where(row=R).one()
term.row, term.var, term.coef

Replace Frame [] with iloc, loc, family, where or column according to intent. Entity selections expose ids.
Use rows.loc[ref] for Ref[row] and vars.loc[ref] for Ref[var] with matching uid/version. A Ref outside the selection raises KeyError.
Use ref.bind(model) to navigate the full matching snapshot; do not strip .id to bypass an axis or snapshot mismatch.
Use V.project(...) directly for source-preserving strict adjacency projection.
Use m.matrix.nnz(rows=R, vars=V, axis="var") for zero-aware participation counts.
Terms are the unified edge view; exported to_records() is the explicit mutable-record exit.

See also: concept.frame, api.VarFrame.project, api.MatrixAccessor.nnz

## Table predicates and affine model expressions

Table predicates and affine model expressions [error.expressions]

QueryExpr selects table records; LinExpr describes an affine function of model variables.

Establishes: Query predicates use &, | and ~. Mathematical inequalities use s <= U or s >= L, also LinExpr.le(U) or LinExpr.ge(L). Equalities use .eq().

Does not establish: x == y is entity identity, not a linear equation. Claim and Report have no implicit truth value.

Illustrative example (requires caller-provided inputs):
T = m.terms().where((mf.col("coef") < 0) & mf.col("var").isin(V))
claim = (2*x - y).eq(0)

Core Frame predicates do not accept arbitrary Python callbacks. Use col to form serializable expressions.
Inspect report.status or call require(). Constructing a Claim does not add a row or prove it.

See also: api.col, api.LinExpr, concept.proofs

## Template similarity is not full model symmetry

Template similarity is not full model symmetry [limitation.symmetry]

Graph or template equality does not prove a permutation preserves the whole MIP.

Does not establish: Graph or template equality does not prove a permutation preserves the whole MIP.

A full claim must account for every coefficient, row bound, variable bound, domain, objective and external coupling under an explicit bijection. No general MIP symmetry checker or symmetry-breaking certificate is provided.

See also: recipe.templates, api.alg.graph.isomorphism_classes

## General disjunction certificates are not available

General disjunction certificates are not available [limitation.disjunction]

One-hot epigraph strengthening may require reasoning outside the current checkers.

Does not establish: One-hot epigraph strengthening may require reasoning outside the current checkers.

Frame queries can validate the one-hot rows and assemble branch data. check.cases combines same-target proofs over one finite model-integer variable, retaining other assumptions. There is no general continuous disjunction merger or infeasible-branch elimination rule. Do not fabricate a PROVED Report.

See also: recipe.epigraph, concept.conditions, api.Checker.cases

## Optimization and presolve are external

Optimization and presolve are external [limitation.optimization]

mipframe inspects and verifies snapshots; it does not solve or provide a presolve/postsolve mapping.

Does not establish: mipframe inspects and verifies snapshots; it does not solve or provide a presolve/postsolve mapping.

Use an external solver for candidate points, multipliers and optimization. Bind any returned data to the actual snapshot. check.solution establishes feasibility and an objective value, not optimality. Solver statuses and tolerances are external evidence.

See also: concept.exact, api.Checker.solution

## Exact native and LP output

Exact native and LP output [limitation.export]

Native save is lossless; LP output has explicit representability restrictions.

Does not establish: Native save is lossless; LP output has explicit representability restrictions.

There is no MPS writer. LP export rejects nonterminating decimal rationals and unrepresentable free rows rather than changing mathematics. Use native save for lossless persistence and audit.roundtrip for supported LP files.

See also: api.Model.write_lp, api.audit.roundtrip

## Dropping a variable is not elimination

Dropping a variable is not elimination [limitation.elimination]

Edit.drop_vars only removes variables absent from all final rows and the final objective.

Does not establish: Edit.drop_vars only removes variables absent from all final rows and the final objective.

LinExpr.substitute is algebra without proof. Edit does not provide automatic variable elimination, quotient reformulation, Model.from_frames or projection equivalence for changed coordinates.

See also: api.Edit.drop_vars, api.LinExpr.substitute

## Explicit display and query settings

Explicit display and query settings [concept.options]

options.display, options.ir and options.query configure presentation or working limits.

Does not establish: Display limits do not reduce the mathematical scope of a query or verification.

See also: concept.resources
