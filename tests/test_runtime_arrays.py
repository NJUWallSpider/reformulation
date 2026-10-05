"""Runtime ownership, exactness and control/data boundary contracts."""
from fractions import Fraction
from pathlib import Path
import json
import shutil
import numpy as np
import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    source=tmp_path/'source.lp'
    source.write_text('Minimize\n obj:x\nSubject To\n a:x-y<=0\n b:y-z<=0\n c:x-z<=0\nBounds\n 0<=x<=2\n 0<=y<=2\n 0<=z<=2\nEnd\n')
    with mf.read(source) as m:yield m


def test_hot_paths_and_overlay_borrow_arrays(model,monkeypatch):
    from mipframe.frame.executor import Context
    from mipframe.store.overlay import OverlayStore
    from mipframe.check.kernel import facts
    monkeypatch.setattr(__import__('sqlite3'),'connect',lambda *a,**kw:pytest.fail('numeric SQL'))
    assert not hasattr(OverlayStore,'_csr')
    assert not hasattr(OverlayStore,'db')
    traces=[]
    model.model.db.set_trace_callback(traces.append)
    f=facts(model)
    assert np.shares_memory(f.row_ptr,model.model.row_ptr)
    assert len(model.rows.where(mf.col('nnz')==2))==3
    assert len(model.rows.head(1).terms.to_records())==2
    assert model.rows.iloc[0].coef_min==-1
    proof=model.check.linear_rows(targets=[2],witnesses=[[0,1]]).require()
    child=model.edit().drop_rows([2],because=proof).commit()
    model.close()
    try:
        assert child.shape==(2,3) and child.nnz==4
        assert len(child.terms().to_records())==4
        assert child.matrix.view().nnz==4
        assert child.rows.iloc[0].formula(order='name')
        v=child.vars.select('var',value=1)
        assert [r.activity for r in child.matrix.activity(v)]==[0,0]
        assert child.model._arrays is None and child.model._db is None
        assert not traces
    finally:child.close()


def test_native5_portable_delta_and_replay(model,tmp_path,monkeypatch):
    from mipframe.store.overlay import OverlayStore
    assert not hasattr(OverlayStore,'_csr')
    assert not hasattr(OverlayStore,'db')
    proof=model.check.linear_rows(targets=[2],witnesses=[[0,1]]).require()
    with model.edit().drop_rows([2],because=proof).commit() as child:
        saved=child.save(tmp_path/'saved')
        assert json.loads((saved/'manifest.json').read_text())['schema_version']==7
        assert not list(saved.rglob('facts.json'))
        assert (saved/'base/row_ptr.npy').read_bytes()==(model.model.path/'row_ptr.npy').read_bytes()
    relocated=tmp_path/'relocated'
    shutil.copytree(saved,relocated)
    shutil.rmtree(saved)
    with mf.load(relocated) as loaded:
        assert loaded.shape==(2,3) and loaded.nnz==4
        assert loaded.diff(model).semantic['mip']=='UNKNOWN'
        assert loaded.audit(source=model)['status']=='PROVED'
        assert loaded.model._arrays is None and loaded.model._db is None


@pytest.mark.parametrize('version',[3,4,5,6])
def test_old_native_rejected(tmp_path,version):
    (tmp_path/'manifest.json').write_text(json.dumps(dict(schema_version=version)))
    with pytest.raises(mf.MIPFrameError,match='re-import'):
        mf.load(tmp_path)


def test_large_exact_column_no_float_or_overflow():
    from mipframe.data.columns import ExactColumn,EnumColumn
    values=[2**100,-2**100,Fraction(1,3),2**100]
    column=ExactColumn(values)
    assert list(column)==values and len(column.values)==3
    assert column.ids.dtype==np.dtype('uint32')
    with pytest.raises(ValueError):column.ids.flags.writeable=True
    labels=EnumColumn('ub',('lb','ub','eq'),100000)
    assert labels.codes.nbytes==100000 and labels[-1]=='ub'


def test_graph_array_scratch_cleanup(monkeypatch):
    from mipframe.frame.executor import Context
    from mipframe.data.runtime import WorkContext
    monkeypatch.setattr(__import__('sqlite3'),'connect',lambda *a,**kw:pytest.fail('numeric SQL'))
    closed=[];original=WorkContext.close
    def close(self):
        closed.append(self.disk)
        original(self)
    monkeypatch.setattr(WorkContext,'close',close)
    edges=mf.Frame.from_records((dict(u=i,v=i+1) for i in range(1500)),schema={'u':'int','v':'int'})
    result=mf.ops.graph.path(edges,source=0,target=1500,memory_mb=0.015625)
    assert len(result)==1500 and closed and closed[-1]>0
    assert result.head(1).to_records()==[dict(step=0,u=0,v=1)]


def test_low_budget_repeated_terms_and_full_csc(tmp_path):
    """Many runs exercise bounded fan-in; exact cancellation crosses run boundaries."""
    from mipframe.data.builder import Writer
    writer=Writer(tmp_path,memory_mb=.015625)
    r=writer.new_row('r',1);v=writer.variable('x',1)
    writer.row_bounds(r,None,Fraction(1,3))
    for _ in range(5000):
        writer.term(r,v,Fraction(1,3));writer.term(r,v,Fraction(-1,3))
    writer.term(r,v,2**100)
    writer.finish();writer.close()
    assert np.load(tmp_path/'csc_row_ids.npy').tolist()==[0]
    assert np.load(tmp_path/'row_ptr.npy').tolist()==[0,1]
    assert np.load(tmp_path/'col_ptr.npy').tolist()==[0,1]
    assert np.load(tmp_path/'coeff_id.npy').tolist()==np.load(tmp_path/'csc_value_ids.npy').tolist()
    assert not list(tmp_path.glob('*.raw')) and not list(tmp_path.glob('terms-*'))


def test_overlay_ir_preserves_holes_without_matrix_copy(model,monkeypatch):
    from mipframe.store.overlay import OverlayStore
    from mipframe.frame.executor import Context
    assert not hasattr(OverlayStore,'_csr')
    assert not hasattr(OverlayStore,'db')
    import sqlite3
    original=sqlite3.connect;statements=[]
    def connect(*args,**kwargs):
        db=original(*args,**kwargs);db.set_trace_callback(statements.append);return db
    monkeypatch.setattr(sqlite3,'connect',connect)
    with model.edit().drop_rows([1]).build() as child:
        for method in ('template','exact_structural','algebraic','typed'):
            family=mf.analysis.families(child.rows,method=method)
            assert {r.row.id for r in family.related('members')}=={0,2}
        assert child.rows.families().related('histogram').count()>0
        assert child.ir.row_shapes().related('members').count()==2
        assert child.ir.name_templates('var').related('members').count()==3
        assert child.ir.structure['depth']==2
        assert child.ir.topology['connected_components']==1
        assert child.model._arrays is None and child.model._db is None

    assert not any(name in query for query in statements for name in ('row_data','var_data','definitions','key_chunks','template_tokens'))


def test_csc_consistency_validation_rejects_remapped_values(model):
    from mipframe.store.integrity import dimensions_valid
    from types import SimpleNamespace
    arrays=dict(model.model.data.arrays)
    arrays['csc_value_ids']=arrays['csc_value_ids'].copy()
    arrays['csc_value_ids'][0]=0
    fake=SimpleNamespace(data=SimpleNamespace(arrays=arrays),rows=model.model.rows,variables=model.model.variables)
    assert not dimensions_valid(fake)


def test_exact_sparse_spill_cancellation_bigints_and_duplicates(tmp_path):
    from fractions import Fraction
    from mipframe.data.exact_sparse import ExactSparse
    from mipframe.check.algorithm import DiskBudgetExceeded
    reference={}
    def records():
        for i in range(4500):
            v=i%301;c=Fraction((-1)**i*(10**30+i),i%7+1)
            reference[v]=reference.get(v,0)+c
            yield v,c
    storage=ExactSparse(0.015625)
    try:
        storage.build(records())
        assert dict(storage.items())=={v:c for v,c in reference.items() if c}
        assert storage.values.dtype.kind!='O' and storage.serial>32 and storage.disk>0
        directory=storage.temp.name
    finally:storage.close()
    assert not Path(directory).exists()
    storage=ExactSparse(0.015625)
    try:
        with pytest.raises(ValueError,match='Duplicate'):
            storage.build(((i%1000,0) for i in range(2000)),duplicates=True)
    finally:storage.close()
    storage=ExactSparse(0.015625,0.001)
    try:
        with pytest.raises(DiskBudgetExceeded):storage.build((i,Fraction(i+1,3)) for i in range(2000))
    finally:storage.close()


def test_patch_fixed_nonzero_and_domain_edits(tmp_path):
    source=tmp_path/'fixed.mps'
    source.write_text('NAME test\nROWS\n N obj\n L cap\n E eq\nCOLUMNS\n x obj 2 cap 3\n x eq 1\n y cap 1 eq 1\n z cap 1\nRHS\n rhs cap 10 eq 4\nBOUNDS\n FX bnd x 2\n UP bnd y 8\n UP bnd z 6\nENDATA\n')
    with mf.read(source) as m:
        x=m.vars.by_name('x')
        with m.edit().substitute_fixed([x]).commit() as child:
            out=tmp_path/'result.mps';receipt=child.write(out)
            assert receipt['writer']=='stream_patch'
            with mf.read(out) as reread:
                assert reread.objective.constant==4
                assert reread.rows.by_name('cap').ub==4
                assert reread.rows.by_name('eq').lb==2


def test_frame_certificate_streaming_and_identity(model,monkeypatch):
    from mipframe.frame.base import Frame
    from mipframe.frame.schema import ref_type
    m=model
    targets=mf.Frame.from_records([dict(target='c',rhs=0,relation='<=')],
        schema=dict(target='str',rhs='number',relation='str'))
    terms=mf.Frame.from_records([dict(target='c',var=m.vars.loc[0],coef=2),
        dict(target='c',var=m.vars.loc[0],coef=-1),dict(target='c',var=m.vars.loc[2],coef=-1)],
        schema=dict(target='str',var=ref_type(m,'var'),coef='number'),models=(m,))
    using=mf.Frame.from_records([dict(target='c',row=m.rows.loc[r],side='ub',multiplier=1) for r in [0,1]],
        schema=dict(target='str',row=ref_type(m,'row'),side='str',multiplier='number'),models=(m,))
    monkeypatch.setattr(Frame,'to_records',lambda *a,**kw:pytest.fail('whole certificate records'))
    result=m.check.linear_many(targets=targets,terms=terms,using=using,memory_mb=0.015625)
    assert result.ok and result.for_target('c').ok
    assert result._certificate.sources.tolist()==[0,1]
    assert mf.BatchProof.replay(result.to_spec(),model=m).ok


def test_successful_diagnostic_retains_details(model):
    from mipframe.check.diagnostics import diagnostic
    result=diagnostic(model,[dict(status='PROVED',diagnostics=[dict(reason='verified detail')])],kind='test')
    assert len(result.evidence['details'].to_records())==2
    assert result.evidence['details'].count()==2
    assert result.violations.count()==0


def test_edit_bitmap_budget_and_parent_lifetime(tmp_path):
    source=tmp_path/'bitmap.lp'
    source.write_text('Minimize\n obj:x\nSubject To\n'+''.join(f' r{i}:x<=1\n' for i in range(20000))+'Bounds\n 0<=x<=1\nEnd\n')
    with mf.read(source) as model:
        with model.edit(memory_mb=0.015625,disk_mb=0.0001) as edit:
            with pytest.raises(mf.DiskBudgetExceeded):edit.drop_rows([1])
            assert edit.summary()['rows_dropped']==0
        with model.edit(memory_mb=0.015625,disk_mb=1) as edit:
            edit.drop_rows(range(0,20000,2))
            assert edit._work.disk>0
            child=edit.build()
        model.close()
        try:assert child.shape==(10000,1) and child.nnz==10000
        finally:child.close()


def test_uint64_ids_and_workspace_overflow():
    from mipframe.data.runtime import WorkContext
    certificate=mf.CertificateBatch([0,1],[2**40])
    assert certificate.sources.dtype==np.dtype('uint64')
    with WorkContext() as work:
        with pytest.raises(OverflowError):work.array((2**62,8),'u8')


def test_noncontiguous_readback_and_exact_cancellation(tmp_path):
    from mipframe.store.mps_output import compare_readback
    from mipframe.check.kernel import facts
    source=tmp_path/'split.mps'
    source.write_text('NAME split\nROWS\n N obj\n L r\nCOLUMNS\n x r 2\n y r 3\n x r -1\nRHS\n rhs r 5\nENDATA\n')
    with mf.read(source) as model:
        result=compare_readback(facts(model),source,mps_format='free')
        assert result['nonzeros']==2
        source.write_text(source.read_text().replace('x r -1','x r -2'))
        with pytest.raises(ValueError,match='coefficient mismatch'):compare_readback(facts(model),source,mps_format='free')


def test_graph_output_keeps_big_integer_labels_exact():
    base=2**100
    edges=mf.Frame.from_records([dict(u=base,v=base+1),dict(u=base+1,v=base+2)],schema=dict(u='int',v='int'))
    result=mf.ops.graph.path(edges,source=base,target=base+2)
    assert [(r['u'],r['v']) for r in result.to_records()]==[(base,base+1),(base+1,base+2)]


def test_external_array_sort_stable_and_bounded():
    from mipframe.data.runtime import WorkContext
    from mipframe.data.sorting import lexorder
    from mipframe.check.algorithm import DiskBudgetExceeded
    rng=np.random.default_rng(74)
    a=rng.integers(-3,4,4096);b=rng.integers(-2,3,4096)
    expected=np.lexsort((b,a))
    with WorkContext(.016,2) as work:
        order=lexorder(work,(a,b))
        assert np.array_equal(order,expected)
        assert work.resident<=work.memory and work.disk>0
        directory=Path(work.temp.name)
    assert not directory.exists()
    with WorkContext(.016,.001) as work:
        with pytest.raises(DiskBudgetExceeded):lexorder(work,(a,b))


def test_typed_overlay_override_layers():
    from mipframe.data.overrides import Overrides,OverlayOverrides
    from mipframe.data.ids import IDSet
    a=Overrides({2:(0,Fraction(1,3)),5:(-float('inf'),7)},kind='bounds')
    b=Overrides({2:(1,4),9:(0,2)},kind='bounds')
    first=OverlayOverrides(None,a,IDSet())
    second=OverlayOverrides(first,b,IDSet([5]))
    assert dict(second)=={2:(1,4),9:(0,2)}
    assert first[2]==(0,Fraction(1,3)) and first[5]==(-float('inf'),7)
    assert second.layers[0] is a and second.layers[1] is b
    assert 5 not in second and 2**70 not in second


def test_solution_streams_frame_and_spills(model,monkeypatch):
    from mipframe.frame.base import Frame
    from mipframe.check.algorithm import DiskBudgetExceeded
    point=model.vars.select('var',value=0)
    monkeypatch.setattr(Frame,'to_records',lambda *a,**k:pytest.fail('Point record expansion'))
    report=model.check.solution(point,memory_mb=.016)
    try:
        assert report.ok
        assert report.to_spec()['values']=={'0':'0','1':'0','2':'0'}
        assert report._values.storage.values.dtype.names==('id','n','d','offset','length')
    finally:report.close()
    with pytest.raises(DiskBudgetExceeded):model.check.solution(point,memory_mb=.016,disk_mb=.00001)
