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
        non_guarantees='', failures='', example='', related=(), resources='', aliases=(), keywords=(), body='', constructor=False):
    receiver = ('m.check.' + symbol.split('.', 1)[1] if symbol.startswith('Checker.') else
                'm.matrix.' + symbol.split('.', 1)[1] if symbol.startswith('MatrixAccessor.') else
                'm.' + symbol.split('.', 1)[1] if symbol.startswith('Model.') else None)
    if receiver and receiver not in aliases:
        aliases = (*aliases, receiver)
    item = topic('api.' + symbol, symbol, summary, kind='api', aliases=aliases, keywords=keywords,
                 when=summary, inputs=inputs, returns=returns, guarantees=guarantees,
                 non_guarantees=non_guarantees or 'No model change or additional mathematical conclusion is implied.',
                 failures=failures, resources=resources, example=example, related=related,
                 bindings={symbol: binding}, body=body)
    if constructor:
        item['constructor'] = True
    return item


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
Verify: LinExpr, Claim, m.check; ProofRef/BatchProof for implications, PointReport for points.
Modify: m.edit(), commit() for verified changes, build() for experiments, diff(), save(), write().

Run mf.help("recipe.quickstart", detail="full") for a complete temporary-file example.
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
Use .iloc for positions, .loc for declared keys, .by_name for names and m.family(label).rows or .vars for family members.
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
read/load prepare only a cheap numerical scan. Families, topology and indexed discovery require explicit access; help, repr and default IR exports never start them.
Only native v7 is accepted; reimport LP/MPS for native v3/v4/v5/v6.''',
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
      'Use check.solution(domain="relaxation") for complete exact LP point feasibility.',
      kind='limitation', aliases=('fractional point feasibility','LP point feasibility','relaxation feasibility'),
      keywords=('fractional solution feasible relaxation witness rational point row variable bounds',),
      inputs='A complete exact Frame(var,value), uniquely keyed by var and bound to the model snapshot.',
      guarantees='check.solution(domain="relaxation") checks complete variable coverage, all variable bounds and both row sides with exact arithmetic.',
      non_guarantees='A PointReport is not an implication proof and cannot authorize edits. A relaxation point need not satisfy integrality or optimality.',
      body='''Use m.check.solution(values, domain="relaxation").require() for one complete fractional point. For multiple points, register batch.solution(values, domain="relaxation", result_key=...) and run once.
The default missing="error" requires every variable, including variables absent from all rows. Variable bounds and both row sides are always checked. Only integrality is omitted in the explicit relaxation domain.
The default domain="auto" and domain="integer" enforce integrality. scope.domain records which domain was checked; saved point replay preserves it.
Use m.matrix.activity only when row activities themselves are needed. LinExpr.evaluate evaluates one expression, not full feasibility.''',
      related=('api.MatrixAccessor.activity','api.Frame.join','api.Checker.solution','concept.exact'))

topic('concept.report-resources', 'Report resource lifecycle',
      'Mathematical Reports share paged certificate storage; evidence views and proof dependencies retain the data they need.',
      aliases=('report resource lifecycle','report lifecycle','too many open files','release reports'),
      keywords=('report resources file descriptor exhaustion cleanup close memory batch certificates',),
      guarantees='Scalar mathematical checkers release their temporary workspace after publication and share retained evidence per live model owner. Batch targets share one store; for_target does not open another database.',
      non_guarantees='Report and LinExpr have no public close() method. Retained evidence still uses memory and disk. Unrelated queries and separately retained batches have their own storage; memory_mb is not a global process budget.',
      body='''Prefer check.batch() to register related checks with per-target conditions. Use linear_many as an adapter for existing compact arrays or Frames.
A BatchProof retains compact arrays and dependency leases; scalar ProofRef views do not own separate stores. Diagnostic Reports retain lazy presentation artifacts. Releasing the last expression/result view releases its owned scratch artifacts.
Small expressions use exact in-memory terms; large materialized expressions use typed outputs and bounded sorted-run spill. Scratch lifetime follows retained views; manual garbage collection is not part of the API contract.
Keep source models and proof dependencies alive until verification and any Edit that consumes them are complete.
Export needed audit data with Report.to_spec or Report.write; exported status alone is not a live proof and must be replayed before reuse.
Release unneeded reports, evidence Frames, expressions and containers holding them. Dependent reports and staged edits may still retain references, so deleting one name need not free storage immediately.
Model.close releases owned query resources and invalidates dependent live results. Close a model only when its live results are no longer needed.
Do not call private report stores to close shared evidence. This release offers no per-report deterministic close API.''',
      related=('api.Checker.linear_batch','api.Checker.linear_many','api.Report.for_target','api.Report.write','api.Report.replay','api.Model.close','concept.resources'))

topic('concept.exact', 'Exact arithmetic and external numerical values', 'Use integer, Fraction, finite Decimal or mf.q text for mathematical scalars.',
      aliases=('exact arithmetic','float','rational','decimal','9.6'),
      guarantees='m.check and LinExpr operate on the exact rational values of the bound snapshot.',
      non_guarantees='Converting a solver float to a rational does not certify feasibility, optimality or a dual bound.',
      example='a = mf.q("9.6")\nb = mf.q("43/39")',
      body='''Booleans, NaN, infinity and Python floats are rejected as algebraic scalars. Infinity is allowed in Frame number columns representing bounds.
Frame arithmetic keeps finite quotients exact, including finite/infinity = Fraction(0). Indeterminate infinity arithmetic (infinity-infinity, zero*infinity, infinity/infinity and sums of opposite infinities) raises ExactArithmeticError.
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
memory_mb controls working buffers; disk_mb limits temporary spill (None is unlimited). Child operations share a ledger.
to_records and to_spec use a separate materialize_mb limit, default 128 MiB, and can raise MaterializationLimitError; use iter_batches/write for large outputs.
Frame input, relation execution and diagnostics use typed arrays and mmap; there is no SQLite numerical fallback.
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
 ('export','Exact native, LP and MPS output','Native save is lossless; LP and MPS output have explicit representability restrictions.',('MPS write','write','export mps','free row','roundtrip'),
  'MPS supports stream patching and canonical output. Both LP and MPS reject nonterminating decimal rationals and unrepresentable free rows rather than changing mathematics. Use native save for lossless persistence and audit.roundtrip for supported LP files.',('api.Model.write','api.audit.roundtrip')),
 ('elimination','Fixed-variable elimination and lifting','Edit.drop_vars only removes variables absent from all final rows and the final objective.',('elimination','substitution','quotient','reformulation'),
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
 ('Report','mipframe.check.report:Report','Structural and integrity diagnostic result; mathematical checkers return ProofRef, BatchProof or PointReport.',('concept.proofs',)),
 ('Edit','mipframe.edit.plan:Edit','Source-relative simultaneous modifications producing a new independent snapshot.',('recipe.edits',)),
 ('MatrixView','mipframe.ops.matrix:MatrixView','Logical sparse slice with explicit preview and float64 export.',('api.MatrixView.preview',)),
 ('MatrixAccessor','mipframe.ops.matrix:MatrixAccessor','Model matrix operations accessed through m.matrix.',('api.MatrixAccessor.nnz',)),
 ('Checker','mipframe.check.accessor:Checker','Exact certificate verification through m.check.',('concept.proofs',)),
 ('LinearBatch','mipframe.check.batch:LinearBatch','Incremental linear certificate builder obtained through m.check.linear_batch().',('api.Checker.linear_batch','concept.report-resources')),
 ('CompressedIR','mipframe.ir.compressed:CompressedIR','Bounded factual model map prepared by read/load.',('home',)),
 ('IndexedResult','mipframe.ir.indexing.views:IndexedResult','Completed coordinate and operator discovery, with exact verification.',('recipe.coordinates',)),
 ('Ref','mipframe.frame.schema:Ref','Detached snapshot-qualified entity identity; navigate with ref.bind(model) or matching rows.loc[ref]/vars.loc[ref].',('concept.identity',)),
 ('DType','mipframe.frame.schema:DType','Scalar column type, including nullable and snapshot-qualified references.',('concept.frame',)),
 ('Record','mipframe.frame.base:Record','Live read-only record; to_record exports detached values.',('concept.identity',)),
 ('RecordBatch','mipframe.frame.base:RecordBatch','Schema-carrying immutable batch with detached records.',('api.Frame.iter_batches',)),
 ('ModelDiff','mipframe.edit.diff:ModelDiff','Parent-child comparison with actual identity maps and verified semantic directions.',('recipe.edits',)),
]:
    api(symbol,binding,summary,returns='A public domain type. Obtain live handles through their owning Model or query.',related=related)

api('read','mipframe.io:read','Import an LP/MPS file into a new independent exact snapshot.',inputs='Path; optional explicit format and parser options. MPS accepts mps_format="auto" (default), "fixed" or "free", and explicit rhs/bounds/ranges vector names.',returns='Model with a cached level-0 scan; structural analyses are explicit.',guarantees='Independent read calls receive independent uid. MPS auto checks complete record boundaries; padded free-format names and exact numbers are not truncated into fixed-width fields.',resources='memory_mb defaults to 128; parser minimum is 32 MiB.',related=('concept.identity',),example='m = mf.read("model.mps")')
api('load','mipframe.io:load','Load validated native v7 numerical content and a cheap scan; saved analyses restore on explicit access.',inputs='Native directory.',returns='Model.',failures='Old or corrupt numerical formats are rejected; reimport old models from LP/MPS. Saved analysis caches are validated on explicit analysis access.',related=('concept.identity',))
api('q','mipframe.linear.scalar:q','Parse an exact finite rational scalar.',inputs='int, Fraction, finite Decimal or decimal/scientific/fraction string.',returns='Fraction.',failures='Float, bool, NaN and infinite algebraic scalars raise ExactArithmeticError.',example='mf.q("43/39")',related=('concept.exact',))
api('col','mipframe.frame.expr:col','Reference a named Frame column in a query expression.',inputs='Existing column name when bound to a Frame.',returns='QueryExpr.',example='mf.col("coef") < 0',related=('error.expressions',))
api('count','mipframe.frame.expr:count','Construct a group aggregation counting records.',returns='Aggregate for Grouped.agg; unlike column count it counts rows.',related=('api.Grouped.agg',))

for name,summary,ret in [
 ('terms','Select original nonzero matrix terms on optional row/variable axes.','TermFrame(row,var,coef); includes no objective or boundary terms.'),
 ('linexpr','Construct a canonical affine expression from a term Frame or selected variables.','LinExpr with duplicate variables summed and zero terms removed.'),
 ('edit','Create a source-relative Edit transaction.','Edit; no snapshot is built yet.'),
 ('diff','Compare ancestor deltas or unrelated numerical snapshots.','ModelDiff for an ancestor (including self); NumericDiff for unrelated snapshots, with equivalence UNKNOWN.'),
 ('save','Atomically save a new native v7 directory.','Saved path; existing destinations are rejected.'),
 ('write','Atomically serialize an exact snapshot to MPS or LP.','WriteReceipt; unsupported representations fail without publishing a partial file.'),
 ('close','Close owned resources and invalidate live dependent queries.','None; independently built snapshots and detached exports remain usable.'),
 ('verify','Validate numerical storage and replay prepared indexed results.','Report scoped to model integrity and prepared indexed runs; not MIP feasibility or optimality.'),
]:
    api('Model.'+name,'mipframe.model:Model.'+name,summary,returns=ret,related=('concept.identity',),body=LIVE)

for name,summary in {
 'uid':'Snapshot UUID; independent imports differ.', 'version':'Ordered canonical mathematical content fingerprint.',
 'storage_version':'Native storage format version, independent of mathematical identity.',
 'source':'Import or Edit provenance; not a second numerical model.', 'parent':'Parent snapshot identity or None.',
 'stage':'Explicit snapshot stage such as imported or edited.', 'shape':'Number of rows and variables.',
 'nnz':'Number of original nonzero constraint terms.', 'rows':'RowFrame of all rows.', 'vars':'VarFrame of all variables.',
 'ir':'Cheap numerical scan; prepared by read/load, or lazily after a logical Edit build. Structural properties compute on explicit access.',
 'matrix':'MatrixAccessor; exact sparse counts/activity and explicit SciPy export.',
 'check':'Checker for exact certificate verification.',
 'row_families':'Template row-family Frame at depth 2.', 'var_families':'Template variable-family Frame at depth 2.',
 'objective':'Original objective direction, constant and coefficients.', 'lineage':'Saved Edit history, not automatically trusted live proof.',
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
 ('to_records','Export independent records within the materialization budget.','Optional memory_mb, disk_mb and materialize_mb (independent output limit).','Detached Ref identities and exact scalar values.'),
 ('iter_batches','Stream complete schema-carrying detached batches.','Positive batch_size and optional memory_mb/disk_mb.','Iterator; interrupted/closed live queries raise instead of silently ending.'),
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
api('Frame.from_records','mipframe.frame.serialization:from_records','Create a typed relation from explicit records.',inputs='Records plus declared schema; optional key/models and memory_mb/disk_mb.',returns='Ordinary Frame. Ref identity must be valid; this does not create a Model.',
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
        ('families','Group selected members by full-model template families.','Frame retaining global family identity and selected-member counts.'),
        ('by_name','Find an entity by its original name explicitly.','One live entity; missing names raise KeyError.'),
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
 ('Row','frame.row',('source_line','id','name','lb','ub','lhs','rhs','relation','nnz','expr','terms','vars','lb_claim','ub_claim','eq_claim','formula','to_record')),
 ('Var','frame.var',('source_line','id','name','kind','type','is_binary','lb','ub','obj','nnz','expr','terms','rows','lb_claim','ub_claim','eq','to_record')),
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
For trusted sources use row.lb_claim, row.ub_claim, var.lb_claim or var.ub_claim, or a compatible proved ProofRef.
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
        inputs=('Exact Frame(var,value) or Var/VarRef mapping from this snapshot. missing="error" requires every variable; missing="zero" explicitly fills absent values.' if n=='solution' else 'Exact claims, original source sides or previously verified ProofRefs; see signature.'),
        returns=('PointReport with feasibility status, objective summary and violation evidence; not an implication proof.' if n=='solution' else 'BatchProof.' if n=='linear_many' else 'ProofRef with status, conclusion, scope, dependencies and evidence; not a diagnostic Report.'),guarantees=guarantees,
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
            'linear_many':('api.Checker.linear_batch','api.Frame.schema','api.Report.for_target','concept.report-resources'),
            'solution':('limitation.fractional-point',),
        }.get(n,()),
        body={
            'cases':'''var is a same-snapshot Var or VarRef with integer kind and finite model bounds. cases maps exact integer values to verified exact PROVED ProofRefs with the same target relation, constant and coefficients.
Keys must exactly cover ceil(lb)..floor(ub); duplicates after normalization, empty domains, floats and booleans are rejected. Coverage never uses branch assumptions.
Each branch must assume a*y+b==0 for this variable and value. Remove matching equalities only; preserve the union of every other explicit or inherited assumption. Nested calls can discharge multiple variables.
domain="auto" and "integer" produce integer proofs; "relaxation" is rejected. Missing/invalid branches raise, not REFUTED. Contradiction proofs cannot substitute for proofs of the requested target.
Use ordinary linear checks to align stronger or differently scaled conclusions first. Branches may contain continuous variables. Results support Edit.because only when no assumptions remain; all model fact dependencies and integer-domain coverage bounds remain auditable.
The cases and discharged evidence Frames record branch parent indices and removed assumptions. Replay rechecks all branches and coverage.''',
            'identity':'Without premises, compare the exact affine expressions. With using, verify the supplied equality combination.',
            'linear':'Equality targets use equality-only sources, or explicit using={"le": ..., "ge": ...} certificates. residual="bounds" compensates coefficient residuals with original variable bounds.',
            'infeasible':'Supply a contradiction combination explicitly. Single-variable assumptions tighten bounds or substitute values and remain recorded in the conditional scope.',
            'round':'The target must keep the same nonconstant integer-valued expression as the proved source bound. Round upper bounds down and lower bounds up.',
            'enumerate':'vars must contain every remaining variable after substitutions; continuous or unbounded integer domains are unsupported.',
            'solution':'''missing="error" requires all model variables; missing="zero" is explicit. Inputs from a solver are not silently rounded.
domain="auto" and domain="integer" enforce the original integer domains. domain="relaxation" checks all variable bounds and both row sides without integrality. All modes require complete variable coverage by default, including variables absent from all rows. scope.domain and point artifacts retain the chosen domain. Use batch.solution for multiple points; see limitation.fractional-point.''',
            'linear_many':'''targets has target/relation/rhs, with unique string target IDs; terms has target/var/coef.
using has target and exactly one source field row/var/proof per record, plus side and multiplier (direction for equality target sides).
Reference columns use m.rows.schema["row"] or m.vars.schema["var"]. A proof source is a string key in the explicit reports mapping.
Use residual="bounds" for bound compensation. This Frame-native entry point has no assume parameter; use check.batch() for ordinary Claim inputs, per-target assumptions and proof dependencies.
The result shares paged storage across targets; inspect evidence and use for_target(target_id) for one conclusion. require() on the batch requires every target to be PROVED.''',
        }[n])

api('Checker.linear_batch','mipframe.check.accessor:Checker.linear_batch',
    'Adapt scalar linear certificate inputs to the unified execution engine.',
    inputs='residual="exact" or "bounds"; domain="auto", "relaxation" or "integer"; memory_mb and disk_mb apply to the whole batch.',
    returns='LinearBatch context manager. add stages each check; run verifies all targets and returns a Report.',
    guarantees='Consumes inputs incrementally in shared storage. No caller-authored schemas or reports mapping are required.',
    non_guarantees='No multiplier search, condition discharge, enumeration batch or model edit.',
    failures='Unsupported methods or options raise. Invalid inputs, cancellation and resource failures abort unpublished work.',
    resources=BUDGET,
    aliases=('m.check.linear_batch','linear batch builder','batch checks with assumptions','batch.add',
             'batch linear proof','many linear certificates','batch certificates',
             'check many linear certificates at once'),
    keywords=('batch linear certificates bulk scalar claims assume assumptions branch proofs simple builder',),
    example='''with m.check.linear_batch(residual="bounds") as batch:
    for row in m.rows:
        batch.add(key=str(row.id), target=row.ub_claim, using=[row.ub_claim])
    report = batch.run()
report.require()''',
    body='''The example requires finite upper bounds on the selected rows. Replace targets and sources with the actual propositions and certificates to verify.
Prefer check.batch() for new multi-rule work. This builder remains a compatibility adapter for linear-only inputs; registration loops do not execute checks.
The returned report remains usable after leaving the with block. Use report.for_target(key) for one conclusion, including its explicit and inherited assumptions.''',
    related=('api.LinearBatch.add','api.LinearBatch.run','api.LinearBatch.close','api.Checker.linear_many','concept.report-resources','concept.conditions'))
api('LinearBatch.add','mipframe.check.batch:LinearBatch.add',
    'Stage one keyed target with the same using and assume arguments as check.linear.',
    inputs='A unique nonempty string key, Claim target, original source Claims or verified Reports, optional exact multipliers and assumptions. Equality certificates also accept using={"le": ..., "ge": ...}.',
    returns='The builder for chaining. No proof is published by add.',
    guarantees='Consumes generators and copies mathematical inputs into shared paged staging now; does not retain a list of target expressions. Parent proof dependencies remain live.',
    failures='Duplicate keys raise. On a staging error, leave the context or close the builder and start a fresh batch. A closed or already-run builder rejects further additions.',
    related=('api.Checker.linear_batch','api.Checker.linear','api.LinearBatch.run','concept.conditions'))
api('LinearBatch.run','mipframe.check.batch:LinearBatch.run',
    'Verify every staged linear target and seal the builder.',
    returns='Report with evidence["results"] and for_target(key). An empty batch is PROVED.',
    guarantees='Overall PROVED requires every target proved. Insufficient certificates yield per-target INCONCLUSIVE. All assumptions and domains remain attached to their own conclusions.',
    failures='Errors or cancellation publish no partial result. run may be called only once.',
    body='Successful run transfers result storage to the Report. The result, subreports and evidence views survive builder.close() or context exit while their source models remain open.',
    related=('api.BatchProof.for_target','api.BatchProof.require','api.BatchProof.replay','concept.report-resources'))
api('LinearBatch.close','mipframe.check.batch:LinearBatch.close',
    'Release unpublished batch work and close the builder.',
    returns='None.', guarantees='Idempotent. BatchProof results already returned by run remain valid.',
    related=('api.Checker.linear_batch','api.LinearBatch.run','concept.report-resources'))
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
 ('set_domains','Set the canonical variable domain.','Source variables or IDs; kind is the scalar string C or I. Split selections into separate calls for different domains; a value Frame is not accepted.'),
 ('set_objective','Replace the entire objective independent of staging order.','Affine source/draft expression and min/max sense; includes its constant.'),
 ('replace_rows','Stage explicit row replacement relationships.','old source rows, new rows/terms and correspondence(old_row,new_label).'),
 ('summary','Validate and summarize the staged plan without building a Model.','No arguments; reports whether build is possible.'),
 ('build','Atomically build an independent exact snapshot; structural analyses are lazy by default.','allow_infeasible=False rejects directly visible impossible bounds/empty rows. prepare="logical" defers IR/indexed; prepare="indexed" prepares both before returning.'),
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
    ('to_dict', 'Export a detached scan; include_analysis=True explicitly computes analyses.', 'Independent dict; rational values remain Fractions.', 'The exported data outlives the model and is not truncated by display limits.'),
    ('to_json', 'Export scan JSON; include_analysis=True explicitly computes analyses.', 'JSON string; rational values are encoded as strings.', 'The exported data outlives the model and is not truncated by display limits.'),
]:
    api('CompressedIR.'+name, 'mipframe.ir.compressed:CompressedIR.'+name, summary,
        returns=returns, guarantees=guarantees,
        non_guarantees='The summary does not embed the full numerical model, member lists or parameter histograms. Structural groups do not prove mathematical equivalence.',
        body='IR schema 0.4 defaults to level-0 scan only. Counts, degrees, bounds and numeric extrema are exact; bounded high-cardinality histograms carry error bounds. Wide-row fingerprints are candidates, and lexical templates are hypotheses. Default Mapping iteration and exports do not traverse lazy fields. structure/topology/indexed and objective role_support retain lazy lookup compatibility. Use row_shapes() and name_templates() for exact full directories with selected members. Display limits do not rescan or truncate cached machine summaries; scan computation itself has a separate budget.',
        related=('api.Model.ir', 'api.Model.family', 'api.Model.indexed'))

for name,summary,body in [
 ('row_shapes','Compute a full exact local row-shape Frame.','Group raw sense, exact bounds, nnz and coefficient multiset, without normalization or adjacent roles. Filter with where; rows returns original members and related("coefficients") returns full multiplicities. Directory IDs are model-local and independent of scan preview positions. Keep group_key for navigation.'),
 ('name_templates','Compute a full exact lexical-template Frame.','kind is row or var. Tokenize integer slots preserving delimiters. Filter templates; rows/vars returns original members and related("slots") gives exact minimum, maximum and distinct counts. Names are lexical hypotheses, not structural evidence. Keep group_key for navigation.'),
]:
    api('CompressedIR.'+name,'mipframe.ir.compressed:CompressedIR.'+name,summary,
        returns='A cached navigable Frame requiring an open model.',body=body,
        resources='Explicit exact analysis may use disk-backed working storage.',
        related=('api.Model.ir','api.Frame.related'))

for cls,module,names in [
 ('IndexedResult','ir.indexing.views',('tensors','partitions','operators','evidence','tensor','operator','partition','evidence_item','verify','validate','to_record')),
]:
    for n in names:
        summary = ('Replay prepared tensors/operators or an explicit directory selection.' if n=='verify' else
                   'Validate an independently supplied indexed hypothesis.' if n=='validate' else
                   'Export detached run metadata.' if n=='to_record' else
                   'Select one indexed item by its required ID.' if n in ('tensor','operator','partition','evidence_item') else
                   'Query the indexed '+n+' directory.')
        body = ('An explicit selection must retain indexed_key and belong to this snapshot and run. Partitions can be selected; evidence cannot. Empty selection returns checked=0. Summary checked/failures count primary items, not diagnostic rows; evidence["checks"] lists items, evidence["details"] includes their details. Inconclusive or skipped checks cannot produce PROVED. Inconsistency returns REFUTED; require() raises. Input and execution errors raise directly.' if n=='verify' else
                'Candidate metadata, AST, coordinates, bindings, kernels, alignment and partition memberships accept Frames. Complete independent candidate dictionaries remain supported. Identity never bypasses exact verification. Diagnostic reports cannot be used as linear certificates.' if n=='validate' else
                'Counts use row_count/var_count; members use rows/vars. Dimensions, AST and numerical details are related tables. Singular lookups reject unknown IDs with KeyError.')
        api(cls+'.'+n,'mipframe.'+module+':'+cls+'.'+n,summary,returns=('Report with explicit indexed scope.' if n in ('verify','validate') else 'Detached run metadata.' if n=='to_record' else 'Ordinary Frame with selected-key related navigation.'),
            guarantees='Verification recipes replay exact checks; directory navigation follows the selected identity keys.',
            non_guarantees='A structural signature alone does not prove coordinates, symmetry or a valid cut.',body=body,related=('concept.indexed','recipe.coordinates','concept.frame'))
api('Ref.bind','mipframe.frame.schema:Ref.bind','Bind a detached identity to the corresponding live snapshot.',inputs='Model with matching uid/version and correct axis/ID.',returns='Live Row or Var.',related=('concept.identity',))
for symbol,bind in [('frame_from_spec','mipframe.frame.serialization:frame_from_spec'),('read_frame','mipframe.frame.serialization:read_frame')]:
    api(symbol,bind,'Restore an exact relation with explicit snapshot/dependency bindings.',returns='Frame; externally claimed uniqueness is not trusted.',related=('concept.identity','api.Frame.write'))

# Additional domain facades are registered by their public reachability, not private helpers.
for cls,module,names in [
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

# Finalize contracts for the entity APIs declared above. Receiver aliases are
# registered by api(), including APIs declared later in this file.
for item in TOPICS:
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
 ('NameIndex','mipframe.store.names:NameIndex','Explicit original-name lookup index; names do not replace typed Ref identity.'),
 ('DraftRef','mipframe.edit.draft:DraftRef','Detached Edit-local draft identity, distinct from a source snapshot Ref.'),
 ('canonical_ratio','mipframe.ir.algebraic:canonical_ratio','Compute a canonical exact coefficient ratio for signature observations.'),
]:
    api(symbol,binding,summary,non_guarantees='A signature, name or draft identity does not establish a model equivalence.',related=('concept.frame','concept.identity'))
for binding,ident in EXCEPTIONS.items():
    symbol=binding.split(':')[1]
    api(symbol,binding,'Diagnostic exception; inspect original message and any attached complete Report.',returns='Exception type, not a mathematical result.',related=(ident,))
api('NotDAGError','mipframe.check.algorithm:NotDAGError','Directed graph is not a DAG; inspect the attached cycle evidence.',related=('api.ops.graph.topological_order',))
topic('concept.options','Explicit display and query settings','options.display, options.ir and options.query configure presentation or working limits.',aliases=('options',),
    non_guarantees='Display limits do not reduce the mathematical scope of a query or verification.',related=('concept.resources',))
topic('capability.ops','Exact structural operations','Use sets, graphs and grouped multiset equality for complete structural facts.',kind='capability',aliases=('ops','ops.sets','ops.graph'),
    body='Search support containment, hypergraph components, directed reachability, exact template comparison or resource union aggregation.',
    guarantees='These operations finish or raise; output is not silently truncated.',non_guarantees='Structural facts are not optimization certificates.',related=('recipe.support','recipe.closure','recipe.decomposition'))
topic('capability.alg','Discrete searches','Find graph candidates or run explicit exhaustive searches over a declared domain.',kind='capability',aliases=('alg','alg.graph','alg.integer'),
    guarantees='Candidate and exhaustive APIs have distinct completeness contracts.',non_guarantees='Search results do not grant proof or Edit authority.',related=('concept.search','recipe.enumeration'))
topic('capability.audit','Serialization audit','Use audit.roundtrip to reparse supported LP output and compare exact facts.',kind='capability',aliases=('audit',),
    non_guarantees='An I/O audit grants no solver postsolve map or linear implication certificate. Model.write supports MPS and LP readback.',related=('api.audit.roundtrip','limitation.export'))


for symbol, binding, summary, returns in [
 ('Model.families','mipframe.model:Model.families','Inspect template structural family directories and their members.','Frame; kind=row or var, depth=2 by default.'),
 ('Model.family','mipframe.model:Model.family','Select one template family by F/V label and explicit depth.','Exactly one-row Frame; unknown labels raise KeyError, conflicting depths raise ValueError.'),
 ('Model.refinement','mipframe.model:Model.refinement','Inspect parent/child family transitions, including unchanged groups.','Frame with selected/global counts and related before/after directories.'),
 ('Model.indexed','mipframe.model:Model.indexed','Retrieve or discover indexed coordinates with explicit structural configuration.','IndexedResult; cached full-model results are reused.'),
 ('Frame.related','mipframe.frame.base:Frame.related','Navigate a declared relation using the current selected keys.','Frame, RowFrame or VarFrame according to the relation declaration.'),
 ('Frame.rows','mipframe.frame.base:Frame.rows','Read declared row members of the current family or indexed selection.','Deduplicated RowFrame; selections without a declared row-member axis reject access.'),
 ('Frame.vars','mipframe.frame.base:Frame.vars','Read declared variable members of the current family or indexed selection.','Deduplicated VarFrame; selections without a declared variable-member axis reject access.'),
 ('Frame.inspect','mipframe.frame.base:Frame.inspect','Render a bounded table or provider-specific family/indexed details.','String; verbose includes all-orientation diagnostics as bounded previews.'),
 ('analysis.families','mipframe.analysis:families','Group entities with an explicit advanced feature rule.','Frame with global identities and selected member counts.'),
 ('analysis.signatures','mipframe.analysis:signatures','Read exact entity features, patterns and structural levels.','Frame; one Row input produces one row, never a Signature object.'),
]:
    api(symbol,binding,summary,returns=returns,
        body=('max_items bounds the preview for every Frame. Family views also support max_splits, samples, sample_formula, max_terms and verbose; indexed views support max_items and verbose. Unsupported non-default display options raise ValueError.' if symbol=='Frame.inspect' else 'include_singletons=False skips partitions with only one row set during discovery, before allocating component artifacts. These partitions cannot contribute to tensor pairing. Use include_singletons=True to retain them in I.partitions; this may produce very large directories. The option is part of the cached run identity and is preserved by save/load. Tensor and operator discovery and exact verification are unchanged.' if symbol=='Model.indexed' else ''),
        aliases=(('m.' + symbol.split('.', 1)[1],) if symbol.startswith('Model.')
                 else ('mf.' + symbol,) if symbol.startswith('analysis.') else ()),
        guarantees='Snapshot and analysis configuration remain bound. Family and indexed views share storage; original mathematical facts remain accessible through entity members.',
        non_guarantees='A repeated structure is an observation, not symmetry, redundancy, coordinates or a mathematical proof.',
        related=('concept.families','concept.frame','recipe.roles'))

topic('concept.families','Families as Frames','Start with m.row_families, m.var_families or m.family(label).',
    aliases=('family','families','structural signatures','template structural','family members','family parameters'),
    keywords=('structure template rows vars family membership refinement depth parameters',),
    body='''m.families(kind="row", depth=2) returns a family Frame; m.family("F0") returns a single-row Frame.
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
Views are live and require the model to stay open; they do not own one database per family. repr is bounded and does not compute numeric profiles.''',
    related=('api.Model.families','api.Model.family','api.Frame.related','api.Frame.inspect','api.analysis.families','api.analysis.signatures'))


topic('api.analysis','Advanced feature analysis','Explicit feature rules returning Frames.',
    kind='api', aliases=('mf.analysis','analysis'),
    body='Use analysis.families or analysis.signatures with an explicit method. The Model family methods default to template structural.',
    related=('api.analysis.families','api.analysis.signatures','concept.families'))

# Indexed data follows the same Model/Frame navigation contract as families.
topic('concept.indexed', 'Indexed directories and related Frames',
      'Use ordinary Frames for indexed coordinates, partitions, operators and evidence.',
      aliases=('indexed frames','indexed kernel','indexed axes','indexed AST'),
      body='I = m.indexed() binds a snapshot, configuration and declared scope.\nI.tensors, I.operators, I.partitions and I.evidence are ordinary Frames.\nBy default partitions with only one row set are skipped before materialization; m.indexed(include_singletons=True) retains them. This does not change tensor/operator discovery.\nI.tensor(id), I.operator(id), I.partition(id), I.evidence_item(id) return exactly one-row Frames; unknown IDs raise KeyError.\nUse O.one().row_count for metadata, O.rows for original rows, and O.related("kernel"), "bindings", "alignment", "ast", "statistics" or "dimensions" for complete data.\nUse T.vars, T.related("coordinates"), and T.related("axes").where(axis=0).rows. Coordinates retain the complete declared axis domains.\nFrame.shape always means table dimensions. Indexed dimensions live in related("dimensions") as component,axis,size.\nAST, origins and diagnostics use path,node_type,text,number,boolean node tables; empty containers and array order are preserved. Statistics retain exact numeric values.\nFiltering, sorting, slicing and key-preserving projection restrict related results. Keep indexed_key for navigation; axes additionally require axis. Members are deduplicated.\nI.verify(selection=None) checks prepared tensors/operators by default; an explicit directory selection can also verify partitions. Empty selection checks zero items. Discovery evidence is not a proposition.\nI.validate(spec) checks independently supplied hypotheses, including Frame relations. Neither directory identity nor saved status grants proof authority.\nm.verify() returns Report for storage integrity and prepared indexed runs. Inconsistency returns REFUTED; require() raises. Corrupt base data skips dependent checks; execution and input errors still raise.\nThese reports cannot be used as linear certificates or Edit justifications. Live views share stores; detached records remain usable after close.',related=('api.Model.indexed','api.IndexedResult.verify','api.IndexedResult.validate','api.Frame.related','api.Model.verify'))

# Proof-carrying v2 replaces the former SQL certificate/Edit execution contracts.
for item in TOPICS:
    item['bindings']={k:v.replace('mipframe.check.accessor:Checker','mipframe.check.compiler:Checker')
                       .replace('mipframe.check.batch:LinearBatch','mipframe.check.compiler:LinearBatch')
                       .replace('mipframe.edit.plan:Edit.','mipframe.edit.delta:DeltaEdit.')
                       .replace('mipframe.edit.plan:Edit','mipframe.edit.delta:DeltaEdit')
                       .replace('mipframe.edit.lineage:Lineage','mipframe.edit.compact_lineage:Lineage')
                       for k,v in item['bindings'].items()}
    if item['id'].startswith(('api.Checker.','api.LinearBatch.','api.Edit.')):
        item['resources']='Certificate execution uses exact sparse arrays and Python integers; Frame conversion, when requested, finishes before checking. Query budgets do not bound process RSS.'
    if item['id']=='concept.proofs':
        item.update(summary='Discover witnesses, check compact certificates, then commit a proof-carrying delta.',
                    body='''The checker executes explicit witnesses; it does not search for proofs. Existing row targets use integer IDs and CertificateBatch offsets/sources. Custom targets use Targets sparse arrays. Scalar Claims and optional Frames compile into the same exact kernel. A BatchProof stores statuses and dependencies without materializing target Reports. for_target returns a lazy ProofRef; select returns a compact selection. Edits accept these objects directly.
Proofs bind an immutable snapshot and domain. Unproved/conditional/foreign evidence cannot authorize commit. Serialized status is never trusted: replay re-executes the certificate. Public Frame assertion Reports are separate from mathematical proofs.''')
    if item['id']=='concept.report-resources':
        item.update(summary='BatchProof owns immutable certificate arrays; ProofRef is a lightweight view.',body='''Successful batch execution creates no target Report, evidence Frame or symbolic Claim. for_target and evidence access are explicit lazy presentation. Mathematical values use Python integers or exact Fraction only when needed. Closing a source Model does not release data still shared by a child overlay. Entity/Frame navigation still requires its owner to be open.''')
    if item['id']=='api.Checker.linear_many':
        item.update(summary='Check compact custom targets and ragged explicit witnesses.',inputs='targets=mf.Targets(...), witnesses=mf.CertificateBatch(...), residual="exact" or "bounds".',returns='BatchProof.',
                    guarantees='All targets must be proved for overall PROVED. Compact Targets use distinct nonnegative integer IDs; Frame adapters retain their external target keys for for_target(key).',
                    related=['api.Targets','api.CertificateBatch','api.BatchProof.for_target','api.Checker.linear_rows','api.Checker.linear_batch'],
                    body='''Targets(ids, kind="row", sides="ub") reads existing rows. kind="var" additionally needs rhs. kind="affine" needs rhs, offsets, variables and coefficients. IDs are distinct nonnegative integers. CertificateBatch(offsets,sources,multipliers=1,sides="ub",kinds="row") describes one source slice per target; kinds may be row or var. Finite equality sources use side="eq" and allow signed multipliers. Inequality multipliers must be nonnegative. Unit path witnesses can use CertificateBatch.paths(paths).
The positional certificate form accepts a snapshot-bound LinearCertificate from graph witnesses; do not mix it with separate targets/witnesses/terms/using.
For the Frame adapter, targets has target/relation/rhs with unique string keys, terms has target/var/coef, and using has target, exactly one row/var/proof source, side and multiplier. Row/var columns retain same-snapshot Ref types; proof keys refer to ProofRefs in reports. Frame direction must be main; use scalar linear for directional equalities. There is no assume argument here: use check.batch() for separate conditional branches and dependent tasks.
Frames compile once at the boundary; prefer linear_rows for existing row targets. No SQL runs in the checker.''')
    if item['id']=='api.Checker.linear_batch':
        item.update(summary='Compile scalar Claim inputs into one compact batch.',inputs='residual="exact"/"bounds", domain="auto"/"integer"/"relaxation", memory_mb/disk_mb. Conditions are tracked per target.',returns='LinearBatch; run returns BatchProof.',body='Compatibility adapter for linear-only inputs. Prefer check.batch() for new work, including different branch conditions and dependent checks; do not execute scalar checkers in a Python loop.')
    if item['id']=='api.LinearBatch.add':
        item.update(inputs='Unique key, Claim target, explicit using sources and optional per-target assumptions.',guarantees='Copies canonical sparse target data. No per-target Report is constructed.',body='Directional equality is available in the scalar linear API. Use check.batch() to compose conditional branches and cases in one plan.')
    if item['id']=='api.LinearBatch.run':
        item.update(returns='BatchProof; empty batches are PROVED.',guarantees='All successes are stored as status bytes; for_target creates a lazy ProofRef.',body='Results survive builder context exit. Conditions remain attached and cannot authorize edits until explicitly discharged.')
    if item['id']=='api.Edit.build':
        item.update(summary='Build a cheap overlay, including experimental unproved changes.',inputs='prepare="logical" by default; "indexed" explicitly computes structural analyses.',returns='Model overlay.',body='Missing or ungrounded evidence yields UNKNOWN semantic preservation. Build never silently performs proof search. Use commit() when equivalence is required. Parent closure does not invalidate a child snapshot.')
    if item['id']=='api.Edit.summary':
        item.update(summary='Read edit counts and certificate-derived semantics without global analysis.',returns='dict with counts and semantic relationships.',body='Does not flatten the matrix, run semantic SQL, materialize Reports or replay certificates. Complete model statistics are explicit requests.')
    if item['id'].startswith('api.Edit.'):
        item['failures']='Malformed or foreign evidence and structural conflicts raise. build permits missing evidence and marks preservation UNKNOWN; commit rejects uncovered or ungrounded operations.'
    if item['id']=='api.Model.diff':
        item.update(summary='Read an ancestor delta directly, or explicitly compare unrelated numerical models.',related=['api.ModelDiff','api.NumericDiff','concept.identity'],body='Direct-parent and multi-generation diff use retained deltas. Unrelated snapshots are compared numerically with mathematical equivalence UNKNOWN. No certificate replay occurs; the compatibility verify argument does not trigger an audit. Use Model.audit for explicit replay.')
    if item['id']=='limitation.elimination':
        item.update(summary='Fixed-variable elimination carries an explicit lifting map.',related=['api.Edit.substitute_fixed','api.Model.lift','api.Edit.drop_vars','api.LinExpr.substitute'],body='Use substitute_fixed after proving finite equal bounds. Nonzero fixed values shift row bounds and the objective constant exactly. General variable substitution and arbitrary dimension-changing reformulations require additional mapping rules.')
    if item['id']=='concept.identity':
        item['body']='Snapshot uid/revision identify immutable facts; content_hash computes an exact numerical hash only on explicit access. Entity IDs remain stable across overlays; iloc is a position and loc is identity. Native v7 restores numerical arrays, deltas and historical certificate data. Native v3/v4/v5/v6 are rejected; reimport the original LP/MPS. Reloaded historical verdicts are UNKNOWN until an explicit audit; they cannot authorize a new edit.'

api('CertificateBatch','mipframe.check.kernel:CertificateBatch','Immutable ragged source arrays for exact certificate execution.',
    inputs='offsets has target_count+1 entries, starts at 0, is nondecreasing and ends at len(sources). sources contains nonnegative entity IDs. multipliers, sides and kinds are scalars or source-aligned arrays.',
    returns='Compact certificate input; not a proof.', constructor=True,
    body='Public original-fact sources use kinds="row"/"var" and sides="lb"/"ub"/"eq". Inequality multipliers must be nonnegative; equality multipliers may be signed. Slice offsets[i]:offsets[i+1] supplies target i. Use scalar/Frame adapters for proof or assumption sources; their indices require adapter-managed bindings.',
    example='witnesses = mf.CertificateBatch(offsets=[0, 2], sources=[a.id, b.id])',
    related=('api.CertificateBatch.paths','api.Checker.linear_many',))
api('CertificateBatch.paths','mipframe.check.kernel:CertificateBatch.paths','Compile source row-ID paths with unit multipliers and upper sides.',
    inputs='One iterable of source row IDs per target, in target order. Empty paths are allowed.',
    returns='CertificateBatch with multipliers=1, sides="ub", kinds="row".',
    non_guarantees='A path is an unchecked witness. The linear checker must verify the original coefficients and bounds.',
    example='witnesses = mf.CertificateBatch.paths([[a.id, b.id]])',
    related=('api.Checker.linear_rows','api.CertificateBatch'))
api('Targets','mipframe.check.kernel:Targets','Existing entity targets or custom CSR affine targets.',
    inputs='Distinct nonnegative integer ids in target order. kind="row" uses existing rows and their selected sides. kind="var" uses variable IDs and explicit rhs. kind="affine" uses target labels plus rhs and CSR arrays.',
    constructor=True, returns='Compact target data; constructing it does not prove the claims.',
    body='sides is lb/ub/eq, scalar or target-aligned. rhs is exact and scalar or target-aligned. For affine targets, offsets starts at 0, has len(ids)+1 entries and ends at len(variables)==len(coefficients); each slice contains its variable IDs and exact coefficients. kind="integrality" is used by specialized integrality proofs, not the linear certificate API.',
    example='targets = mf.Targets([r.id], kind="row", sides="ub")',
    related=('api.Checker.linear_many','api.Checker.linear_rows'))
api('BatchProof','mipframe.check.compact:BatchProof','Immutable checked batch with lazy target presentation.',related=('concept.proofs',))
api('ProofRef','mipframe.check.compact:ProofRef','A checked result position in a BatchProof.',
    body='Scalar mathematical checkers and BatchProof.for_target return this view. Inspect status, conclusion, scope, dependencies and evidence; there is no explain() or instance replay() method. Replay its to_spec()/write() output with mf.Report.replay(spec_or_path, models=[m]); use BatchProof.replay only for whole-batch artifacts.',
    related=('concept.proofs','api.Report.replay','api.BatchProof'))
api('ProofSelection','mipframe.check.compact:ProofSelection','Immutable selected positions in a BatchProof.',related=('concept.proofs',))
for name, summary, returns in [
    ('target_ids','Read compact integer target IDs in batch order.','Immutable integer array; scalar/Frame adapters use positional internal IDs. Use the supplied external keys with for_target/select.'),
    ('ok','Test whether every target is PROVED.','bool; an empty batch is successful.'),
    ('status','Read the aggregate proof status.','PROVED if all pass; otherwise REFUTED if any refutation exists, or INCONCLUSIVE.'),
    ('summary','Read batch operation, target count and failure count.','dict; enumeration results add scope-specific statistics.'),
    ('scope','Read the snapshot, exact arithmetic, domain and assumption count.','dict; assumptions remain conditional and cannot authorize an edit.'),
    ('require','Require every target to be proved.','The same BatchProof, or ValidationError carrying this batch.'),
    ('for_target','Select one conclusion by its target key.','Lazy ProofRef; integer ID for compact inputs, supplied external key for scalar/Frame adapters.'),
    ('select','Select several conclusions by their target keys.','ProofSelection usable directly as Edit because evidence; key rules match for_target.'),
    ('round_many','Round proved variable bounds using model integrality.','A new integer-domain BatchProof; source assumptions and dependencies are retained.'),
    ('to_spec','Materialize an exact certificate graph for replay.','Detached dictionary within materialize_mb; use write for large certificates.'),
    ('write','Export an exact certificate graph to an artifact directory.','Saved path; serialized status does not grant live proof authority.'),
    ('replay','Bind a saved batch certificate to a model and rerun its checks.','Fresh BatchProof. For ProofRef or PointReport artifacts use Report.replay(spec_or_path, models=[m]); never trusts stored status.'),
]:
    api('BatchProof.'+name,'mipframe.check.compact:BatchProof.'+name,summary,returns=returns,
        related=('concept.proofs','api.ProofRef'))
next(t for t in TOPICS if t['id']=='api.BatchProof.round_many').update(
    inputs='A batch with kind="var" targets. vars optionally selects variable IDs; exactly one of ub or lb supplies requested exact bounds, scalar or aligned with vars.',
    guarantees='Requires model-integer variables and matching source/target sides. Floors source upper bounds or ceils lower bounds. A requested bound is PROVED only if the source was PROVED and its rounded bound implies the request.',
    non_guarantees='No model mutation. Integer-only evidence does not establish a relaxation bound.',
    failures='Affine/row targets, continuous variables, mismatched directions or specifying both/neither side raise. An unsupported stronger requested bound is INCONCLUSIVE.',
    example='# Requires an integer variable x with a finite model upper bound.\nbounds = m.check.bound(vars=[x.id], values=[x.ub], side="ub").require()\nrounded = bounds.round_many(ub=x.ub // 1).require()')
for name in ('ok','status','scope','conclusion','require','method','summary','dependencies','evidence','counterexample','to_spec','write'):
    api('ProofRef.'+name,'mipframe.check.compact:ProofRef.'+name,'Read a selected checked result: '+name+'.',related=('concept.proofs',))
api('Checker.linear_rows','mipframe.check.compiler:Checker.linear_rows','Check existing row IDs directly from ragged source witnesses.',
    inputs='targets=row IDs, witnesses=CertificateBatch or paths, side="ub"/"lb"/"eq", residual="exact"/"bounds".',returns='BatchProof.',
    example='proof = m.check.linear_rows(targets=dropped_ids, witnesses=retained_paths).require()\nfinal = m.edit().drop_rows(dropped_ids, because=proof).commit()',related=('api.CertificateBatch','api.Edit.commit'))
api('Checker.bound','mipframe.check.compiler:Checker.bound','Check direct variable-bound implications in a batch.',inputs='vars=integer IDs, values=exact bounds, side="ub" or "lb".',returns='BatchProof.',related=('api.BatchProof.round_many',))
api('Checker.affine_integrality','mipframe.check.compiler:Checker.affine_integrality','Check integer affine definitions independently of the target integer declaration.',
    inputs='vars=defined variable IDs, rows=matching equality definition IDs.',returns='BatchProof usable for set_domains(...,kind="C",because=proof).',
    guarantees='After normalization, constant and coefficients must be integers; all other variables must be integer. Dependencies survive into commit.',related=('api.Edit.set_domains',))
api('Checker.one_hot_affine','mipframe.check.compiler:Checker.one_hot_affine','Validate shared one-hot affine structure once, then check bound exclusions.',
    inputs='groups=[(onehot_row_id, [(definition_row_id, defined_var_id), ...]), ...].',
    returns='Shared checker with bound_exclusions() -> BatchProof and expand_exclusions() -> reference expansion.',
    guarantees='Checks complete disjoint one-hot groups, binary domains, normalized affine definitions and coverage. Does not assume business-specific names.',
    example='shared = m.check.one_hot_affine(groups=groups)\nzero = shared.bound_exclusions().require()\ntightened = m.edit().set_var_bounds(zero.target_ids, ub=0, because=zero).commit()',related=('api.Edit.substitute_fixed',))
api('Edit.commit','mipframe.edit.delta:DeltaEdit.commit','Require grounded MIP equivalence and objective-value preservation before building an overlay.',
    guarantees='Checks target coverage and dependency closure rooted in final retained facts. Cyclic row deletion and cyclic integrality relaxation fail.',related=('api.Edit.build','api.Edit.summary'))
api('Edit.substitute_fixed','mipframe.edit.delta:DeltaEdit.substitute_fixed','Substitute finite fixed values and retain an exact lifting map.',
    inputs='vars=IDs or a variable selection; equal finite bounds, possibly set by a proved preceding edit.',
    guarantees='Shifts all retained row bounds and objective constant; removes columns. A child diff exposes lifting.',related=('api.Edit.commit',))
api('Model.audit','mipframe.model:Model.audit','Explicitly replay a persisted proof-carrying delta and compare numerical output.',
    inputs='source=bound root Model; optional bundle path and artifact MPS path.',returns='Fresh audit receipt; historical status fields are ignored.',
    body='statistics separates serialization, replay, edit validation, numerical comparison and file readback seconds. Each audit independently rechecks certificates and committed coverage; timing data grants no proof authority. Large proof lineages may require an explicit finite check.budget(memory_mb=...) context.',related=('api.Model.write',))
api('Model.content_hash','mipframe.model:Model.content_hash','Explicitly compute a numerical fingerprint; snapshot construction does not compute it.',related=('concept.identity',))

# Exception classes retain their diagnostic import paths.
for item in TOPICS:
    item['bindings']={k:v.replace('mipframe.edit.delta:DeltaEditConflictError','mipframe.edit.plan:EditConflictError').replace('mipframe.edit.delta:DeltaEditValidationError','mipframe.edit.plan:EditValidationError') for k,v in item['bindings'].items()}

for item in TOPICS:
    if item['id']=='concept.report-resources':item['keywords'].extend('release retained report evidence storage'.split())


api('LinearCertificate','mipframe.ops.reduction:LinearCertificate','Snapshot-bound typed input to the exact linear checker; never a proof.',related=('api.Checker.linear_many',))
api('TransitiveReductionResult','mipframe.ops.reduction:TransitiveReductionResult','Complete retained and removed edge views, ragged retained-edge witnesses and execution statistics.',resources=OPS,related=('api.ops.graph.transitive_reduction',))
api('PatchPlan','mipframe.store.patch:PatchPlan','Immutable MPS serialization plan with support reasons and physical record actions.',related=('api.Model.write',))
api('PatchNotSupportedError','mipframe.store.patch:PatchNotSupportedError','Strict patch output cannot represent this edit or source format.',related=('api.Model.write',))
api('SubstitutionInfeasibleError','mipframe.edit.substitution:SubstitutionInfeasibleError','An affected empty row excludes activity zero.',related=('api.Edit.substitute_fixed',))
for owner,binding in [('Model','mipframe.model:Model'),('Edit','mipframe.edit.delta:DeltaEdit')]:
    api(owner+'.patch_plan',binding+'.patch_plan','Plan source patch support without sealing an edit or replaying proofs.',returns='PatchPlan: STREAM_PATCH or CANONICAL_REQUIRED.',resources=BUDGET,related=('api.Model.write',))
    api(owner+'.lift',binding+'.lift','Restore fixed variables to the direct parent or a requested ancestor.',inputs='Complete snapshot-qualified exact solution values.',returns='Frame(var,value). Does not check feasibility.',related=('api.Edit.substitute_fixed',))
api('Edit.write','mipframe.edit.delta:DeltaEdit.write','Validate the final edit, write and audit atomically, then seal on success.',inputs='Fresh path; strategy auto/patch/canonical; audit readback or None.',returns='Write receipt.',related=('api.Edit.commit','api.Model.write'))
for item in TOPICS:
    if item['id']=='api.Model.write':
        item.update(summary='Atomically serialize an exact snapshot to MPS or LP.',inputs='Fresh path, format inferred from suffix, strategy auto/patch/canonical, audit readback or None.',returns='Receipt with actual writer, digest and phase timings.',body='Patch supports row deletion, fixed substitution, row/variable bounds and integrality relaxation when an indexed MPS source is retained. Strict patch rejects unsupported edits. Auto falls back to canonical. Readback verifies numerical output and grants no proof authority. Both writers reject nonterminating decimal rationals. Existing output files are never overwritten.')
    if item['id']=='api.Edit.substitute_fixed':
        item.update(inputs='vars=None freezes source variables with equal finite effective bounds; explicit selections are validated atomically. because supplements bound lineage. drop_trivial_rows defaults True.',body='Compiles selected CSC incidence once. Zero values do not decode coefficients. Affected empty rows are checked exactly; contradictions reject build/commit/write unless build(allow_infeasible=True). Coordinates are PROJECTED. Proved MIP fixedness grants EQUIVALENT_UNDER_EXTENSION, while LP scope follows its evidence. Objective representation gains constant_delta, with value offset zero after lifting. Edits remain source-relative; new rows cannot mention substituted variables.')
    if item['id']=='api.ops.graph.transitive_reduction':
        item.update(inputs='DAG edge Frame; witnesses=False retains Frame return. witnesses=True adds edge_id, memory_mb, disk_mb, max_output_rows and max_witness_edges controls.',returns='Frame or TransitiveReductionResult(kept, removed, witnesses, stats).',body='Parallel retained endpoint pairs preserve every original record. Every removed record receives a complete path using only retained representatives. Components choose packed bitsets or bounded traversal; witness fallback shares BFS by endpoint. Total witness output is bounded separately. witnesses.as_linear_certificate(target="row",source="row",side="ub") gathers typed row IDs and shares offsets; m.check.linear_many(cert) verifies actual mathematics.',resources=OPS+' Complete path output requires work proportional to its total length.')

api('WriteReceipt','mipframe.store.output:WriteReceipt','Immutable numerical artifact readback receipt with writer, digest and phase timings.',related=('api.Model.write',))

api('PointReport','mipframe.check.finite:PointReport','Exact point-feasibility result returned by m.check.solution().',
    returns='A result handle; obtain it through Checker.solution, not its constructor.',
    body='status is PROVED or REFUTED; ok is a bool. summary includes the exact objective and violations; scope.domain records integer or relaxation feasibility. require() raises ValidationError on failure. evidence contains violating_rows and violating_vars Frames. This type has no implication conclusion or explain() method and cannot authorize Edit because. close() releases its point and diagnostic storage; finish consuming evidence before closing.',
    related=('api.Checker.solution','api.PointReport.evidence','api.PointReport.close'))
for name, summary, returns in [
    ('require','Require exact point feasibility in the selected domain.','The same PointReport, or ValidationError carrying it.'),
    ('evidence','Inspect every violated row side, variable bound and integrality condition.','Mapping with violating_rows and violating_vars Frames; requires an open model and point result.'),
    ('to_spec','Materialize the exact point for later validation.','Detached dictionary within materialize_mb; no saved verdict is trusted.'),
    ('write','Write the exact point as a portable artifact.','Saved path; replay with Report.replay(path, models=[m]) reruns the point checker.'),
    ('close','Release retained point and violation storage.','None; consume or detach needed evidence first.'),
]:
    api('PointReport.'+name,'mipframe.check.finite:PointReport.'+name,summary,returns=returns,
        related=('api.PointReport','api.Checker.solution'))
api('NumericDiff','mipframe.edit.numeric_diff:NumericDiff','Numerical comparison of unrelated model snapshots returned by Model.diff().',
    returns='A comparison handle; obtain it from target.diff(source).',
    body='Matches rows and variables by name for numerical comparison. rows_added/rows_changed and vars_added/vars_changed contain target IDs; rows_dropped and vars_dropped contain source IDs. numerically_equal also checks objective sense and constant. semantic stays UNKNOWN even when numerical values match. This is distinct from the ancestor ModelDiff and has no nnz_before/nnz_after or degree-statistics interface.',
    non_guarantees='Names are a comparison alignment, not cross-snapshot Ref identity or a mathematical equivalence proof.',
    related=('api.Model.diff','api.ModelDiff'))
api('OneHotAffine','mipframe.check.compact:OneHotAffine','Validated shared one-hot affine structure returned by Checker.one_hot_affine().',
    returns='Shared checker; obtain it through m.check.one_hot_affine(groups=...).',
    related=('api.Checker.one_hot_affine','api.OneHotAffine.bound_exclusions','api.OneHotAffine.expand_exclusions'))
api('OneHotAffine.bound_exclusions','mipframe.check.compact:OneHotAffine.bound_exclusions','Prove that options violating a defined variable bound are zero.',
    returns='Integer-domain BatchProof of option upper bounds equal to zero; may be empty.',
    related=('api.Checker.one_hot_affine','api.Edit.set_var_bounds'))
api('OneHotAffine.expand_exclusions','mipframe.check.compact:OneHotAffine.expand_exclusions','Expand exclusions into explicit linear certificates and rounding.',
    returns='Integer-domain BatchProof; an explicit reference calculation that can be more expensive than bound_exclusions.',
    failures='A degenerate inconsistent affine range requires a separate contradiction certificate.',
    related=('api.OneHotAffine.bound_exclusions','api.BatchProof.round_many'))

# api() derives "when" from the summary. Keep it in sync after the contract
# refinements above so brief help never exposes an obsolete capability claim.
for item in TOPICS:
    if item['kind'] == 'api':
        item['when'] = item['summary']

# Unified execution is the default route for repeated checks.
BATCH_GUIDANCE = """Prefer m.check.batch() for repeated checks. Do not execute scalar checkers in a Python for loop, comprehension or map, or rewrite complete checks as handwritten row/variable loops. Use Frame operations for relations. Loops may register batch tasks; run once after registration. Construct expressions inside the batch context to share common subexpressions. m.linexpr(Frame) registers a lazy query leaf there; reachable leaves are read at run(), sharing parent relations and exact equality-filter indexes. Do not call to_records(), shape or other materializing operations inside a registration loop. Full parent join coverage/cardinality is validated before filtering. Unused query tasks are not executed; query work has the same finite time/work budget. Statistics distinguish registration, query, compilation, kernel and publication time; query time is included in compilation.
Each registration returns CheckHandle, not a signed proof: no .ok/.status and no edit authority. A handle may be used only as a dependency in its own plan. result_key is unique and exports a result; unnamed tasks are internal. run() executes the named dependency closure and seals the plan. Context exit releases unpublished work and never runs implicitly.
results.require() checks all exported results. results[key] preserves its type: ProofRef/BatchProof for mathematics, PointReport for points, Report for diagnostics. Points and diagnostics cannot authorize edits. Conditions remain target-local; cases discharges only its matching branch conditions. Separate snapshots require separate batches.
Finite defaults: time_limit=30 seconds, max_tasks=1000000 logical tasks, max_nodes=4096 compiled execution nodes, max_work=100000000, memory_mb=128. Explicit finite increases are supported; time/work limits cannot be disabled. ProofBudgetExceeded reports the phase and limit; unfinished work is never PROVED or mathematically REFUTED."""
api('Checker.batch','mipframe.check.compiler:Checker.batch',
    'Register related checks once and execute their shared dependency graph.',
    aliases=('m.check.batch','batch checks','prefer batch','avoid scalar loops'),
    returns='CheckBatch; run() returns named CheckResults.',
    body=BATCH_GUIDANCE, related=('concept.proofs','api.Checker.budget'),
    example="with m.check.batch() as batch:\n    for key, target, sources in tasks:\n        batch.linear(target, using=sources, result_key=key)\n    results = batch.run()\nresults.require()")
api('Checker.budget','mipframe.check.compiler:Checker.budget',
    'Share explicit finite limits across nested check operations.',
    returns='Budget context manager.', body='Use explicit finite overrides when needed; nested operations share the same allowance. Limits cannot be disabled.',
    related=('api.Checker.batch',))
for item in TOPICS:
    if item['id'] in ('home','concept.proofs','concept.report-resources'):
        item['body'] = BATCH_GUIDANCE + '\n\n' + item.get('body','')
        item['related'] = list(dict.fromkeys([*item.get('related',()), 'api.Checker.batch']))

for name in ('linear','identity','bound','infeasible','cases','round','enumerate','affine_integrality','solution','frame','model_verify'):
    api('CheckBatch.'+name, 'mipframe.check.graph:CheckBatch.'+name,
        'Register a '+name+' task without executing it.',
        returns='CheckHandle; result_key optionally exports its completed result.',
        body='Use the matching checker arguments shown in this signature. Pending handles may be proof dependencies only inside this plan. Call run() once after registration.',
        related=('api.Checker.batch',))
for name,summary in (('run','Execute exported dependency closures and seal the plan.'),('close','Release unpublished plan resources.')):
    api('CheckBatch.'+name,'mipframe.check.graph:CheckBatch.'+name,summary,
        returns='CheckResults for run; None for close.',related=('api.Checker.batch',))
api('CheckHandle','mipframe.check.graph:CheckHandle','Pending task identity, never a checked proof.',
    body='No ok/status, boolean truth or edit authority. Use only within its own batch as a dependency.',related=('api.Checker.batch',))
api('CheckResults.require','mipframe.check.graph:CheckResults.require','Require every named exported result to succeed.',
    returns='This CheckResults mapping; raises on any failed or incomplete result.',related=('api.Checker.batch',))

api('CheckBatch','mipframe.check.graph:CheckBatch','Deferred checks owned by one model snapshot.',related=('api.Checker.batch',))
api('CheckResults','mipframe.check.graph:CheckResults','Named completed results with distinct mathematical and diagnostic permissions.',related=('api.CheckResults.require','api.Checker.batch'))
api('ProofBudgetExceeded','mipframe.check.budget:ProofBudgetExceeded','A finite check budget stopped unpublished work.',
    body='Inspect phase, resource, consumed and limit. This is not a mathematical counterexample; use a finite larger budget or share related work in one batch.',related=('api.Checker.batch',))

for item in TOPICS:
    if item['id'].startswith('api.Checker.') and item['id'] not in ('api.Checker.batch','api.Checker.budget'):
        item['when'] = 'For repeated checks, prefer m.check.batch(); do not execute scalar checkers in a Python loop. ' + item['summary']
        item['related'] = list(dict.fromkeys([*item.get('related',()), 'api.Checker.batch']))
    if item['id']=='home':
        item['summary'] = 'Use Frame operations for structural work and m.check.batch() for repeated checks; avoid scalar checker loops. ' + item['summary']
