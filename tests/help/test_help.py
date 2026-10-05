import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import mipframe as mf
from mipframe._help.query import corpus
from mipframe._help.diagnostics import CLASS_TOPICS

ROOT=Path(__file__).resolve().parents[2]


def source_environment():
    return {**os.environ, 'PYTHONPATH': os.pathsep.join(filter(None, (str(ROOT/'src'), os.environ.get('PYTHONPATH'))))}


def run_cli(*args,cwd=None):
    return subprocess.run([sys.executable,'-m','mipframe','help',*args],cwd=cwd or ROOT,
                          env=source_environment(),text=True,capture_output=True)


def test_all_exact_names_resolve_without_internal_context():
    for t in corpus()['topics']:
        for symbol in t.get('symbols',[]):
            result=mf.help('mf.'+symbol,format='json')
            assert result['match']=='exact'
            assert any(symbol in r['symbols'] for r in result['results']),symbol
            assert not any('ctx' in str(r['signatures']) or '0x' in str(r['signatures']) for r in result['results'])
    for topic in CLASS_TOPICS.values():assert mf.help(topic,format='json')['match']=='exact'


def test_signatures_and_generated_files():
    import runpy
    builder=runpy.run_path(str(ROOT/'scripts/build_help.py'))
    for t in corpus()['topics']:
        for symbol,binding in t.get('bindings',{}).items():
            obj=builder['resolve'](binding)
            assert t['signatures'][symbol]==builder['signature'](obj, constructor=t.get('constructor', False))
            raw=obj.fget if isinstance(obj,property) else obj
            assert 'MIPFRAME HELP BEGIN' in (inspect.getdoc(raw) or '')
    result=subprocess.run([sys.executable,str(ROOT/'scripts/build_help.py'),'--check'],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_text_json_independence_and_validation(capsys):
    from mipframe.frame.row import Formula
    assert isinstance(mf.help(),Formula)
    assert capsys.readouterr().out==''
    result=mf.help('recipe.coordinates',format='json')
    result['results'][0]['related'].clear()
    assert mf.help('recipe.coordinates',format='json')['results'][0]['related']
    json.dumps(mf.help('skill',detail='full',format='json'))
    for limit in (True,False,0,-1,1.5):
        with pytest.raises(ValueError):mf.help(limit=limit)
    with pytest.raises(ValueError):mf.help(detail='all')
    with pytest.raises(ValueError):mf.help(format='html')
    with pytest.raises(TypeError):mf.help(object())
    assert 'More:' in mf.help('recipe.coordinates')


def test_cli_and_unknown_names(tmp_path):
    for query in ('recipe.coordinates','join cardinality','skill'):
        result=run_cli(query,'--format','json',cwd=tmp_path)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)==mf.help(query,format='json')
    assert run_cli('--limit','0').returncode==2
    for query in ('mf.detect_network()', 'm.auto_reformulate()', '__import__("os").system("bad")','zzzxqv',
                  'api.Report.close', 'Report.close', 'api.LinExpr.close',
                  'api.LinExpr.__le__', 'api.ModelDiff.summary', 'Frame.jion',
                  'mf.alg.graph.maximum_matching'):
        result=run_cli(query,'--format','json')
        assert result.returncode==1
        assert json.loads(result.stdout)['results']==[]
    assert mf.help('Frame.jion',format='json')['suggestions']
    assert 'frame.join' in mf.help('api.Frame.jion',format='json')['suggestions']


def test_claim_and_schema_help_examples(recipe_env):
    env = recipe_env('linear')
    for query in ('claim construction', 'schema'):
        topic = mf.help(query, detail='full', limit=1, format='json')['results'][0]
        exec(compile(topic['example'], topic['id'], 'exec'), env)
    assert all(isinstance(env[name], mf.Claim) for name in ('upper', 'lower', 'equality'))
    terms = mf.Frame.from_records(
        [{'target':'bound', 'var':env['x'], 'coef':mf.q('1/2')}],
        schema=env['schema'], models=(env['m'],),
    )
    assert terms.one().coef == mf.q('1/2')


def test_fractional_feasibility_guidance_matches_checker(recipe_env):
    m = recipe_env('linear')['m']  # x,y integer, x+y<=4
    point = mf.Frame.from_records(
        [{'var':v, 'value':mf.q('1/2')} for v in m.vars],
        schema={'var':m.vars.schema['var'], 'value':'number'},
        key=('var',), models=(m,),
    )
    assert m.matrix.activity(point).one().activity == 1
    assert m.check.solution(point).status == 'REFUTED'
    assert m.check.solution(point, domain='relaxation').require().scope['domain']=='relaxation'
    assert not hasattr(mf.Report, 'close')
    assert not hasattr(mf.LinExpr, 'close')


def test_static_lookup_after_close(recipe_env,monkeypatch):
    env=recipe_env('linear');m=env['m'];r=env['r'];frame=m.rows;check=m.check;fn=check.linear
    var_project=m.vars.project
    m.close()
    def forbidden(*a,**kw):raise AssertionError('Help touched live data')
    monkeypatch.setattr(mf.Model,'model',property(forbidden))
    monkeypatch.setattr(mf.Frame,'_iterate',forbidden)
    for obj in (m,r,frame,fn,check,mf.ops.sets.pairs,mf.Model,mf.Frame):
        assert mf.help(obj,format='json')['match']=='exact'
    assert mf.help(var_project,format='json')['results'][0]['id']=='api.VarFrame.project'


def test_error_to_help_to_correction(recipe_env):
    env=recipe_env('linear');m=env['m'];x=env['x'];r=env['r']
    with pytest.raises(AttributeError) as err:r.coef
    assert err.value.help_topic=='error.navigation'
    assert mf.help(err.value,format='json')['results'][0]['id']=='error.navigation'
    assert r.terms.column('coef').min()==1
    with pytest.raises(mf.ExactArithmeticError) as err:mf.q(9.6)
    assert mf.help(err.value,format='json')['results'][0]['id']=='concept.exact'
    assert mf.q('9.6')==mf.q('48/5')
    with pytest.raises(TypeError) as err:bool(mf.col('coef')>0)
    assert err.value.help_topic=='error.expressions'
    assert len(r.terms.where((mf.col('coef')>0)&(mf.col('coef')<2)))==2
    with pytest.raises(TypeError) as err:m.rows[0]
    assert err.value.help_topic=='error.navigation' and m.rows.iloc[0]==r
    with pytest.raises(AttributeError):m.rows.index
    A=m.terms().select('var');B=A.select('var')
    with pytest.raises(mf.SchemaError) as err:A.join(B,on='var')
    assert 'Unknown' in str(err.value) and err.value.help_topic=='error.join'
    assert len(A.join(B,on='var',cardinality='1:1',require='both'))==2
    proof=m.check.linear(x<=3,using=[r.ub_claim],residual='bounds')
    with pytest.raises(mf.ValidationError) as err:proof.require()
    assert err.value.report is proof and mf.help(err.value,format='json')['results']


def test_bounded_capsules_and_no_installation_paths():
    for t in corpus()['topics']:
        rendered=str(mf.help(t['id']))
        assert len(rendered)<8000,(t['id'],len(rendered))
        assert '/Users/' not in rendered and 'test34' not in rendered
        if t.get('example'):assert t['example'] in rendered
    assert len(str(mf.help()))<6000


def test_import_does_not_load_corpus_or_write_cache(tmp_path):
    code='''
import importlib.resources
original=importlib.resources.files
def blocked(package,*args,**kwargs):
    if package == "mipframe._help": raise AssertionError("eager help resource read")
    return original(package,*args,**kwargs)
importlib.resources.files=blocked
import mipframe
from mipframe._help.query import corpus
assert corpus.cache_info().currsize==0
try: mipframe.q(0.1)
except mipframe.ExactArithmeticError as e: assert e.help_topic == "concept.exact"
assert corpus.cache_info().currsize==0
'''
    result=subprocess.run([sys.executable,'-B','-c',code],cwd=tmp_path,env=source_environment(),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert list(tmp_path.iterdir())==[]


def test_version_warning_and_build_checksum(monkeypatch):
    monkeypatch.setattr(mf,'__version__','different')
    assert mf.help(format='json')['warnings']
    from mipframe._help.maintenance import validate
    assert validate(ROOT/'src/mipframe')['version']!='different'


def test_development_signature_drift_is_explicit(monkeypatch):
    original=mf.Frame.join
    def changed(self,other,*,on,new_parameter=False):
        raise AssertionError('Help executed a query')
    monkeypatch.setattr(mf.Frame,'join',changed)
    data=mf.help('Frame.join',format='json')
    assert data['warnings'] and 'new_parameter' in data['results'][0]['signatures']['Frame.join']


def test_every_top_level_export_has_help():
    for name in mf.__all__:
        result=mf.help('mf.'+name,format='json')
        assert result['match']=='exact',name
        assert mf.help(getattr(mf,name),format='json')['results'],name


def test_join_observed_errors_and_corrected_calls():
    def frame(records):
        return mf.Frame.from_records(records, schema={'id':'int', 'value':'int'})
    a = frame([{'id':1,'value':2}, {'id':1,'value':3}]).select('id')
    b = frame([{'id':1,'value':4}])
    with pytest.raises(mf.CardinalityError) as error:
        len(a.join(b, on='id', cardinality='1:1'))
    assert error.value.report is not None
    assert mf.help(error.value,format='json')['results']
    assert len(a.join(b,on='id',cardinality='m:1',require='left')) == 2
    missing = frame([{'id':2,'value':0}])
    with pytest.raises(mf.CardinalityError) as error:
        len(a.join(missing,on='id',cardinality='m:1',require='left'))
    assert mf.help(error.value,format='json')['results']
    assert len(a.join(missing,on='id',how='left',cardinality='m:1')) == 2
    with pytest.raises(mf.SchemaError) as error:
        b.join(b,on='id',cardinality='1:1')
    assert error.value.help_topic=='error.join'
    renamed=b.select('id',other=mf.col('value'))
    assert b.join(renamed,on='id',cardinality='1:1').one().other == 4


def test_proof_truth_values_and_local_domain_recovery(recipe_env):
    env=recipe_env('linear');m=env['m'];x=env['x'];r=env['r']
    claim=r.expr <= r.ub
    with pytest.raises(TypeError) as error:bool(claim)
    assert mf.help(error.value,format='json')['results'][0]['id']=='error.expressions'
    proof=m.check.linear(claim,using=[r.ub_claim])
    with pytest.raises(TypeError) as error:bool(proof)
    assert mf.help(error.value,format='json')['results']
    assert proof.status=='PROVED'
    continuous = recipe_env('conditions')
    with pytest.raises(mf.UnsupportedEnumerationDomain) as error:
        continuous['m'].check.enumerate(continuous['x'] <= 100,vars=[continuous['x']])
    assert mf.help(error.value,format='json')['results'][0]['id']=='api.Checker.enumerate'
    # For a continuous variable, retain a linear certificate instead of inventing
    # an integer domain to make enumeration accept the input.
    m.check.linear(claim,using=[r.ub_claim]).require()
    m.close()
    with pytest.raises(mf.MIPFrameError) as error:list(r.terms)
    assert error.value.help_topic=='concept.identity'
    assert mf.help(error.value,format='json')['results']
