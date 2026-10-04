"""Bounded structural rendering uses Frames, not display result objects."""
import pytest
import mipframe as mf
from test_refinement import read_text


def test_parameter_summary_distinct_from_histograms_and_directions(tmp_path):
    with read_text(tmp_path,'Minimize\n o:0\nSubject To\n a:2x<=7\n b:4y<=9\n c:3p-3q=0\nEnd\n') as m:
        f=m.family('F0')
        rendered=f.inspect(samples=0)
        assert isinstance(rendered,str)
        assert 'canonical' in rendered and 'Member samples' not in rendered
        assert 'Diagnostics' not in rendered
        verbose=f.inspect(verbose=True,samples=0)
        assert 'Diagnostics' in verbose and 'orientation' in verbose
        assert f.related('parameters').schema['minimum'].kind=='number'
        assert f.related('histogram').schema['value'].kind=='number'


@pytest.mark.parametrize('kind',['row','var'])
@pytest.mark.parametrize('depth',[0,1,2,3])
def test_role_labels_parent_navigation_and_degree_ranges(tmp_path,kind,depth):
    with read_text(tmp_path) as m:
        families=m.families(kind=kind,depth=depth)
        for r in families:
            members=getattr(families.where(id=r.id),kind+'s')
            degrees=[entity.nnz for entity in members]
            assert (r.nnz_min,r.nnz_max)==(min(degrees),max(degrees))
            if depth:
                parent=m.families(kind=kind,depth=depth-1).where(family_key=r.parent_key)
                assert parent.one().id==r.parent
                assert set(members.ids)<=set(getattr(parent,kind+'s').ids)
            else: assert r.parent is None
        assert isinstance(families.inspect(max_items=1,max_splits=1,samples=0),str)


def test_adaptive_inspection_formulas_metadata_and_omitted_counts(tmp_path):
    text='Minimize\n o:0\nSubject To\n'+ '\n'.join(f'r{i}:'+ '+'.join(f'x{i}_{j}' for j in range(i+1))+'<=1' for i in range(13))+'\nEnd\n'
    with read_text(tmp_path,text) as m:
        all_f=m.families(depth=2,multiplicity_ratio=1.01)
        assert 'omitted' in all_f.inspect(max_items=1,samples=0)
        for row in all_f:
            f=all_f.where(id=row.id)
            text=f.inspect(samples=1,max_terms=2)
            assert 'Member samples' in text
            if row.nnz_min>2:
                assert 'terms shown' not in text
                assert 'terms shown' in f.inspect(samples=1,max_terms=2,sample_formula=True)
        assert 'Member samples' not in all_f.inspect(samples=0)


def test_inspect_validates_before_numerical_work_and_guards_close(tmp_path,monkeypatch):
    import mipframe.ir.family_attributes as numeric
    with read_text(tmp_path) as m:
        frame=m.row_families
        monkeypatch.setattr(numeric,'_build',lambda *args: pytest.fail('validation must precede aggregation'))
        for kwargs in (dict(samples=-1),dict(max_terms=-1),dict(max_items=0),dict(max_splits=0),dict(sample_formula='yes')):
            with pytest.raises(ValueError): frame.inspect(**kwargs)
        with pytest.raises(TypeError): frame.inspect(verbose='yes')
    with pytest.raises(mf.MIPFrameError): frame.inspect()


def test_multidepth_refinement_and_empty_selection(tmp_path):
    with read_text(tmp_path) as m:
        for kind in ('row','var'):
            f=m.refinement(0,3,kind=kind)
            assert len(f.related('before'))==len(m.families(kind=kind,depth=0))
            assert len(f.related('after'))==len(m.families(kind=kind,depth=3))
            assert f.head(0).related('after').count()==0
        for start,stop in ((1,1),(-1,2),(3,2),(True,2)):
            with pytest.raises(ValueError): m.refinement(start,stop)
