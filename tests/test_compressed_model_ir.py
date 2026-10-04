"""Independent oracles for the public factual compressed model map."""
from fractions import Fraction as Q
import json
from pathlib import Path

import numpy as np
from external_models import model_path

import pytest
import mipframe as mf
from mipframe.ir.compressed import CompressedIR
from mipframe.ir.compressed import NEAR_ZERO
from mipframe.frame.row import Formula


TEXT = '''Minimize
 obj: 3 + 2x - 2y + 0z + 0unused
Subject To
 link: x - y = 0
 rhs: 2x + 0.0000000001z >= -4
 box: -2 <= z <= 5
 separate: 4w <= 8
 empty: x - x = 0
Bounds
 x free
 y <= 10
 z >= -3
 w = 2
 unused free
Binaries
 b
Generals
 y
End
'''


def read_text(tmp_path, text=TEXT, name='model.lp'):
    path = tmp_path/name
    path.write_text(text)
    return mf.read(path)


@pytest.mark.parametrize('source', ['lp','mps','native'])
def test_model_family_properties_reuse_loaded_ir(tmp_path, monkeypatch, source):
    from mipframe.ir.refinement_store import RefinementStore
    if source=='native':
        with read_text(tmp_path) as original: saved=original.save(tmp_path/'saved')
        m=mf.load(saved)
    elif source=='mps': m=mf.read(Path(__file__).resolve().parents[1]/'examples/data/tiny.mps')
    else: m=read_text(tmp_path)
    rows,variables=m.row_families,m.var_families
    monkeypatch.setattr(RefinementStore,'_round',lambda *a: pytest.fail('must reuse completed refinement'))
    try:
        assert m.row_families.to_records()==rows.to_records()
        assert m.var_families.to_records()==variables.to_records()
        assert set(rows.column('depth'))==set(variables.column('depth'))=={2}
        assert m.family('F0').one().family_key==rows.head(1).one().family_key
    finally: m.close()
    with pytest.raises(mf.MIPFrameError): rows.count()


def test_eager_schema_facts_exact_numerics(tmp_path):
    with read_text(tmp_path) as m:
        assert isinstance(m.__dict__['_ir'], CompressedIR)
        ir=m.ir
        assert ir.schema_version=='0.3'
        assert ir.size == dict(variables=6,rows=5,nonzeros=6)
        assert ir.matrix['density']==Q(1,5)
        assert ir.matrix['row_nnz']['min']==0
        assert ir.matrix['row_nnz']['median']==1
        assert ir.matrix['row_nnz']['p90']==2
        assert ir.matrix['column_nnz']['median']==1
        assert ir.domains['variable_types']=={'B':1,'I':1,'C':4}
        assert ir.domains['row_senses']=={'<=':1,'=':2,'>=':1,'range':1}
        a=ir.numerics['absolute_coefficients']
        assert (a['min'],a['median'],a['max'])==(Q(1,10**10),Q(1),Q(4))
        assert ir.numerics['coefficient_dynamic_range']==4*10**10
        assert ir.numerics['near_zero_coefficients']['count']==1
        assert ir.numerics['near_zero_coefficients']['threshold']==NEAR_ZERO
        rhs=ir.numerics['nonzero_rhs']
        assert (rhs['count'],rhs['min'],rhs['max'])==(4,-4,8)
        obj=ir.objective
        assert (obj['sense'],obj['constant'],obj['nonzeros'],obj['kind'])==('min',3,2,'varying')
        assert obj['sign_counts']==dict(positive=1,negative=1,zero=4)
        assert sum(r['fraction'] for r in obj['role_support'])==1
        assert {m.vars.loc[i].name for i in m.vars.where(mf.col('obj') != 0).ids}=={'x','y'}
        assert len(m.terms().where((abs(mf.col('coef')) > 0) & (abs(mf.col('coef')) <= NEAR_ZERO)))==1
        assert sum(ir.domains['bounds'].values())==6
        assert all(item['kind'] in ('fact','structural_abstraction') for item in ir.attention)
        assert isinstance(ir.show(),Formula)
        assert 'link' not in str(ir.show())
        assert 'signature_id' not in repr(ir)
        assert 'depth=2' in repr(ir)


def test_machine_export_independent_exact_json_and_lifecycle(tmp_path):
    m=read_text(tmp_path)
    ir=m.ir
    snapshot=ir.to_dict()
    assert dict(ir)==snapshot
    snapshot['size']['rows']=999
    assert ir.size['rows']==5
    snapshot=ir.to_dict()
    exported=json.loads(ir.to_json())
    assert exported['numerics']['absolute_coefficients']['min']=='1/10000000000'
    assert exported['matrix']['density']=='1/5'
    pretty=[]
    class Printer:
        def text(self,text): pretty.append(text)
    ir._repr_pretty_(Printer(),False)
    assert pretty==[str(ir.show())]
    iterator=iter(ir)
    next(iterator)
    m.close()
    assert snapshot['size']['rows']==5
    for operation in (lambda:m.ir,lambda:ir.size,lambda:ir['model'],lambda:repr(ir),lambda:ir.show(),
                      lambda:ir.to_dict(),lambda:ir.to_json(),lambda:len(ir),lambda:next(iterator)):
        with pytest.raises(mf.MIPFrameError,match='closed'):
            operation()


def test_topology_independent_traversal_and_typed_queries(tmp_path):
    with read_text(tmp_path) as m:
        ir=m.ir
        graph={('r',i):set() for i in m.rows.ids}
        graph.update({('v',i):set() for i in m.vars.ids})
        for r in m.rows.ids:
            for v,c in m.model.iter_terms(r):
                graph['r',r].add(('v',v)); graph['v',v].add(('r',r))
        remaining=set(graph)
        expected=[]
        while remaining:
            stack=[next(iter(remaining))]; found=set()
            while stack:
                node=stack.pop()
                if node in found: continue
                found.add(node); stack.extend(graph[node]-found)
            remaining-=found
            expected.append(found)
        top=ir.topology
        assert top['connected_components']==5
        assert top['isolated_variables']==2 and top['isolated_rows']==1
        membership = mf.ops.sets.components(m.terms(), set="row", member="var")
        actual = []
        for component in {r.component for r in membership}:
            V = membership.where(mf.col("component") == component).refs("var")
            R = V.rows
            actual.append({('r', r) for r in R.ids} | {('v', v) for v in V.ids})
        actual.extend({('r', r)} for r in m.rows.where(mf.col("nnz") == 0).ids)
        assert {frozenset(s) for s in actual} == {frozenset(s) for s in expected}
        observed = sorted((c['rows'], c['variables'], c['nonzeros']) for c in top['components'])
        independent = sorted((
            sum(axis == 'r' for axis, _ in nodes),
            sum(axis == 'v' for axis, _ in nodes),
            sum(len(graph[node]) for node in nodes if node[0] == 'r'),
        ) for nodes in expected)
        assert observed == independent
        assert top['largest_component']['nonzeros']==5
        assert top['largest_component']['nonzero_fraction']==Q(5,6)
        assert len(m.vars.where(mf.col('nnz') == 0))==2
        assert len(m.rows.where(mf.col('nnz') == 0))==1
        assert sum(c['row_fraction'] for c in top['components'])==1
        assert sum(c['var_fraction'] for c in top['components'])==1


def test_structure_matches_typed_queries_without_handles(tmp_path):
    with read_text(tmp_path) as m:
        ir=m.ir
        s=ir.structure
        assert s['mode']=='template' and s['depth']==2 and s['variable_role_depth']==1
        assert [r['depth'] for r in s['overview']]==[0,1,2]
        for level in s['overview']:
            assert level['row_roles']==len(m.families(kind='row',depth=level['depth']))
            assert level['var_roles']==len(m.families(kind='var',depth=level['depth']))
        assert sum(r['count'] for r in s['variable_roles'])==len(m.vars)
        assert sum(r['count'] for r in s['row_families'])==len(m.rows)
        for kind,records in [('var',s['variable_roles']),('row',s['row_families'])]:
            assert [r['count'] for r in records]==sorted((r['count'] for r in records),reverse=True)
            for r in records:
                family=m.family(r['id'], depth=r['depth']).one()
                assert r['count']==family.count
                assert r['parent']==family.parent
                assert r['signature_id'].startswith('rf:2:' if kind=='row' else 'vr:1:')
                if kind=='row':
                    for role in r['structure']:
                        assert role['var_role_depth'] == family.depth - 1
                        assert len(m.family(role["var_role"], depth=role["var_role_depth"]).vars)>0
                    assert r['parameters']['varying_coefficient_roles']==[r.role for r in m.family(r['id'],depth=r['depth']).related('parameters').where(basis='canonical',orientation=0,quantity='coefficient',varying=True) if r.role not in ('lb','ub')]
        for delta in s['refinements']:
            live=m.refinement(delta['from_depth'],delta['to_depth'],kind=delta['kind'])
            assert delta['split_count']==live.where(unchanged=False).select('parent_key').distinct().count()
            unchanged=live.where(unchanged=True)
            assert delta['unchanged']==dict(families=unchanged.count(),members=unchanged.column('count').sum() or 0)
            assert sum(split['count'] for split in delta['splits'])+delta['unchanged']['members']==(len(m.rows) if delta['kind']=='row' else len(m.vars))
            for split in delta['splits']:
                assert sum(c['count'] for c in split['children'])==split['count']
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    assert 'handle' not in key
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(ir.to_dict())
        with pytest.raises(AttributeError):
            getattr(ir, 'resolve')


@pytest.mark.parametrize('text,nr,nv,nz',[
    ('Minimize\n o: 4\nSubject To\nEnd\n',0,0,0),
    ('Minimize\n o: 4 + 0x\nSubject To\nEnd\n',0,1,0),
    ('Minimize\n o: 0\nSubject To\n z: 0=0\nEnd\n',1,0,0),
    ('Maximize\n o: -x-y\nSubject To\n a: x+y<=2\nEnd\n',1,2,2),
])
def test_empty_axes_and_uniform_objective(tmp_path,text,nr,nv,nz):
    with read_text(tmp_path,text) as m:
        ir=m.ir
        assert m.shape==(nr,nv)
        display = repr(m)
        assert f"variables    : {nv}" in display
        assert f"constraints  : {nr}" in display
        types = f"C {nv}" if nv else "—"
        assert f"var types    : {types}" in display
        if not nz:
            assert "density      : 0" in display
            assert "objective nz : 0" in display
        assert ir.size['nonzeros']==nz
        assert ir.matrix['density']==(_fraction(nz,nr*nv))
        if not nz:
            assert ir.numerics['coefficient_dynamic_range'] is None
            assert ir.numerics['absolute_coefficients']['kind']=='empty'
            assert ir.topology['connected_components']==nr+nv
            assert ir.objective['kind']=='constant'
        else:
            assert ir.objective['kind']=='uniform'
            assert ir.objective['coefficient_profile']['value']==-1
            assert 'constant(-1)' in repr(ir)
        assert 'MIPFrame compressed IR' in repr(ir)


def _fraction(n,d): return Q(n,d) if d else Q(0)


def test_save_load_stable_machine_ir_reuses_refinement(tmp_path,monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('Load must reuse published refinement')
    for source in ('tiny.lp','tiny.mps'):
        with mf.read(Path(__file__).resolve().parents[1]/'examples'/'data'/source) as m:
            expected=m.ir.to_dict()
            path=m.save(tmp_path/source)
        from mipframe.ir.refinement_store import RefinementStore
        with monkeypatch.context() as patch:
            patch.setattr(RefinementStore,'_round',forbidden)
            patch.setattr(RefinementStore,'_prepare',forbidden)
            with mf.load(path) as m:
                assert isinstance(m.__dict__['_ir'],CompressedIR)
                assert m.ir.to_dict()==expected
                record = expected['structure']['row_families'][0]
                assert m.family(f"{record['id']}@{record['depth']}").rows.ids is not None


def test_display_budget_does_not_truncate_machine_ir(tmp_path,monkeypatch):
    import random
    rng=random.Random(12)
    text='Minimize\n o: '+' + '.join(f'{i+1}x{i}' for i in range(20))+'\nSubject To\n'
    text+='\n'.join(f'r{i}: '+ ' + '.join(f'{rng.choice([-1,1])}x{j}' for j in rng.sample(range(20),rng.randint(1,5))).replace('+ -','- ') + '<=1' for i in range(40))+'\nEnd\n'
    with read_text(tmp_path,text) as m:
        original=m.ir.to_dict()
        assert len(original['structure']['row_families'])>3
        monkeypatch.setattr(mf.options.ir,'max_row_families',3)
        monkeypatch.setattr(mf.options.ir,'max_var_roles',2)
        monkeypatch.setattr(mf.options.ir,'max_splits',1)
        monkeypatch.setattr(mf.options.ir,'max_attention',0)
        monkeypatch.setattr(mf.options.display,'max_rows',1)
        monkeypatch.setattr(mf.options.display,'max_cols',1)
        from mipframe.store.model_store import NumericalStore
        monkeypatch.setattr(NumericalStore,'num',lambda *args:pytest.fail('rendering must not decode data'))
        text=str(m.ir.show())
        assert 'showing 3 of ' in text and 'showing 2 of ' in text
        assert 'families omitted → m.row_families' in text
        assert 'shown families cover' in text and 'singleton families:' in text
        assert 'items omitted' in text
        assert m.ir.to_dict()==original
        assert len(text.splitlines())<110


def test_display_option_validation(monkeypatch):
    for field in ['max_var_roles', 'max_row_families', 'max_splits', 'max_attention']:
        for bad in (True,1.5,'2'):
            with pytest.raises(TypeError): setattr(mf.options.ir,field,bad)
        with pytest.raises(ValueError): setattr(mf.options.ir,field,-1)
        monkeypatch.setattr(mf.options.ir,field,0)
        assert getattr(mf.options.ir,field)==0


def test_creation_failure_releases_store_and_temporary(tmp_path,monkeypatch):
    from mipframe.store.model_store import NumericalStore
    paths=[]; closed=[]
    original=NumericalStore.close
    def close(store):
        closed.append(store)
        return original(store)
    def fail(ir):
        paths.append(ir._owner.model.path)
        raise RuntimeError('injected IR failure')
    monkeypatch.setattr(NumericalStore,'close',close)
    monkeypatch.setattr(CompressedIR,'_build',fail)
    with pytest.raises(RuntimeError,match='injected'):
        read_text(tmp_path)
    assert len(closed)==1
    assert paths and not paths[0].exists()


def test_sparse_large_shape_bounded_render_no_dense_or_scipy(tmp_path,monkeypatch):
    # Matrix area is 1,000,000, whereas the graph has only 1,000 edges.
    n=1000
    text='Minimize\n o: 0\nSubject To\n'+'\n'.join(f'r{i}: x{i}<=1' for i in range(n))+'\nEnd\n'
    from mipframe.ops.matrix import MatrixView
    monkeypatch.setattr(MatrixView,'to_scipy',lambda *a:pytest.fail('IR does not export'))
    monkeypatch.setattr(MatrixView,'preview',lambda *a,**k:pytest.fail('IR does not preview'))
    for method in ('empty','zeros','ones','full'):
        original=getattr(np,method)
        def checked(shape,*args,_original=original,**kwargs):
            assert (np.prod(shape) if isinstance(shape, tuple) else shape) <= 4*n
            return _original(shape,*args,**kwargs)
        monkeypatch.setattr(np,method,checked)
    with read_text(tmp_path,text) as m:
        assert m.ir.topology['connected_components']==n
        assert m.ir.size['nonzeros']==n
        assert len(m.ir.structure['row_families'])==1
        assert len(m.ir.structure['variable_roles'])==1
        assert len(str(m.ir.show()).splitlines())<85
        data=m.ir.to_json()
        assert 'r999' not in data and 'x999' not in data


def test_danoint_matches_requested_structural_map():
    source=model_path("danoint.lp")
    with mf.read(source) as m:
        ir=m.ir
        assert (ir.size['variables'],ir.size['rows'],ir.size['nonzeros'])==(521,664,3232)
        assert ir.numerics['absolute_coefficients']['min']==Q(1,2)
        assert ir.numerics['absolute_coefficients']['max']==Q(133,2)
        assert [(level['row_roles'],level['var_roles']) for level in ir.structure['overview']]==[(4,4),(10,7),(11,7)]
        assert ir.objective['role_support']==[dict(id='V0',depth=1,count=1,fraction=Q(1))]
        roles={r['id']:r for r in ir.structure['variable_roles']}
        assert [roles[f'V{i}']['count'] for i in range(7)]==[1,56,56,56,336,8,8]
        assert [roles[f'V{i}']['degree']['value'] for i in range(7)]==[64,12,3,5,6,2,2]
        f=ir.structure['row_families'][0]
        assert f['id']=='F0' and f['count']==336 and f['parent']=='F0'
        assert [(r['sign'],r['var_role'],r['multiplicity']['value']) for r in f['structure']]==[('-','V1',1),('+','V4',1)]
        row_delta=next(d for d in ir.structure['refinements'] if d['kind']=='row' and d['to_depth']==2)
        assert row_delta['split_count']==1 and row_delta['unchanged']==dict(families=9,members=272)
        split=row_delta['splits'][0]
        assert split['parent']=='F0' and split['count']==392
        assert [(c['id'],c['count']) for c in split['children']]==[('F0',336),('F4',56)]
        variable_delta=next(d for d in ir.structure['refinements'] if d['kind']=='var' and d['to_depth']==1)
        assert variable_delta['splits'][0]['parent']=='V2'
        assert sorted(c['count'] for c in variable_delta['splits'][0]['children'])==[8,56,56,336]
        assert ir.topology['connected_components']==1
        text=str(ir.show())
        assert 'density: 9.34e-3' in text
        assert 'F0 (392)' in text and '9 families unchanged (272 members)' in text
        assert len(text.splitlines())<110


def test_varying_degree_and_coefficient_parameters(tmp_path):
    rows=[f'a{i}: {i+1}x <= 1' for i in range(5)] + [f'b{i}: {i+1}y <= 1' for i in range(8)]
    with read_text(tmp_path,'Minimize\n o: 0\nSubject To\n'+'\n'.join(rows)+'\nEnd\n') as m:
        roles=m.ir.structure['variable_roles']
        assert len(roles)==1
        assert roles[0]['degree']['kind']=='varying'
        assert (roles[0]['degree']['min'],roles[0]['degree']['max'],roles[0]['degree']['mode'])==(5,8,5)
        assert '5..8 (mode 5)' in repr(m.ir)
        family=m.ir.structure['row_families'][0]
        assert family['parameters']['varying_coefficient_roles']==['+V0']
        assert family['structure'][0]['multiplicity']==dict(kind='constant',value=1)
        assert 'histogram' not in repr(m.ir)


def test_ir_variation_summary_leaves_histograms_lazy(tmp_path, monkeypatch):
    import mipframe.ir.family_attributes as numeric

    text = ('Minimize\n o:0\nSubject To\n'
            ' a:2x+3y<=7\n b:4x+3y<=9\n c:-2x-3y>=-7\nEnd\n')
    with monkeypatch.context() as patch:
        patch.setattr(numeric, '_build',
                      lambda *args: pytest.fail('IR summary built a full histogram'))
        m = read_text(tmp_path, text)
    with m:
        for record in m.ir.structure['row_families']:
            family = m.family(record['id'], depth=record['depth'])
            expected = [r.role for r in family.related('parameters').where(basis='canonical',orientation=0,quantity='coefficient',varying=True)
                        if r.role not in ('lb', 'ub')]
            assert record['parameters']['varying_coefficient_roles'] == expected


def test_extreme_numeric_range_rendering_is_bounded_and_exact(tmp_path):
    text='Minimize\n o: 1e400x\nSubject To\n a: 1e-400x + 1e400y<=1\nEnd\n'
    with read_text(tmp_path,text) as m:
        assert m.ir.numerics['absolute_coefficients']['min']==Q(1,10**400)
        assert m.ir.numerics['coefficient_dynamic_range']==10**800
        rendered=repr(m.ir)
        assert '1.00e+800' in rendered and '1.00e-400' in rendered
        assert max(map(len,rendered.splitlines()))<200
        assert json.loads(m.ir.to_json())['numerics']['coefficient_dynamic_range']==str(10**800)


def test_optional_depth_and_owner_closes_eager_query_resources(tmp_path):
    import sqlite3
    with read_text(tmp_path) as m:
        for depth in (0,1,3):
            extra=mf.CompressedIR(m,depth=depth)
            assert extra.structure['depth']==depth
            assert len(extra.structure['overview'])==depth+1
            assert len(extra.structure['refinements'])==2*depth
            assert 'MIPFrame compressed IR' in repr(extra)
        eager=m.ir
        dbs=[store.db for store in m._refinement_store.family_stores]
        assert eager._data
    assert eager._data=={}
    for db in dbs:
        with pytest.raises(sqlite3.ProgrammingError): db.execute('SELECT 1')
