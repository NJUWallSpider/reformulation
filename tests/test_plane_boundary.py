"""Public numerical ingress and failures may never open a SQL connection."""
import sqlite3
from fractions import Fraction
import pytest
import mipframe as mf
from mipframe.frame.entity import RowFrame


def deny_sql(*args,**kwargs):raise AssertionError('Numeric execution attempted SQLite')


@pytest.mark.parametrize('budget',[128,0.015625])
def test_frame_ingress_operations_and_full_failure(monkeypatch,budget):
    monkeypatch.setattr(sqlite3,'connect',deny_sql)
    old=mf.options.query.memory_mb;mf.options.query.memory_mb=budget
    try:
        n=260
        f=mf.Frame.from_records((dict(k=i%17,v=Fraction(i,3)) for i in range(n)),schema={'k':'int','v':'number'},memory_mb=budget)
        assert f.check('unique','k',memory_mb=budget).summary['violations']==17
        g=f.group_by('k').agg(v=mf.col('v').sum())
        assert sum(r['v'] for r in g.to_records())==Fraction(n*(n-1),6)
        assert len(f.select('k').distinct())==17
        assert f.sort_by('v',descending=True).iloc[0].v==Fraction(n-1,3)
        with pytest.raises(mf.CardinalityError) as error:
            f.select('k').join(f.select('k'),on='k',cardinality='1:1').head(0).to_records()
        assert error.value.report.summary['violations']==34
    finally:mf.options.query.memory_mb=old


def test_selection_and_row_terms_do_not_open_sql(tmp_path,monkeypatch):
    path=tmp_path/'m.lp';path.write_text('Minimize\n x\nSubject To\n a: x + y <= 2\n b: x - y <= 1\n c: y <= 1\nBounds\n 0 <= x <= 2\n 0 <= y <= 2\nEnd\n')
    with mf.read(path) as m:
        monkeypatch.setattr(sqlite3,'connect',deny_sql)
        selected=RowFrame(m,[2,0,2,1])
        assert list(selected.ids)==[2,0,1]
        assert list(selected.iloc[::-1].ids)==[1,0,2]
        assert sum(r.terms.count() for r in m.rows)==5
        assert m.rows.check('unique','row').ok
        assert 'spill backend: mmap' in str(selected.explain())


def test_filter_limit_streams_and_disk_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(sqlite3,'connect',deny_sql)
    from mipframe.frame.base import Frame
    seen=[]
    def source(fields,ctx):
        for i in range(100000):
            seen.append(i)
            yield {n:i for n in fields}
    f=Frame({'v':'int'},source=source)
    assert f.where(mf.col('v')>=2).head(1).to_records(memory_mb=.015625)==[{'v':2}]
    assert seen==[0,1,2]
    with pytest.raises(mf.DiskBudgetExceeded):
        mf.Frame.from_records(({'v':i} for i in range(10000)),schema={'v':'int'},memory_mb=.015625,disk_mb=.001)


def test_data_dependency_boundary():
    import ast
    from pathlib import Path
    root=Path(__file__).parents[1]/'src/mipframe/data'
    forbidden=('sqlite3','mipframe.frame','mipframe.check','mipframe.alg','mipframe.ops','mipframe.edit','mipframe.ir','mipframe.control','mipframe.store')
    violations=[]
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            if any(m==f or m.startswith(f+'.') for m in modules for f in forbidden):violations.append((path.name,node.lineno))
    assert not violations


def test_literal_membership_builds_once_and_preserves_exact_nulls(monkeypatch):
    from mipframe.frame.expr import QueryExpr
    original=QueryExpr.evaluate
    calls=0
    def evaluate(self,row,ctx):
        nonlocal calls
        if self.op=='literal':calls+=1
        return original(self,row,ctx)
    f=mf.Frame.from_records(({'v':i} for i in range(1000)),schema={'v':'int'})
    query=f.where(mf.col('v').isin(range(500)))
    monkeypatch.setattr(QueryExpr,'evaluate',evaluate)
    assert query.count()==500
    assert calls==500
    q=mf.Frame.from_records([{'v':None},{'v':1},{'v':Fraction(3,2)},{'v':10**30}],schema={'v':mf.DType('number',True)})
    assert q.where(mf.col('v').isin([None,Fraction(3,2),10**30])).count()==2
    assert q.where(mf.col('v').isin([1,2])).count()==1


def test_typed_batches_keep_their_lease_after_iteration_and_model_close(tmp_path):
    source=tmp_path/'m.lp';source.write_text('Minimize\n x\nSubject To\n r:x<=2\nEnd\n')
    m=mf.read(source)
    batches=list(m.rows.iter_batches(batch_size=1,memory_mb=.015625))
    ident=m.rows.iloc[0].id
    m.close()
    assert batches[0].to_records()[0]['row'].id==ident
    from mipframe.frame.base import RecordBatch
    batch=RecordBatch({'v':'int'},({'v':i} for i in range(500)),memory_mb=.015625)
    assert len(batch)==500 and batch.to_records()[-1]=={'v':499}
    with pytest.raises(mf.MaterializationLimitError):batch.to_records(materialize_mb=.015625)


def test_loaded_refinement_uses_external_cache_and_new_budget(tmp_path):
    import hashlib
    from mipframe.ir.signatures import refinement
    source=tmp_path/'m.lp';source.write_text('Minimize\n x\nSubject To\n r:x-y<=0\nBounds\n 0<=x<=2\n 0<=y<=2\nEnd\n')
    with mf.read(source) as m:
        old=refinement(m,depth=1)
        small=refinement(m,depth=1,memory_mb=.015625)
        assert old._store is not small._store
        assert small._store.work.memory==16384
        saved=m.save(tmp_path/'saved')
    def snapshot():return {str(p.relative_to(saved)):hashlib.sha256(p.read_bytes()).hexdigest() for p in saved.rglob('*') if p.is_file()}
    before=snapshot()
    with mf.load(saved) as loaded:
        result=refinement(loaded,depth=2,memory_mb=.015625)
        assert result._store.work.ledger.peak_resident<=16384
        assert loaded.model.data.cache_directory!=loaded.model.path
    assert snapshot()==before


def test_point_bundle_replay_and_materialization_limit(tmp_path):
    source=tmp_path/'m.lp';source.write_text('Minimize\n x\nSubject To\n r:x<=2\nEnd\n')
    with mf.read(source) as m:
        report=m.check.solution({0:1})
        saved=report.write(tmp_path/'point',memory_mb=.015625)
        assert mf.Report.replay(saved,models=[m],memory_mb=.015625).ok
        assert report.to_spec()['values']=={'0':'1'}


def test_reduction_retains_validated_input_for_certificate_bridge(tmp_path):
    source=tmp_path/'m.lp'
    source.write_text('Minimize\n x\nSubject To\n a:x-y<=0\n b:y-z<=0\n c:x-z<=0\nEnd\n')
    with mf.read(source) as m:
        scanned=[]
        def scan(fields,ctx):
            for row,u,v in ((0,0,1),(1,1,2),(2,0,2)):
                scanned.append(row)
                values=dict(row=row,u=u,v=v)
                yield {name:values[name] for name in fields}
        edge=mf.Frame(dict(row=m.rows.schema['row'],u=m.vars.schema['var'],v=m.vars.schema['var']),models=(m,),source=scan)
        result=mf.ops.graph.transitive_reduction(edge,witnesses=True)
        certificate=result.witnesses.as_linear_certificate()
        assert len(result.kept.to_records())==2
        assert len(result.removed.to_records())==1
        assert scanned==[0,1,2]
        result.close()
        assert m.check.linear_many(certificate).ok


@pytest.mark.parametrize('budget',[128,.015625])
@pytest.mark.parametrize('keys',[(2**53+1,2**53+2),(10**40,10**40+1),(Fraction(1,3),Fraction(2,3))])
def test_join_ranges_preserve_exact_keys_and_stable_input_order(budget,keys):
    a,b=keys
    left=mf.Frame.from_records([dict(k=b,l=0),dict(k=a,l=1),dict(k=b,l=2)],schema={'k':'number','l':'int'},memory_mb=budget)
    right=mf.Frame.from_records([dict(k=a,r=7),dict(k=b,r=8)],schema={'k':'number','r':'int'},memory_mb=budget)
    rows=left.join(right,on='k',cardinality='m:1',require='both').to_records(memory_mb=budget)
    assert [(v['l'],v['r']) for v in rows]==[(0,8),(1,7),(2,8)]
    nullable=mf.DType('number',True)
    l=mf.Frame.from_records([dict(k=a,s=1,l=0),dict(k=None,s=2,l=1)],schema={'k':nullable,'s':'int','l':'int'},memory_mb=budget)
    r=mf.Frame.from_records([dict(k=None,s=2,r=7),dict(k=a,s=1,r=8)],schema={'k':nullable,'s':'int','r':'int'},memory_mb=budget)
    assert [v['r'] for v in l.join(r,on=('k','s'),cardinality='1:1',how='left').to_records(memory_mb=budget)]==[8,None]
    with pytest.raises(mf.CardinalityError) as exc:
        l.join(r,on=('k','s'),cardinality='1:1',require='both').head(0).to_records(memory_mb=budget)
    assert len(exc.value.report.violations)==2


def test_primitive_stream_preserves_exact_comparisons_and_lazy_errors(tmp_path):
    f=mf.Frame.from_records([dict(v=2**53+1),dict(v=2**53+2)],schema={'v':'int'})
    assert f.where(mf.col('v')==2**53+1).count()==1
    assert f.where(mf.col('v')>Fraction(2**54+3,2)).count()==1
    assert f.where(mf.col('v')<10**40).count(memory_mb=.015625)==2
    zero=mf.Frame.from_records([dict(v=1),dict(v=0)],schema={'v':'int'})
    assert zero.where(1/mf.col('v')==1).head(1).count()==1
    source=tmp_path/'batch.lp';source.write_text('Minimize\n x\nSubject To\n a:x-y<=0\n b:0 x<=0\n c:y-z<=0\nEnd\n')
    with mf.read(source) as m:
        selected=m.rows.where(nnz=2).terms.where(coef=1)
        assert selected.count(memory_mb=.015625)==2
        assert [r['row'].id for r in selected.to_records(memory_mb=.015625)]==[0,2]


def test_byte_pool_full_equality_under_hash_collision_and_rewind(monkeypatch):
    from mipframe.data import intern
    from mipframe.data.runtime import WorkContext
    class Collision:
        def __init__(self,*args,**kwargs):pass
        def update(self,chunk):pass
        def digest(self):return b'\0'*8
    monkeypatch.setattr(intern.hashlib,'blake2b',Collision)
    with WorkContext(.015625) as work:
        pool=intern.BytePool(work)
        values=[b'x'*1500+b'a',b'x'*1500+b'b',b'short']
        for i,value in enumerate(values):assert pool.intern((value,))==i
        for i,value in enumerate(values):
            assert pool.intern(iter((value[:700],value[700:])))==i
            assert pool.value(i)==value
        assert len(pool.data)==sum(map(len,values))
        assert pool.intern((b'next',))==3
        assert pool.value(3)==b'next'
        assert work.ledger.peak_resident<=16384


@pytest.mark.parametrize('budget',[128,.015625])
def test_sparse_projection_counts_full_spans_in_selection_order(budget):
    import numpy as np
    from mipframe.data.projection import sparse_projection
    from mipframe.data.runtime import WorkContext
    # Empty rows, disjoint spans, nonmonotone selection, and more than one
    # batch of matches. Counts must not stop after the second witness.
    degree=300
    ptr=np.array([0,degree,degree,degree+2,degree+3,degree+6],dtype='<u8')
    neighbors=np.concatenate((np.arange(degree),[1,3,2,1,2,3])).astype('<u4')
    sources=np.array([4,1,0,2],dtype='<i8')
    targets=np.arange(degree,dtype='<u4')
    with WorkContext(budget) as work:
        counts,values=sparse_projection(work,ptr,neighbors,sources,targets)
        assert list(counts)==[3,0,degree,2]
        assert values[1]==-1
        assert values[0] in (1,2,3) and values[2] in range(degree) and values[3] in (1,3)
        empty,_=sparse_projection(work,ptr,neighbors,sources,targets[:0])
        assert not np.any(empty)
        assert work.ledger.peak_resident<=budget*1024**2
