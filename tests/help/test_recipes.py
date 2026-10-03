import pytest
import mipframe as mf
from mipframe._help.query import corpus

RECIPES=[t for t in corpus()['topics'] if t['kind']=='recipe']


@pytest.mark.parametrize('topic',RECIPES,ids=lambda t:t['id'])
def test_installed_recipe_executes_exact_text(topic,recipe_env):
    env=recipe_env(topic['snippet'])
    try:
        exec(compile(topic['example'],topic['id'],'exec'),env)
        ident=topic['snippet']
        if ident=='coordinates':assert len(env['P'])==4 and all(r.distinct==1 for r in env['stats'])
        if ident=='decomposition':assert env['C'].column('component').nunique()==2 and len(env['crossing'])==2
        if ident=='templates':assert env['classes'].column('class').nunique()==1
        if ident=='support':assert len(env['D'])==1 and env['zero'].ok
        if ident=='network':assert len(env['order'])==3 and env['C'].column('component').nunique()==1
        if ident=='closure':assert env['conflicts'].one().value==9
        if ident=='odd-cycle':assert env['cut'].ok and env['cut'].scope['domain']=='integer'
        if ident=='enumeration':assert env['proof'].ok and len(env['assignments'].relations['summary'])==3
        if ident=='conditions':
            with pytest.raises(mf.UndischargedAssumptionError):env['m'].edit().fix(env['z'],0,because=env['conditional'])
        if ident=='round':assert env['integer_bound'].ok
    finally:
        if 'm2' in env:env['m2'].close()
        if 'edit' in env:env['edit'].close()


def test_perturbations_are_not_certified(recipe_env):
    env=recipe_env('linear');m=env['m']
    bad=m.check.linear(env['x']<=3,using=[env['r'].ub_claim],residual='bounds')
    assert bad.status=='INCONCLUSIVE'
    with pytest.raises(mf.ValidationError):bad.require()
    env=recipe_env('coordinates')
    with pytest.raises(mf.CardinalityError):len(env['V'].project(i=env['A'].head(1),j=env['B']))
    env=recipe_env('support');m=env['m']
    bad=m.check.linear((env['b'].expr-env['a'].expr).eq(1),using=[env['b'].eq_claim,(-1,env['a'].eq_claim)])
    assert not bad.ok
