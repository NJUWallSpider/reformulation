"""Exercise documented contracts against current public results and inputs."""
import inspect
import json
from pathlib import Path

import pytest
import mipframe as mf
from mipframe._help.query import corpus


def contract(name):
    return mf.help(name, detail='full', limit=1, format='json')['results'][0]


def test_all_model_receiver_paths_resolve():
    prefixes = {'Model.': 'm.', 'Checker.': 'm.check.', 'MatrixAccessor.': 'm.matrix.'}
    for topic in corpus()['topics']:
        for symbol in topic['symbols']:
            for prefix, receiver in prefixes.items():
                if symbol.startswith(prefix):
                    result = mf.help(receiver + symbol[len(prefix):], format='json')
                    assert result['match'] == 'exact', symbol
                    assert result['results'][0]['id'] == topic['id'], symbol


def test_compact_constructors_and_path_witnesses(recipe_env):
    env = recipe_env('transitive')
    m = env['m']
    a, b, c = (m.rows.by_name(name) for name in ('a', 'b', 'c'))
    for cls in (mf.Targets, mf.CertificateBatch):
        signature = contract(cls.__name__)['signatures'][cls.__name__]
        assert signature == str(inspect.signature(cls))
        assert 'memory_mb' in signature
    assert contract(mf.CertificateBatch.paths)['id'] == 'api.CertificateBatch.paths'
    assert contract('CertificateBatch.paths')['id'] == 'api.CertificateBatch.paths'
    examples = dict(mf=mf, a=a, b=b, r=c)
    for name in ('CertificateBatch', 'CertificateBatch.paths', 'Targets'):
        exec(compile(contract(name)['example'], name, 'exec'), examples)
    proof = m.check.linear_many(targets=examples['targets'], witnesses=examples['witnesses']).require()
    assert proof.for_target(c.id).ok
    with pytest.raises(ValueError, match='offsets'):
        mf.CertificateBatch([1, 2], [a.id, b.id])


def test_live_constructor_drift_is_reported(monkeypatch):
    current = inspect.signature(mf.Targets)
    changed = current.replace(parameters=[*current.parameters.values(),
        inspect.Parameter('future_option', inspect.Parameter.KEYWORD_ONLY, default=False)])
    monkeypatch.setattr(mf.Targets, '__signature__', changed, raising=False)
    result = mf.help('Targets', format='json')
    assert result['warnings']
    assert 'future_option' in result['results'][0]['signatures']['Targets']


def test_scalar_checker_types_and_point_navigation(recipe_env, tmp_path):
    env = recipe_env('linear')
    m, x, y = env['m'], env['x'], env['y']
    bound = m.check.linear(x <= 5, using=[x.ub_claim])
    results = {
        'linear': bound,
        'identity': m.check.identity(x, x),
        'infeasible': m.check.infeasible(),
        'round': m.check.round(x <= 5, from_=bound),
        'enumerate': m.check.enumerate(x <= 5, vars=m.vars),
        'cases': m.check.cases(x <= 5, var=y, cases={k:
            m.check.linear(x <= 5, using=[x.ub_claim], assume=[y.eq(k)])
            for k in range(6)}),
    }
    for name, result in results.items():
        assert isinstance(result, mf.ProofRef)
        assert 'ProofRef' in contract('Checker.' + name)['returns']
        assert contract(result)['id'] == 'api.ProofRef'
    assert not hasattr(bound, 'explain')
    assert isinstance(mf.Report.replay(bound.to_spec(), models=[m]).require(), mf.ProofRef)
    assert isinstance(mf.BatchProof.replay(bound.batch.to_spec(), model=m).require(), mf.BatchProof)
    assert '_internal' not in contract('BatchProof.replay')['signatures']['BatchProof.replay']
    point = m.check.solution({v: 0 for v in m.vars})
    try:
        assert type(point).__name__ == 'PointReport'
        assert 'PointReport' in contract('Checker.solution')['returns']
        assert contract(point)['id'] == 'api.PointReport'
        assert contract(point.close)['id'] == 'api.PointReport.close'
        assert set(point.evidence) == {'violating_rows', 'violating_vars'}
        path = point.write(tmp_path / 'point')
        replayed = mf.Report.replay(path, models=[m])
        try:
            assert replayed.require().ok
        finally:
            replayed.close()
    finally:
        point.close()


def test_domains_and_common_batch_assumptions(recipe_env):
    env = recipe_env('linear')
    m, x = env['m'], env['x']
    values = mf.Frame.from_records([dict(var=v, value='C') for v in m.vars],
        schema={'var': m.vars.schema['var'], 'value': 'str'}, key=('var',), models=(m,))
    with m.edit() as edit:
        with pytest.raises(ValueError, match='Domain must be I or C'):
            edit.set_domains(m.vars, kind=values)
        edit.set_domains(m.vars, kind='C')
    assert 'Frame is not accepted' in contract('Edit.set_domains')['inputs']
    with m.check.linear_batch() as batch:
        batch.add(key='a', target=x <= 5, using=[x.ub_claim], assume=[x.eq(0)])
        batch.add(key='b', target=x <= 5, using=[x.ub_claim], assume=[x.eq(1)])
        checked=batch.run().require()
        assert checked.for_target('a').scope['assumptions']==1
        assert checked.for_target('b').scope['assumptions']==1
        assert checked.assumptions(0)!=checked.assumptions(1)
    brief = mf.help('Checker.linear_batch', format='json')['results'][0]
    assert 'per target' in brief['inputs']


def test_diff_result_and_native_version_contracts(recipe_env, tmp_path):
    m = recipe_env('linear')['m']
    other = recipe_env('linear')['m']
    assert isinstance(m.diff(m), mf.ModelDiff)
    diff = m.diff(other)
    assert diff.numerically_equal
    assert diff.semantic['mip'] == 'UNKNOWN'
    assert contract(diff)['id'] == 'api.NumericDiff'
    assert 'NumericDiff' in contract('Model.diff')['returns']
    assert 'unrelated snapshots are rejected' not in str(contract('Model.diff'))
    native = tmp_path / 'native'
    m.save(native)
    manifest = native / 'manifest.json'
    spec = json.loads(manifest.read_text())
    assert spec['schema_version'] == 7
    assert 'Native v7' in contract('concept.identity')['body']
    spec['schema_version'] = 6
    manifest.write_text(json.dumps(spec))
    with pytest.raises(mf.MIPFrameError, match='v7'):
        mf.load(native)


def test_round_many_documented_requirements(recipe_env):
    m = recipe_env('round')['m']
    x, row = m.vars.one(), m.rows.one()
    proof = m.check.linear_many(
        targets=mf.Targets([x.id], kind='var', sides='lb', rhs=mf.q('43/39')),
        witnesses=mf.CertificateBatch([0, 1], [row.id], multipliers=mf.q('1/39'), sides='lb'),
    ).require()
    rounded = proof.round_many(lb=2).require()
    assert rounded.scope['domain'] == 'integer'
    assert proof.round_many(lb=3).status == 'INCONCLUSIVE'
    for kwargs in ({}, {'lb': 2, 'ub': 2}, {'ub': 2}):
        with pytest.raises(ValueError):
            proof.round_many(**kwargs)
    row_proof = m.check.linear_rows(targets=[row.id], witnesses=mf.CertificateBatch(
        [0, 1], [row.id], sides='lb'), side='lb')
    with pytest.raises(ValueError, match='variable bounds'):
        row_proof.round_many(lb=2)
    topic = contract('BatchProof.round_many')
    assert 'integer' in topic['guarantees'] and 'exactly one' in topic['inputs']
    exec(compile(topic['example'], topic['id'], 'exec'), dict(mf=mf, m=m, x=x))


def test_skill_quickstart_is_the_tested_recipe():
    skill = contract('skill')['body']
    snippet = contract('recipe.quickstart')['example']
    assert snippet in skill
    assert '{{quickstart}}' not in skill
    source = Path(__file__).resolve().parents[2] / 'docs/agent/SKILL.md'
    assert source.read_text() == skill
