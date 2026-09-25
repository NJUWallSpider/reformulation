import json,shutil,subprocess,sys,os
from pathlib import Path
import pytest
from lpir import IR,LPIRError
from lpir.generate import generate
from lpir.api import Relations


def test_pagination_and_handles(test1):
    first=test1.query('filter','temporal','L[source] + q > U[target]',limit=2)
    second=test1.query(handle=first['handle'],offset=2,limit=2)
    assert first['total']==second['total']==3341
    assert first['items']!=second['items']
    assert second['offset']==2 and second['truncated']
    assert test1.query('relation','temporal','q == cost_switch + cost_arc')['total']>0
    assert test1.inspect('parameter','q',limit=1)['total']==4160
    assert test1.inspect('row','c0',limit=2)['total']==105
    assert test1.inspect('variable','x0')['items'][0]['name']=='x0'
    assert test1.query('neighbors','variable:x0')['total']>=2
    assert test1.inspect('template')['total']>0

@pytest.mark.parametrize('s',["__import__('os')",'q * M','q.__class__','[x for x in []]','q > 0.1'])
def test_relation_language_rejects_code(test1,s):
    with pytest.raises(LPIRError):test1.query('filter','temporal',s)

def test_exact_literals():
    assert Relations('q == "0.1"')({'q':__import__('fractions').Fraction(1,10)})

@pytest.mark.parametrize('mode',['coefficient','domain','missing','duplicate'])
def test_checker_rejects_corruption(test1,tmp_path,mode):
    directory=tmp_path/'corrupt';shutil.copytree(test1.model.path,directory)
    path=directory/'compressed.json';c=json.loads(path.read_text())
    if mode=='coefficient':
        f=next(f for f in c['families'] if f['kind']=='temporal');f['instances'][0]['q']=0
    elif mode=='domain':c['variables'][0][3]=None
    elif mode=='missing':c['families'][0]['instances'].pop()
    else:c['families'][0]['instances'].append(c['families'][0]['instances'][0])
    path.write_text(json.dumps(c))
    with pytest.raises(LPIRError):IR(directory).verify()

def test_cli_and_optimized_python(test1,tmp_path):
    env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
    cmd=[sys.executable,'-O','-m','lpir','verify',str(test1.model.path)]
    done=subprocess.run(cmd,text=True,capture_output=True,env=env,check=True)
    assert json.loads(done.stdout)['status']=='PASS'
    done=subprocess.run([sys.executable,'-m','lpir','inspect',str(test1.model.path),'row','--key','c0','--limit','1'],text=True,capture_output=True,env=env,check=True)
    assert json.loads(done.stdout)['total']==105

def test_generator_small_only(make_ir,tmp_path):
    ir=make_ir('''Minimize
 o: 2x + 3
Subject To
 r: x >= 1
Bounds
 x <= 4
End
''')
    path=tmp_path/'copies.lp';result=generate(ir.model,path,copies=3)
    other=IR.build(path,tmp_path/'copies')
    assert result['copies']==3 and other.overview()['rows']==3 and other.overview()['objective_constant']=='9'
    path2=tmp_path/'sized.lp';result=generate(ir.model,path2,target_mb=0.002)
    assert result['bytes']>=int(0.002*1024*1024)
    assert IR.build(path2,tmp_path/'sized').verify()['status']=='PASS'
