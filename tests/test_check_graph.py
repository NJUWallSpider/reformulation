from copy import deepcopy
from fractions import Fraction
import pytest
import mipframe as mf
from mipframe.check.budget import ProofBudgetExceeded
from mipframe.check.compact import trusted
from mipframe.check.kernel import facts


@pytest.fixture
def m(tmp_path):
    path = tmp_path / 'graph.lp'
    path.write_text('''Minimize
 obj: x
Subject To
 cap: x <= 6
 old: x + 1197 y <= 1200
Bounds
 0 <= x <= 10
 0 <= y <= 1
 0 <= z <= 1
Generals
 y z
End
''')
    with mf.read(path) as model:
        yield model


def staged(m):
    x, y, z = (m.vars.by_name(k) for k in ('x', 'y', 'z'))
    with m.check.batch() as batch:
        target = x + 3*y <= 6
        branches = {k: batch.linear(target, using=[m.rows.by_name(name).ub_claim],
                                   assume=[y.eq(k)], residual='bounds', result_key=f'b{k}')
                    for k, name in ((0, 'cap'), (1, 'old'))}
        batch.cases(target, var=y, cases=branches, result_key='result')
        # An unused, invalid proof must not run or taint another conclusion.
        batch.linear(x <= -100)
        result = batch.run()
    return target, result


def test_handles_and_scopes(m):
    with m.check.batch() as b:
        h = b.linear(m.vars.by_name('x') <= 10, residual='bounds', result_key='x')
        assert not hasattr(h, 'ok') and not hasattr(h, 'status')
        with pytest.raises(TypeError):
            bool(h)
        with pytest.raises(Exception):
            trusted(h, facts(m))
        r = b.run().require()
        assert r['x'].ok
        with pytest.raises(ValueError):
            b.run()
    target, r = staged(m)
    r.require()
    assert r['b0'].scope['assumptions'] == 1
    assert r['result'].scope['assumptions'] == 0
    trusted(r['result'], facts(m))
    with pytest.raises(mf.UndischargedAssumptionError):
        trusted(r['b0'], facts(m))
    assert r['result'].conclusion.expr == target.expr


def test_query_leaves_are_lazy_shared_and_replayable(m,tmp_path):
    calls=[]
    ids=list(m.vars.ids)
    def scan(fields,ctx):
        calls.append(tuple(fields))
        for group,var in enumerate(ids):
            row=dict(var=var,coef=1,group=group)
            yield {k:row[k] for k in fields}
    relation=mf.Frame(dict(var=m.vars.schema['var'],coef=mf.DType('number'),group=mf.DType('int')),
                      models=(m,),source=scan)
    def forbidden(*args):raise AssertionError('dead query executed')
    dead=mf.Frame(dict(var=m.vars.schema['var'],coef=mf.DType('number')),models=(m,),source=forbidden)
    with m.check.batch() as batch:
        for i in range(len(ids)):
            target=m.linexpr(relation.where(mf.col('group')==i).select('var','coef'))<=10
            batch.linear(target,residual='bounds',result_key=str(i))
        batch.linear(m.linexpr(dead)<=0)
        assert not calls
        result=batch.run().require()
    assert len(calls)==1
    for spec in (result['0'].batch.to_spec(),result['0'].batch.write(tmp_path/'lazy')):
        assert mf.BatchProof.replay(spec,model=m).ok
    assert len(calls)==1


def test_lazy_query_does_not_hide_join_errors(m):
    x=mf.Ref(m.uid,m.version,'var',m.vars.by_name('x').id)
    left=mf.Frame.from_records([dict(var=x,group=0),dict(var=x,group=1)],
        schema=dict(var=m.vars.schema['var'],group='int'),models=(m,))
    right=mf.Frame.from_records([dict(var=x,coef=1)],schema=dict(var=m.vars.schema['var'],coef='number'),models=(m,))
    with m.check.batch() as batch:
        expr=m.linexpr(left.join(right,on='var',cardinality='1:1').where(mf.col('group')==0).select('var','coef'))
        batch.linear(expr<=10,residual='bounds',result_key='bad')
        with pytest.raises(mf.CardinalityError):batch.run()


def test_discharge_preserves_unrelated_conditions(m):
    x,y,z=(m.vars.by_name(k) for k in ('x','y','z'))
    with m.check.batch() as batch:
        target=x<=10
        branches={k:batch.linear(target,assume=[y.eq(k),z.eq(1)],residual='bounds') for k in (0,1)}
        batch.cases(target,var=y,cases=branches,result_key='conditional')
        batch.linear(target,residual='bounds',result_key='unconditional')
        result=batch.run().require()
    assert result['conditional'].scope['assumptions']==1
    assert result['unconditional'].scope['assumptions']==0
    with pytest.raises(mf.UndischargedAssumptionError):trusted(result['conditional'],facts(m))
    replay=mf.BatchProof.replay(result['conditional'].batch.to_spec(),model=m)
    assert replay.for_target(result['conditional'].index).scope['assumptions']==1


def test_wide_integer_residual_never_rounds_or_builds_fraction_trees(tmp_path,monkeypatch):
    path=tmp_path/'wide-products.lp'
    expr=' + '.join(f'x{i}' for i in range(12))
    path.write_text(f'Minimize\n obj: x0\nSubject To\n zero: {expr} = 0\nBounds\n'+
                    '\n'.join(f' 0 <= x{i} <= 1' for i in range(12))+'\nEnd\n')
    from mipframe.check.sparse_tree import SparseTree
    def forbidden(*args):raise AssertionError('wide integer tile fell back to Fraction tree')
    monkeypatch.setattr(SparseTree,'residual',forbidden)
    with mf.read(path) as model:
        row=model.rows.by_name('zero');large=2**110+1
        with model.check.batch() as batch:
            batch.linear(row.expr<=0,using=[(large,row.eq_claim)],residual='bounds',result_key='valid')
            batch.linear(row.expr<=-1,using=[(large,row.eq_claim)],residual='bounds',result_key='invalid')
            result=batch.run()
        assert result['valid'].ok and not result['invalid'].ok
        restored=mf.BatchProof.replay(result['valid'].batch.to_spec(),model=model)
        assert tuple(restored._status)==tuple(result['valid'].batch._status)


def test_overlay_frame_reference_validation_uses_live_index(m,monkeypatch):
    x=m.vars.by_name('x');proof=m.check.linear(x<=10,residual='bounds')
    edit=m.edit();edit.add_row(x<=10,name='copy',because=proof)
    with edit.commit() as child:
        refs=child.vars.select('var').to_records()
        class Unscannable(tuple):
            def __contains__(self,value):raise AssertionError('linear ID membership scan')
        monkeypatch.setattr(child.model,'entity_ids',lambda axis:Unscannable(facts(child).var_ids()))
        points=[dict(var=r['var'],value=0) for r in refs]
        frame=mf.Frame.from_records(points,schema=dict(var=child.vars.schema['var'],value='number'),models=(child,))
        child.check.solution(frame).require()
        with pytest.raises(mf.SchemaError):
            mf.Frame.from_records([dict(var=mf.Ref(child.uid,child.version,'var',999))],
                                  schema=dict(var=child.vars.schema['var']),models=(child,))


def test_mixed_point_domains_keep_scope_and_replay(m,tmp_path):
    values={v:Fraction(1,2) for v in m.vars.ids}
    with m.check.batch() as batch:
        batch.solution(values,result_key='integer')
        batch.solution(values,domain='relaxation',result_key='lp')
        results=batch.run()
    assert not results['integer'].ok and results['lp'].ok
    report=results['lp']
    assert report.scope['domain']=='relaxation'
    restored=mf.Report.replay(report.write(tmp_path/'lp-point'),models=[m]).require()
    assert restored.scope['domain']=='relaxation'
    with pytest.raises(Exception):trusted(restored,facts(m))
    bad=report.to_spec();bad['domain']='integer'
    assert not mf.Report.replay(bad,models=[m]).ok


def test_graph_replay_edit_and_tampering(m, tmp_path):
    target, results = staged(m)
    p = results['result']
    spec = p.batch.to_spec()
    assert spec['schema'] == 'mipframe-certificate-3'
    for source in (spec, p.batch.write(tmp_path/'proof')):
        q = mf.BatchProof.replay(source, model=m)
        assert q.ok and q.for_target(p.index).scope['assumptions'] == 0
    bad = deepcopy(spec)
    graph = bad['nodes'][-1]['recipe']
    graph['tasks'][2][4][1][1] = 0
    with pytest.raises(Exception):
        mf.BatchProof.replay(bad, model=m)
    edit = m.edit()
    edit.add_row(target, name='tight', because=p)
    with edit.commit() as child:
        audit = child.audit(source=m)
        assert audit['status'] == 'PROVED'


def test_exact_cancellation_and_budget(m):
    x = m.vars.by_name('x')
    large = 2**90 + 3
    with m.check.batch() as b:
        a = large*x + Fraction(1, 7)*x
        b.identity(a - a, result_key='zero')
        assert b.run().require()['zero'].ok
    with pytest.raises(ProofBudgetExceeded):
        with m.check.batch(max_tasks=1) as b:
            b.linear(x <= 10)
            b.linear(x <= 10)
    for bad in (None, float('inf'), 0, -1):
        with pytest.raises(ValueError):
            m.check.batch(time_limit=bad)


def test_wrong_branch_and_foreign_handle(m):
    y = m.vars.by_name('y')
    with m.check.batch() as b, m.check.batch() as other:
        h = other.linear(y <= 1, residual='bounds')
        with pytest.raises(Exception):
            b.linear(y <= 1, using=[h])
    with m.check.batch() as b:
        h = b.linear(y <= 1, assume=[y.eq(0)], residual='bounds')
        b.cases(y <= 1, var=y, cases={0: h}, result_key='bad')
        with pytest.raises(Exception):
            b.run()


def test_mixed_results_keep_authority(m):
    from mipframe.check.finite import PointReport
    with m.check.batch() as b:
        x = m.vars.by_name('x')
        b.linear(x <= 10, residual='bounds', result_key='proof')
        point = {int(v): 0 for v in m.vars.ids}
        h = b.solution(point, result_key='point')
        b.solution(point, result_key='same_point')
        b.frame(m.vars, 'unique', columns=('var',), result_key='frame')
        b.model_verify(result_key='integrity')
        with pytest.raises(Exception):
            b.linear(x <= 10, using=[h])
        results = b.run().require()
    assert isinstance(results['point'], PointReport)
    for key in ('point','same_point','frame','integrity'):
        with pytest.raises(Exception):
            trusted(results[key], facts(m))
    for key in ('point','same_point'):
        results[key].close()


def test_mixed_condition_groups_and_enumeration(m):
    x,y,z=(m.vars.by_name(k) for k in ('x','y','z'))
    with m.check.batch() as b:
        bounds=b.bound(vars=[x.id,y.id],values=[10,1],result_key='bounds')
        b.linear(x <= 10,using=[bounds.for_target(x.id)],result_key='use')
        b.enumerate(y+z <= 2,vars=[y,z],result_key='enumerated')
        b.enumerate(y+z <= 2,vars=[y,z],max_states=1,result_key='incomplete')
        b.enumerate(y+z <= 0,vars=[y,z],result_key='refuted')
        r=b.run()
    assert r['bounds'].for_target(y.id).ok and r['use'].ok
    assert r['enumerated'].ok and r['enumerated'].summary['states']==4
    assert r['incomplete'].status=='INCONCLUSIVE'
    assert r['refuted'].status=='REFUTED'
    assert len(r['refuted'].counterexample)>0
    restored=mf.BatchProof.replay(r['enumerated'].batch.to_spec(),model=m)
    assert restored.for_target(r['enumerated'].index).ok
    with pytest.raises(mf.ValidationError):r.require()


def test_failed_dependency_and_unrelated_edit(m):
    x=m.vars.by_name('x')
    with m.check.batch() as b:
        bad=b.linear(x <= -1)
        b.linear(x <= 0,using=[bad],result_key='blocked')
        b.linear(x <= 10,residual='bounds',result_key='valid')
        r=b.run()
    assert r['blocked'].status=='INCONCLUSIVE' and r['valid'].ok
    edit=m.edit();edit.add_row(x <= 10,name='still_valid',because=r['valid'])
    with edit.commit() as child:assert child.audit(source=m)['status']=='PROVED'


def test_default_limits_fail_without_partial_authority(m):
    x=m.vars.by_name('x')
    parent=m.check.linear(x <= 10,residual='bounds')
    with pytest.raises(ProofBudgetExceeded):
        with m.check.batch(max_work=1) as b:
            b.linear(x <= 10,residual='bounds',result_key='x')
            b.run()
    assert parent.ok
    with pytest.raises(ProofBudgetExceeded):
        with m.check.batch(time_limit=1e-12) as b:pass
    for option in ({'max_nodes':float('inf')},{'max_work':None}):
        with pytest.raises(ValueError):m.check.batch(**option)


def test_random_exact_certificates_against_finite_oracle(tmp_path):
    from itertools import product
    import random
    path=tmp_path/'oracle.lp'
    path.write_text('Minimize\n obj: a\nSubject To\n cap: a + b + c + d <= 2\nBinary\n a b c d\nEnd\n')
    rng=random.Random(711)
    with mf.read(path) as model:
        variables=[model.vars.by_name(n) for n in 'abcd']
        cap=model.rows.by_name('cap').ub_claim
        cases=[]
        with model.check.batch() as batch:
            for i in range(120):
                coefficients=[Fraction(rng.randrange(-8,9),rng.choice([1,3,7])) for _ in variables]
                if i%20==0:coefficients[0]*=2**70
                rhs=Fraction(rng.randrange(-8,17),3)
                expr=sum((c*v for c,v in zip(coefficients,variables)),model.linexpr())
                assumption=(rng.randrange(4),rng.randrange(2)) if i%3==0 else None
                assume=[] if assumption is None else [variables[assumption[0]].eq(assumption[1])]
                batch.linear(expr <= rhs,using=[(rng.randrange(4),cap)],assume=assume,residual='bounds',result_key=str(i))
                cases.append((coefficients,rhs,assumption))
            results=batch.run()
        for i,(coefficients,rhs,assumption) in enumerate(cases):
            proof=results[str(i)]
            if not proof.ok:continue
            for point in product((0,1),repeat=4):
                if sum(point)>2 or assumption is not None and point[assumption[0]]!=assumption[1]:continue
                assert sum(c*x for c,x in zip(coefficients,point))<=rhs
        replayed=mf.BatchProof.replay(results['0'].batch.to_spec(),model=model)
        assert tuple(replayed._status)==tuple(results['0'].batch._status)


def test_v2_graph_compiles_before_execution(m):
    f=facts(m);x=m.vars.by_name('y')
    def node(rule,targets,certificate=None,parents=(),recipe=None):
        return dict(schema='mipframe-certificate-node-2',snapshot=f.uid,revision=f.revision,
                    rule=rule,targets=targets,certificate=certificate,parents=list(parents),recipe=recipe or {})
    targets=dict(ids=[x.id],kind='var',sides='ub',rhs=[1])
    nodes=[node('bound',targets),node('round',targets,parents=[0],recipe=dict(positions=[0]))]
    spec=dict(schema='mipframe-certificate-2',snapshot=f.uid,revision=f.revision,nodes=nodes,root=1)
    replayed=mf.BatchProof.replay(spec,model=m).require()
    assert replayed._rule=='check_graph'
    assert len(replayed._graph.tasks)==2
    assert not replayed._parents
    assert set(replayed.dependencies())>={('var',x.id,'ub'),('integer',x.id,'kind')}
    spec['nodes'][0]['targets']=dict(targets,rhs=[0])
    assert not mf.BatchProof.replay(spec,model=m).ok


def test_round_target_provenance_tampering(m):
    x=m.vars.by_name('y');y=m.vars.by_name('z')
    proof=m.check.bound(vars=[x],values=[1]).round_many(ub=1).require()
    spec=proof.to_spec()
    node=spec['nodes'][spec['root']]
    task=node['recipe']['tasks'][node['recipe']['roots'][0]]
    task[4][0]=y.id
    with pytest.raises((ValueError,mf.InvalidCertificateError)):
        mf.BatchProof.replay(spec,model=m)


def test_finite_nested_budget_and_retained_node_cap(m):
    for options in (dict(max_tasks=1.5),dict(max_work=True),dict(time_limit=None)):
        with pytest.raises(ValueError):m.check.batch(**options)
    budget=m.check.budget(max_nodes=2)
    with budget:
        with budget:
            first=m.check.identity(0,0)
        second=m.check.identity(0,0)
        with pytest.raises(ProofBudgetExceeded,match='nodes'):
            m.check.identity(0,0)
    assert first.ok and second.ok
