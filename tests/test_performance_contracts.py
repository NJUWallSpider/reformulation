"""Correctness and resource invariants of the optimized proof/edit paths."""
from fractions import Fraction
import gc
import weakref

import pytest
import mipframe as mf


@pytest.fixture
def m(tmp_path):
    p = tmp_path / 'perf.lp'
    p.write_text('''Minimize
 obj: x
Subject To
 cap: x + y <= 4
 old: x + 1197 z <= 1200
Bounds
 0 <= x <= 4
 0 <= y <= 4
 0 <= z <= 1
Generals
 z
End
''')
    with mf.read(p) as model:
        yield model


def test_logical_build_numeric_save_and_retry(m, tmp_path, monkeypatch):
    from mipframe.model import Model
    real = Model._build_ir
    calls = []
    def fail(self, **kw):
        calls.append(self)
        raise RuntimeError('analysis unavailable')
    monkeypatch.setattr(Model, '_build_ir', fail)
    e = m.edit()
    e.add_row(m.vars.by_name('x') <= 3)
    with e.build() as n:
        assert n._ir is None and not calls
        assert n.shape == (3, 3)
        assert n.nnz == 5
        assert len(n.rows.to_records()) == 3
        assert 'constraints' in repr(n)
        assert n.diff(m).nnz_after == 5
        n.save(tmp_path / 'logical.native')
        assert not calls
        with pytest.raises(RuntimeError, match='analysis unavailable'):
            n.ir
        assert n.rows.count() == 3 and n._ir is None
        monkeypatch.setattr(Model, '_build_ir', real)
        assert n.ir is n.ir
        assert not getattr(n, '_indexed_results', {})


def test_partial_lazy_failure_keeps_numeric_and_retries(m, monkeypatch):
    from mipframe.model import Model
    with m.edit().build() as n:
        real = Model.indexed
        def fail(*a, **k):
            raise RuntimeError('indexed interrupted')
        monkeypatch.setattr(Model, 'indexed', fail)
        scan = n.ir.to_dict()
        with pytest.raises(RuntimeError, match='indexed interrupted'):
            n.ir.indexed
        assert n.ir.to_dict() == scan and n.nnz == m.nnz
        monkeypatch.setattr(Model, 'indexed', real)
        assert n.ir.indexed['status'] == 'complete'
        assert n.ir.to_dict() == scan


def test_receipt_survives_edit_close_and_matches_replay(m, tmp_path, monkeypatch):
    e=m.edit();row=m.rows.by_name('cap')
    proof=m.check.linear(row.ub_claim,using=[row.ub_claim])
    e.add_row(row.ub_claim,name='extra',because=proof)
    n=e.commit();e.close()
    with n:
        from mipframe.check.compact import BatchProof
        monkeypatch.setattr(BatchProof,'replay',lambda *a,**k:pytest.fail('Implicit replay'))
        auto=n.diff(m)
        assert auto.semantic['mip']=='SAME' and len(auto.rows_added)==1
        assert n._store._arrays is None
        path=n.save(tmp_path/'saved.native')
        with mf.load(path) as external:
            assert external.diff(m).semantic['mip']=='UNKNOWN'
        monkeypatch.undo()
        with mf.load(path) as external:
            assert external.audit(source=m)['semantic']==auto.semantic


def test_receipt_does_not_retain_edit_or_source(m):
    e = m.edit()
    n = e.build()
    ref = weakref.ref(e)
    e.close()
    del e
    gc.collect()
    assert ref() is None
    # The retained maps belong only to the child, not to the Edit/source store.
    assert n._exact_facts.parent is m._exact_facts
    m.close()
    assert n.nnz == 4
    with pytest.raises(mf.MIPFrameError): n.diff(m)
    n.close()


@pytest.mark.parametrize('budget', [128, 0.015625])
def test_batch_table_count_and_exact_spill(m, budget):
    with m.check.linear_batch(residual='bounds', memory_mb=budget) as b:
        traces = []
        x = m.vars.by_name('x')
        m.model.db.set_trace_callback(traces.append)
        for i in range(40):
            b.add(key=str(i), target=x <= 5+i)
        r = b.run()
    assert r.ok
    assert not traces
    for i in (0, 39):
        report = r.for_target(str(i))
        assert report.ok
        assert report.evidence['bounds'].to_records() == report.evidence['bounds'].to_records()
        assert mf.Report.replay(report.to_spec(), models=[m], memory_mb=budget).ok


def test_large_sparse_residual_spills_exactly(tmp_path):
    p = tmp_path/'wide.lp'
    terms = ' + '.join(f'x{i}' for i in range(80))
    p.write_text(f'Minimize\n obj: x0\nSubject To\n cap: {terms} <= 80\nBounds\n' +
                 ''.join(f' 0 <= x{i} <= 1\n' for i in range(80)) + 'End\n')
    with mf.read(p) as n:
        target = n.rows.by_name('cap').ub_claim
        for budget in (128, 0.015625):
            with n.check.linear_batch(residual='bounds', memory_mb=budget) as b:
                b.add(key='wide', target=target)
                b.add(key='cancel', target=target, using=[target])
                r=b.run()
            assert r.ok
            assert len(r.for_target('wide').evidence['bounds']) == 80
            assert len(r.for_target('cancel').evidence['bounds']) == 0
