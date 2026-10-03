"""Executable examples; inputs are set up by independent development fixtures."""


def add_recipes(topic):
    def recipe(ident,title,when,inputs,pattern,code,establishes,limits,related):
        topic('recipe.'+ident,title,when,kind='recipe',aliases=(ident,),
              keywords=(title,pattern),when=when,inputs=inputs,example=code,
              guarantees=establishes,non_guarantees=limits,related=related,snippet=ident,
              body='\n\n'.join(['## When to use\n'+when+'\nInputs: '+inputs,
                    '## Primitive pattern\n'+pattern, '## Minimal code\n```python\n'+code+'\n```',
                    '## What it establishes\n'+establishes,'## What it does not establish\n'+limits]))

    recipe('roles','Inspect structural roles and raw coefficients',
      'A signature suggests repeated rows, but coefficients may vary.',
      'm is open; R is an explicitly selected RowFrame.',
      'IR / signature → selected Terms → exact grouped coefficient statistics.',
      '''ir = m.ir
T = m.terms(rows=R)
G = T.group_by("row").agg(
    distinct=mf.col("coef").nunique(),
    amin=mf.col("coef").min(), amax=mf.col("coef").max(),
)
G.head()''',
      'Actual observed coefficient statistics of the selected rows.',
      'A family label is not an original formula, and observations do not prove a semantic role.',
      ('api.Structure.signatures','api.StructuralConstraintFamily.inspect'))
    recipe('coordinates','Recover Cartesian coordinates',
      'Every variable should touch exactly one row in each proposed coordinate domain.',
      'V is a VarFrame; A and B are same-snapshot RowFrames with declared full axis domains.',
      'Strict project → product check → coefficient join → group-by.',
      '''P = V.project(i=A, j=B)
P.check("product", ("i", "j")).require()
W = m.terms(rows=A, vars=V).select("var", a=mf.col("coef"))
Q = P.join(W, on="var", cardinality="1:1", require="both")
stats = Q.group_by("i").agg(distinct=mf.col("a").nunique())''',
      'Unique and complete coordinates in the declared product, plus exact selected coefficients.',
      'Coordinates do not imply equal coefficients, separability, or an optimization reformulation.',
      ('api.VarFrame.project','api.Frame.check','api.Frame.join'))
    recipe('decomposition','Find blocks after removing coupling rows',
      'A small, explicitly chosen set of rows may couple otherwise separate blocks.',
      'm is open; coupling is a same-snapshot RowFrame. Its selection is a hypothesis, not inferred here.',
      'Row set difference → hypergraph components → crossing-row verification.',
      '''local = m.rows - coupling
S = m.terms(rows=local).select(set=mf.col("row"), member=mf.col("var"))
C = mf.ops.sets.components(S, vertices=m.vars)
joined = m.terms().join(C, on={"var": "member"}, cardinality="m:1")
crossing = joined.group_by("row").agg(n=mf.col("component").nunique())
crossing = crossing.where(mf.col("n") > 1)''',
      'Components in the selected hypergraph, including isolates, and the rows crossing them.',
      'The full model need not decompose: excluded constraints and objective still matter.',
      ('api.ops.sets.components','api.Frame.join'))
    recipe('templates','Compare supports and exact templates',
      'Explicit local coordinates make repeated blocks comparable.',
      'F has scalar group, slot and exact coef fields; local slot correspondence is already established.',
      'Explicit local payload → grouped multiset equality.',
      '''classes = mf.ops.equal_groups(F, by="group", rows=("slot", "coef"))
classes.head()''',
      'Equal complete payload multisets under the supplied coordinates, including multiplicities.',
      'No full-model automorphism: bounds, domains, objective and external couplings require separate checks.',
      ('api.ops.equal_groups','limitation.symmetry'))
    recipe('support','Compare supports, containment and differences',
      'Two unit-coefficient equality supports may imply a zero difference.',
      'R is a RowFrame; a and b are selected unit equalities with equal RHS and support(a) strictly inside support(b).',
      'Set containment → difference members → equality subtraction → nonnegative bounds.',
      '''S = R.terms.select(set=mf.col("row"), member=mf.col("var"))
P = mf.ops.sets.pairs(S, sets=R, relation="strict_subset")
D = mf.ops.sets.members(S, pairs=P, op="right_only")
zero = m.check.linear((b.expr - a.expr).eq(0),
                      using=[b.eq_claim, (-1, a.eq_claim)])
zero.require()''',
      'Exact support differences and a checked equality difference for the explicitly selected rows.',
      'Containment alone does not fix variables. Unit coefficients, equal bounds and nonnegativity must be verified.',
      ('api.ops.sets.pairs','api.ops.sets.members','api.Checker.linear'))
    recipe('network','Recover directed incidence from signed terms',
      'Each selected variable should have exactly one +1 and one -1 in the chosen row submatrix.',
      'R and V declare the full row and variable selections.',
      'Sign filtering → strict endpoint join → complete variable coverage → graph operations.',
      '''T = m.terms(rows=R, vars=V)
assert T.where((mf.col("coef") != 1) & (mf.col("coef") != -1)).count() == 0
tail = T.where(coef=1).select("var", u=mf.col("row"))
head = T.where(coef=-1).select("var", v=mf.col("row"))
E = tail.join(head, on="var", cardinality="1:1", require="both")
assert len(E) == len(V)
C = mf.ops.graph.components(E, vertices=R)
order = mf.ops.graph.topological_order(E, tail="u", head="v", vertices=R)''',
      'Complete signed incidence and DAG/component facts for the chosen submatrix.',
      'This alone does not prove a unit-flow network, integrality, or independence from other rows.',
      ('api.ops.graph.components','api.ops.graph.topological_order'))
    recipe('conflicts','Build conflict relations with original witnesses',
      'A selected set of source rows has independently established pairwise mutual exclusion.',
      'R are verified mutual-exclusion source rows; V is their variable domain. Binary domains and source row semantics are premises.',
      'Transpose source membership → overlap pairs → witness expansion → clique candidates.',
      '''S = m.terms(rows=R, vars=V).select(set=mf.col("var"), member=mf.col("row"))
P = mf.ops.sets.pairs(S, sets=V, relation="overlap")
W = mf.ops.sets.members(S, pairs=P, op="intersection")
E = P.select(u=mf.col("left"), v=mf.col("right"))
C = mf.alg.graph.clique_candidates(E, vertices=V, top=100)''',
      'Each edge has shared original sources; C contains graph-theoretic candidates.',
      'Neither C nor W is a linear Report. Never pass the graph result as Edit.because.',
      ('concept.search','concept.proofs','recipe.odd-cycle'))
    recipe('odd-cycle','Verify an odd-cycle inequality by exact rounding',
      'Verified pair bounds around an odd cycle can be added and rounded.',
      'V is exactly the 2k+1 binary cycle vertices; edges is a small list of PROVED unconditional Reports xi+xj<=1, one per cycle edge.',
      'Half-sum edge certificates → exact linear bound → integer rounding.',
      '''n = len(V)
assert n >= 3 and n % 2 == 1
s = m.linexpr(vars=V, coef=1)
upper = m.check.linear(s <= mf.q(n) / 2,
                       using=[(mf.q("1/2"), edge) for edge in edges])
upper.require()
cut = m.check.round(s <= n // 2, from_=upper)
cut.require()''',
      'An integer-valid cycle inequality with explicit linear and domain dependencies.',
      'It is not LP-valid. Candidate cycle search does not supply edge proofs by itself.',
      ('api.Checker.round','api.alg.graph.odd_cycle_candidates'))
    recipe('closure','Aggregate resource use of closure unions',
      'Directed implications and nonnegative resource data motivate a pair compatibility test.',
      'E(u,v), pairs(left,right), values(member,resource,coef), capacities(resource,capacity) are exact Frames; capacity keys are unique.',
      'Reachability including self → set membership → union aggregation → exact comparison.',
      '''closure = mf.ops.graph.reachability(E, include_self=True)
C = closure.select(set=mf.col("source"), member=mf.col("target"))
usage = mf.ops.sets.pair_aggregate(
    C, pairs, values, by="resource", value="coef", op="union")
conflicts = usage.join(capacities, on="resource", cardinality="m:1", require="left")
conflicts = conflicts.where(mf.col("value") > mf.col("capacity"))''',
      'Exact union resource totals with shared members counted once.',
      'A graph implication must be proved from original rows; resource excess alone does not produce a reusable MIP cut certificate.',
      ('api.ops.graph.reachability','api.ops.sets.pair_aggregate'))
    recipe('transitive','Remove transitive rows using retained-path certificates',
      'A DAG reduction suggests redundant precedence rows.',
      'E(u,v,row) retains original row identity. targets/terms/using are explicit linear_many Frames built from paths in the retained graph; dropped is the matching RowFrame.',
      'Graph reduction → retained path → exact batched certificates → grounded Edit deletion.',
      '''reduced = mf.ops.graph.transitive_reduction(E)
B = m.check.linear_many(targets=targets, terms=terms, using=using)
B.require()
edit = m.edit()
edit.drop_rows(dropped, because=B)
m2 = edit.build()
assert m2.diff(m).semantic["lp"] == "SAME"''',
      'The supplied row deletions preserve LP/MIP feasible sets when proof closure succeeds.',
      'Reduction alone is not deletion authorization. Paths must use retained facts; parallel source records are not interchangeable identities.',
      ('api.Checker.linear_many','api.Edit.drop_rows','api.ops.graph.path'))
    recipe('linear','Combine rows and compensate exact residuals',
      'A supplied source combination differs from a target only by bound-controlled coefficients.',
      'x,y are variables; r is a source row x+y<=b with y>=0; target_rhs is a candidate bound on x.',
      'Explicit source side → linear residual → sign-selected original variable bounds.',
      '''target = x <= target_rhs
proof = m.check.linear(target, using=[r.ub_claim], residual="bounds")
proof.require()
proof.evidence
with m.check.batch("linear", residual="bounds") as batch:
    batch.add(key="bound", target=target, using=[r.ub_claim])
    batch.add(key="conditional", target=x <= 0, assume=[x.eq(0)])
    proofs = batch.run()
proofs.require()
assert proofs.for_target("conditional").scope["assumptions"] == 1''',
      'The exact supplied certificate implies the target within its recorded domain.',
      'No search for multipliers; failure is INCONCLUSIVE, not proof that the target is false.',
      ('api.Checker.linear','api.Checker.batch','concept.proofs','concept.report-resources'))
    recipe('conditions','Check a conditional contradiction',
      'A binary switch value may conflict with original bounds and rows.',
      'x is continuous with ub=3; z is binary; r is the original row x-4z>=0.',
      'Assume one value → substitute → exact contradiction certificate.',
      '''conditional = m.check.infeasible(
    using=[r.lb_claim, x.ub_claim], assume=[z.eq(1)],
)
conditional.require()
assert conditional.scope["assumptions"] > 0''',
      'No point satisfies the supplied original facts together with z=1.',
      'Assumption discharge is not automatic. This Report cannot directly justify edit.fix(z,0).',
      ('concept.conditions','limitation.disjunction'))
    recipe('cases','Discharge binary cases and replace a big-M row',
      'Continuous activity and a binary switch need different certificates in each branch.',
      'x is continuous and nonnegative, y is binary; cap is x<=6; old is x+1197y<=1200.',
      'Prove each branch → exhaust the integer domain → add and build → prove and delete the old row.',
      '''target = x + 3*y <= 6
p0 = m.check.linear(target, using=[cap.ub_claim], assume=[y.eq(0)], residual="bounds")
p1 = m.check.linear(target, using=[old.ub_claim], assume=[y.eq(1)], residual="bounds")
proof = m.check.cases(target, var=y, cases={0:p0, 1:p1})
proof.require()
assert proof.scope["assumptions"] == 0
with m.edit() as edit:
    edit.add_row(target, name="tight", because=proof)
    middle = edit.build()
try:
    assert middle.diff(m).semantic["mip"] == "SAME"
    old2 = middle.rows.by_name("old")
    y2 = middle.vars.by_name("y")
    reverse = middle.check.linear(old2.ub_claim,
        using=[middle.rows.by_name("tight").ub_claim, (1194, y2.ub_claim)])
    with middle.edit() as edit:
        edit.drop_rows(old2, because=reverse)
        m2 = edit.build()
    assert m2.diff(middle).semantic["lp"] == "SAME"
    assert m2.diff(middle).semantic["mip"] == "SAME"
finally:
    middle.close()''',
      'Each direct-parent Diff certifies MIP SAME and optimal_value PRESERVED. Addition is LP SUBSET; deletion is LP SAME.',
      'SUBSET does not establish strictness. No cross-generation Diff, optimal value computation, continuous disjunction or infeasible-branch elimination.',
      ('api.Checker.cases','concept.conditions','concept.big-m','recipe.edits'))
    recipe('enumeration','Search and verify a finite local integer claim',
      'A small explicitly bounded pure-integer subsystem is suitable for exhaustive verification.',
      'V is the integer selection; R is the local row selection; target contains only V; all relevant premises are explicit.',
      'Explicit integer system → assignments; local target → check.enumerate.',
      '''assignments = mf.alg.integer.enumerate(
    V.select("var", "lb", "ub"), R.terms, R.select("row", "lb", "ub"),
    max_states=100_000,
)
proof = m.check.enumerate(target, vars=V, using=premises, max_states=100_000)
proof.require()''',
      'A completed checker proves only the declared local implication and retains all its premises.',
      'A local counterexample may not extend to a feasible MIP point. Budget exhaustion is not proof.',
      ('api.alg.integer.enumerate','api.Checker.enumerate'))
    recipe('round','Verify an exact lower bound and round it',
      'A rational linear certificate yields a lower bound on an integer expression.',
      'x is a model integer variable; sources explicitly imply x>=43/39.',
      'Exact linear certificate with residual bounds → ceil integer lower bound.',
      '''bound = m.check.linear(x >= mf.q("43/39"), using=sources, residual="bounds")
bound.require()
integer_bound = m.check.round(x >= 2, from_=bound)
integer_bound.require()''',
      'An integer-valid bound derived from checked exact sources.',
      'A floating solver objective cannot replace sources; the rounded bound is not generally relaxation-valid.',
      ('api.Checker.linear','api.Checker.round','concept.exact'))
    recipe('edits','Build and audit a verified independent snapshot',
      'A completed proof justifies an explicit modification.',
      'r is a finite upper-bound row; output_dir is a fresh writable directory; the model is exactly LP-representable.',
      'Source proof → Edit addition → Diff → native save → LP audit.',
      '''proof = m.check.linear(r.ub_claim, using=[r.ub_claim])
edit = m.edit()
edit.add_row(r.ub_claim, name="verified_copy", because=proof)
m2 = edit.build()
assert m2.diff(m).semantic["lp"] == "SAME"
m2.save(output_dir / "snapshot.native")
m2.write_lp(output_dir / "candidate.lp")
mf.audit.roundtrip(m2, output_dir / "candidate.lp").require()''',
      'The redundant addition preserves the feasible set and the exported LP matches the target snapshot.',
      'Self-implication is valid for a redundant addition, not for deleting its own only source. No optimization improvement is established.',
      ('api.Edit.build','api.Model.diff','api.audit.roundtrip'))
    recipe('epigraph','Inspect one-hot epigraph candidates',
      'One-hot groups and coefficient bounds suggest disjunctive strengthening.',
      'T is selected original Terms; membership(var,group) is a unique explicit coordinate relation.',
      'Exact coefficient association → grouped observations → stop at the proof boundary.',
      '''Q = T.select("var", "coef").join(membership, on="var", cardinality="m:1", require="left")
parameters = Q.group_by("group").agg(
    amin=mf.col("coef").min(), amax=mf.col("coef").max(),
)
parameters.head()''',
      'Exact associated coefficient ranges on the supplied group relation.',
      'This does not prove a one-hot convex hull. General continuous disjunction certificates are not implemented; check.cases only discharges a finite model-integer split with matching target proofs.',
      ('limitation.disjunction','api.Frame.join'))
