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
Use .iloc for positions, .loc for declared keys, .by_name for names and m.family(label).rows or .vars for family members.
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

Snapshot uid/revision identify immutable facts; content_hash computes an exact numerical hash only on explicit access. Entity IDs remain stable across overlays; iloc is a position and loc is identity. Native v7 restores numerical arrays, deltas and historical certificate data. Native v3/v4/v5/v6 are rejected; reimport the original LP/MPS. Reloaded historical verdicts are UNKNOWN until an explicit audit; they cannot authorize a new edit.

See also: api.Ref.bind, api.Model.save, recipe.edits

## Claims, reports and proof scope

Claims, reports and proof scope [concept.proofs]

Discover witnesses, check compact certificates, then commit a proof-carrying delta.

Establishes: Report status is PROVED, REFUTED or INCONCLUSIVE. require() succeeds only for PROVED.

Does not establish: Structural checks, feasible points, solver status and graph candidates cannot serve as linear proofs.

Prefer m.check.batch() for repeated checks. Do not execute scalar checkers in a Python for loop, comprehension or map, or rewrite complete checks as handwritten row/variable loops. Use Frame operations for relations. Loops may register batch tasks; run once after registration. Construct expressions inside the batch context to share common subexpressions.
Each registration returns CheckHandle, not a signed proof: no .ok/.status and no edit authority. A handle may be used only as a dependency in its own plan. result_key is unique and exports a result; unnamed tasks are internal. run() executes the named dependency closure and seals the plan. Context exit releases unpublished work and never runs implicitly.
results.require() checks all exported results. results[key] preserves its type: ProofRef/BatchProof for mathematics, PointReport for points, Report for diagnostics. Points and diagnostics cannot authorize edits. Conditions remain target-local; cases discharges only its matching branch conditions. Separate snapshots require separate batches.
Finite defaults: time_limit=30 seconds, max_tasks=1000000 logical tasks, max_nodes=4096 compiled execution nodes, max_work=100000000, memory_mb=128. Explicit finite increases are supported; time/work limits cannot be disabled. ProofBudgetExceeded reports the phase and limit; unfinished work is never PROVED or mathematically REFUTED.

The checker executes explicit witnesses; it does not search for proofs. Existing row targets use integer IDs and CertificateBatch offsets/sources. Custom targets use Targets sparse arrays. Scalar Claims and optional Frames compile into the same exact kernel. A BatchProof stores statuses and dependencies without materializing target Reports. for_target returns a lazy ProofRef; select returns a compact selection. Edits accept these objects directly.
Proofs bind an immutable snapshot and domain. Unproved/conditional/foreign evidence cannot authorize commit. Serialized status is never trusted: replay re-executes the certificate. Public Frame assertion Reports are separate from mathematical proofs.

See also: concept.conditions, api.Checker.linear, api.Report.require, api.Checker.batch

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

BatchProof owns immutable certificate arrays; ProofRef is a lightweight view.

Establishes: Scalar mathematical checkers release their temporary workspace after publication and share retained evidence per live model owner. Batch targets share one store; for_target does not open another database.

Does not establish: Report and LinExpr have no public close() method. Retained evidence still uses memory and disk. Unrelated queries and separately retained batches have their own storage; memory_mb is not a global process budget.

Prefer m.check.batch() for repeated checks. Do not execute scalar checkers in a Python for loop, comprehension or map, or rewrite complete checks as handwritten row/variable loops. Use Frame operations for relations. Loops may register batch tasks; run once after registration. Construct expressions inside the batch context to share common subexpressions.
Each registration returns CheckHandle, not a signed proof: no .ok/.status and no edit authority. A handle may be used only as a dependency in its own plan. result_key is unique and exports a result; unnamed tasks are internal. run() executes the named dependency closure and seals the plan. Context exit releases unpublished work and never runs implicitly.
results.require() checks all exported results. results[key] preserves its type: ProofRef/BatchProof for mathematics, PointReport for points, Report for diagnostics. Points and diagnostics cannot authorize edits. Conditions remain target-local; cases discharges only its matching branch conditions. Separate snapshots require separate batches.
Finite defaults: time_limit=30 seconds, max_tasks=1000000 logical tasks, max_nodes=4096 compiled execution nodes, max_work=100000000, memory_mb=128. Explicit finite increases are supported; time/work limits cannot be disabled. ProofBudgetExceeded reports the phase and limit; unfinished work is never PROVED or mathematically REFUTED.

Successful batch execution creates no target Report, evidence Frame or symbolic Claim. for_target and evidence access are explicit lazy presentation. Mathematical values use Python integers or exact Fraction only when needed. Closing a source Model does not release data still shared by a child overlay. Entity/Frame navigation still requires its owner to be open.

See also: api.Checker.linear_batch, api.Checker.linear_many, api.Report.for_target, api.Report.write, api.Report.replay, api.Model.close, concept.resources, api.Checker.batch

## Exact arithmetic and external numerical values

Exact arithmetic and external numerical values [concept.exact]

Use integer, Fraction, finite Decimal or mf.q text for mathematical scalars.

Establishes: m.check and LinExpr operate on the exact rational values of the bound snapshot.

Does not establish: Converting a solver float to a rational does not certify feasibility, optimality or a dual bound.

Illustrative example (requires caller-provided inputs):
a = mf.q("9.6")
b = mf.q("43/39")

Booleans, NaN, infinity and Python floats are rejected as algebraic scalars. Infinity is allowed in Frame number columns representing bounds.
Frame arithmetic keeps finite quotients exact, including finite/infinity = Fraction(0). Indeterminate infinity arithmetic (infinity-infinity, zero*infinity, infinity/infinity and sums of opposite infinities) raises ExactArithmeticError.
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
memory_mb controls working buffers; disk_mb limits temporary spill (None is unlimited). Child operations share a ledger.
to_records and to_spec use a separate materialize_mb limit, default 128 MiB, and can raise MaterializationLimitError; use iter_batches/write for large outputs.
Frame input, relation execution and diagnostics use typed arrays and mmap; there is no SQLite numerical fallback.
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

## Exact native, LP and MPS output

Exact native, LP and MPS output [limitation.export]

Native save is lossless; LP and MPS output have explicit representability restrictions.

Does not establish: Native save is lossless; LP and MPS output have explicit representability restrictions.

MPS supports stream patching and canonical output. Both LP and MPS reject nonterminating decimal rationals and unrepresentable free rows rather than changing mathematics. Use native save for lossless persistence and audit.roundtrip for supported LP files.

See also: api.Model.write, api.audit.roundtrip

## Fixed-variable elimination and lifting

Fixed-variable elimination and lifting [limitation.elimination]

Fixed-variable elimination carries an explicit lifting map.

Does not establish: Edit.drop_vars only removes variables absent from all final rows and the final objective.

Use substitute_fixed after proving finite equal bounds. Nonzero fixed values shift row bounds and the objective constant exactly. General variable substitution and arbitrary dimension-changing reformulations require additional mapping rules.

See also: api.Edit.substitute_fixed, api.Model.lift, api.Edit.drop_vars, api.LinExpr.substitute

## Explicit display and query settings

Explicit display and query settings [concept.options]

options.display, options.ir and options.query configure presentation or working limits.

Does not establish: Display limits do not reduce the mathematical scope of a query or verification.

See also: concept.resources

## Families as Frames

Families as Frames [concept.families]

Start with m.row_families, m.var_families or m.family(label).

m.families(kind="row", depth=2) returns a family Frame; m.family("F0") returns a single-row Frame.
Both row_families and var_families default to depth 2. Row patterns at depth d explicitly reference variable roles at d-1.
Use f.rows for row members and v.vars for variable members. f.rows.vars and v.vars.rows traverse neighboring entities.
Filtering a directory restricts related members and statistics; selected entity families retain global labels and family_key.
count describes selected members; total_count describes the full-model family. Use family_key for cross-table identity, not display labels alone.
related("pattern") exposes complete normalized patterns. related("parameters"), related("histogram") and related("sources") retain basis and orientation.
Use .where(basis="canonical") for canonical observations, or raw / scale_normalized explicitly. sources retains original row and transformation factors.
config records the bound options; levels exposes depth overview. Scalar columns and normalized relations replace all old Family/Signature facades.
Keep family_key when projecting if subsequent family navigation is required; direct renaming is supported. Aggregation removes navigation.
Use mf.analysis.families/signatures with explicit method=algebraic, typed, template or exact_structural for advanced work. Typo aliases algebratic/exact_strctural exist only there.
Model.refinement returns parent/child transitions; Model.indexed accepts explicit depth/method/options rather than a signatures object.
Views are live and require the model to stay open; they do not own one database per family. repr is bounded and does not compute numeric profiles.

See also: api.Model.families, api.Model.family, api.Frame.related, api.Frame.inspect, api.analysis.families, api.analysis.signatures

## Indexed directories and related Frames

Indexed directories and related Frames [concept.indexed]

Use ordinary Frames for indexed coordinates, partitions, operators and evidence.

I = m.indexed() binds a snapshot, configuration and declared scope.
I.tensors, I.operators, I.partitions and I.evidence are ordinary Frames.
By default partitions with only one row set are skipped before materialization; m.indexed(include_singletons=True) retains them. This does not change tensor/operator discovery.
I.tensor(id), I.operator(id), I.partition(id), I.evidence_item(id) return exactly one-row Frames; unknown IDs raise KeyError.
Use O.one().row_count for metadata, O.rows for original rows, and O.related("kernel"), "bindings", "alignment", "ast", "statistics" or "dimensions" for complete data.
Use T.vars, T.related("coordinates"), and T.related("axes").where(axis=0).rows. Coordinates retain the complete declared axis domains.
Frame.shape always means table dimensions. Indexed dimensions live in related("dimensions") as component,axis,size.
AST, origins and diagnostics use path,node_type,text,number,boolean node tables; empty containers and array order are preserved. Statistics retain exact numeric values.
Filtering, sorting, slicing and key-preserving projection restrict related results. Keep indexed_key for navigation; axes additionally require axis. Members are deduplicated.
I.verify(selection=None) checks prepared tensors/operators by default; an explicit directory selection can also verify partitions. Empty selection checks zero items. Discovery evidence is not a proposition.
I.validate(spec) checks independently supplied hypotheses, including Frame relations. Neither directory identity nor saved status grants proof authority.
m.verify() returns Report for storage integrity and prepared indexed runs. Inconsistency returns REFUTED; require() raises. Corrupt base data skips dependent checks; execution and input errors still raise.
These reports cannot be used as linear certificates or Edit justifications. Live views share stores; detached records remain usable after close.

See also: api.Model.indexed, api.IndexedResult.verify, api.IndexedResult.validate, api.Frame.related, api.Model.verify
