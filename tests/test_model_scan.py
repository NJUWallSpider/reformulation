"""Independent oracles and no-analysis contracts for the initial model scan."""
from collections import Counter
from fractions import Fraction as Q
import json
from pathlib import Path
import random

import pytest
import mipframe as mf
from mipframe.store.scan import HeavyHitters, Scan


def forbidden(*a, **kw):
    pytest.fail('Default scan started structural analysis')


@pytest.mark.parametrize('suffix', ['lp','mps'])
def test_read_load_exports_never_analyze(suffix,tmp_path,monkeypatch):
    from mipframe.ir.refinement_store import RefinementStore
    from mipframe.ir.compressed import CompressedIR
    monkeypatch.setattr(RefinementStore,'compute',forbidden)
    monkeypatch.setattr(CompressedIR,'_topology',forbidden)
    monkeypatch.setattr(mf.Model,'indexed',forbidden)
    with mf.read(Path(__file__).parents[1]/'examples/data'/f'tiny.{suffix}') as m:
        expected=m.ir.to_dict()
        assert not {'structure','topology','indexed'} & expected.keys()
        assert 'role_support' not in expected['objective']
        assert dict(m.ir)==expected
        assert 'Local row shapes' in repr(m.ir)
        saved=m.save(tmp_path/'native')
        assert not (saved/'refinement.sqlite').exists()
        assert not (saved/'indexing.sqlite').exists()
    with mf.load(saved) as m:
        assert m.ir.to_dict()==expected
        assert json.loads(m.ir.to_json())['schema_version']=='0.4'
        m.model.db.set_trace_callback(forbidden)
        assert m.ir.to_dict()==expected
        repr(m.ir)
        dict(m.ir.objective)
        m.ir.to_json()
        m.model.db.set_trace_callback(None)


def test_independent_analyses_cached_and_default_unchanged(make_model,monkeypatch):
    from mipframe.ir.compressed import CompressedIR
    m=make_model('Minimize\n o:x\nSubject To\n r:x-y<=0\nEnd\n')
    expected,display=m.ir.to_dict(),repr(m.ir)
    with monkeypatch.context() as patch:
        patch.setattr(mf.Model,'indexed',forbidden)
        patch.setattr(CompressedIR,'_topology',forbidden)
        structure=m.ir.structure
        roles=m.ir.objective['role_support']
        assert structure['depth']==2 and roles
    with monkeypatch.context() as patch:
        from mipframe.ir.refinement_store import RefinementStore
        patch.setattr(RefinementStore,'compute',forbidden)
        assert m.ir.structure==structure
        assert m.ir.objective['role_support']==roles
        assert m.ir.topology['connected_components']==1
        patch.setattr(CompressedIR,'_topology',forbidden)
        assert m.ir.topology['connected_components']==1
    complete=m.ir.to_dict(include_analysis=True)
    assert complete['structure']==structure
    assert complete['indexed']['status']=='complete'
    assert complete['objective']['role_support']==roles
    assert m.ir.to_dict()==expected and repr(m.ir)==display


def test_shape_direction_exact_raw_multisets_and_navigation(make_model):
    m=make_model('Minimize\n o:2x-2x+3z\nSubject To\n'
        ' a_1:x-y<=0\n a_2:z-x<=0\n b:2x-2y<=0\n c:-x+y>=0\n'
        ' d:x-x=0\n e:-2<=x+y<=3\nBounds\n z free\nEnd\n')
    s=m.ir.to_dict()
    assert s['size']['nonzeros']==10
    assert s['objective']['nonzeros']==1
    exact=m.ir.row_shapes()
    assert sum(exact.column('count'))==6
    pair=exact.where(sense='<=',nnz=2,count=2)
    assert {r.name for r in pair.rows}=={'a_1','a_2'}
    coefficients=pair.related('coefficients').to_records()
    assert {(r['coefficient'],r['count']) for r in coefficients}=={(Q(1),1),(Q(-1),1)}
    assert pair.select(key=mf.col('group_key')).rows.count()==2
    assert pair.head(0).rows.count()==0
    assert m.ir.row_shapes() is exact
    with pytest.raises(KeyError): pair.vars
    observed={}
    for row in m.rows:
        key=(row.lb,row.ub,tuple(sorted(Counter(t.coef for t in row.terms).items())))
        observed[key]=observed.get(key,0)+1
    assert sorted(exact.column('count'))==sorted(observed.values())


def test_name_templates_slots_and_selected_members(make_model):
    m=make_model('Minimize\n o:0\nSubject To\n'
        ' start_1:x[12,3]+x[13,3]<=1\n start_2:x[12,4]<=2\n finish_2:x_12_3<=3\nEnd\n')
    rows=m.ir.name_templates('row')
    assert rows.where(template='start_{int}').rows.count()==2
    variables=m.ir.name_templates('var')
    square=variables.where(template='x[{int},{int}]')
    assert square.vars.count()==3
    slots=square.related('slots').to_records()
    assert [(r['minimum'],r['maximum'],r['distinct']) for r in slots]==[(12,13,2),(3,4,2)]
    assert variables.where(template='x_{int}_{int}').vars.count()==1
    assert m.ir.names['convention']=='lexical hypothesis; not structural evidence'
    with pytest.raises(ValueError): m.ir.name_templates('rows')


def test_weighted_heavy_hitters_error_bounds_and_heap_storage():
    rng=random.Random(7)
    tracker=HeavyHitters(7)
    oracle=Counter()
    for _ in range(5000):
        value,weight=rng.randrange(80),rng.randrange(1,7)
        tracker.add(value,weight);oracle[value]+=weight
        assert len(tracker.heap)<=tracker.capacity
    summary=tracker.summary()
    assert summary['total_count']==sum(oracle.values())
    assert summary['total_groups'] is None and not summary['ranking_exact']
    for e in summary['items']:
        assert e['count_lower']<=oracle[e['value']]<=e['count']
    for key in oracle.keys()-tracker.entries.keys():
        assert oracle[key]<=summary['omitted_count_upper']


def test_overflow_is_marked_and_exact_directory_recovers(make_model,monkeypatch):
    original=Scan.__init__
    def tiny(self,memory_mb=128): original(self,0.125)
    monkeypatch.setattr(Scan,'__init__',tiny)
    m=make_model('Minimize\n o:0\nSubject To\n' +
        ''.join(f' family{i}_1:{i+1}x<=1\n' for i in range(40))+'End\n')
    data=m.ir.to_dict()
    shapes=data['local_row_shapes']
    assert shapes['total_count']==40 and shapes['total_groups'] is None
    assert all(e['count_lower']<=1<=e['count'] for e in shapes['items'])
    assert m.ir.row_shapes().count()==40
    assert data['numerics']['absolute_coefficients']['min']==1
    assert data['numerics']['absolute_coefficients']['max']==40
    assert data['numerics']['sign_counts']==dict(positive=40,negative=0)
    assert data['numerics']['common_coefficients']['complete'] is False


def test_wide_shapes_are_candidates_not_exact_equalities(make_model,monkeypatch):
    original=Scan.__init__
    def tiny(self,memory_mb=128): original(self,0.125)
    monkeypatch.setattr(Scan,'__init__',tiny)
    terms=' + '.join(f'{i+1}x{i}' for i in range(100))
    m=make_model(f'Minimize\n o:0\nSubject To\n a:{terms}<=1\n b:{terms}<=1\nEnd\n')
    e=m.ir.local_row_shapes['items'][0]
    assert e['candidate'] and not e['exact'] and e['count_lower']==0
    assert not e['coefficients_complete']
    assert m.ir.row_shapes().one().count==2
    assert m.ir.row_shapes().related('coefficients').count()==100


def test_names_unprofiled_and_slot_distinct_lower_bounds(make_model):
    m=make_model('Minimize\n o:0\nSubject To\n' +
        ''.join(f'r{i}:x{i}<=1\n' for i in range(80))+' a'+('b'*600)+':x0<=1\nEnd\n')
    names=m.ir.names
    assert names['rows']['unprofiled']==1
    assert names['rows']['total_groups'] is None
    slot=names['vars']['items'][0]['slots'][0]
    assert not slot['distinct_exact'] and slot['distinct_lower_bound']==33
    assert (slot['min'],slot['max'])==(0,79)
    assert m.ir.name_templates('var').related('slots').one().distinct==80


def test_character_budget_and_detached_results(make_model,monkeypatch):
    m=make_model('Minimize\n o:x\nSubject To\n r:x<=1\nEnd\n')
    original=m.ir.to_dict()
    monkeypatch.setattr(mf.options.ir,'max_chars',120)
    assert len(repr(m.ir))<=120
    assert m.ir.to_dict()==original
    directory=m.ir.row_shapes()
    detached=directory.to_records()
    m.close()
    assert detached
    with pytest.raises(mf.MIPFrameError): directory.count()


def test_canonical_binary_bounds_and_cancelled_terms(make_model):
    m=make_model('Minimize\n o:0x+0y+0z\nSubject To\n r:x-x+y<=0\nBounds\n x=0\n y=1\n 0<=z<=1\nGenerals\n x y z\nEnd\n')
    d=m.ir.domains
    assert d['variable_types']==dict(B=3,I=0,C=0)
    assert d['unit_interval']==1 and d['bounds']['fixed']==2
    assert m.ir.size['nonzeros']==1
    assert m.ir.presolve_cues['isolated_cols']==2


def test_exact_directory_serialization_and_failed_publication(make_model,monkeypatch):
    from mipframe.ir.scan_views import Directory
    m=make_model('Minimize\n o:x_1\nSubject To\n r_1:x_1-x_2<=0\n r_2:2x_1<=3\nEnd\n')
    original=Directory._build
    stores=[]
    def fail(self):
        stores.append(self.partition)
        original(self)
        raise RuntimeError('interrupted exact directory')
    with monkeypatch.context() as patch:
        patch.setattr(Directory,'_build',fail)
        with pytest.raises(RuntimeError,match='interrupted'): m.ir.row_shapes()
    assert all(s.closed for s in stores)
    assert 'row_shapes' not in m.ir._directories
    for view in (m.ir.row_shapes().where(nnz=2).related('members'),
                 m.ir.name_templates('var').related('slots')):
        restored=mf.frame_from_spec(view.to_spec(),models={m.uid:m})
        assert restored.to_records()==view.to_records()


def test_compact_frequency_spill_cleanup_and_exact_extrema(make_model,monkeypatch):
    original=Scan.prepare_numbers
    files=[]
    def track(self,count):
        original(self,count)
        if self._coefficient_file is not None: files.append(self._coefficient_file)
    monkeypatch.setattr(Scan,'prepare_numbers',track)
    m=make_model('Minimize\n o:0\nSubject To\n' +
        ''.join(f'r{i}:{i+1}x<=1\n' for i in range(300))+'End\n')
    # Explicit construction with the minimum query budget forces the numeric-ID
    # vector to scratch mmap; the numerical store remains independent.
    ir=mf.CompressedIR(m,memory_mb=0.015625)
    assert files and all(f.closed for f in files)
    assert ir.numerics['absolute_coefficients']['min']==1
    assert ir.numerics['absolute_coefficients']['max']==300
    assert ir.numerics['sign_counts']['positive']==300
    assert ir.size['nonzeros']==300


def test_free_and_ranged_rows_empty_objective(make_model):
    m=make_model('Minimize\n o:0\nSubject To\n r:x<=3\n s:y<=2\nEnd\n')
    edit=m.edit()
    edit.set_row_bounds(m.rows.by_name('r'),lb=-float('inf'),ub=float('inf'))
    edit.set_row_bounds(m.rows.by_name('s'),lb=-2,ub=3)
    with edit.build() as n:
        assert n.ir.domains['row_senses']=={'<=':0,'=':0,'>=':0,'free':1,'range':1}
        assert n.ir.numerics['nonzero_rhs']['count']==2
        assert n.ir.objective['nonzeros']==0
        assert n.ir.row_shapes().where(sense='free').one().lb is None
        assert n.ir.row_shapes().where(sense='range').one().ub==3


def test_membership_probe_does_not_compute_legacy_fields(make_model,monkeypatch):
    m=make_model('Minimize\n o:x\nSubject To\n r:x<=1\nEnd\n')
    monkeypatch.setattr(m.ir,'_build_structure',forbidden)
    assert 'structure' not in m.ir
    assert 'topology' not in m.ir.keys()


def test_truncated_exact_histogram_omission_bound():
    h=HeavyHitters(100)
    for key in range(50): h.add(key,key+1)
    summary=h.summary(limit=8)
    assert summary['ranking_exact'] and not summary['complete']
    assert summary['total_groups']==50
    shown={e['value'] for e in summary['items']}
    assert all(n+1<=summary['omitted_count_upper'] for n in range(50) if n not in shown)


def test_lexical_heavy_hitters_never_claim_complete_slots(make_model,monkeypatch):
    original=Scan.__init__
    def tiny(self,memory_mb=128): original(self,0.125)
    monkeypatch.setattr(Scan,'__init__',tiny)
    m=make_model('Minimize\n o:0\nSubject To\n'+''.join(
        f'{chr(65+i//26)}{chr(65+i%26)}_{i}:x<=1\n' for i in range(40))+'End\n')
    summary=m.ir.names['rows']
    assert not summary['complete'] and summary['total_groups'] is None
    for item in summary['items']:
        assert item['count_lower']<=1<=item['count']
        assert all(not s['complete'] and not s['distinct_exact'] for s in item['slots'])
    assert m.ir.name_templates().count()==40


def test_derived_numeric_ratio_beyond_python_integer_text_limit(make_model):
    m=make_model('Minimize\n o:0\nSubject To\n r:1e4000x+1e-4000y<=1\nEnd\n')
    assert m.ir.numerics['coefficient_dynamic_range']==10**8000
    assert '1.00e+8000' in repr(m.ir)
    result=json.loads(m.ir.to_json())
    assert result['numerics']['coefficient_dynamic_range']=='1'+'0'*8000


def test_zero_value_display_budget_does_not_claim_absence(make_model,monkeypatch):
    m=make_model('Minimize\n o:x\nSubject To\n r:x<=1\nEnd\n')
    monkeypatch.setattr(mf.options.ir,'max_values',0)
    assert 'common coefficients: (not shown)' in repr(m.ir)
    assert m.ir.objective['nonzeros']==1
