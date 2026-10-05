"""Fixed task-intent and boundary queries, distinct from exact-name lookup."""
import mipframe as mf
import pytest

QUERIES=[
 ('recover two coordinates for variables from row families',{'recipe.coordinates'}),
 ('row support subset',{'recipe.support'}),
 ('find connected blocks after removing coupling rows',{'recipe.decomposition'}),
 ('compare repeated templates',{'recipe.templates'}),
 ('build a directed graph from plus one minus one incidence',{'recipe.network'}),
 ('shared source conflict witnesses',{'recipe.conflicts'}),
 ('verify an odd cycle inequality',{'recipe.odd-cycle'}),
 ('resource consumption of closure union',{'recipe.closure'}),
 ('remove transitive constraints',{'recipe.transitive'}),
 ('exact linear verification',{'concept.proofs','api.Checker.linear','recipe.linear'}),
 ('conditional contradiction',{'recipe.conditions','concept.conditions'}),
 ('local integer enumeration',{'recipe.enumeration'}),
 ('rational lower bound then rounding',{'recipe.round'}),
 ('edit model',{'recipe.edits'}),
 ('one hot epigraph strengthening',{'recipe.epigraph','limitation.disjunction'}),
 ('save and load identity',{'concept.identity'}),
 ('whole matrix scipy escape',{'api.MatrixView.to_scipy'}),
 ('bounded preview active columns',{'api.MatrixView.preview'}),
 ('where are zero degree members',{'api.MatrixAccessor.nnz','error.navigation','concept.frame'}),
 ('overlapping supports with witnesses',{'recipe.conflicts','recipe.support','api.ops.sets.members'}),
 ('sum row combinations cancel variables',{'recipe.linear','api.Checker.identity','recipe.support'}),
 ('local maximum over integer lattice',{'recipe.enumeration','api.Checker.enumerate','api.alg.integer.enumerate'}),
 ('output materialization too large',{'concept.resources','api.Frame.collect','api.Frame.to_records'}),
 ('exact coefficient histograms',{'recipe.roles','api.ColumnView.value_counts','concept.exact'}),
 ('strict projection has missing neighbors',{'api.VarFrame.project','api.RowFrame.project','recipe.coordinates'}),
 ('pass previous proof to a model modification',{'api.Edit.add_row','recipe.edits','concept.proofs'}),
 ('schema and primary key in a table',{'concept.frame','api.Frame.key','api.Frame.schema'}),
 ('exact representation of fractional coefficients',{'concept.exact','api.q','api.LinExpr.terms'}),
 ('independent native snapshots',{'concept.identity','api.Model.save','api.load'}),
 ('finite pure integer system',{'api.alg.integer.enumerate','recipe.enumeration'}),
]
BOUNDARIES=[
 ('graph clique proof for a cut',{'recipe.odd-cycle','concept.search','api.alg.graph.clique_candidates'}),
 ('local counterexample versus feasible MIP solution',{'recipe.enumeration','concept.proofs'}),
 ('unconditional conclusion from assumption',{'concept.conditions'}),
 ('integer valid versus relaxation valid',{'concept.proofs'}),
 ('automatic symmetry breaking',{'limitation.symmetry'}),
 ('write MPS file',{'limitation.export'}),
 ('unknown join uniqueness',{'error.join'}),
 ('coefficient on row entity',{'error.navigation'}),
 ('mathematical expression versus table predicate',{'error.expressions'}),
 ('continuous disjunction proof',{'limitation.disjunction','concept.conditions'}),
]


def ids(q):return {t['id'] for t in mf.help(q,format='json')['results']}


def test_task_recall():
    missed=[q for q,want in QUERIES if not ids(q)&want]
    assert 1-len(missed)/len(QUERIES)>=.95,missed


@pytest.mark.parametrize('query,wanted',BOUNDARIES)
def test_high_risk_boundaries(query,wanted):
    assert ids(query)&wanted


# Real agent trace intents and follow-up analysis. These are first-result
# contracts, not Recall@3: the agent should not need to discard an unrelated hit.
AGENT_INTENTS = [
    ('case discharge', 'api.Checker.cases'),
    ('finite domain case discharge', 'api.Checker.cases'),
    ('binary case proof', 'api.Checker.cases'),
    ('batch linear proof', 'api.Checker.linear_batch'),
    ('many linear certificates', 'api.Checker.linear_batch'),
    ('batch checks with assumptions', 'api.Checker.linear_batch'),
    ('Frame native batch certificates', 'api.Checker.linear_many'),
    ('tighten big M', 'concept.big-m'),
    ('claim construction', 'api.Claim'),
    ('fractional point feasibility', 'limitation.fractional-point'),
    ('resource cover', 'concept.resource-cover'),
    ('report resource lifecycle', 'concept.report-resources'),
    ('conditional case split', 'concept.conditions'),
    ('set cover', 'concept.resource-cover'),
    ('graph matching', 'limitation.graph-matching'),
    ('schema', 'api.Frame.schema'),
]

# Not registered aliases: exercise the compiled index rather than exact lookup.
AGENT_PARAPHRASES = [
    ('how can I check many linear certificates at once', 'api.Checker.linear_batch'),
    ('build a batch of linear proof certificates', 'api.Checker.linear_batch'),
    ('how to tighten a big-M coefficient', 'concept.big-m'),
    ('construct an affine inequality claim', 'api.Claim'),
    ('verify a fractional LP solution', 'limitation.fractional-point'),
    ('choose resource rows covering a target support', 'concept.resource-cover'),
    ('release retained report evidence storage', 'concept.report-resources'),
    ('combine proofs from both binary branches', 'concept.conditions'),
    ('find a minimum weighted set cover', 'concept.resource-cover'),
    ('find maximum cardinality matching in a graph', 'limitation.graph-matching'),
    ('how to declare column types for a table', 'api.Frame.schema'),
]


@pytest.mark.parametrize('query,wanted', AGENT_INTENTS + AGENT_PARAPHRASES)
def test_agent_intent_first_result(query, wanted):
    result = mf.help(query, limit=1, format='json')
    assert [t['id'] for t in result['results']] == [wanted]
    if (query, wanted) in AGENT_PARAPHRASES:
        assert result['match'] == 'search'


def test_alias_words_are_searchable_outside_exact_lookup():
    from mipframe._help.search import index, search
    topics = [
        dict(id='intended', kind='concept', title='A', aliases=['lunar compass']),
        dict(id='distractor', kind='concept', title='Compass'),
    ]
    assert search('compass lunar', topics, index(topics))[0] == 'intended'


@pytest.mark.parametrize('query,restriction', [
    ('tighten big M', 'No automatic big-M optimizer'),
    ('set cover', 'no minimum set-cover solver'),
    ('graph matching', 'No maximum-cardinality or weighted matching solver'),
    ('fractional point feasibility', 'not an implication proof'),
    ('conditional case split', 'infeasible-branch elimination'),
    ('report resource lifecycle', 'no public close() method'),
])
def test_intent_brief_preserves_capability_boundaries(query, restriction):
    first = mf.help(query, limit=1, format='json')['results'][0]
    assert restriction in first['non_guarantees']
