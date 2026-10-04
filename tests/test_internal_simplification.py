"""Regressions for result aggregation, query capabilities and failure cleanup."""
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch
import pytest
import mipframe as mf
from mipframe.check.diagnostics import diagnostic
from mipframe.store.lifecycle import retry_cleanup
from scripts.indexed_instances import model


@pytest.fixture
def m(tmp_path):
    path=tmp_path/'indexed.lp'
    path.write_text(model([[5,2,0],[2,5,1],[0,1,5]],groups=2,scaled=True)[0])
    with mf.read(path) as value:
        yield value


@pytest.mark.parametrize('statuses,expected',[
    ([], 'PROVED'), (['PROVED'], 'PROVED'),
    (['INCONCLUSIVE'], 'INCONCLUSIVE'), (['SKIPPED'], 'INCONCLUSIVE'),
    (['INCONCLUSIVE','PROVED'], 'INCONCLUSIVE'),
    (['REFUTED','INCONCLUSIVE','SKIPPED'], 'REFUTED'),
])
def test_diagnostic_status_reduction(m,statuses,expected):
    report=diagnostic(m,(dict(status=s) for s in statuses),kind='test')
    assert report.status==expected
    assert report.summary['checked']==len(statuses)-statuses.count('SKIPPED')
    assert report.summary['failures']==statuses.count('REFUTED')
    assert report.summary['inconclusive']==statuses.count('INCONCLUSIVE')
    assert report.evidence['checks'].count()==len(statuses)


def test_diagnostic_items_and_details_have_separate_counts(m):
    report=diagnostic(m,[dict(status='PROVED',item='good'),
        dict(status='REFUTED',item='bad',diagnostics=[dict(reason=f'problem {i}') for i in range(3)])],kind='test')
    assert report.summary['checked']==2
    assert report.summary['failures']==1
    assert report.summary['diagnostics']==3
    assert report.violations.count()==3
    assert report.evidence['checks'].count()==2
    assert report.evidence['details'].count()==5
    with pytest.raises(mf.ValidationError):report.require()
    stored=m._diagnostic_store.db.execute('SELECT count(*) FROM diagnostic_records').fetchone()[0]
    with pytest.raises(ValueError,match='Unknown diagnostic status'):
        diagnostic(m,[dict(status='SUCCESS')],kind='test')
    def broken():
        yield dict(reason='unpublished')
        raise RuntimeError('detail failure')
    with pytest.raises(RuntimeError,match='detail failure'):
        diagnostic(m,[dict(status='REFUTED',diagnostics=broken())],kind='test')
    assert m._diagnostic_store.db.execute('SELECT count(*) FROM diagnostic_records').fetchone()[0]==stored
    assert report.violations.count()==3


def test_failed_indexed_selection_counts_items_and_replays(m):
    indexed=m.indexed();selected=indexed.operators.head(1)
    indexed._store.db.execute('UPDATE kernel SET value=CAST(value AS INT)+1 WHERE o=?',(selected.one().id,))
    report=indexed.verify(selected)
    assert report.status=='REFUTED'
    assert report.summary['checked']==report.summary['failures']==1
    assert report.violations.count()>0
    assert report.evidence['checks'].count()==1
    replay=mf.Report.replay(report.to_spec(),models=(m,))
    assert replay.status=='REFUTED' and replay.summary['checked']==1


def test_model_close_releases_every_store_after_directory_failure(m):
    base=m.model
    refinement=m._refinement_store
    temp=refinement.temp
    imported=m._temporary
    with patch.object(temp,'cleanup',side_effect=OSError(24,'injected directory failure')):
        with pytest.raises(OSError,match='injected directory failure'):
            m.close()
    assert base.db is None
    assert refinement.db is None
    assert not imported.name or not Path(imported.name).exists()
    with pytest.raises(mf.MIPFrameError,match='closed'):m.rows
    m.close()
    retry_cleanup()
    assert not Path(temp.name).exists()


def test_close_preserves_active_exception(m):
    temp=m._refinement_store.temp
    with patch.object(temp,'cleanup',side_effect=OSError('cleanup failure')):
        with pytest.raises(RuntimeError,match='original') as caught:
            with m:
                raise RuntimeError('original')
    assert any('cleanup failure' in note for note in caught.value.__notes__)
    assert m._store is None
    retry_cleanup()


def test_equivalent_filters_share_pushdown_and_serialization(m,monkeypatch):
    from mipframe.ir import family_attributes
    original=family_attributes._build
    calls=[]
    def observe(db,result,kind,depth,members,basis):
        calls.append(basis)
        return original(db,result,kind,depth,members,basis)
    monkeypatch.setattr(family_attributes,'_build',observe)
    p=m.family('F0').related('parameters')
    combined=p.where(role='lb',basis='raw')
    records=combined.to_records()
    assert calls==['raw']
    calls.clear()
    chained=p.where(role='lb').where(basis='raw')
    assert chained.to_records()==records
    assert calls==['raw']
    calls.clear()
    restored=mf.frame_from_spec(chained.to_spec(),models={m.uid:m})
    assert restored.to_records()==records
    assert calls==['raw']
    assert p.where(basis='raw').where(basis='canonical').count()==0
    assert m.rows.where(mf.col('nnz')>0).where(mf.col('nnz')<100).to_records()==m.rows.where((mf.col('nnz')>0)&(mf.col('nnz')<100)).to_records()


def test_inspect_limits_and_supported_options(m):
    f=mf.Frame.from_records([{'value':i} for i in range(5)],schema={'value':'int'})
    text=f.inspect(max_items=1)
    assert text.splitlines()[-2:]==['0','...']
    with pytest.raises(ValueError,match='samples'):f.inspect(samples=1)
    with pytest.raises(ValueError,match='max_splits'):m.indexed().operators.inspect(max_splits=1)
    assert isinstance(m.indexed().operators.inspect(verbose=True,max_items=1),str)
    assert isinstance(m.row_families.inspect(samples=0,max_splits=1,max_items=1),str)


def test_exact_recursive_metadata_roundtrip():
    from mipframe.frame.schema import encode, decode_tree
    value={'values':[encode(Fraction(2,7)),encode(float('-inf'))], 'empty':{}, 'flag':False}
    assert decode_tree(value)=={'values':[Fraction(2,7),float('-inf')],'empty':{},'flag':False}
    assert decode_tree({'$frame':'recipe'},frame_decoder=lambda x:('restored',x))==('restored','recipe')
