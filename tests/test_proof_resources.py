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
        ("equality", (x - y).eq(0), {"le": [equality], "ge": [(-1, equality)]}, []),
    ]
    scalar = [model.check.linear(t, using=u, assume=a, residual="bounds", memory_mb=memory)
              for _, t, u, a in cases]
    with model.check.linear_batch(residual="bounds", memory_mb=memory) as builder:
        for key, target, using, assume in cases:
            assert builder.add(key=key, target=target, using=using, assume=assume) is builder
        batch = builder.run()
    assert batch.status == "INCONCLUSIVE"
    replayed = mf.Report.replay(batch.to_spec(), models=[model], memory_mb=memory)
    for (key, _, _, _), expected in zip(cases, scalar):
        for actual in (batch.for_target(key), replayed.for_target(key)):
            assert actual.status == expected.status
            assert actual.scope == expected.scope
            assert actual.conclusion.expr == expected.conclusion.expr
            assert actual.evidence["bounds"].to_records() == expected.evidence["bounds"].to_records()
    with pytest.raises(ValueError):
        builder.run()
    with pytest.raises(mf.UndischargedAssumptionError):
        model.edit().add_row(x >= 1, because=batch.for_target("inherited"))


def test_views_and_edit_keep_only_needed_proofs_alive(model):
    x = model.vars.by_name("x")
    parent = model.check.linear(x <= 4, residual="bounds")
    child = model.check.linear(x <= 5, using=[parent])
    archive = model._certificate_archive()
    view, conclusion = child.evidence["terms"], child.conclusion
    edit = model.edit()
    edit.add_row(x <= 5, because=child)
    del parent, child
    gc.collect()
    assert view.count() > 0 and conclusion.expr.constant == -5
    assert archive.s.execute(f"SELECT count(*) FROM {archive.result}").fetchone()[0] == 2
    built = edit.build()
    built.close()
    edit.close()
    del edit, view, conclusion
    gc.collect()
    assert archive.s.execute(f"SELECT count(*) FROM {archive.result}").fetchone()[0] == 0
    assert not archive.parents


def test_failed_publication_preserves_previous_proofs(model, monkeypatch):
    from mipframe.check.batch import CertificateArchive

    x = model.vars.by_name("x")
    parent = model.check.linear(x <= 4, residual="bounds")
    archive = model._certificate_archive()
    capture = CertificateArchive._capture

    def fail(self, *args):
        capture(self, *args)
        raise RuntimeError("injected publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(CertificateArchive, "_capture", fail)
        with pytest.raises(RuntimeError, match="injected publication failure"):
            model.check.linear(x <= 5, using=[parent])
    assert parent.ok and mf.Report.replay(parent.to_spec(), models=[model]).ok
    assert archive.s.execute(f"SELECT count(*) FROM {archive.result}").fetchone()[0] == 1
    assert not archive.parents
    assert model.check.linear(x <= 5, using=[parent]).ok


def test_large_expressions_share_spill_and_reclaim_rows(tmp_path):
    names = [f"x{i}" for i in range(64)]
    path = tmp_path / "wide.lp"
    path.write_text("Minimize\n obj: x0\nSubject To\n c: " + " + ".join(names)
                    + " <= 64\nBounds\n" + "\n".join(f" 0 <= {n} <= 1" for n in names) + "\nEnd\n")
    with mf.read(path) as m:
        expressions = [m.linexpr(vars=m.vars, coef=i + 1, memory_mb=0.015625) for i in range(128)]
        assert len({id(e._store.archive) for e in expressions}) == 1
        archive = m._expression_archive()
        assert expressions[-1].evaluate({v: 1 for v in m.vars}) == 128 * 64
        terms = expressions[0].terms
        expressions.clear()
        gc.collect()
        assert terms.count() == 64
        assert archive.store.execute(f"SELECT count(DISTINCT expr) FROM {archive.table}").fetchone()[0] == 1
        del terms
        gc.collect()
        assert archive.store.execute(f"SELECT count(*) FROM {archive.table}").fetchone()[0] == 0


def test_archive_dependencies_keep_snapshot_lifecycle_per_node(model, tmp_path):
    model.save(tmp_path / "native")
    x = model.vars.by_name("x")
    independent = model.check.linear(x <= 4, residual="bounds")
    with mf.load(tmp_path / "native") as other:
        alias = other.vars.by_name("x")
        dependent = model.check.linear(x + alias <= 8, residual="bounds")
        frame = dependent.evidence["terms"]
        other.close()
        with pytest.raises(mf.MIPFrameError):
            dependent.status
        with pytest.raises(mf.MIPFrameError):
            frame.to_records()
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
    with pytest.raises(mf.SchemaError, match="Duplicate"):
        with model.check.linear_batch() as b:
            b.add(key="x", target=x <= 5, using=[parent])
            b.add(key="x", target=x <= 5, using=[parent])
    assert parent.ok
    with pytest.raises(KeyboardInterrupt):
        with model.check.linear_batch() as b:
            b.add(key="x", target=x <= 5, using=[parent])
            raise KeyboardInterrupt()
    assert parent.ok
    with pytest.raises(mf.InvalidCertificateError, match="Equality"):
        with model.check.linear_batch() as b:
            b.add(key="valid", target=x <= 5, using=[parent])
            b.add(key="invalid", target=x.eq(4), using=[x.ub_claim])
            b.run()
    assert parent.ok
    with model.check.linear_batch() as empty:
        assert empty.run().ok


def test_first_archive_failure_closes_unpublished_storage(model, monkeypatch):
    from mipframe.check.batch import CertificateArchive

    def fail(*args):
        raise RuntimeError("injected first publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(CertificateArchive, "_capture", fail)
        with pytest.raises(RuntimeError, match="first publication") as caught:
            model.check.linear(model.vars.by_name("x") <= 4, residual="bounds")
        # Keep the exception/traceback alive: it must not keep unpublished FDs.
        assert caught.value.__traceback__ is not None
        archive = model._certificate_archive()
        assert archive is not None and archive.s.db is None
    assert model.check.linear(model.vars.by_name("x") <= 4, residual="bounds").ok


def test_sqlite_callbacks_release_without_gc(model):
    from mipframe.frame.executor import Context

    ctx = Context((model,))
    reference = weakref.ref(ctx)
    gc.disable()
    try:
        ctx.execute("CREATE TABLE numbers(v TEXT)")
        ctx.execute("INSERT INTO numbers VALUES ('1/3'),('2/3')")
        assert ctx.execute("SELECT exact_sum(v) FROM numbers").fetchone()[0] == "1"
        del ctx
        assert reference() is None
    finally:
        gc.enable()


def test_constructor_cleanup_preserves_original_error(model, monkeypatch):
    from mipframe.frame import executor
    from mipframe.store import lifecycle
    from tempfile import TemporaryDirectory

    created = []
    class FailedCleanup(TemporaryDirectory):
        def cleanup(self):
            raise OSError(24, "injected cleanup exhaustion")

    def directory(*args, **kwargs):
        temp = FailedCleanup(*args, **kwargs)
        created.append(temp)
        return temp

    with monkeypatch.context() as patch:
        patch.setattr(executor, "TemporaryDirectory", directory)
        patch.setattr(sqlite3, "connect", lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("injected connect failure")))
        with pytest.raises(sqlite3.OperationalError, match="injected connect failure") as caught:
            executor.Context((model,)).execute("SELECT 1")
        assert "cleanup" in " ".join(caught.value.__notes__)
    for temp in created:
        lifecycle._pending.pop(temp.name, None)
        TemporaryDirectory.cleanup(temp)
