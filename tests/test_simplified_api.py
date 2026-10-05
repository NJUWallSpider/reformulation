"""Model + Frame API contracts, independent metadata and diagnostic regressions."""
import gc
import inspect
from fractions import Fraction
import pytest
import mipframe as mf
from scripts.indexed_instances import model
from mipframe.ir.indexing.nodes import nodes, restore


@pytest.fixture
def m(tmp_path):
    p=tmp_path/'indexed.lp'
    p.write_text(model([[5,2,0],[2,5,1],[0,1,5]],groups=2,scaled=True)[0])
    with mf.read(p) as m:yield m


def test_removed_interfaces_and_exact_contract(m):
    for name in ('variables','constraints','A'):assert not hasattr(m,name)
    for name in ('IndexedTensor','IndexedOperator','ValidationReport'):assert not hasattr(mf,name)
    assert not hasattr(mf.options,'numeric')
    for frame in (m.rows,m.vars):
        for name in ('collect','by_id','at','neighbors'):assert not hasattr(frame,name)
        assert frame.by_name(frame.iloc[0].name)==frame.loc[frame.iloc[0].id]
        assert 'exactly_one' not in inspect.signature(frame.project).parameters
        assert 'column' not in inspect.signature(frame.pattern_counts).parameters
    assert not hasattr(m.rows,'show')
    assert not hasattr(m.check,'batch')
    assert 'method' not in inspect.signature(m.check.linear_batch).parameters
    for name in ('identity','linear','infeasible','round','cases','enumerate','solution'):
        assert 'arithmetic' not in inspect.signature(getattr(m.check,name)).parameters
    x=m.vars.iloc[0]
    with pytest.raises(TypeError):m.check.linear(x>=0,arithmetic='exact')
    with m.check.linear_batch(residual='bounds') as batch:
        batch.add(key='x',target=x>=0,using=[x.lb_claim])
        batch.run().require()


def test_provenance_lifecycle_and_constructed_entities(m,tmp_path):
    for frame in (m.rows,m.vars):
        entity=frame.iloc[0]
        assert entity.source_line>0
        assert entity.to_record()['source_line']==frame.head(1).to_records()[0]['source_line']
    record=m.vars.iloc[0].to_record()
    with m.edit() as edit:
        child=edit.build()
    with child:
        assert child.vars.iloc[0].source_line is None
        assert child.rows.head(1).one().source_line is None
    m.close()
    assert record['source_line']>0


@pytest.mark.parametrize('collection,axis,relation',[('operators','rows','kernel'),('tensors','vars','coordinates'),('partitions','rows','members')])
def test_directory_selection_navigation_and_identity(m,collection,axis,relation):
    I=m.indexed();directory=getattr(I,collection)
    assert type(directory) is mf.Frame
    assert directory.count()>0
    chosen=directory.head(1);key=chosen.one().indexed_key
    rel=chosen.related(relation)
    assert set(rel.column('indexed_key'))<={key}
    empty=directory.head(0)
    assert getattr(empty,axis).count()==0
    assert empty.related(relation).count()==0
    assert empty.related(relation).schema==rel.schema
    renamed=chosen.select(identity=mf.col('indexed_key'))
    assert renamed.related(relation).to_records()==rel.to_records()
    joined=chosen.join(chosen.select('indexed_key'),on='indexed_key',cardinality='1:1')
    assert joined.related(relation).to_records()==rel.to_records()
    with pytest.raises(KeyError):chosen.select('id').related(relation)
    I.verify(chosen).require()
    assert I.verify(empty).summary['checked']==0
    with pytest.raises(mf.SchemaError):I.verify(chosen.select('id'))


def test_axes_domains_and_lossless_metadata(m):
    I=m.indexed();T=I.tensors.head(1);O=I.operators.head(1)
    axes=T.related('axes')
    a,b=(axes.where(axis=i).rows for i in (0,1))
    assert (a&b).count()==0
    assert [len(a),len(b)]==[3,2]
    T.related('coordinates').check('product',('i','j')).require()
    assert T.related('coordinates').count()==6
    for wrong in (lambda:T.rows,lambda:O.vars):
        with pytest.raises(KeyError):wrong()
    raw=I._card('operators',O.one().id)
    assert restore(O.related('ast'))==raw['ast']
    stats=restore(O.related('statistics'))
    assert stats['alignment']['diagonal_constant']==Fraction(5)
    assert stats['zeros']==2
    for payload in ({},[],{'empty':[],'map':{},'a/b~c':[None,True,False,Fraction(3,7),['x']]}):
        assert restore(list(nodes(payload)))==payload
    partition=I.partitions.head(1)
    assert restore(partition.related('origins'))==I._card('partitions',partition.one().id)['origins']
    evidence=I.evidence.head(1)
    if len(evidence):assert restore(evidence.related('details'))==I._card('evidence',evidence.one().id)
    assert O.one().formula==O.one()['formula']
    assert O.one().row_count==O.rows.count()
    assert 'shape' not in T.schema
    with pytest.raises(KeyError):I.tensor('missing')
    with pytest.raises(ValueError):I.verify(I.evidence)
    other=m.indexed(rows=m.rows.head(1))
    with pytest.raises(mf.SchemaError):I.verify(other.tensors)


def test_external_candidates_and_replay(m,tmp_path):
    I=m.indexed();O=I.operators.head(1);T=I.tensors.head(1)
    candidate=dict(operator=O,rows=O.related('bindings'),kernel=O.related('kernel'),coordinates=T.related('coordinates'))
    report=I.validate(dict(kind='indexed_operator',candidate=candidate))
    report.require()
    mf.Report.replay(report.to_spec(),models=(m,)).require()
    bad=dict(candidate,kernel=O.related('kernel').select('indexed_key','id','p','k','j',value=mf.col('value')+1))
    assert I.validate(dict(kind='indexed_operator',candidate=bad)).status=='REFUTED'
    proof=I.verify(O)
    full=m.verify()
    assert full.scope['kind']=='model_integrity' and proof.scope['kind']=='indexed'
    for report in (full,proof):
        with pytest.raises(mf.InvalidCertificateError):m.check.linear(m.vars.iloc[0]>=0,using=[report])
    spec=O.related('kernel').to_spec()
    saved=m.save(tmp_path/'native')
    with mf.load(saved) as loaded:
        restored=mf.frame_from_spec(spec,models={loaded.uid:loaded})
        assert restored.to_records()==O.related('kernel').to_records()
        assert loaded.indexed().operators.one().indexed_key==O.one().indexed_key
        mf.Report.replay(proof.to_spec(),models=(loaded,)).require()
        mf.Report.replay(full.to_spec(),models=(loaded,)).require()


def test_integrity_failure_skips_replay_and_diagnostic_release(m,monkeypatch):
    from mipframe.check import model as integrity
    monkeypatch.setattr(integrity,'sha256',lambda p:'wrong')
    monkeypatch.setattr(type(m.indexed()),'verify',lambda self,*a:pytest.fail('must not replay corrupt base'))
    report=m.verify()
    assert report.status=='REFUTED' and not report.scope['complete']
    assert report.evidence['checks'].where(status='SKIPPED').count()>0
    with pytest.raises(mf.ValidationError):report.require()
    store=m._diagnostic_store
    before=store.record_count
    again=m.verify();view=again.violations
    del again;gc.collect()
    assert len(view)>0
    del view;gc.collect()
    assert store.record_count==before
    from mipframe.check.diagnostics import diagnostic
    def broken():
        yield dict(status='REFUTED',reason='unpublished')
        raise RuntimeError('injected')
    with pytest.raises(RuntimeError):diagnostic(m,broken(),kind='test')
    assert store.record_count==before


def test_independent_complete_candidate_and_cross_run_rejection(m):
    I=m.indexed();O=I.operators.head(1);T=I.tensors.head(1)
    record=I._card('operators',O.one().id)
    record['id']='independent-candidate'
    record['row_count']=record.pop('rows')
    record['ast']=O.related('ast')
    I.validate(dict(kind='indexed_operator',candidate=dict(operator=record,
        rows=O.related('bindings'),kernel=O.related('kernel'),coordinates=T.related('coordinates')))).require()
    other=m.indexed(depth=3)
    with pytest.raises(mf.SchemaError):
        I.validate(dict(kind='indexed_operator',candidate=dict(operator=other.operators.one(),
            rows=O.related('bindings'),kernel=O.related('kernel'),coordinates=T.related('coordinates'))))
    with pytest.raises(mf.SchemaError):
        I.validate(dict(kind='indexed_operator',id=O.one().id,kernel=other.operators.related('kernel')))
    with mf.read('examples/data/tiny.lp') as different:
        specification=I.operators.to_spec()
        with pytest.raises(mf.SchemaError):mf.frame_from_spec(specification,models={m.uid:different})


def test_multiple_sorted_partitions_and_axes_serialization(m):
    I=m.indexed();P=I.partitions.sort_by('row_count',descending=True).iloc[1:3]
    expected=set(P.column('indexed_key'))
    assert set(P.related('members').column('indexed_key'))<=expected
    assert P.select(key=mf.col('indexed_key')).related('members').to_records()==P.related('members').to_records()
    axes=I.tensors.related('axes').where(axis=1).select(parent=mf.col('indexed_key'),dimension=mf.col('axis'))
    restored=mf.frame_from_spec(axes.rows.to_spec(),models={m.uid:m})
    assert restored.to_records()==axes.rows.to_records()


def test_partition_and_alignment_frame_hypotheses(m):
    I=m.indexed();T=I.tensors.head(1);O=I.operators.head(1)
    parts=[I.partition(a.partition) for a in T.related('axes')]
    for part in parts:
        answer=I.validate(dict(kind='indexed_partition',sets=part.related('members'),variables=part.vars))
        answer.require()
        mf.Report.replay(answer.to_spec(),models=(m,)).require()
    I.validate(dict(kind='cartesian',partitions=parts,coordinates=T.related('coordinates'))).require()
    I.validate(dict(kind='axis_alignment',id=O.one().id,mapping=O.related('alignment'))).require()
