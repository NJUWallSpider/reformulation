"""Authoritative English contracts. Generated resources must not be edited."""
TOPICS = []


def topic(ident, title, summary, *, kind='concept', aliases=(), keywords=(), when='',
          inputs='', returns='', guarantees='', non_guarantees='', failures='',
          resources='', example='', body='', related=(), bindings=None, snippet=None):
    item = dict(id=ident, title=title, summary=summary, kind=kind, aliases=list(aliases),
                keywords=list(keywords), when=when, inputs=inputs, returns=returns,
                guarantees=guarantees, non_guarantees=non_guarantees, failures=failures,
                resources=resources, example=example, body=body, related=list(related),
                example_kind='tested_recipe' if snippet else 'illustrative',
                bindings=bindings or {}, symbols=list(bindings or {}))
    if snippet:
        item['snippet'] = snippet
    TOPICS.append(item)
    return item


def api(symbol, binding, summary, *, inputs='', returns='', guarantees='',
        non_guarantees='', failures='', example='', related=(), resources='', aliases=(), keywords=(), body=''):
    return topic('api.' + symbol, symbol, summary, kind='api', aliases=aliases, keywords=keywords,
                 when=summary, inputs=inputs, returns=returns, guarantees=guarantees,
                 non_guarantees=non_guarantees or 'No model change or additional mathematical conclusion is implied.',
                 failures=failures, resources=resources, example=example, related=related,
                 bindings={symbol: binding}, body=body)


LIVE = 'Live results require all referenced snapshots to remain open. Explicit records are detached.'
EXACT = 'Finite mathematical values are exact rationals. Use mf.q("0.1"), not a Python float.'
BUDGET = 'Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.'
OPS = BUDGET + ' max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.'


topic('home', 'Capability map', 'Explore an immutable model, query exact sparse relations, verify claims, and build another snapshot.', kind='capability', aliases=('capabilities',),
      guarantees='Help only retrieves installed documentation; it does not analyze or change a model.',
      non_guarantees='IR roles and graph candidates are not mathematical proofs.',
      body='''Explore: m.ir; m.rows; m.vars; m.terms().
Relate: Frame.where/select/join/group_by; VarFrame.project.
Compare: mf.ops.sets, mf.ops.graph, mf.ops.equal_groups.
Compute: m.matrix.nnz/activity/transpose_activity; sparse MatrixView.
Search: mf.alg.graph and mf.alg.integer; distinguish candidates from exhaustive results.
Verify: LinExpr, Claim, m.check, Report.
Modify: m.edit(), build(), diff(), native save, exact LP audit.

Start with mf.help("coordinates"), mf.help("support containment"),
mf.help("linear certificate"), or mf.help("edit model").
Use detail="full" on a topic ID to read its complete instructions.
Use mf.help("skill", detail="full") for the portable onboarding guide.''',
      related=('concept.frame','concept.proofs','concept.resources','skill'))

topic('concept.frame', 'Rows, variables and sparse terms', 'Rows and Vars are entity sets; Terms are original nonzero (row, var, coef) relationships.',
      aliases=('rows vs terms','entities','tables'), keywords=('coefficient adjacency neighbor sparse schema key',),
      inputs='A live Model. Rows: row,name,lb,ub. Vars: var,name,kind,lb,ub,obj. Terms: row,var,coef.',
      guarantees='Row/Var IDs are typed, snapshot-qualified identities; names are display and explicit lookup data.',
      non_guarantees='An entity neighborhood does not retain one edge per source. A Row or Var has no coef field.',
      example='r = m.rows.iloc[0]\nx = m.vars.by_name("x")\nT = x.terms\nedge = T.head(1).one()\nedge.row, edge.var, edge.coef',
      body='''Use r.vars and x.rows for deduplicated neighborhoods; use r.terms, x.terms or m.terms(rows=R, vars=V) for coefficients.
Use .iloc for positions, .loc for declared keys, .by_name for names and .family for a bound IR family.
RowFrame.loc and VarFrame.loc accept integer IDs or same-snapshot, same-axis Refs within the selection.
select/join return ordinary Frames. where/head/sort preserve entity type. Entity sets support &, | and -.
Ref columns retain their snapshot and axis. .refs("var") returns the corresponding entity set.
Terms group-by observes only present groups. m.matrix.nnz retains zero-degree members.
select does not invent uniqueness for a projected non-key. joins enforce their declared cardinality before yielding even head(1).''',
      related=('api.Frame.join','api.VarFrame.project','concept.identity'))

topic('concept.identity', 'Snapshot identity and lifecycle', 'Independent imports have independent identities; native save/load retains uid and content version.',
      aliases=('cross snapshot','closed model','reference identity','presolve'),
      guarantees='Alignment uses uid, version, entity axis and ID. Canonical kind is C/I; binary is derived from integer bounds.',
      non_guarantees='Names do not establish an original-to-presolved mapping. Exact saved values do not preserve unsaved source tokens.',
      body='''Model is immutable. Keep all models referenced by a query open while using live results or suspended iterators.
Exported records contain detached Ref identity values. Bind them explicitly to the matching snapshot to navigate entities.
Use m.rows.loc[ref] or m.vars.loc[ref] for same-snapshot, same-axis references; selections still enforce membership.
ref.bind(m) uses the full matching model. loc[ref.id] discards the Ref snapshot and axis checks.
Same uid with conflicting versions is rejected. New Edit snapshots have new uid and explicit parent/lineage.
read/load prepare default IR and indexed results synchronously. Help and repr do not start an additional discovery run.
Only native v3 is accepted; reimport LP/MPS for older native formats.''',
      related=('api.Ref.bind','api.Model.save','recipe.edits'))

topic('concept.proofs', 'Claims, reports and proof scope', 'A Claim states a proposition; only the matching completed checker establishes its stated conclusion.',
      aliases=('proof','linear certificate','LP valid','integer valid','evidence'),
      guarantees='Report status is PROVED, REFUTED or INCONCLUSIVE. require() succeeds only for PROVED.',
      non_guarantees='Structural checks, feasible points, solver status and graph candidates cannot serve as linear proofs.',
      body='''identity compares algebra, linear checks supplied multipliers, infeasible checks a contradiction, round uses model integrality,
enumerate checks a finite local integer domain, and solution checks a complete point against the mathematical integer model, including integrality.
For a fractional LP point, use matrix.activity plus exact comparisons of both row sides and all variable bounds, with complete variable coverage.
solution rejects domain="relaxation". linear_many batches explicit certificates.
These checkers do not search for multipliers or solve the MIP. A failed certificate is INCONCLUSIVE, not a proof of the opposite claim.
Assumptions propagate and remain conditional. A local enumeration counterexample need not extend to a feasible point of the full model.
Relaxation-valid evidence can support integer claims, but integer-only evidence cannot establish a relaxation claim.
Use original row/variable side claims, not a newly typed unproved equivalent Claim, as trusted sources.
Proof replay rechecks sources and dependencies; a saved PROVED string carries no authority.''',
      related=('concept.conditions','api.Checker.linear','api.Report.require'))

topic('concept.conditions', 'Conditions and explicit finite case discharge', 'Ordinary checkers retain assumptions; check.cases explicitly discharges exhaustive finite integer branches.',
      aliases=('conditional proof','discharge assumptions','conditional infeasibility','conditional implication',
               'conditional case split','case splitting','branch proof'),
      keywords=('conditional branches case analysis split binary switch merge proofs',),
      guarantees='A conditional report records every explicit and inherited assumption.',
      non_guarantees='Contradiction under z=1 is not automatically converted to an unconditional z=0 Report usable by Edit. Continuous disjunctions and infeasible-branch elimination are unsupported.',
      body='''General linear assumptions require explicit multipliers. Assumptions are not searched or silently removed.
When possible, form an unconditional linear bound from original row sides, compensate the residual with bounds, then round an integer expression.
For a finite model-integer variable y, check.cases(target, var=y, cases={0:p0,1:p1}) checks exhaustive coverage and removes only the matching y=value assumptions.
Every branch must prove the same exact target. Other explicit and inherited assumptions remain as a union, so only an empty residual assumption set gives unconditional evidence.
Coverage uses ceil(model lb)..floor(model ub), not assumption-tightened bounds. The result is integer-only, even when all branches are relaxation-valid.''',
      related=('recipe.conditions','recipe.cases','api.Checker.cases','recipe.round','limitation.disjunction'))

topic('concept.big-m', 'Tighten a big-M bound with explicit certificates',
      'Derive a candidate bound from original row sides, then verify the proposed gated inequality.',
      aliases=('tighten big M','big-M tightening','reduce big M'),
      keywords=('big M bigM tightening strengthen indicator gated constraint bound',),
      inputs='An explicit gated row, switch domain, candidate M and original source sides.',
      guarantees='check.linear verifies supplied unconditional combinations; check.enumerate can verify a finite pure-integer local implication.',
      non_guarantees='No automatic big-M optimizer or general branch-proof merger is provided. A resource bound alone need not imply the gated inequality.',
      body='''Use check.linear(target, using=..., residual="bounds") for an explicit unconditional certificate.
A bound s<=U does not by itself prove s<=U*z. Account for the switch and both branches.
check.infeasible or check.linear with assume=[z.eq(0)] or assume=[z.eq(1)] retains the assumption.
Use check.cases(target, var=z, cases={0:p0,1:p1}) to discharge both branches of a model binary; other assumptions remain.
check.enumerate only accepts a finite integer domain after substitutions; it cannot enumerate continuous activity variables.
For row replacement, first add the proved new row and build, then prove the old row from retained facts on that new snapshot and delete it. Diff compares direct parent snapshots only.
Only a supported unconditional proof can justify Edit.add_row(..., because=proof).''',
      related=('recipe.linear','concept.conditions','concept.resource-cover','api.Checker.enumerate','api.Edit.add_row'))

topic('concept.resource-cover', 'Set covers and resource bounds',
      'Inspect coverage using set relations; verify a chosen resource-row combination with exact linear certificates.',
      aliases=('set cover','resource cover','resource covering','cover bound'),
      keywords=('set covering weighted cover resource capacity upper bound nonnegative coverage',),
      inputs='Selected original resource rows, target expression, explicit multipliers and any required variable bounds.',
      guarantees='check.linear checks the actual coefficients and residual bounds, including overlaps and variables outside the target support.',
      non_guarantees='There is no minimum set-cover solver or automatic cover-cut generator. Support coverage alone is not a linear proof.',
      body='''Build membership from original terms with set=row and member=var. Use ops.sets.pairs and ops.sets.members for containment, overlap and uncovered members.
Choose a cover and its multipliers explicitly or with an external algorithm. Verify target <= U with check.linear and residual="bounds" when original bounds control the coefficient residual.
For many targets, use check.linear_many. A smaller cover is a candidate improvement, not evidence until verification succeeds.
For closure-union consumption, use recipe.closure; that is distinct from choosing resource rows to cover a target.
Cover inequalities for a knapsack need their own integer-valid certificate; they do not follow from set membership alone.''',
      related=('api.ops.sets.pairs','api.ops.sets.members','recipe.linear','api.Checker.linear_many','recipe.closure'))

topic('limitation.graph-matching', 'Graph matching algorithms are not provided',
      'Matching edges with disjoint endpoints is a different task from graph isomorphism or table joins.',
      kind='limitation', aliases=('graph matching','maximum matching','bipartite matching'),
      keywords=('maximum cardinality weighted matching disjoint edges unmatched vertices',),
      non_guarantees='No maximum-cardinality or weighted matching solver is provided. A chosen matching is not a proof of optimality or a MIP inequality.',
      body='''Construct a candidate matching externally and check that its edges exist and its endpoints are disjoint using Frames.
If selected edges encode proved inequalities, combine their original source sides with check.linear to verify an explicit target bound.
Use alg.graph.isomorphism_classes only for label-preserving bijections between graph instances, not for disjoint-edge selection.''',
      related=('concept.search','api.Frame.check','recipe.linear','api.alg.graph.isomorphism_classes'))

topic('limitation.fractional-point', 'Check fractional point feasibility in the relaxation',
      'Use exact matrix activity and bound comparisons to inspect an LP relaxation point.',
      kind='limitation', aliases=('fractional point feasibility','LP point feasibility','relaxation feasibility'),
      keywords=('fractional solution feasible relaxation witness rational point row variable bounds',),
      inputs='A complete exact Frame(var,value), uniquely keyed by var and bound to the model snapshot.',
      guarantees='m.matrix.activity computes exact row activities; compare both row sides and all variable bounds explicitly.',
      non_guarantees='check.solution checks the integer model and rejects domain="relaxation". Fractional values on integer variables fail its integrality check.',
      body='''Use m.matrix.activity(values), with the default missing="error", then join the result to m.rows on row and compare activity against lb and ub.
Independently check full variable coverage and both variable bounds, including variables absent from every row.
A point that passes these comparisons is feasible for the linear relaxation; it need not satisfy integrality.
LinExpr.evaluate only evaluates one expression; it is not a feasibility check. A feasible point is not an optimality or implication certificate.
Use check.solution for a complete point that must also satisfy the original integer domains.''',
      related=('api.MatrixAccessor.activity','api.Frame.join','api.Checker.solution','concept.exact'))

topic('concept.report-resources', 'Report resource lifecycle',
      'Mathematical Reports share paged certificate storage; evidence views and proof dependencies retain the data they need.',
      aliases=('report resource lifecycle','report lifecycle','too many open files','release reports'),
      keywords=('report resources file descriptor exhaustion cleanup close memory batch certificates',),
      guarantees='Scalar mathematical checkers release their temporary workspace after publication and share retained evidence per live model owner. Batch targets share one store; for_target does not open another database.',
      non_guarantees='Report and LinExpr have no public close() method. Retained evidence still uses memory and disk. Unrelated queries and separately retained batches have their own storage; memory_mb is not a global process budget.',
      body='''Use check.batch("linear") for a loop of explicit claims, sources and per-target assumptions. Use check.linear_many when the inputs already exist as Frames.
Retaining thousands of scalar mathematical Reports does not allocate one persistent SQLite connection per Report. Releasing the last Report, Claim, evidence Frame and dependent proof for a node makes its stored rows reclaimable; free database pages can be reused.
Small expressions use bounded exact in-memory terms; larger materialized expressions share spill storage. Temporary SQLite callbacks do not keep discarded workspaces alive; manual garbage collection is not part of the API contract.
Keep source models and proof dependencies alive until verification and any Edit that consumes them are complete.
Export needed audit data with Report.to_spec or Report.write; exported status alone is not a live proof and must be replayed before reuse.
Release unneeded reports, evidence Frames, expressions and containers holding them. Dependent reports and staged edits may still retain references, so deleting one name need not free storage immediately.
Model.close releases owned query resources and invalidates dependent live results. Close a model only when its live results are no longer needed.
Do not call private report stores to close shared evidence. This release offers no per-report deterministic close API.''',
      related=('api.Checker.batch','api.Checker.linear_many','api.Report.for_target','api.Report.write','api.Report.replay','api.Model.close','concept.resources'))

topic('concept.exact', 'Exact arithmetic and external numerical values', 'Use integer, Fraction, finite Decimal or mf.q text for mathematical scalars.',
      aliases=('exact arithmetic','float','rational','decimal','9.6'),
      guarantees='m.check and LinExpr operate on the exact rational values of the bound snapshot.',
      non_guarantees='Converting a solver float to a rational does not certify feasibility, optimality or a dual bound.',
      example='a = mf.q("9.6")\nb = mf.q("43/39")',
      body='''Booleans, NaN, infinity and Python floats are rejected as algebraic scalars. Infinity is allowed only in documented bound fields.
If an external approximate value is useful for candidate ranking, explicitly choose and record its conversion outside mipframe.
For proof multipliers, replay the resulting rational certificate including residual bound compensation.
Exact solution checking may reject a rounded solver point. Do not silently add a tolerance or label it exact.
SciPy export is an explicit float64 conversion; overflow, nonfinite results and nonzero underflow are errors.''',
      related=('api.q','api.Checker.linear','api.MatrixView.to_scipy'))

topic('concept.resources', 'Execution budgets and bounded display', 'Preview size, materialization size, algorithm output and search completeness are different limits.',
      aliases=('memory','disk budget','output limit','performance','large matrix'),
      guarantees='Complete ops finish or raise; no truncated mathematical result is returned.',
      non_guarantees='head() cannot bypass a join validation barrier, sort or aggregation. repr does not promise constant-time upstream work.',
      body=BUDGET+''' Parsing has a separate 32 MiB minimum.
collect/to_records explicitly materialize and can raise MaterializationLimitError; use iter_batches/write for large outputs.
Most ops default to 1,000,000 output records. Set max_output_rows=None only when full output is intended.
alg *_candidates may stop early and record scope/stop reason. Exhaustive algorithms raise SearchBudgetExceeded when incomplete.
check.enumerate returns INCONCLUSIVE when its enumeration budget is exhausted; this is different from alg.integer.enumerate.
MatrixView repr reports statistics without dense conversion. preview defaults to an 8 by 12 grid, independent of global display options.''',
      related=('api.Frame.iter_batches','concept.search','api.MatrixView.preview'))

topic('concept.search', 'Candidate search versus exhaustive algorithms', 'Use alg for potentially expensive searches; inspect its declared domain and completeness.',
      aliases=('candidate search','search completeness','top k','cliques','odd cycles'),
      guarantees='Candidates are verified graph objects in the selected graph. Exhaustive searches either finish or raise.',
      non_guarantees='A top-k candidate ranking is not a global optimum or a MIP cut proof.',
      body='''clique_candidates and odd_cycle_candidates return ordinary Frames with normalized membership and search information in relations.
maximal_cliques means inclusion-maximal, not maximum-cardinality. simple_cycles and isomorphism_classes are explicit exhaustive searches.
vertices declares the graph domain; scope induces the search subgraph. Do not pass a subset as the full domain while retaining out-of-domain edges.
Structural isomorphism only respects the supplied graph labels; it does not prove an automorphism of the whole optimization model.''',
      related=('api.alg.graph.clique_candidates','api.alg.graph.maximal_cliques','limitation.symmetry'))

topic('concept.errors', 'Recover from a failed query', 'Read the original error and its complete report, then query its linked help topic.',
      aliases=('errors','troubleshooting','migration'),
      guarantees='Suggestions name real current APIs; exceptions retain their original types and evidence.',
      non_guarantees='A resource error or malformed input is not a mathematical counterexample.',
      body='''Use mf.help(exc) inside an exception handler. If exc.report exists, inspect its summary and pageable violations.
Do not fix a strict projection by taking the first neighbor. Inspect zero/multiple matches or refine the target selection explicitly.
Do not silence cardinality errors with m:m unless that relationship is intended. Do not bypass proof errors by omitting because and claiming equivalence.''',
      related=('error.join','error.navigation','concept.proofs'))

topic('error.join', 'Join uniqueness and complete matching', 'Join cardinality and match coverage are separate contracts.', kind='troubleshooting',
      aliases=('join cardinality','many to many','duplicate keys','unknown uniqueness','select uniqueness'),
      guarantees='Explicit 1:1, 1:m or m:1 validates the required uniqueness over the entire input.',
      non_guarantees='Unknown uniqueness does not mean duplicates were found. select("var") on Terms does not make var unique.',
      example='Q = A.join(B, on="var", cardinality="1:1", require="left")',
      body='''auto needs an existing uniqueness fact on at least one side. Otherwise specify the intended cardinality explicitly.
Use m:m only for an intended expansion. require=left/right/both verifies complete matching independently.
Rename conflicting non-key fields with select; there are no implicit suffixes. head(1) cannot suppress invalid later input.
A failed check retains all duplicate or unmatched records in its report.''', related=('api.Frame.join','api.Frame.check'))

topic('error.navigation', 'Navigate entities and terms explicitly', 'Coefficients belong to Terms, not Row or Var entities.', kind='troubleshooting',
      aliases=('Row.coef','Var.coef','Frame.index','Edge','incidence_counts'),
      example='term = x.terms.where(row=R).one()\nterm.row, term.var, term.coef',
      guarantees='one() requires exactly one item; it never silently selects a representative.',
      non_guarantees='Neighborhood .rows/.vars forgets source-to-target edge multiplicity.',
      body='''Replace Frame [] with iloc, loc, family, where or column according to intent. Entity selections expose ids.
Use rows.loc[ref] for Ref[row] and vars.loc[ref] for Ref[var] with matching uid/version. A Ref outside the selection raises KeyError.
Use ref.bind(model) to navigate the full matching snapshot; do not strip .id to bypass an axis or snapshot mismatch.
Use V.project(...) directly for source-preserving strict adjacency projection.
Use m.matrix.nnz(rows=R, vars=V, axis="var") for zero-aware participation counts.
Terms are the unified edge view; exported to_records() is the explicit mutable-record exit.''',
      related=('concept.frame','api.VarFrame.project','api.MatrixAccessor.nnz'))

topic('error.expressions', 'Table predicates and affine model expressions', 'QueryExpr selects table records; LinExpr describes an affine function of model variables.', kind='troubleshooting',
      aliases=('and or','QueryExpr LinExpr','boolean','lambda','Claim bool'),
      example='T = m.terms().where((mf.col("coef") < 0) & mf.col("var").isin(V))\nclaim = (2*x - y).eq(0)',
      guarantees='Query predicates use &, | and ~. Mathematical inequalities use s <= U or s >= L, also LinExpr.le(U) or LinExpr.ge(L). Equalities use .eq().',
      non_guarantees='x == y is entity identity, not a linear equation. Claim and Report have no implicit truth value.',
      body='''Core Frame predicates do not accept arbitrary Python callbacks. Use col to form serializable expressions.
Inspect report.status or call require(). Constructing a Claim does not add a row or prove it.''',
      related=('api.col','api.LinExpr','concept.proofs'))

for ident,title,summary,keys,body,related in [
 ('symmetry','Template similarity is not full model symmetry','Graph or template equality does not prove a permutation preserves the whole MIP.',('symmetry','automorphism','isomorphism'),
  'A full claim must account for every coefficient, row bound, variable bound, domain, objective and external coupling under an explicit bijection. No general MIP symmetry checker or symmetry-breaking certificate is provided.',('recipe.templates','api.alg.graph.isomorphism_classes')),
 ('disjunction','General disjunction certificates are not available','One-hot epigraph strengthening may require reasoning outside the current checkers.',('one hot','epigraph','disjunctive','convex hull','peak'),
  'Frame queries can validate the one-hot rows and assemble branch data. check.cases combines same-target proofs over one finite model-integer variable, retaining other assumptions. There is no general continuous disjunction merger or infeasible-branch elimination rule. Do not fabricate a PROVED Report.',('recipe.epigraph','concept.conditions','api.Checker.cases')),
 ('optimization','Optimization and presolve are external','mipframe inspects and verifies snapshots; it does not solve or provide a presolve/postsolve mapping.',('solver','optimality','solve','presolve','postsolve'),
  'Use an external solver for candidate points, multipliers and optimization. Bind any returned data to the actual snapshot. check.solution establishes feasibility and an objective value, not optimality. Solver statuses and tolerances are external evidence.',('concept.exact','api.Checker.solution')),
 ('export','Exact native and LP output','Native save is lossless; LP output has explicit representability restrictions.',('MPS write','write_mps','export mps','free row','roundtrip'),
  'There is no MPS writer. LP export rejects nonterminating decimal rationals and unrepresentable free rows rather than changing mathematics. Use native save for lossless persistence and audit.roundtrip for supported LP files.',('api.Model.write_lp','api.audit.roundtrip')),
 ('elimination','Dropping a variable is not elimination','Edit.drop_vars only removes variables absent from all final rows and the final objective.',('elimination','substitution','quotient','reformulation'),
  'LinExpr.substitute is algebra without proof. Edit does not provide automatic variable elimination, quotient reformulation, Model.from_frames or projection equivalence for changed coordinates.',('api.Edit.drop_vars','api.LinExpr.substitute')),
]:
    topic('limitation.'+ident,title,summary,kind='limitation',keywords=keys,
          non_guarantees=summary,body=body,related=related)

# Explicitly curated public type navigation. Constructors of live handles are not tutorials.
for symbol,binding,summary,related in [
 ('Model','mipframe.model:Model','Immutable numerical snapshot with derived IR and independent Edit construction.',('home','concept.identity')),
 ('Frame','mipframe.frame.base:Frame','Typed exact relation with declared key, domains and composable query plans.',('concept.frame','api.Frame.from_records')),
 ('RowFrame','mipframe.frame.entity:RowFrame','Deduplicated row entities with sparse variable neighborhoods.',('concept.frame','api.RowFrame.project')),
 ('VarFrame','mipframe.frame.entity:VarFrame','Deduplicated variable entities with sparse row neighborhoods.',('concept.frame','api.VarFrame.project')),
 ('TermFrame','mipframe.frame.terms:TermFrame','Original nonzero matrix terms keyed by row and var.',('concept.frame','api.Model.terms')),
 ('Row','mipframe.frame.row:Row','Original row expression and two independent bounds.',('concept.frame','api.Row.expr')),
 ('Var','mipframe.frame.var:Var','Original variable domain, bounds and objective coefficient.',('concept.frame','api.Var.expr')),
 ('Term','mipframe.frame.terms:Term','Read-only original matrix term with lazy exact coefficient.',('concept.frame',)),
 ('ColumnView','mipframe.frame.column:ColumnView','One requested field with exact reductions and aligned comparisons.',('api.ColumnView.argmax','concept.exact')),
 ('QueryExpr','mipframe.frame.expr:QueryExpr','Serializable table expression, not a model-variable affine function.',('error.expressions',)),
 ('LinExpr','mipframe.linear.expr:LinExpr','Immutable sparse affine expression; use variable arithmetic, row.expr or m.linexpr.',('concept.proofs','concept.exact')),
 ('Report','mipframe.check.report:Report','Immutable checker outcome with scope, dependencies and complete pageable evidence.',('concept.proofs',)),
 ('Edit','mipframe.edit.plan:Edit','Source-relative simultaneous modifications producing a new independent snapshot.',('recipe.edits',)),
 ('MatrixView','mipframe.ops.matrix:MatrixView','Logical sparse slice with explicit preview and float64 export.',('api.MatrixView.preview',)),
 ('MatrixAccessor','mipframe.ops.matrix:MatrixAccessor','Model matrix operations accessed through m.matrix.',('api.MatrixAccessor.nnz',)),
 ('Checker','mipframe.check.accessor:Checker','Exact certificate verification through m.check.',('concept.proofs',)),
 ('LinearBatch','mipframe.check.batch:LinearBatch','Incremental linear certificate builder obtained through m.check.batch("linear").',('api.Checker.batch','concept.report-resources')),
 ('CompressedIR','mipframe.ir.compressed:CompressedIR','Bounded factual model map prepared by read/load.',('home',)),
 ('IndexedResult','mipframe.ir.indexing.views:IndexedResult','Completed coordinate and operator discovery, with exact verification.',('recipe.coordinates',)),
 ('IndexedTensor','mipframe.ir.indexing.views:IndexedTensor','Verified coordinate view with explicit declared axes.',('recipe.coordinates',)),
 ('IndexedOperator','mipframe.ir.indexing.views:IndexedOperator','Recovered exact operator with original bindings and kernel.',('api.IndexedOperator.verify',)),
 ('Ref','mipframe.frame.schema:Ref','Detached snapshot-qualified entity identity; navigate with ref.bind(model) or matching rows.loc[ref]/vars.loc[ref].',('concept.identity',)),
 ('DType','mipframe.frame.schema:DType','Scalar column type, including nullable and snapshot-qualified references.',('concept.frame',)),
 ('Record','mipframe.frame.base:Record','Live read-only record; to_record exports detached values.',('concept.identity',)),
 ('RecordBatch','mipframe.frame.base:RecordBatch','Schema-carrying immutable batch with detached records.',('api.Frame.iter_batches',)),
 ('ModelDiff','mipframe.edit.diff:ModelDiff','Parent-child comparison with actual identity maps and verified semantic directions.',('recipe.edits',)),
]:
    api(symbol,binding,summary,returns='A public domain type. Obtain live handles through their owning Model or query.',related=related)

api('read','mipframe.io:read','Import an LP/MPS file into a new independent exact snapshot.',inputs='Path; optional explicit format and parser options. MPS accepts mps_format="auto" (default), "fixed" or "free", and explicit rhs/bounds/ranges vector names.',returns='Model with default IR and indexed prepared.',guarantees='Independent read calls receive independent uid. MPS auto checks complete record boundaries; padded free-format names and exact numbers are not truncated into fixed-width fields.',resources='memory_mb defaults to 128; parser minimum is 32 MiB.',related=('concept.identity',),example='m = mf.read("model.mps")')
api('load','mipframe.io:load','Load native v3 with retained uid/version and validated saved analyses.',inputs='Native directory.',returns='Model.',failures='Old or corrupt formats are rejected; reimport old models from LP/MPS.',related=('concept.identity',))
api('q','mipframe.linear.scalar:q','Parse an exact finite rational scalar.',inputs='int, Fraction, finite Decimal or decimal/scientific/fraction string.',returns='Fraction.',failures='Float, bool, NaN and infinite algebraic scalars raise ExactArithmeticError.',example='mf.q("43/39")',related=('concept.exact',))
api('col','mipframe.frame.expr:col','Reference a named Frame column in a query expression.',inputs='Existing column name when bound to a Frame.',returns='QueryExpr.',example='mf.col("coef") < 0',related=('error.expressions',))
api('count','mipframe.frame.expr:count','Construct a group aggregation counting records.',returns='Aggregate for Grouped.agg; unlike column count it counts rows.',related=('api.Grouped.agg',))

for name,summary,ret in [
 ('terms','Select original nonzero matrix terms on optional row/variable axes.','TermFrame(row,var,coef); includes no objective or boundary terms.'),
 ('linexpr','Construct a canonical affine expression from a term Frame or selected variables.','LinExpr with duplicate variables summed and zero terms removed.'),
 ('edit','Create a source-relative Edit transaction.','Edit; no snapshot is built yet.'),
 ('diff','Compare the current snapshot with its direct parent or identical snapshot.','ModelDiff; unrelated snapshots are rejected.'),
 ('save','Atomically save a new native v3 directory.','Saved path; existing destinations are rejected.'),
 ('write_lp','Atomically export exact LP where decimal representation is lossless.','Output path; unsupported coefficients/free rows fail without publishing a partial file.'),
 ('close','Close owned resources and invalidate live dependent queries.','None; independently built snapshots and detached exports remain usable.'),
 ('verify','Validate numerical storage and replay prepared indexed results.','Verification summary; not a general MIP feasibility or optimality proof.'),
]:
    api('Model.'+name,'mipframe.model:Model.'+name,summary,returns=ret,related=('concept.identity',),body=LIVE)

for name,summary in {
 'uid':'Snapshot UUID; independent imports differ.', 'version':'Ordered canonical mathematical content fingerprint.',
 'storage_version':'Native storage format version, independent of mathematical identity.',
 'source':'Import or Edit provenance; not a second numerical model.', 'parent':'Parent snapshot identity or None.',
 'stage':'Explicit snapshot stage such as imported or edited.', 'shape':'Number of rows and variables.',
 'nnz':'Number of original nonzero constraint terms.', 'rows':'RowFrame of all rows.', 'vars':'VarFrame of all variables.',
 'ir':'Prepared factual compressed IR; reading this property does not start another search.',
 'matrix':'MatrixAccessor; exact sparse counts/activity and explicit SciPy export.',
 'check':'Checker for exact certificate verification.', 'structure':'Signature and indexed analysis access.',
 'objective':'Original objective direction, constant and coefficients.', 'lineage':'Saved Edit history, not automatically trusted live proof.',
 'A':'Independent float64 SciPy CSR export of the full model; conversion is explicit and can fail.',
 'row_index':'Original row IDs for full matrix axis mapping.', 'var_index':'Original variable IDs for full matrix axis mapping.',
}.items():
    api('Model.'+name,'mipframe.model:Model.'+name,summary,returns=summary,related=('concept.identity',))

FRAME_METHODS = [
 ('where','Filter by serializable boolean predicates and keyword equalities.','Predicates combine by intersection. Ref membership validates axis/snapshot.','Same Frame type and order; nullable filters retain only True.'),
 ('select','Choose, rename or calculate columns.','Column names and named QueryExpr expressions; duplicate outputs are errors.','Ordinary Frame. Direct retained/renamed keys can propagate; computed columns gain no inferred uniqueness.'),
 ('join','Join relations with explicit cardinality and match requirements.','on=str/tuple/mapping; inner or left; auto/1:1/1:m/m:1/m:m; require=None/left/right/both.','Ordinary Frame ordered by left then right. Complete validation precedes output.'),
 ('group_by','Declare grouping keys before aggregation.','One or more scalar columns.','Grouped object; use agg to compute a Frame sorted by group keys.'),
 ('distinct','Remove duplicate complete output records.','Current output columns.','Ordinary Frame retaining first occurrences, with all output columns unique.'),
 ('sort_by','Stable exact ordering by selected columns.','Column names; descending bool or per-column directions.','Same type; missing values last.'),
 ('head','Construct a lazy prefix limit.','Nonnegative integer n.','Same type, original domains; does not count first or skip validation barriers.'),
 ('tail','Select a final suffix.','Nonnegative integer n; may require full input evaluation.','Same type and relative order.'),
 ('sample','Sample without replacement and retain relative input order.','n and optional integer random_state.','Same type; reproducible with fixed random_state.'),
 ('one','Require exactly one output record.','Zero-argument query.','One record/entity; checks at most two outputs after upstream barriers.'),
 ('refs','Extract unique nonmissing typed entity references.','A Ref column; not an untyped integer column.','RowFrame or VarFrame in model order.'),
 ('column','Read only one column and its dependencies.','Column name.','ColumnView with exact reductions and aligned comparisons.'),
 ('nunique','Count distinct combinations of selected columns.','Column name or tuple.','Exact integer count.'),
 ('check','Verify a declared relational property.','unique/product with columns; function with key/value.','Report; product includes all declared domains, never a materialized Cartesian product.'),
 ('collect','Explicitly materialize detached records within a budget.','Optional memory_mb.','Independent list; over limit raises MaterializationLimitError without a partial return.'),
 ('to_records','Export independent records using the collect contract.','Optional memory_mb.','Detached Ref identities and exact scalar values.'),
 ('iter_batches','Stream complete schema-carrying detached batches.','Positive batch_size and optional memory_mb.','Iterator; interrupted/closed live queries raise instead of silently ending.'),
 ('write','Atomically write a complete relation.','Path; JSONL or optional Parquet.','Exact rational and Ref identity encoding; Parquet needs mipframe[parquet].'),
 ('to_spec','Serialize a versioned query AST.','No implicit execution or trusted proof flags.','Independent spec; restoration requires explicit model/relation bindings.'),
 ('explain','Inspect a query plan without executing it.','No arguments.','Schema, keys, dependencies, paths and blocking/validation stages.'),
 ('count','Compute an exact record count.','No arguments.','Integer; arbitrary queries may require complete execution.'),
]
for name,summary,inp,ret in FRAME_METHODS:
    api('Frame.'+name,'mipframe.frame.base:Frame.'+name,summary,inputs=inp,returns=ret,
        failures='Schema/type errors or execution/resource errors preserve their diagnostics.',
        guarantees=LIVE,related=('concept.frame','error.join') if name=='join' else ('concept.frame',),
        resources=BUDGET, body='Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.')
api('Grouped.agg','mipframe.frame.base:Grouped.agg','Compute exact grouped aggregates.',inputs='Named mf.count() or col(...).count/sum/min/max/nunique expressions.',returns='Ordinary Frame keyed by the grouping columns.',guarantees='Only observed groups are emitted. Missing values ignored; empty group extrema are None.',example='G = T.group_by("row").agg(n=mf.count(), total=mf.col("coef").sum())',related=('api.Frame.group_by',))
api('Frame.from_records','mipframe.frame.serialization:from_records','Create a typed relation from explicit records.',inputs='Records plus declared schema; optional key/models and memory_mb.',returns='Ordinary Frame. Ref identity must be valid; this does not create a Model.',
    aliases=('construct a typed frame','create a table with schema'), keywords=('schema construction records column types typed references',),
    related=('api.Frame.schema','concept.frame'))
api('Frame.schema','mipframe.frame.base:Frame.schema','Read the mapping from column names to DType metadata.',
    aliases=('schema','table schema','column types'), keywords=('schema dtype typed columns reference identity',),
    returns='Column DTypes, including snapshot-qualified row and variable references.',
    example='schema = {"target": "str", "var": m.vars.schema["var"], "coef": "number"}',
    body='''For Frame.from_records, provide a schema mapping such as str, int or number for scalar fields.
Copy reference types from m.vars.schema["var"] or m.rows.schema["row"] and pass models=(m,) when building model-bound records.
Use key=("target",) only for genuinely unique target records. A terms table can contain several variables per target.
Schema describes column types; it does not establish uniqueness, coverage or a mathematical claim.''',
    related=('api.Frame.from_records','api.DType','api.Frame.key','concept.frame'))
for n in ('key','unique','models','domains','relations','iloc','loc','columns','shape'):
    api('Frame.'+n,'mipframe.frame.base:Frame.'+n,{
        'iloc':'Position lookup or slice; supports negative positions, rejects bool.',
        'loc':'Lookup by declared key, with tuple for compound keys; no-key lookup fails.',
        'domains':'Declared domains retained through filtering; not reduced to observed values.',
        'relations':'Read-only mapping of attached normalized Frames, such as members and search metadata.',
        'shape':'Exact row count and number of fields; unlike repr this can evaluate the query.',
    }.get(n,'Read the Frame '+n+' metadata.'),returns=LIVE,related=('concept.frame',))

for cls in ('RowFrame','VarFrame'):
    for name,summary,ret in [
        ('project','Map each source to exactly one neighbor in every named target domain.','Ordinary Frame keyed by row/var, with named Ref columns and declared target domains.'),
        ('family','Select a family from the bound structural signature configuration.','Entity Frame; family labels are local to snapshot/configuration/depth.'),
        ('by_name','Find an entity by its original name explicitly.','One live entity; missing names raise KeyError.'),
        ('at','Find one entity by positional index.','Row or Var; negative positions allowed, bool rejected.'),
        ('family_membership','Expose family membership as a normalized relation.','Frame retaining explicit signature configuration and depth.'),
        ('pattern_counts','Count source entities by their family-neighborhood pattern.','Ordinary Frame with normalized pattern items/members in relations.'),
    ]:
        api(cls+'.'+name,'mipframe.frame.entity:'+cls+'.'+name,summary,returns=ret,
            failures='Projection zero or multiple matches raises CardinalityError with complete violations and matches.',
            non_guarantees='Adjacency cardinality does not establish coefficient equality or product coverage.',
            example='P = V.project(i=A, j=B)\nP.check("product", ("i", "j")).require()' if name=='project' else '',
            related=('recipe.coordinates','concept.frame'))
    for name in ('ids','terms', 'vars' if cls=='RowFrame' else 'rows'):
        api(cls+'.'+name,'mipframe.frame.entity:'+cls+'.'+name,'Read '+('original IDs' if name=='ids' else 'original terms' if name=='terms' else 'deduplicated neighboring entities')+'.',returns=LIVE,related=('concept.frame',))
for cls,module,names in [
 ('Row','frame.row',('id','name','lb','ub','lhs','rhs','relation','nnz','expr','terms','vars','lb_claim','ub_claim','eq_claim','formula','to_record')),
 ('Var','frame.var',('id','name','kind','type','is_binary','lb','ub','obj','nnz','expr','terms','rows','lb_claim','ub_claim','eq','to_record')),
 ('Term','frame.terms',('row','var','coef','to_record')),
]:
    for n in names:
        summary={
            'expr':'Original affine expression; row expressions contain no bound and constant zero.',
            'lb_claim':'Original finite lower side with snapshot/entity provenance.',
            'ub_claim':'Original finite upper side with snapshot/entity provenance.',
            'eq_claim':'Original equality side; both finite row bounds must coincide.',
            'formula':'Bounded exact Formula display with explicitly limited terms.',
            'to_record':'Export an independent exact record; subsequent mutation cannot change the model.',
            'kind':'Canonical C/I domain; binary is derived from effective integer bounds.',
            'is_binary':'Whether the nonempty integer domain is contained in {0,1}, including fixed 0/1.',
            'terms':'Original nonzero terms, not a dense row or column.',
            'coef':'The original exact Fraction coefficient, decoded only when requested.',
            'eq':'Construct a mathematical equality Claim; entity == retains identity semantics.',
        }.get(n,'Read the original '+n+' field or entity relation.')
        api(cls+'.'+n,'mipframe.'+module+':'+cls+'.'+n,summary,returns=LIVE,
            failures='Absent finite source bounds raise MissingBoundError; closed models reject live access.',related=('concept.frame','concept.proofs'))
for n in ('head','count','sum','min','max','nunique','argmin','argmax','value_counts','ids'):
    api('ColumnView.'+n,'mipframe.frame.column:ColumnView.'+n,
        'Return all tied extremizers in original order and Frame type.' if n in ('argmin','argmax') else 'Read or reduce one exact column: '+n+'.',
        guarantees='Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.',
        returns='Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.',related=('concept.exact',))
for n in ('isin','is_null','is_not_null','count','sum','min','max','nunique'):
    api('QueryExpr.'+n,'mipframe.frame.expr:QueryExpr.'+n,'Construct the '+n+' table expression.',returns='Serializable predicate or aggregation expression.',related=('error.expressions',))

for name,summary,inp,ret,limits in [
 ('pairs','Compare exact set supports and their cardinalities.','Unique (set,member); optional declared sets, left/right or directed candidates.','Frame(left,right,left_n,right_n,intersection,left_only,right_only,union_n).','equal/subset/strict_subset/superset/strict_superset/overlap/disjoint. Empty sets participate. Symmetric same-domain pairs default unordered without self.'),
 ('members','Expand a specified pair relation into exact members.','Unique membership and pairs(left,right).','Frame(left,right,member).','intersection/union/left_only/right_only. Recomputes membership rather than trusting pair statistics.'),
 ('equal_groups','Classify exact equal supports or member-to-value maps.','Unique (set,member); optional value column and declared sets.','Frame(set,class) with class the smallest original set key.','No hash-collision assumption. Equal support alone says nothing about row coefficients or bounds.'),
 ('components','Find hypergraph member components without clique expansion.','Unique membership; optional sets and full member vertices.','Frame(member,component), retaining isolated declared members.','A structural component does not establish separability from excluded rows or the objective.'),
 ('aggregate','Sum exact member attributes per set and optional group.','membership(set,member), values unique on (member,*by).','Frame(set,*by,value).','Finite exact values; absent attributes are sparse zero, explicit None fails. No-by empty sums are zero.'),
 ('pair_aggregate','Aggregate members of pair unions/intersections/differences without double counting.','membership, pairs(left,right), values unique on (member,*by).','Frame(left,right,*by,value).','Only sum; absent attributes are zero. by-groups are observed, including exact cancellations to zero.'),
]:
    api('ops.sets.'+name,'mipframe.ops.sets:'+name,summary,inputs=inp,returns=ret,guarantees=limits,
        failures='Duplicate set/member records, domain violations and resource limits fail the complete call.',
        resources=OPS,related=('recipe.support','concept.resources'))
api('ops.equal_groups','mipframe.ops.grouping:equal_groups','Compare grouped payload multisets exactly.',inputs='Frame, scalar by column and payload rows columns; optional declared groups.',returns='Frame(by,class) preserving empty declared groups.',guarantees='Order is ignored; duplicate multiplicity and None payloads matter.',non_guarantees='No automatic coordinate recovery, proportional normalization or variable permutation.',resources=OPS,related=('recipe.templates',))
for name,summary,ret,extra in [
 ('components','Compute undirected components of an endpoint relation.','Frame(vertex,component).','Includes isolated declared vertices.'),
 ('scc','Compute directed strongly connected components.','Frame(vertex,scc).','Nonrecursive traversal; representative is minimum original key.'),
 ('topological_order','Order a directed acyclic graph deterministically.','Frame(vertex,order,level).','Smallest available vertex first; level is longest predecessor depth. Cycles raise GraphCycleError with full SCC evidence.'),
 ('reachability','Compute exact reachable endpoint pairs.','Frame(source,target).','sources/targets restrict returned endpoints, not intermediate vertices. include_self=False excludes all diagonals even in cycles.'),
 ('transitive_reduction','Reduce a DAG while preserving reachability.','Original input schema and all parallel source records for retained endpoint pairs.','Rejects cycles/self-loops. This is not a proof that original constraints can be deleted.'),
 ('path','Return one stable shortest directed path.','Frame(step,u,v), plus relations["status"].','Unreachable and source==target both have empty edge paths; status distinguishes unreachable from length zero.'),
]:
    api('ops.graph.'+name,'mipframe.ops.graph:'+name,summary,inputs='Frame with compatible nonmissing endpoint columns; optional declared vertices.',returns=ret,guarantees=extra,resources=OPS,related=('recipe.network','recipe.closure'))
GRAPH_SEARCH = {
 'clique_candidates': (
   'Find greedy inclusion-maximal cliques, ranked among those found.',
   'E(u,v); optional vertices domain, induced scope and unique score(vertex,value). Scores must cover scope and be finite exact values.',
   'Frame(clique,vertex); relations["summary"] has clique,size,score; relations["search"] has scope, budgets and stop reason.',
   'Simple undirected adjacency; reversed/parallel edges deduplicate and self-loops confer no adjacency. min_size=1 includes isolates. top counts whole objects, not member records.',
   'Roots/extensions use exact score, degree, then key. Maximal means inclusion-maximal in the induced scope, not maximum size or weight. Negative scores are allowed. No guarantee of the globally best top-k.'),
 'maximal_cliques': (
   'Enumerate every inclusion-maximal clique in the declared scope.',
   'E(u,v); optional full vertices, induced scope and score(vertex,value); min_size filters objects.',
   'Frame(clique,vertex), with summary(clique,size,score) and search metadata relations.',
   'Every qualifying maximal clique or an exception. No top parameter. min_size=1 includes isolated singletons; no empty clique.',
   'Scope is an induced graph; maximality need not hold in the original larger graph. score orders objects, not a maximum-weight optimization guarantee.'),
 'odd_cycle_candidates': (
   'Find verified undirected odd cycles of specified lengths.',
   'E(u,v); lengths is a nonempty sequence of odd integers >=3; optional vertices, induced scope, score(vertex,value), chordless and top.',
   'Frame(cycle,position,vertex); summary(cycle,length,score) and search metadata relations.',
   'Vertices are distinct; zero-based positions omit the repeated closing vertex. chordless rejects adjacency between nonconsecutive vertices.',
   'Canonical rotation starts at the smallest key; reversed undirected cycles deduplicate. Scoring sums vertex values and does not establish an inequality violation.'),
 'simple_cycles': (
   'Enumerate all simple cycles in an explicitly directed or undirected graph.',
   'E(u,v); directed must be specified. Optional min_length/max_length, vertices, induced scope, chordless and score(vertex,value).',
   'Frame(cycle,position,vertex); summary(cycle,length,score) and search metadata relations.',
   'Exhaustive within declared length and induced scope. Directed cycles include self-loops and reciprocal 2-cycles by default; undirected minimum length is 3.',
   'Directed reversal is different; undirected reversal deduplicates. chordless=True is supported only for undirected graphs. max_length=None gives no length ceiling.'),
 'isomorphism_classes': (
   'Classify complete explicitly labeled graph instances by exact isomorphism.',
   'E(graph,u,v,*edge_labels); vertices(graph,vertex,*vertex_labels) declares each graph separately. Optional single-column graphs includes empty graphs; explicit directed and label names.',
   'Frame(graph,class); relations["mapping"] has graph,vertex,representative_vertex; search metadata describes the exhaustive scope.',
   'Representative is the smallest graph key. A complete label/adjacency-preserving bijection is verified, not inferred from stable colors.',
   'Loops and isolates participate. Duplicate endpoint labels must agree. None is an exact label; vertex IDs are identities, not labels. Not subgraph matching, automorphism enumeration, or a MIP symmetry certificate.'),
}
for name,(summary,inp,ret,guarantee,body) in GRAPH_SEARCH.items():
    module='mipframe.alg._isomorphism' if name=='isomorphism_classes' else 'mipframe.alg.graph'
    candidate=name.endswith('_candidates')
    api('alg.graph.'+name,module+':'+name,summary,inputs=inp,returns=ret,
        guarantees=guarantee+' '+('Every returned object is verified; search can omit others and stop early with an explicit reason.' if candidate else 'State/time exhaustion raises SearchBudgetExceeded without a partial successful Frame.'),
        non_guarantees='No MIP proof, automatic symmetry claim or approved cut is produced.',
        resources=OPS+' Output counts membership plus summary/mapping rows. State/time limits are separate; resource failures never silently truncate an object.',
        related=('concept.search','recipe.conflicts'),body=body)
api('alg.integer.enumerate','mipframe.alg.integer:enumerate','Enumerate the complete feasible assignments of an explicit finite integer system.',
    inputs='variables(var,lb,ub); terms(row,var,coef); rows(row,lb,ub), with compatible typed endpoint keys. Variable bounds are finite; row bounds may be infinite.',
    returns='Frame(assignment,var,value); relations["summary"] contains every assignment, including the feasible empty assignment.',
    guarantees='Explicit pure-integer domains ceil(lb)..floor(ub). Exact leaf checks; exhaustive or SearchBudgetExceeded, with no partial successful result.',
    non_guarantees='No continuous projection, objective optimization, implicit model integrality or implication Report.',resources=OPS,
    related=('recipe.enumeration','api.Checker.enumerate'))

for n,ret in [('view','MatrixView retaining original selected axis order.'),('nnz','Frame(row,nnz) or Frame(var,nnz), including zero-degree axis members.'),('activity','Frame(row,activity), including zero rows.'),('transpose_activity','Frame(var,value), including zero columns.')]:
    api('MatrixAccessor.'+n,'mipframe.ops.matrix:MatrixAccessor.'+n,'Access the original sparse matrix: '+n+'.',returns=ret,
        inputs='Same-snapshot axes; activity input is unique Frame(var,value), transpose input Frame(row,value).',
        guarantees='Activity is exact and requires all participating inputs unless missing="zero".',resources=BUDGET,related=('concept.frame','concept.exact'))
for n in ('rows','vars','shape','nnz','density','row_nnz','var_nnz','entries','preview','to_scipy'):
    api('MatrixView.'+n,'mipframe.ops.matrix:MatrixView.'+n,{
        'preview':'Explicit small exact preview; default 8 rows by 12 variables.',
        'to_scipy':'Export independent float64 CSR with local axes matching rows.ids and vars.ids.',
        'entries':'Original selected terms as a TermFrame; no dense conversion.',
    }.get(n,'Read selected matrix '+n+'.'),
        guarantees='Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.',
        non_guarantees='Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.',
        failures='SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.',related=('concept.resources','concept.exact'))

api('Claim','mipframe.linear.claim:Claim','Construct an unproved affine proposition with <=, >=, LinExpr.le/ge or .eq().',
    aliases=('claim construction','construct a claim','create inequality','create equality'),
    keywords=('claim construction inequality equality comparison operators affine proposition',),
    inputs='Model variables or a LinExpr and an exact scalar or compatible affine expression.',
    returns='Claim; constructing it neither proves it nor adds a row.',
    example='expr = 2*x - y\nupper = expr <= 3\nlower = expr >= 0\nequality = expr.eq(1)',
    non_guarantees='Python == is not mathematical equality for model entities. A newly constructed Claim is not a trusted original source side.',
    body='''Build expressions from variable arithmetic, row.expr or m.linexpr. Use mf.q("1/2") for fractional scalars.
Create target inequalities with <= or >= and equalities with .eq(); Claim and Report have no implicit truth value.
LinExpr.le(U) and LinExpr.ge(L) are equivalent method forms of <= and >=.
For trusted sources use row.lb_claim, row.ub_claim, var.lb_claim or var.ub_claim, or a compatible proved Report.
Verify a target with m.check before passing its proof to an Edit. Direct Claim constructor arguments are not the expression-building interface.''',
    related=('api.LinExpr','api.Model.linexpr','api.Checker.linear','concept.proofs','error.expressions'))

for cls,axis in (('RowFrame','row'),('VarFrame','var')):
    api(cls+'.loc','mipframe.frame.entity:'+cls+'.loc',
        'Look up an original integer ID or same-snapshot Ref within this entity selection.',
        inputs=f'Integer {axis} ID or Ref[{axis}] with matching snapshot uid/version.',
        returns='One live entity in this selection.',
        guarantees='Ref axis and snapshot are checked before ID lookup. Selection membership is always enforced.',
        failures='Wrong axis or snapshot raises SchemaError; missing or excluded IDs raise KeyError. Names require by_name().',
        example=f'entity = m.{"rows" if axis == "row" else "vars"}.loc[ref]',
        related=('concept.identity','api.Ref.bind','error.navigation'))

for n in ('le','ge','eq'):
    relation={'le':'<=','ge':'>=','eq':'=='}[n]
    api('LinExpr.'+n,'mipframe.linear.expr:LinExpr.'+n,
        'Construct an unproved mathematical '+relation+' Claim.',
        inputs='Exact finite scalar, Var or compatible affine expression from the same snapshot.',
        returns='Claim, or DraftClaim when the other operand contains draft variables.',
        guarantees=('Equivalent to the '+relation+' operator.' if n!='eq' else 'Mathematical equality; Python == retains expression equality semantics.'),
        non_guarantees='Does not add a row, prove the proposition or permit implicit bool.',
        failures='Inexact scalars, cross-snapshot operands and closed owners are rejected.',
        example=f'claim = s.{n}(U)',
        related=('error.expressions','concept.proofs','concept.exact'))

for n in ('terms','vars','constant','substitute','evaluate','formula','to_spec'):
    api('LinExpr.'+n,'mipframe.linear.expr:LinExpr.'+n,{
        'substitute':'Simultaneous one-level affine substitution; right sides are not recursively substituted.',
        'evaluate':'Evaluate an affine expression at exact values without checking feasibility.',
        'terms':'Canonical sorted nonzero expression terms; an ordinary Frame, not original TermFrame.',
        'formula':'Bounded Formula display; does not verify the expression.',
        'to_spec':'Export the versioned exact expression representation.',
    }.get(n,'Read or construct affine expression '+n+'.'),inputs='Exact finite scalars and compatible snapshot identities.',
        returns=LIVE,non_guarantees='Expression manipulation alone does not establish a valid model constraint.',related=('concept.proofs','concept.exact'))
for n,summary,guarantees in [
 ('cases','Discharge an exhaustive finite integer split over one variable.','Every branch proves the same exact target; only its matching equality assumption is removed. Remaining assumptions are unioned. The result is integer-only.'),
 ('identity','Check an exact affine identity or explicit equality combination.','No-premise coefficient mismatch refutes identity; insufficient specified premises yield INCONCLUSIVE.'),
 ('linear','Verify that supplied sources imply the target inequality or equality.','Inequality multipliers nonnegative; equality multipliers may have either sign. bounds compensates exact residuals using required bounds.'),
 ('infeasible','Verify an explicit contradiction certificate.','PROVED means contradiction in the recorded scope; failure to find contradiction is INCONCLUSIVE, never feasible.'),
 ('round','Round a proved bound on a model-integer-valued expression.','Same nonconstant integer coefficients and integer variables required; floor upper / ceil lower; scope becomes integer.'),
 ('enumerate','Verify a claim on a finite local integer domain.','Exhaustion proves; a local counterexample refutes only this local proposition; budget exhaustion yields INCONCLUSIVE.'),
 ('solution','Validate a complete exact point against the mathematical integer model, including integrality, original bounds and both row sides.','Returns all violations or feasibility with exact objective. No optimality conclusion.'),
 ('linear_many','Batch explicit linear certificates in shared paged working storage.','Targets keyed by strings; for_target returns one checked conclusion. Overall PROVED requires all targets proved.'),
]:
    api('Checker.'+n,'mipframe.check.accessor:Checker.'+n,summary,
        inputs=('Exact Frame(var,value) or Var/VarRef mapping from this snapshot. missing="error" requires every variable; missing="zero" explicitly fills absent values.' if n=='solution' else 'Exact claims, original source sides or previously verified Reports; see signature.'),
        returns='Report with complete scope, dependencies and evidence.',guarantees=guarantees,
        non_guarantees='No solver call, multiplier search, automatic condition discharge or model edit.',
        failures='Invalid sources, missing input, bad multipliers, cross-snapshot input or unsupported domains raise; they are not INCONCLUSIVE.',
        resources=BUDGET,
        aliases=('m.check.'+n,) + {'linear_many':('Frame native batch certificates','batch certificate Frames'),
                                 'cases':('case discharge','finite domain case discharge','binary case proof','exhaust cases'),
                                 'solution':('integer point feasibility',)}.get(n,()),
        keywords={'linear_many':('batch batched bulk many multiple linear certificates proofs shared storage',),
                  'solution':('integer solution point feasibility exact values',)}.get(n,()),
        related=('concept.proofs','concept.conditions') + {
            'cases':('recipe.cases',),
            'linear_many':('api.Checker.batch','api.Frame.schema','api.Report.for_target','concept.report-resources'),
            'solution':('limitation.fractional-point',),
        }.get(n,()),
        body={
            'cases':'''var is a same-snapshot Var or VarRef with integer kind and finite model bounds. cases maps exact integer values to verified exact PROVED Reports with the same target relation, constant and coefficients.
Keys must exactly cover ceil(lb)..floor(ub); duplicates after normalization, empty domains, floats and booleans are rejected. Coverage never uses branch assumptions.
Each branch must assume a*y+b==0 for this variable and value. Remove matching equalities only; preserve the union of every other explicit or inherited assumption. Nested calls can discharge multiple variables.
domain="auto" and "integer" produce integer proofs; "relaxation" is rejected. Missing/invalid branches raise, not REFUTED. Infeasible Reports cannot substitute for target proofs.
Use ordinary linear checks to align stronger or differently scaled conclusions first. Branches may contain continuous variables. Results support Edit.because only when no assumptions remain; all model fact dependencies and integer-domain coverage bounds remain auditable.
The cases and discharged evidence Frames record branch parent indices and removed assumptions. Replay rechecks all branches and coverage.''',
            'identity':'Without premises, compare the exact affine expressions. With using, verify the supplied equality combination.',
            'linear':'Equality targets use equality-only sources, or explicit using={"le": ..., "ge": ...} certificates. residual="bounds" compensates coefficient residuals with original variable bounds.',
            'infeasible':'Supply a contradiction combination explicitly. Single-variable assumptions tighten bounds or substitute values and remain recorded in the conditional scope.',
            'round':'The target must keep the same nonconstant integer-valued expression as the proved source bound. Round upper bounds down and lower bounds up.',
            'enumerate':'vars must contain every remaining variable after substitutions; continuous or unbounded integer domains are unsupported.',
            'solution':'''missing="error" requires all model variables; missing="zero" is explicit. Inputs from a solver are not silently rounded.
domain="auto" and domain="integer" both enforce the original integer domains; domain="relaxation" is rejected. For fractional LP points, use m.matrix.activity(values) plus exact checks of both row sides and every variable bound. Verify complete variable coverage, including variables absent from all rows; see limitation.fractional-point.''',
            'linear_many':'''targets has target/relation/rhs, with unique string target IDs; terms has target/var/coef.
using has target and exactly one source field row/var/proof per record, plus side and multiplier (direction for equality target sides).
Reference columns use m.rows.schema["row"] or m.vars.schema["var"]. A proof source is a string key in the explicit reports mapping.
Use residual="bounds" for bound compensation. This Frame-native entry point has no assume parameter; use check.batch("linear").add(..., assume=...) for per-target assumptions and ordinary Claim/Report inputs.
The result shares paged storage across targets; inspect evidence and use for_target(target_id) for one conclusion. require() on the batch requires every target to be PROVED.''',
        }[n])

api('Checker.batch','mipframe.check.accessor:Checker.batch',
    'Build a batch of linear certificates with scalar arguments and per-target assumptions.',
    inputs='method="linear"; residual="exact" or "bounds"; domain="auto", "relaxation" or "integer"; memory_mb and disk_mb apply to the whole batch.',
    returns='LinearBatch context manager. add stages each check; run verifies all targets and returns a Report.',
    guarantees='Consumes inputs incrementally in shared storage. No caller-authored schemas or reports mapping are required.',
    non_guarantees='No multiplier search, condition discharge, enumeration batch or model edit.',
    failures='Unsupported methods or options raise. Invalid inputs, cancellation and resource failures abort unpublished work.',
    resources=BUDGET,
    aliases=('m.check.batch','linear batch builder','batch checks with assumptions','batch.add',
             'batch linear proof','many linear certificates','batch certificates',
             'check many linear certificates at once'),
    keywords=('batch linear certificates bulk scalar claims assume assumptions branch proofs simple builder',),
    example='''with m.check.batch("linear", residual="bounds") as batch:
    for row in m.rows:
        batch.add(key=str(row.id), target=row.ub_claim, using=[row.ub_claim])
    report = batch.run()
report.require()''',
    body='''The example requires finite upper bounds on the selected rows. Replace targets and sources with the actual propositions and certificates to verify.
Prefer this builder for ordinary Python loops. If targets/terms/using are already Frames, use linear_many instead.
The returned report remains usable after leaving the with block. Use report.for_target(key) for one conclusion, including its explicit and inherited assumptions.''',
    related=('api.LinearBatch.add','api.LinearBatch.run','api.LinearBatch.close','api.Checker.linear_many','concept.report-resources','concept.conditions'))
api('LinearBatch.add','mipframe.check.batch:LinearBatch.add',
    'Stage one keyed target with the same using and assume arguments as check.linear.',
    inputs='A unique nonempty string key, Claim target, original source Claims or verified Reports, optional exact multipliers and assumptions. Equality certificates also accept using={"le": ..., "ge": ...}.',
    returns='The builder for chaining. No proof is published by add.',
    guarantees='Consumes generators and copies mathematical inputs into shared paged staging now; does not retain a list of target expressions. Parent proof dependencies remain live.',
    failures='Duplicate keys, invalid sources and staging failures abort and close the builder. A closed or already-run builder rejects further additions.',
    related=('api.Checker.batch','api.Checker.linear','api.LinearBatch.run','concept.conditions'))
api('LinearBatch.run','mipframe.check.batch:LinearBatch.run',
    'Verify every staged linear target and seal the builder.',
    returns='Report with evidence["results"] and for_target(key). An empty batch is PROVED.',
    guarantees='Overall PROVED requires every target proved. Insufficient certificates yield per-target INCONCLUSIVE. All assumptions and domains remain attached to their own conclusions.',
    failures='Errors or cancellation publish no partial result. run may be called only once.',
    body='Successful run transfers result storage to the Report. The result, subreports and evidence views survive builder.close() or context exit while their source models remain open.',
    related=('api.Report.for_target','api.Report.require','api.Report.replay','concept.report-resources'))
api('LinearBatch.close','mipframe.check.batch:LinearBatch.close',
    'Release unpublished batch work and close the builder.',
    returns='None.', guarantees='Idempotent. Reports already returned by run remain valid.',
    related=('api.Checker.batch','api.LinearBatch.run','concept.report-resources'))
for n in ('status','ok','summary','scope','conclusion','method','dependencies','evidence','counterexample','violations','require','explain','to_spec','write','replay','for_target'):
    api('Report.'+n,'mipframe.check.report:Report.'+n,{
        'require':'Raise ValidationError carrying this report unless status is PROVED.',
        'replay':'Bind dependencies and rerun certificate verification; never trust saved status.',
        'for_target':'Select one conclusion from a batch Report, retaining shared evidence storage.',
        'write':'Atomically export the exact certificate package, not the whole model.',
    }.get(n,'Read report '+n+' with the original conclusion and scope.'),returns=LIVE,related=('concept.proofs',))

for n,summary,inp in [
 ('add_row','Stage a new row with a new identity.','Claim or LinExpr with explicit lb/ub; optional name and because.'),
 ('add_rows','Stage a complete batch of new rows atomically.','rows(label,name?,lb?,ub?); terms(label,var,coef), or nullable var/draft_var endpoints.'),
 ('add_vars','Stage new variables and return Edit-local handles.','specs(label,name?,kind?,lb?,ub?,obj?); defaults C,0,+inf,0.'),
 ('drop_rows','Stage deletion of source rows.','Source Row/RowFrame; because must cover every lost finite side if provided.'),
 ('drop_vars','Remove only finally unreferenced variables.','Source Var/VarFrame; final row degree and objective coefficient must be zero.'),
 ('set_row_bounds','Change source row bounds without changing its expression.','Source rows; scalar or exactly key-aligned Frame(row,value) per side.'),
 ('set_var_bounds','Change source variable bounds.','Source variables; scalar or exactly key-aligned Frame(var,value) per side.'),
 ('fix','Set both source variable bounds to one exact value.','Source variables, value and optional verified evidence.'),
 ('set_domains','Set the canonical variable domain.','Source variables; kind C/I or key-aligned value Frame.'),
 ('set_objective','Replace the entire objective independent of staging order.','Affine source/draft expression and min/max sense; includes its constant.'),
 ('replace_rows','Stage explicit row replacement relationships.','old source rows, new rows/terms and correspondence(old_row,new_label).'),
 ('summary','Validate and summarize the staged plan without building a Model.','No arguments; reports whether build is possible.'),
 ('build','Atomically build an independent edited snapshot and prepare its own IR/indexed.','allow_infeasible=False rejects directly visible impossible bounds/empty rows.'),
 ('close','Release the Edit and live draft resources.','No arguments.'),
]:
    api('Edit.'+n,'mipframe.edit.plan:Edit.'+n,summary,inputs=inp,returns='New staging result, Formula summary or independent Model as appropriate.',
        guarantees='All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.',
        non_guarantees='Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.',
        failures='Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.',
        resources=BUDGET,related=('recipe.edits','concept.conditions'),
        body='''Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
Use native save to retain lineage; historical saved status is not a new live proof.''')
api('audit.roundtrip','mipframe.audit:roundtrip','Numerically reparse an LP and compare exact model facts.',inputs='Model, LP path, optional explicit rename mapping.',returns='I/O Report with complete differences.',non_guarantees='I/O equality is not a reusable linear certificate.',related=('limitation.export','recipe.edits'))

for name, summary, returns, guarantees in [
    ('show', 'Render a bounded factual model overview.', 'Notebook-friendly Formula text; does not print.', 'Rendering uses completed summaries and does not run discovery.'),
    ('to_dict', 'Export the complete factual IR summary with exact numbers.', 'Independent dict; rational values remain Fractions.', 'The exported data outlives the model and is not truncated by display limits.'),
    ('to_json', 'Export the complete factual IR summary as JSON.', 'JSON string; rational values are encoded as strings.', 'The exported data outlives the model and is not truncated by display limits.'),
]:
    api('CompressedIR.'+name, 'mipframe.ir.compressed:CompressedIR.'+name, summary,
        returns=returns, guarantees=guarantees,
        non_guarantees='The summary does not embed the full numerical model, member lists or parameter histograms. Structural groups do not prove mathematical equivalence.',
        body='IR schema 0.3 retains IDs, depths, scopes and configurations. Access detailed objects through m.rows, m.vars, m.matrix and m.structure. For a family use m.structure.signatures().family(id, depth=depth); for indexed tensors and operators use m.structure.indexed().tensor(id) or .operator(id).',
        related=('api.Model.ir', 'api.StructuralSignatures.family', 'api.Structure.indexed'))

for cls,module,names in [
 ('IndexedResult','ir.indexing.views',('tensors','partitions','operators','evidence','tensor','operator','partition','evidence_item','verify','validate','to_record')),
 ('IndexedTensor','ir.indexing.views',('vars','axes','coordinates','verify')),
 ('IndexedOperator','ir.indexing.views',('rows','bindings','kernel','alignment','formula','verify')),
]:
    for n in names:
        api(cls+'.'+n,'mipframe.'+module+':'+cls+'.'+n,'Inspect or verify '+cls+' '+n+'.',returns='Public summary, domain handle, Frame or verification Report.',
            guarantees='Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.',
            non_guarantees='A structural signature alone does not prove coordinates, symmetry or a valid cut.',related=('recipe.coordinates','concept.frame'))
api('Ref.bind','mipframe.frame.schema:Ref.bind','Bind a detached identity to the corresponding live snapshot.',inputs='Model with matching uid/version and correct axis/ID.',returns='Live Row or Var.',related=('concept.identity',))
for symbol,bind in [('frame_from_spec','mipframe.frame.serialization:frame_from_spec'),('read_frame','mipframe.frame.serialization:read_frame')]:
    api(symbol,bind,'Restore an exact relation with explicit snapshot/dependency bindings.',returns='Frame; externally claimed uniqueness is not trusted.',related=('concept.identity','api.Frame.write'))

# Additional domain facades are registered by their public reachability, not private helpers.
for cls,module,names in [
 ('Structure','ir.signatures',('indexed','signatures','summary','splits')),
 ('StructuralSignatures','ir.signatures',('config','mode','rows','vars','row_families','var_families','family','summary','splits')),
 ('StructuralConstraintFamily','ir.signatures',('rows','params','inspect')),
 ('VariableFamily','ir.signatures',('vars','params','inspect')),
 ('Lineage','edit.lineage',('parent','semantic','validation','findings','operations','evidence','changes','mapping','resolve','proof')),
 ('ModelDiff','edit.diff',('semantic','objective_changed','nnz_before','nnz_after','row_nnz_stats','col_nnz_stats')),
]:
    api(cls,'mipframe.'+module+':'+cls,'Read-only '+cls+' domain facade.',returns='Obtain from the corresponding Model analysis or lineage property.',related=('concept.identity',)) if cls not in ('ModelDiff',) else None
    for n in names:
        api(cls+'.'+n,'mipframe.'+module+':'+cls+'.'+n,{
            'indexed':'Retrieve default completed indexed results or discover an explicitly selected custom scope.',
            'signatures':'Compute or reuse structural refinement with explicit mode, depth and numeric policy.',
            'params':'Inspect observed signed-role coefficient parameters, distinct values and ranges.',
            'inspect':'Display bounded family details, including changing numeric roles.',
            'proof':'Replay stored lineage evidence with explicitly bound source snapshots.',
            'mapping':'Read explicit source/target entity correspondence, optionally binding the parent.',
            'semantic':'Read declared semantic scope; saved lineage summaries are historical, not live proofs.',
        }.get(n,'Read '+cls+' '+n+'.'),returns='Bounded summary, Frame or domain result.',
            non_guarantees='Family labels are run/configuration-local. Canonical coefficients are not raw matrix values. Historical proof status cannot authorize a new Edit.',
            related=('concept.identity','recipe.roles'))

# Public access-path aliases, including bound Model facades.
for item in TOPICS:
    for symbol in item['symbols']:
        if symbol.startswith('Checker.'):
            item['aliases'].append('m.check.' + symbol.split('.',1)[1])
        if symbol.startswith('MatrixAccessor.'):
            item['aliases'].append('m.matrix.' + symbol.split('.',1)[1])
        if symbol.startswith('Structure.'):
            item['aliases'].append('m.structure.' + symbol.split('.',1)[1])
        if symbol.startswith('Model.'):
            item['aliases'].append('m.' + symbol.split('.',1)[1])
    # Avoid projecting an unrelated failure/limitation onto entity lookup methods.
    if item['id'].startswith(('api.RowFrame.', 'api.VarFrame.')) and not item['id'].endswith('.project'):
        item['failures'] = 'Invalid selections, unknown labels/names and closed owners raise explicit errors.'
        item['non_guarantees'] = 'No coefficient or mathematical equivalence is inferred from navigation.'
    if item['id'] == 'api.alg.integer.enumerate':
        item['inputs'] = 'variables(var,lb,ub); terms(row,var,coef); rows(row,lb,ub), with compatible endpoint keys.'
        item['returns'] = 'Frame(assignment,var,value); relations["summary"] contains every assignment, including the empty one.'

EXCEPTIONS = {
 'mipframe.linear.scalar:ExactArithmeticError':'concept.exact',
 'mipframe.linear.scalar:NonlinearExpressionError':'error.expressions',
 'mipframe.linear.claim:MissingBoundError':'concept.proofs',
 'mipframe.check.certificate:InvalidCertificateError':'concept.proofs',
 'mipframe.check.certificate:UnsupportedEnumerationDomain':'api.Checker.enumerate',
 'mipframe.check.report:CardinalityError':'error.join',
 'mipframe.check.report:ValidationError':'concept.proofs',
 'mipframe.check.algorithm:SearchBudgetExceeded':'concept.search',
 'mipframe.check.algorithm:QueryTooLarge':'concept.resources',
 'mipframe.check.algorithm:DiskBudgetExceeded':'concept.resources',
 'mipframe.check.algorithm:GraphCycleError':'api.ops.graph.topological_order',
 'mipframe.frame.executor:QueryMemoryError':'concept.resources',
 'mipframe.frame.executor:MaterializationLimitError':'concept.resources',
 'mipframe.frame.schema:SchemaError':'concept.frame',
 'mipframe.edit.plan:EditConflictError':'api.Edit',
 'mipframe.edit.plan:EditValidationError':'api.Edit.build',
 'mipframe.edit.plan:UndischargedAssumptionError':'concept.conditions',
 'mipframe.edit.plan:NonIsolatedVariableError':'limitation.elimination',
 'mipframe.store.numbers:MIPFrameError':'concept.errors',
}

# Recipes and Skill share the same versioned corpus and renderer.
from .recipes import add_recipes
add_recipes(topic)
api('help','mipframe._help:help','Retrieve current offline documentation without executing model queries.',inputs='English query, topic ID, registered public object or exception.',returns='Formula text or independent JSON-serializable dictionary.',guarantees='No model data reads or side effects; unrecognized APIs are not invented.',related=('home','skill'))
for item in TOPICS:
    if item['id']=='recipe.edits':item['aliases'].append('edit model')
    if item['id']=='concept.proofs':item['aliases'].append('exact linear verification')
    if item['id']=='error.navigation':item['aliases'].extend(['MatrixEntries','Projection','IncidenceCounts','legacy analyze'])
for symbol,binding,summary in [
 ('AlgebraicSignature','mipframe.ir.algebraic:AlgebraicSignature','Canonical exact single-row coefficient pattern, distinct from the original row formula.'),
 ('TypedSignature','mipframe.ir.typed:TypedSignature','Single-row pattern with variable-domain observations.'),
 ('StructuralSignature','mipframe.ir.signatures:StructuralSignature','One entity role in a bound structural refinement configuration.'),
 ('ConstraintFamily','mipframe.ir.families:ConstraintFamily','Read-only family facade with explicit original members.'),
 ('NameIndex','mipframe.store.names:NameIndex','Explicit original-name lookup index; names do not replace typed Ref identity.'),
 ('DraftRef','mipframe.edit.draft:DraftRef','Detached Edit-local draft identity, distinct from a source snapshot Ref.'),
 ('canonical_ratio','mipframe.ir.algebraic:canonical_ratio','Compute a canonical exact coefficient ratio for signature observations.'),
]:
    api(symbol,binding,summary,non_guarantees='A signature, name or draft identity does not establish a model equivalence.',related=('concept.frame','concept.identity'))
for binding,ident in EXCEPTIONS.items():
    symbol=binding.split(':')[1]
    api(symbol,binding,'Diagnostic exception; inspect original message and any attached complete Report.',returns='Exception type, not a mathematical result.',related=(ident,))
api('NotDAGError','mipframe.check.algorithm:NotDAGError','Directed graph is not a DAG; inspect the attached cycle evidence.',related=('api.ops.graph.topological_order',))
api('ValidationReport','mipframe.check.report:ValidationReport','Compatibility name for the same Report implementation.',related=('concept.proofs',))
topic('concept.options','Explicit display and query settings','options.display, options.ir and options.query configure presentation or working limits.',aliases=('options',),
    non_guarantees='Display limits do not reduce the mathematical scope of a query or verification.',related=('concept.resources',))
topic('capability.ops','Exact structural operations','Use sets, graphs and grouped multiset equality for complete structural facts.',kind='capability',aliases=('ops','ops.sets','ops.graph'),
    body='Search support containment, hypergraph components, directed reachability, exact template comparison or resource union aggregation.',
    guarantees='These operations finish or raise; output is not silently truncated.',non_guarantees='Structural facts are not optimization certificates.',related=('recipe.support','recipe.closure','recipe.decomposition'))
topic('capability.alg','Discrete searches','Find graph candidates or run explicit exhaustive searches over a declared domain.',kind='capability',aliases=('alg','alg.graph','alg.integer'),
    guarantees='Candidate and exhaustive APIs have distinct completeness contracts.',non_guarantees='Search results do not grant proof or Edit authority.',related=('concept.search','recipe.enumeration'))
topic('capability.audit','Serialization audit','Use audit.roundtrip to reparse supported LP output and compare exact facts.',kind='capability',aliases=('audit',),
    non_guarantees='No MPS export, solver postsolve map or linear implication certificate is provided.',related=('api.audit.roundtrip','limitation.export'))
