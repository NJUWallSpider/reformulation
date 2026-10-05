"""Public family relations replace object hierarchies without losing observations."""
from fractions import Fraction as Q
import json
import pytest
import mipframe as mf
from test_refinement import TEXT, read_text


@pytest.fixture
def model(tmp_path):
    with read_text(tmp_path) as m:
        yield m


@pytest.mark.parametrize('kind', ['row','var'])
@pytest.mark.parametrize('depth', [0,1,2,3])
def test_directories_members_and_stable_subset_identity(model,kind,depth):
    entities=model.rows if kind=='row' else model.vars
    directory=model.families(kind=kind,depth=depth)
    assert type(directory) is mf.Frame
    members=directory.related('members')
    assert sorted(r[kind].id for r in members)==list(entities.ids)
    assert sum(r.count for r in directory)==len(entities)
    selected=entities.iloc[::2].families(depth=depth)
    global_keys={r.id:r.family_key for r in directory}
    for row in selected:
        assert row.family_key==global_keys[row.id]
        assert row.count<=row.total_count
    assert set(getattr(selected,kind+'s').ids)==set(entities.iloc[::2].ids)
    assert directory.related('pattern').schema['role_depth'].kind=='int'
    if depth==0: assert directory.related('pattern').count()==0
    else: assert set(directory.related('pattern').column('role_depth'))=={depth-1}
    empty=entities.head(0).families(depth=depth)
    assert empty.count()==getattr(empty,kind+'s').count()==empty.related('parameters').count()==0


def test_model_defaults_single_selection_and_removed_objects(model):
    for name in ('Structure','StructuralSignatures','StructuralSignature','AlgebraicSignature','TypedSignature','ConstraintFamily','VariableFamily'):
        assert not hasattr(mf,name)
    assert not hasattr(model,'structure')
    assert not hasattr(model.rows,'family') and not hasattr(model.rows,'signature')
    assert not hasattr(model.rows.iloc[0],'signature')
    for kind,prefix in [('row','F'),('var','V')]:
        directory=getattr(model,kind+'_families')
        first=directory.head(1).one()
        single=model.family(first.id)
        assert type(single) is mf.Frame and single.one().depth==2
        assert single.one().family_key==first.family_key
        assert model.family(first.id+'@2').one().family_key==first.family_key
        with pytest.raises(KeyError): single.related('vars' if kind=='row' else 'rows')
    for bad in ('F999999','F-1','F01','X0',''):
        with pytest.raises(KeyError): model.family(bad)
    with pytest.raises(ValueError): model.family('F0@1',depth=2)
    for depth in (-1,True,1.5):
        with pytest.raises(ValueError): model.families(depth=depth)


def test_navigation_follows_selection_projection_join_and_scope(model):
    f=model.row_families
    chosen=f.head(1)
    expected=set(chosen.rows.ids)
    assert set(f.where(id=chosen.one().id).rows.ids)==expected
    assert set(chosen.select(key=mf.col('family_key')).rows.ids)==expected
    with pytest.raises(KeyError): chosen.select('id').rows
    duplicate=mf.Frame.from_records([{'family_key':chosen.one().family_key,'n':1},{'family_key':chosen.one().family_key,'n':2}],schema={'family_key':'str','n':'int'})
    joined=f.join(duplicate,on='family_key',cardinality='1:m')
    assert set(joined.rows.ids)==expected and len(joined.rows)==len(expected)
    with pytest.raises(KeyError): f.group_by('kind').agg(n=mf.count()).rows
    scoped=model.rows.iloc[1:].families().where(mf.col('count')>0)
    assert 0 not in scoped.rows.ids
    assert len(scoped.rows)==len(model.rows)-1


@pytest.mark.parametrize('method',['template','exact_structural','algebraic','typed','algebratic','exact_strctural'])
def test_advanced_families_and_signatures_are_frames(model,method):
    families=mf.analysis.families(model.rows,method=method)
    signatures=mf.analysis.signatures(model.rows,method=method)
    assert type(families) is type(signatures) is mf.Frame
    assert len(families.rows)==len(signatures)==len(model.rows)
    assert len(mf.analysis.signatures(model.rows.iloc[0],method=method))==1
    assert signatures.schema['signature_id'].kind=='str'
    for group in families:
        members=families.where(id=group.id).rows
        features=mf.analysis.signatures(members,method=method)
        assert len(set(features.column('signature_id')))==1
    if method in ('template','exact_structural','exact_strctural'):
        assert len(signatures.head(1).related('levels'))==3
    else:
        assert {r.row.id for r in signatures.head(1).related('pattern')}<={0}
        with pytest.raises(ValueError): mf.analysis.families(model.vars,method=method)
        with pytest.raises(ValueError): mf.analysis.families(model.rows,method=method,depth=2)


@pytest.mark.parametrize('method',['template','exact_structural'])
def test_refinement_cache_and_native_identity(tmp_path,method):
    with read_text(tmp_path) as m:
        original=mf.analysis.families(m.rows,method=method,depth=3)
        keys=list(original.column('family_key'))
        transitions=m.refinement(0,3,method=method)
        assert sum(r.count for r in transitions)==len(m.rows)
        for r in transitions:
            parent=transitions.where(child_key=r.child_key).related('before')
            child=transitions.where(child_key=r.child_key).related('after')
            assert set(child.rows.ids)<=set(parent.rows.ids)
        source=m.save(tmp_path/'saved')
        spec=json.loads(json.dumps(original.head(1).related('sources').where(basis='raw').to_spec()))
        data=original.head(1).related('sources').where(basis='raw').to_records()
    with mf.load(source) as m:
        assert list(mf.analysis.families(m.rows,method=method,depth=3).column('family_key'))==keys
        assert mf.frame_from_spec(spec,models={m.uid:m}).to_records()==data
        m.indexed(depth=3,method=method).verify().require()


@pytest.mark.parametrize('basis',['canonical','raw','scale_normalized'])
@pytest.mark.parametrize('kind',['row','var'])
def test_complete_sources_replay_original_coefficients_and_profiles(model,basis,kind):
    families=model.families(kind=kind)
    source=families.related('sources').where(basis=basis)
    for r in source:
        assert isinstance(r.factor,Q) and isinstance(r.scale,Q)
        if basis=='raw': assert r.factor==1
        elif basis=='canonical': assert abs(r.factor)==1
        else: assert abs(r.factor*r.scale)==1
    hist=families.related('histogram').where(basis=basis)
    summary=families.related('parameters').where(basis=basis)
    groups={}
    for r in hist:
        key=(r.family_key,r.orientation,r.role,r.quantity,r.coefficient)
        groups.setdefault(key,[]).append((r.value,r.count))
    for r in summary:
        values=groups[(r.family_key,r.orientation,r.role,r.quantity,r.coefficient)]
        assert r.minimum==min(v for v,n in values) and r.maximum==max(v for v,n in values)
        assert r.count==sum(n for v,n in values)
        assert r.distinct==len(values) and r.varying==(len(values)>1)
    assert groups


def test_dual_orientations_exact_bases_subset_counts_and_implicit_zeros(tmp_path):
    text='Minimize\n o:0\nSubject To\n a:2x-2y=0\n b:-4u+4v=0\n c:3p-3q=0\nEnd\n'
    with read_text(tmp_path,text) as m:
        f=m.row_families.one()
        group=m.family(f.id)
        assert f.count==3 and f.sign_symmetric
        h=group.related('histogram').where(basis='canonical')
        assert set(h.column('orientation'))=={0,1}
        for o in (0,1):
            bounds=h.where(orientation=o,role='lb',quantity='value')
            assert bounds.one().value==0 and bounds.one().count==3
            multiplicity=h.where(orientation=o,quantity='coefficient_multiplicity')
            assert any(r.value==0 and r.count==2 for r in multiplicity)
        selected=m.rows.head(1).families()
        assert selected.one().family_key==f.family_key
        assert selected.one().count==1 and selected.one().total_count==3
        assert all(r.distinct==1 for r in selected.related('parameters').where(basis='canonical'))
        scaled=group.related('histogram').where(basis='scale_normalized',quantity='coefficient')
        assert {abs(r.value) for r in scaled}=={1}


def test_budget_failures_close_and_no_implicit_numeric_work(model,monkeypatch):
    import mipframe.ir.family_attributes as numeric
    original=numeric._build
    def forbidden(*args): raise RuntimeError('numeric requested')
    monkeypatch.setattr(numeric,'_build',forbidden)
    repr(model.row_families)
    assert model.row_families.rows.count()==model.rows.count()
    with pytest.raises(RuntimeError): model.row_families.related('parameters').head(1).to_records()
    monkeypatch.setattr(numeric,'_build',original)
    frame=model.row_families.related('parameters')
    assert frame.head(1).count()==1
    model.close()
    for f in (frame,):
        with pytest.raises(mf.MIPFrameError): f.count()


def test_basis_filter_does_not_build_other_numeric_views(model,monkeypatch):
    import mipframe.ir.family_attributes as numeric
    original=numeric._build;seen=[]
    def record(db,result,kind,depth,members,basis):
        seen.append(basis);return original(db,result,kind,depth,members,basis)
    monkeypatch.setattr(numeric,'_build',record)
    model.row_families.related('parameters').where(basis='raw').to_records()
    assert seen and set(seen)=={'raw'}


def test_family_views_5000_without_gc_or_descriptor_growth():
    import os,subprocess,sys
    from pathlib import Path
    if os.name!='posix': pytest.skip('RLIMIT_NOFILE requires POSIX')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'tests/family_resource_probe.py'),'5000'],
        env=dict(os.environ,PYTHONPATH=str(root/'src')),capture_output=True,text=True,timeout=240)
    assert result.returncode==0,result.stdout+result.stderr
    data=json.loads(result.stdout)
    assert data['peak']-data['baseline']<=8 and data['automatic_gc'] is False


@pytest.mark.parametrize('basis',['canonical','raw','scale_normalized'])
@pytest.mark.parametrize('kind',['row','var'])
def test_histograms_reconstructed_from_original_sparse_coefficients(model,basis,kind):
    from collections import Counter,defaultdict
    families=model.families(kind=kind)
    expected=defaultdict(Counter)
    for s in families.related('sources').where(basis=basis):
        coefficients={t.var.id:t.coef for t in s.row.terms}
        values=coefficients.values() if kind=='row' else [coefficients[s.member.id]]
        for value in values:
            expected[(s.family_key,s.orientation)][value*s.factor]+=1
    actual=defaultdict(Counter)
    for h in families.related('histogram').where(basis=basis,quantity='coefficient'):
        actual[(h.family_key,h.orientation)][h.value]+=h.count
    assert actual==expected


def test_bound_depth_is_immutable_when_other_queries_extend_cache(model):
    shallow=model.families(depth=1)
    before=shallow.to_records()
    initial=model.indexed(depth=2)
    model.families(depth=4)
    assert shallow.to_records()==before
    assert {r.depth for r in shallow.related('levels')}=={0,1}
    assert model.indexed(depth=2) is initial


def test_numeric_failure_is_atomic_and_all_generators_close(model,monkeypatch):
    import mipframe.ir.family_attributes as numeric
    original=numeric._build
    def fail(*args):
        original(*args)
        raise RuntimeError('injected observation failure')
    params=model.row_families.related('parameters').where(basis='raw')
    expected=params.to_records()
    monkeypatch.setattr(numeric,'_build',fail)
    with pytest.raises(RuntimeError,match='injected'): params.to_records()
    monkeypatch.setattr(numeric,'_build',original)
    assert params.to_records()==expected


def test_large_complete_histograms_small_query_budget(tmp_path):
    text='Minimize\n o:0\nSubject To\n'+'\n'.join(f'r{i}:{i+1}x{i}<=1' for i in range(180))+'\nEnd\n'
    with read_text(tmp_path,text) as m:
        f=m.family('F0')
        h=f.related('histogram').where(basis='raw',quantity='coefficient')
        records=[r for batch in h.iter_batches(4,memory_mb=0.015625) for r in batch.to_records()]
        assert {r['value'] for r in records}==set(range(1,181))
        assert sum(r['count'] for r in records)==180
        subset=m.rows.head(3).families()
        assert subset.one().family_key==f.one().family_key
        assert subset.one().total_count==180
        assert len(subset.related('histogram').where(basis='raw',quantity='coefficient'))==3


def test_signature_pattern_and_level_specs_roundtrip(model):
    for method in ('template','exact_structural','algebraic','typed'):
        f=mf.analysis.signatures(model.rows,method=method).head(2)
        for name in (('pattern','levels') if method in ('template','exact_structural') else ('pattern',)):
            relation=f.related(name)
            restored=mf.frame_from_spec(json.loads(json.dumps(relation.to_spec())),models={model.uid:model})
            assert restored.to_records()==relation.to_records()


def test_empty_selection_has_empty_declared_relations(model):
    for name in ('members','pattern','parameters','histogram','sources','config','levels'):
        full = model.row_families.related(name)
        empty = model.row_families.head(0).related(name)
        assert empty.schema == full.schema
        assert empty.count() == 0


def test_numeric_predicates_avoid_unrequested_coefficient_distributions(model, monkeypatch):
    from contextlib import contextmanager
    import mipframe.ir.family_attributes as numeric
    original = numeric.Observations.distributions
    requested=[]
    def traced(self,orientation,kind,filters):
        for record in original(self,orientation,kind,filters):
            requested.append(record[1]);yield record
    monkeypatch.setattr(numeric.Observations,'distributions',traced)
    source = model.row_families.related('parameters')
    filtered = source.where(mf.col('quantity').isin(('coefficient',)), basis='raw')
    actual = filtered.to_records()
    assert actual
    assert set(requested)=={'coefficient'}
    assert actual == [r for r in source.to_records() if r['basis'] == 'raw' and r['quantity'] == 'coefficient']
    restored = mf.frame_from_spec(json.loads(json.dumps(filtered.to_spec())), models={model.uid:model})
    assert restored.to_records() == actual
    assert filtered.where(quantity='multiplicity').count() == 0


def test_label_filters_do_not_override_previous_selection(model):
    labels = list(model.row_families.column('id'))
    assert len(labels) > 1
    selected = model.row_families.where(id=labels[0]).where(id=labels[1])
    assert selected.count() == selected.rows.count() == 0
    assert selected.related('parameters').count() == 0


def test_pattern_neighbor_base_and_signed_role_keys(model):
    for p in model.row_families.related('pattern'):
        neighbor = model.family(p.role, depth=p.role_depth).one()
        assert (p.type,p.bounds_class,p.objective_class) == (neighbor.type,neighbor.bounds_class,neighbor.objective_class)
        assert p.role_key == ('+' if p.coefficient > 0 else '-') + p.role
    for p in model.var_families.related('pattern'):
        assert p.role_kind == 'row_state' and p.relation in ('=', '<=', 'range')
        assert p.type is None


def test_query_identity_and_configuration_are_validated(model,tmp_path):
    from copy import deepcopy
    from mipframe.frame.schema import SchemaError
    with read_text(tmp_path, name='other.lp') as other:
        left = set(model.row_families.column('family_key'))
        right = set(other.row_families.column('family_key'))
        assert not left & right
        exact = set(mf.analysis.families(model.rows,method='exact_structural').column('family_key'))
        assert not left & exact
        for query in (model.row_families,mf.analysis.signatures(model.rows,method='template')):
            spec = query.to_spec()
            altered = deepcopy(spec)
            altered['plan']['source'] = other.rows.to_spec()['plan']
            with pytest.raises(SchemaError,match='another model'):
                mf.frame_from_spec(altered,models={model.uid:model,other.uid:other})
            altered = deepcopy(spec)
            altered['plan']['config']['mode'] = 'exact'
            with pytest.raises(SchemaError,match='configuration'):
                mf.frame_from_spec(altered,models={model.uid:model})
            altered = deepcopy(spec)
            altered['plan']['version'] = 'not-this-version'
            with pytest.raises(SchemaError,match='identity/version'):
                mf.frame_from_spec(altered,models={model.uid:model})
    with pytest.raises(ValueError): model.refinement(method='exact_strctural')
    with pytest.raises(ValueError): model.indexed(method='exact_strctural')


def test_pre_refactor_native_v3_and_membership_specifications(tmp_path):
    from pathlib import Path
    import zipfile
    fixture = Path(__file__).parent / 'fixtures' / 'families-native-v3.zip'
    with zipfile.ZipFile(fixture) as archive:
        archive.extractall(tmp_path)
    with pytest.raises(mf.MIPFrameError,match='re-import'):
        mf.load(tmp_path/'native-v3')


def test_row_local_signatures_do_not_build_global_grouping(model,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('A row-local signature must not group the full model')
    monkeypatch.setattr(mf.analysis,'_FamilyStore',forbidden)
    for method in ('algebraic','typed'):
        signature = mf.analysis.signatures(model.rows.iloc[0],method=method)
        assert signature.one().row.id == 0
        assert signature.related('pattern').count() > 0
