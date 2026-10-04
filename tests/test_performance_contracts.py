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
        assert len(n._indexed_results) == 1


def test_partial_lazy_failure_keeps_numeric_and_retries(m, monkeypatch):
    from mipframe.model import Model
    with m.edit().build() as n:
        real = Model.indexed
        def fail(*a, **k):
            raise RuntimeError('indexed interrupted')
        monkeypatch.setattr(Model, 'indexed', fail)
        with pytest.raises(RuntimeError, match='indexed interrupted'):
            n.ir
        assert n._ir is None and n.nnz == m.nnz
        monkeypatch.setattr(Model, 'indexed', real)
        assert n.ir is n.ir


def test_receipt_survives_edit_close_and_matches_replay(m, tmp_path, monkeypatch):
    from mipframe.edit.lineage import Lineage
    e = m.edit()
    row = m.rows.by_name('cap')
    proof = m.check.linear(row.ub_claim, using=[row.ub_claim])
    e.add_row(row.ub_claim, name='extra', because=proof)
    n = e.build()
    e.close()
    with n:
        auto = n.diff(m)
        assert auto._frames == {} and auto._stats == {}
        strict = n.diff(m, verify='replay')
        assert auto.semantic == strict.semantic
        for field in ('rows_added','rows_dropped','vars_added','vars_dropped',
                      'row_bounds_changed','var_bounds_changed','domains_changed','rows_changed'):
            assert getattr(auto,field).to_records() == getattr(strict,field).to_records()
        assert auto.row_nnz_stats == strict.row_nnz_stats
        assert auto.col_nnz_stats == strict.col_nnz_stats
        path = n.save(tmp_path / 'saved.native')
        calls = []
        original = Lineage._restore
        def observed(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        monkeypatch.setattr(Lineage, '_restore', observed)
        assert n.diff(m).semantic == auto.semantic
        assert not calls
        with mf.load(path) as external:
            assert external.diff(m).semantic == auto.semantic
            assert calls == [1]
            external.diff(m)
            assert calls == [1]
            external.diff(m, verify='replay')
            assert calls == [1,1]
        with pytest.raises(ValueError): n.diff(m, verify='unchecked')


def test_receipt_does_not_retain_edit_or_source(m):
    e = m.edit()
    n = e.build()
    ref = weakref.ref(e)
    e.close()
    del e
    gc.collect()
    assert ref() is None
    # The retained maps belong only to the child, not to the Edit/source store.
    assert n._verified_edit.s.models == (n,)
    m.close()
    assert n.nnz == 4
    with pytest.raises(mf.MIPFrameError): n.diff(m)
    n.close()


@pytest.mark.parametrize('budget', [128, 0.015625])
def test_batch_table_count_and_exact_spill(m, budget):
    with m.check.linear_batch(residual='bounds', memory_mb=budget) as b:
        traces = []
        b._store.db.set_trace_callback(traces.append)
        x = m.vars.by_name('x')
        for i in range(40):
            b.add(key=str(i), target=x <= 5+i)
        r = b.run()
    assert r.ok
    creates = [s for s in traces if s.startswith('CREATE TABLE')]
    assert len(creates) < 50
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
