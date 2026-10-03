"""Readable structural overviews, stable references and explicit diagnostics."""

from model_helpers import family_catalog

from fractions import Fraction as Q

from external_models import model_path

import pytest

import mipframe as mf
from mipframe.ir.family_attributes import FamilyAttributes
from test_refinement import read_text



def test_danoint_summary_depths_legend_and_single_meaningful_split(monkeypatch):
    with mf.read(model_path("danoint.lp")) as m:
        result = m.structure.refine(depth=3)
        colors = list(result.row_color[2])
        # All overview displays must work without opening a numeric profile.
        monkeypatch.setattr(FamilyAttributes, '_ensure', lambda *a: pytest.fail('overview queried coefficients'))
        summary = result.summary(depth=2)
        assert summary.levels == (
            {'depth':0,'row_roles':4,'var_roles':4,'delta_rows':None,'delta_vars':None},
            {'depth':1,'row_roles':10,'var_roles':7,'delta_rows':6,'delta_vars':3},
            {'depth':2,'row_roles':11,'var_roles':7,'delta_rows':1,'delta_vars':0},
        )
        text = repr(summary)
        assert text.index('<VariableRoles depth=1') < text.index('<RowFamilies depth=2')
        assert '10 → 11 row families; 1 split' in text
        assert 'F0 (392 rows)' in text
        assert 'F0: 336 rows' in text and 'F4: 56 rows' in text
        assert 'Unchanged: 9 families / 272 rows' in text
        assert 'orientations(' not in text and 'canonical[' not in text
        assert 'coefficients=' not in text and 'Rstate' not in text
        assert 'rhs_class' in text
        assert 'rhs_class follows canonical orientation' in text
        assert 'delta_rows' in text and 'delta_vars' in text
        assert 'largest 336/664 (50.6%)' in text
        assert 'top-5 coverage 84.3%; singleton families 0' in text
        assert summary.concentration['top5_coverage'] == Q(560,664)
        assert 'V2 (456 variables)' in text
        assert summary.variable_delta.start == 0 and summary.variable_delta.stop == 1
        assert [r['count'] for r in summary.variable_delta[0]['children']] == [336,56,56,8]
        assert list(result.row_color[2]) == colors
        delta = summary.delta
        assert delta.before_count == 10 and delta.after_count == 11 and len(delta) == 1
        assert delta.unchanged == {'families':9,'members':272}
        assert delta[0]['parent'] == 'F0'
        assert [r['count'] for r in delta[0]['children']] == [336,56]
        unchanged = result.splits(depth=3)
        assert len(unchanged) == 0
        assert unchanged.unchanged == {'families':11,'members':664}
        assert '11 → 11' in repr(unchanged)
        assert repr(m.rows.refinement(1,2)) == repr(delta)
        assert repr(m.structure.splits(depth=2)) == repr(delta)


def test_role_labels_and_true_family_parents_resolve():
    with mf.read(model_path("danoint.lp")) as m:
        rows = m.ir._result._row_families(2)
        variables = m.ir._result._var_families(1)
        assert [r['id'] for r in variables] == [f'V{i}' for i in range(7)]
        assert [r['count'] for r in variables] == [1,56,56,56,336,8,8]
        assert variables['V4'].count == 336
        assert variables['V0'].vars.head(1).iloc[0]['name'] == 'x1'
        assert len(variables['V3'].vars)==56
        assert rows['F0'].parent == rows['F4'].parent == 'F0'
        previous = m.structure.signatures(depth=1)._row_families()
        for record in rows:
            family = rows[record['id']]
            assert family.depth == 2 and family.parent_depth == 1
            assert all(r in previous[family.parent].rows.ids for r in family.rows.ids)
        # Existing positional/F aliases remain available; V names match colors.
        old = variables.loc['F0']
        assert old.id == 'V4' and old.count == 336
        assert variables.head(1)['V0'].count == 1
        with pytest.raises(KeyError): variables.head(1)['V4']
        with pytest.raises(KeyError): variables['V04']
        with pytest.raises(KeyError): variables['V999']
        assert len(variables.tail(2)) == 2
        assert [r['id'] for r in variables[::-1]] == [f'V{i}' for i in reversed(range(7))]
        assert 'order: selection order' in repr(variables[::-1])
        assert rows['F0'].id == 'F0'
        assert variables['V4'].id == 'V4'
        for label in ('F0@2', 'F0@1', 'F0@02', 'F0@2@2', 'F0@'):
            with pytest.raises(KeyError): rows[label]
        for label in ('V4@1', 'V4@0', 'V04', 'V4@1@1', 'V4@'):
            with pytest.raises(KeyError): variables[label]


def test_parameter_summary_is_distinct_from_histograms_and_directions():
    with mf.read(model_path("danoint.lp")) as m:
        rows = m.ir._result._row_families(2)
        f = rows['F0']
        assert 'attributes' not in rows.columns and len(rows.columns)==7
        assert 'coefficients' not in repr(f)
        assert 'orientations(' not in repr(rows)
        text = repr(f._parameters)
        assert 'varying[-66.5,-47], distinct=19' in text
        assert 'constant(1)' in text and 'rhs: constant(0)' in text
        assert '-66.5:' not in text
        p = f._parameters['V1']
        assert p.min==Q(-133,2) and p.max==-47 and p.distinct==19
        histogram = p.value_counts()
        assert sum(histogram.values()) == 336
        assert Q(-133,2) in histogram
        assert {r.value:r.count for r in f.coefficients.relations['histogram'].where(role='-V1')}==dict(histogram)
        assert isinstance(f.coefficients, mf.Frame)
        assert 'role' in repr(f.coefficients.relations['histogram'])
        assert 'Diagnostics' not in repr(f.inspect(samples=0))
        assert 'Diagnostics' in repr(f.inspect(verbose=True,samples=0))
        assert 'coefficient -66.5 / member' in repr(f.inspect(verbose=True,samples=0))
        assert len(f.inspect().neighbors)==len(f.rows.neighbors())
        # Display selects the aggregate-positive direction, but retains both internally.
        balance = rows['F1']
        assert balance.sign_symmetric
        assert balance.structure=='+V2 ×1 | -V3 ×1 | -V4 ×6'
        assert balance._parameters['+V2'].constant==1
        assert balance._parameters['-V3'].constant==-1
        assert len(balance.signature.patterns)==2
        assert 'orientations(' in repr(balance.inspect(verbose=True,samples=0))
        mixed = rows['F2']
        assert mixed.structure=='+V3 ×1 | +V4 ×6 | -V4 ×6'
        with pytest.raises(ValueError,match='Ambiguous'):
            mixed._parameters['V4']
        assert mixed._parameters['+V4'].constant==1
        assert mixed._parameters['-V4'].constant==-1
        assert mixed._parameters['-V4'].constant==-1
        assert '-V4' in list(mixed._parameters)
        with pytest.raises(KeyError): mixed._parameters['-V4@1']
        with pytest.raises(ValueError,match='Ambiguous'): mixed._parameters['V4']


def test_assign_varying_counts_are_parameters_and_numeric_values_preserved():
    with mf.read(model_path("assign1-5-8.lp")) as m:
        f=m.ir._result._row_families(2)['F0']
        assert f.structure=='-V0 ×1 | +V1 ×n1'
        assert '22' not in f.structure and '26' not in f.structure
        assert f._parameters['V1'].multiplicity.min==22
        assert f._parameters['V1'].multiplicity.max==26
        assert dict(f._parameters['V1'].multiplicity.value_counts())=={22:5,24:5,25:20,26:100}
        assert dict(f._parameters['V1'].coefficient_multiplicity(5).value_counts())=={1:130}
        assert f._parameters.rhs.constant==5
        assert f.attributes.view(basis='scale_normalized').rhs.constant==1
        assert f.rhs.describe()['min']==1


def test_subset_delta_global_parent_and_local_parameters():
    with mf.read(model_path("danoint.lp")) as m:
        # Includes members of both children of the one splitting parent.
        selected=m.rows.iloc[184:204]
        delta=selected.refinement(1,2)
        assert delta.before_count==1 and delta.after_count==2
        assert delta[0]['count']==20
        assert sum(c['count'] for c in delta[0]['children'])==20
        assert delta.unchanged=={'families':0,'members':0}
        selected_families=family_catalog(selected, signature='structural')
        for record in selected_families:
            f=selected_families[record['id']]
            assert f.parent=='F0'  # A global parent, not the integer state color.
            assert f._parameters.rhs.count==f.count
            assert sum(f._parameters['V1'].value_counts().values())==f.count
        assert 'covering 20 selected rows' in repr(selected_families)


@pytest.mark.parametrize('mode',['template','exact'])
def test_empty_depth_zero_exact_mode_and_guarded_views(tmp_path,mode):
    with read_text(tmp_path,'Minimize\n o: 0\nSubject To\nEnd\n') as m:
        summary=m.structure.summary(depth=0,mode=mode)
        assert summary.levels==({'depth':0,'row_roles':0,'var_roles':0,'delta_rows':None,'delta_vars':None},)
        assert summary.delta is None
        assert '0 rows, 0 variables' in repr(summary)
        delta=m.structure.splits(depth=2,mode=mode)
        assert delta.before_count==delta.after_count==0
        assert delta.unchanged=={'families':0,'members':0}
    for call in (lambda:repr(summary),lambda:repr(delta),lambda:len(delta)):
        with pytest.raises(mf.MIPFrameError,match='closed'):call()
    with read_text(tmp_path,name='other.lp') as m:
        family=m.structure.signatures(mode=mode)._row_families()['F0']
        params=family._parameters
        inspection=family.inspect()
        roles=m.structure.signatures(mode=mode)._var_families()
        assert len(roles.head(2))==2
    for call in (lambda:repr(params),lambda:repr(inspection),lambda:repr(roles)):
        with pytest.raises(mf.MIPFrameError,match='closed'):call()


def test_delta_multiple_depths_variable_roles_and_bounded_output(monkeypatch):
    with mf.read(model_path("danoint.lp")) as m:
        full=m.structure.splits(depth=2,from_depth=0)
        assert sum(s['count'] for s in full)+full.unchanged['members']==664
        assert len(full)>1
        assert f'{len(full)-1} additional splits omitted' in full.render(max_splits=1)
        assert 'additional splits omitted' not in full.render(max_splits=len(full))
        variables=m.structure.splits(depth=1,kind='var')
        assert variables.before_count==4 and variables.after_count==7
        assert len(variables)==1 and variables[0]['parent']=='V2'
        assert [c['count'] for c in variables[0]['children']]==[336,56,56,8]
        monkeypatch.setattr(mf.options.display,'max_rows',1)
        assert '... (' in repr(variables)
        assert len(variables[0]['children'])==4
        assert [c['count'] for c in variables[0]['children'][::-1]]==[8,56,56,336]
        with pytest.raises(IndexError): variables[1]


def test_invalid_transitions_before_compute(tmp_path):
    for start, stop in [(1, 1), (-1, 2), (2, 1), (True, 2), (0, 1.5)]:
        with read_text(tmp_path) as m:
            before = tuple(m._refinement_store.db.iterdump())
            with pytest.raises(ValueError):m.rows.refinement(start,stop)
            assert tuple(m._refinement_store.db.iterdump()) == before
            with pytest.raises(ValueError):m.structure.splits(depth=stop,from_depth=start)


def test_inspection_validation_and_summary_from_saved_cache(tmp_path,monkeypatch):
    with read_text(tmp_path) as m:
        s=m.structure.refine(depth=2)
        f=s._row_families()['F0']
        with pytest.raises(ValueError): f.inspect(samples=-1)
        with pytest.raises(TypeError): f.inspect(verbose='yes')
        before=repr(s.summary())
        path=m.save(tmp_path/'saved')
    from mipframe.ir.refinement_store import RefinementStore
    with mf.load(path) as m:
        monkeypatch.setattr(RefinementStore,'_round',lambda *a:pytest.fail('should reuse cache'))
        monkeypatch.setattr(FamilyAttributes,'_ensure',lambda *a:pytest.fail('summary has no numerical profiles'))
        assert repr(m.structure.summary())==before


def test_bounded_large_overview_coverage_and_expansion(tmp_path, monkeypatch):
    from test_template_refinement import counts_model
    from mipframe.ir.display import family_statistics
    # 24 distinct templates in exact mode, with sizes 24, 23, ..., 1.
    counts=[k for k in range(1,25) for _ in range(25-k)]
    with read_text(tmp_path,counts_model(counts)) as m:
        s=m.structure.refine(depth=1,mode='exact')
        monkeypatch.setattr(FamilyAttributes,'_ensure',lambda *a:pytest.fail('overview queried coefficients'))
        monkeypatch.setattr(mf.options.display,'max_rows',100000)
        text=repr(s.summary())
        assert 'showing 15 of 24 families' in text
        assert '9 families omitted' in text
        assert 'cover 255/300 rows (85.0%)' in text
        assert 'largest 24/300 (8.0%)' in text
        assert 'top-5 coverage 36.7%; singleton families 1' in text
        assert '19 additional children omitted' in text
        assert len(text.splitlines())<70
        smaller=repr(s.summary(max_families=5,max_splits=2))
        assert 'showing 5 of 24 families' in smaller
        assert 'cover 110/300 rows (36.7%)' in smaller
        assert '22 additional children omitted' in smaller
        families=s._row_families()
        assert len(families)==24
        assert 'showing 24 of 24 families' in str(families.show(limit=24))
        assert '24 families omitted' in str(families.show(limit=0))
        assert [r['count'] for r in families]==list(range(24,0,-1))
        stats=family_statistics(families.tail(3))
        assert stats['members']==6 and stats['largest_coverage']==Q(1,2)
        assert stats['top5_count']==6 and stats['singleton_families']==1
        assert 'cover 6/6 rows (100.0%)' in repr(families.tail(3))
        assert 'Selection from 24 families' in repr(families.tail(3))
        assert family_statistics(families[:0])['top5_coverage']==0
        # Global options remain an upper bound even for explicit expansion.
        monkeypatch.setattr(mf.options.display,'max_rows',2)
        assert 'showing 2 of 24 families' in str(families.show(limit=24))


def test_variable_degree_range_is_original_incidence(tmp_path):
    text='Minimize\n o: 0x+0y+0z\nSubject To\n a: x-y=0\n b: x<=1\nEnd\n'
    with read_text(tmp_path,text) as m:
        roles=m.structure.signatures(depth=0)._var_families()
        assert len(roles)==1
        assert roles[0]['row_degree']==(0,2)
        assert roles[0]['degree']==(0,2)  # Legacy metadata alias.
        rendered=repr(roles)
        assert 'row_degree' in rendered and '0..2' in rendered
        assert '(0, 2)' not in rendered
        assert 'original constraint incidence count' in rendered
        assert 'row_degree=0..2' in roles['V0'].structure
        assert roles[0]['count']==3


def test_adaptive_inspection_formulas_and_metadata(tmp_path,monkeypatch):
    from test_template_refinement import counts_model
    with mf.read(model_path("danoint.lp")) as m:
        f=m.ir._result._row_families(2)['F0']
        short=repr(f.inspect(samples=1))
        assert 'c186: -50 x2 + x60 <= 0' in short
        assert '<RowFrame' not in short
        assert '<RowFrame' in repr(f.inspect(samples=1,sample_formula=False))
        assert 'Member samples' not in repr(f.inspect(samples=0))
        for value in ('yes',1,None):
            with pytest.raises(ValueError):f.inspect(sample_formula=value)
        with pytest.raises(ValueError):f.inspect(max_terms=-1)
    with read_text(tmp_path,counts_model([13])) as m:
        f=m.ir._result._row_families(2)['F0']
        from mipframe.frame.row import Row
        original=Row.formula
        monkeypatch.setattr(Row,'formula',lambda *a,**kw:pytest.fail('long auto sample should only read metadata'))
        text=repr(f.inspect())
        assert 'Metadata for rows with more than 12 terms' in text
        assert '<RowFrame' in text
        monkeypatch.setattr(Row,'formula',original)
        assert '[3 of 13 terms shown]' in repr(f.inspect(sample_formula=True,max_terms=3))
        assert 'terms shown' not in repr(f.inspect(max_terms=13))
        assert 'r0: x0_0 + x0_1' in repr(f.inspect(max_terms=13))


def test_summary_limits_validated_before_compute(tmp_path):
    for kwargs in [
        {'max_families': 0},
        {'max_families': True},
        {'max_families': 1.5},
        {'max_splits': -1},
        {'max_splits': False},
    ]:
        with read_text(tmp_path) as m:
            before = tuple(m._refinement_store.db.iterdump())
            with pytest.raises(ValueError):m.structure.summary(**kwargs)
            assert tuple(m._refinement_store.db.iterdump()) == before
