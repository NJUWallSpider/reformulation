"""Descriptor bounds, shared proof ownership, batch semantics and failure recovery."""

import gc
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import weakref

import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "proof.lp"
    path.write_text("""Minimize
 obj: x
Subject To
 cap: x + y <= 4
 eq: x - y = 0
 gate: x - 4 z >= -2
Bounds
 0 <= x <= 4
 0 <= y <= 4
 0 <= z <= 1
Generals
 x y
Binaries
 z
End
""")
    with mf.read(path) as model:
        yield model


@pytest.mark.parametrize("mode", ["retained", "discarded", "expressions", "batch"])
def test_5000_results_under_low_fd_limit(mode):
    if os.name != "posix":
        pytest.skip("RLIMIT_NOFILE stress probe requires POSIX")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("proof_resource_probe.py")), mode, "5000"],
        env={**os.environ, "PYTHONPATH": str(root / "src")}, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_builder_matches_scalar_conditions_directions_and_replay(model, memory):
    x, y, z = (model.vars.by_name(n) for n in ("x", "y", "z"))
    equality = model.rows.by_name("eq").eq_claim
    parent = model.check.linear(x >= 2, using=[model.rows.by_name("gate").lb_claim], assume=[z.eq(1)])
    cases = [
        ("bound", x + y <= 4, [model.rows.by_name("cap").ub_claim], []),
        ("failed", x + y <= 3, [model.rows.by_name("cap").ub_claim], []),
        ("inherited", x >= 1, [parent], []),
        ("explicit", x <= 0, [], [x.eq(0)]),
        ("equality", (x - y).eq(0), [equality], []),
    ]
    scalar = [model.check.linear(t, using=u, assume=a, residual="bounds", memory_mb=memory)
              for _, t, u, a in cases]
    for (key, target, using, assume), expected in zip(cases,scalar):
        with model.check.linear_batch(residual="bounds", memory_mb=memory) as builder:
            assert builder.add(key=key,target=target,using=using,assume=assume) is builder
            batch=builder.run()
        replayed=mf.Report.replay(batch.to_spec(),models=[model],memory_mb=memory)
        for actual in (batch.for_target(key),replayed.for_target(key)):
            assert actual.status==expected.status
            assert actual.scope==expected.scope
            assert actual.conclusion.expr==expected.conclusion.expr
            assert actual.evidence["bounds"].to_records()==expected.evidence["bounds"].to_records()
        with pytest.raises(ValueError):builder.run()
        if key=="inherited":
            with pytest.raises(mf.UndischargedAssumptionError):model.edit().add_row(x>=1,because=batch.for_target(key))
    with model.check.linear_batch() as builder:
        builder.add(key="a",target=x<=4,assume=[x.eq(0)])
        with pytest.raises(ValueError,match="share assumptions"):
            builder.add(key="b",target=x<=4,assume=[x.eq(1)])


def test_views_and_edit_keep_only_needed_proofs_alive(model):
    x = model.vars.by_name("x")
    parent = model.check.linear(x <= 4, residual="bounds")
    child = model.check.linear(x <= 5, using=[parent])
    references=[weakref.ref(parent.batch),weakref.ref(child.batch)]
    view, conclusion = child.evidence["terms"], child.conclusion
    edit = model.edit()
    edit.add_row(x <= 5, because=child)
    del parent, child
    gc.collect()
    assert view.count() > 0 and conclusion.expr.constant == -5
    assert all(r() is not None for r in references)
    built = edit.build()
    built.close()
    edit.close()
    del edit, view, conclusion, built
    gc.collect()
    assert all(r() is None for r in references)


def test_failed_publication_preserves_previous_proofs(model, monkeypatch):
    import mipframe.check.compiler as compiler
    x=model.vars.by_name("x")
    parent=model.check.linear(x<=4,residual="bounds")
    def fail(*args,**kwargs):raise RuntimeError("injected publication failure")
    with monkeypatch.context() as patch:
        patch.setattr(compiler,"BatchProof",fail)
        with pytest.raises(RuntimeError,match="publication"):
            model.check.linear(x<=5,using=[parent])
    assert parent.ok and mf.Report.replay(parent.to_spec(),models=[model]).ok
    assert model.check.linear(x<=5,using=[parent]).ok


def test_large_expressions_share_spill_and_reclaim_rows(tmp_path):
    names = [f"x{i}" for i in range(64)]
    path = tmp_path / "wide.lp"
    path.write_text("Minimize\n obj: x0\nSubject To\n c: " + " + ".join(names)
                    + " <= 64\nBounds\n" + "\n".join(f" 0 <= {n} <= 1" for n in names) + "\nEnd\n")
    with mf.read(path) as m:
        expressions = [m.linexpr(vars=m.vars, coef=i + 1, memory_mb=0.015625) for i in range(128)]
        import weakref
        stores=[weakref.ref(e._store) for e in expressions]
        directories=[Path(e._store.temp.name) for e in expressions]
        assert expressions[-1].evaluate(m.vars.select('var',value=1)) == 128 * 64
        terms = expressions[0].terms
        expressions.clear()
        gc.collect()
        assert terms.count() == 64
        assert sum(ref() is not None for ref in stores)==1
        assert sum(path.exists() for path in directories)==1
        del terms
        gc.collect()
        assert all(ref() is None for ref in stores)
        assert all(not path.exists() for path in directories)


def test_archive_dependencies_keep_snapshot_lifecycle_per_node(model, tmp_path):
    model.save(tmp_path / "native")
    x = model.vars.by_name("x")
    independent = model.check.linear(x <= 4, residual="bounds")
    with mf.load(tmp_path / "native") as other:
        alias = other.vars.by_name("x")
        dependent = model.check.linear(x + alias <= 8, residual="bounds")
        frame = dependent.evidence["terms"]
        other.close()
        assert dependent.ok and frame.to_records()
        assert independent.ok


def test_model_close_attempts_all_query_stores(model):
    calls = []
    class FailedStore:
        def _close_connection(self):
            calls.append("descriptor")
        def close(self):
            calls.append("cleanup")
            raise OSError("injected cleanup failure")
    first, second = FailedStore(), FailedStore()
    model._query_stores.update((first, second))
    with pytest.raises(OSError, match="injected cleanup failure"):
        model.close()
    assert calls == ["descriptor", "descriptor", "cleanup", "cleanup"]
    model._query_stores.discard(first)
    model._query_stores.discard(second)


def test_builder_failure_and_cancellation_leave_sources_valid(model):
    x = model.vars.by_name("x")
    parent = model.check.linear(x <= 4, residual="bounds")
    with pytest.raises(ValueError, match="Duplicate"):
        with model.check.linear_batch() as b:
            b.add(key="x", target=x <= 5, using=[parent])
            b.add(key="x", target=x <= 5, using=[parent])
    assert parent.ok
    with pytest.raises(KeyboardInterrupt):
        with model.check.linear_batch() as b:
            b.add(key="x", target=x <= 5, using=[parent])
            raise KeyboardInterrupt()
    assert parent.ok
    with pytest.raises(mf.InvalidCertificateError, match="[Ee]quality"):
        with model.check.linear_batch() as b:
            b.add(key="valid", target=x <= 5, using=[parent])
            b.add(key="invalid", target=x.eq(4), using=[x.ub_claim])
            b.run()
    assert parent.ok
    with model.check.linear_batch() as empty:
        assert empty.run().ok


def test_first_archive_failure_closes_unpublished_storage(model, monkeypatch):
    import mipframe.check.compiler as compiler
    from mipframe.data.runtime import WorkContext
    closed=[];real=WorkContext.close
    def close(work):
        real(work);closed.append(work.closed)
    def fail(*args,**kwargs):raise RuntimeError("injected first publication failure")
    with monkeypatch.context() as patch:
        patch.setattr(WorkContext,"close",close)
        patch.setattr(compiler,"BatchProof",fail)
        with pytest.raises(RuntimeError,match="first publication") as caught:
            model.check.linear(model.vars.by_name("x")<=4,residual="bounds")
        assert caught.value.__traceback__ is not None
        assert closed and all(closed)
    assert model.check.linear(model.vars.by_name("x")<=4,residual="bounds").ok


def test_typed_context_release_without_gc(model):
    from mipframe.frame.executor import Context
    ctx=Context((model,),memory_mb=.015625);reference=weakref.ref(ctx)
    gc.disable()
    try:
        table,_=ctx.stage(model.rows)
        assert len(table)==len(model.rows)
        del table,ctx
        assert reference() is None
    finally:gc.enable()


def test_constructor_cleanup_preserves_original_error(model,monkeypatch):
    from mipframe.data.runtime import WorkContext
    import numpy as np
    work=WorkContext(memory_mb=.015625)
    def fail(*args,**kwargs):raise OSError('injected mmap failure')
    monkeypatch.setattr(np.lib.format,'open_memmap',fail)
    with pytest.raises(OSError,match='injected mmap failure'):work.array(20000,'u1')
    assert work.disk==0
    work.close()
