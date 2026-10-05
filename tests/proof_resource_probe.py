"""Subprocess stress probe: run with MODE COUNT, never change the test runner's limits."""

import gc
import json
import os
from pathlib import Path
import resource
import sys
from tempfile import TemporaryDirectory

import mipframe as mf


def fd_count():
    path = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    return len(os.listdir(path))


def run(mode, count):
    unraisable = []
    sys.unraisablehook = lambda error: unraisable.append(str(error.exc_value))
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "tiny.lp"
        path.write_text("Minimize\n obj: x\nSubject To\n cap: x <= 1\nBounds\n 0 <= x <= 1\nEnd\n")
        with mf.read(path) as model:
            x = model.vars.by_name("x")
            claim, source = model.rows.by_name("cap").ub_claim, x.ub_claim
            gc.collect()
            gc.disable()
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(256, soft), hard))
            baseline = fd_count()
            peak = baseline
            reports = []
            if mode == "batch":
                with model.check.linear_batch(residual="bounds") as builder:
                    for i in range(count):
                        builder.add(key=str(i), target=claim, using=[source], assume=[x.eq(0)])
                        if i % 100 == 0:
                            peak = max(peak, fd_count())
                    batch = builder.run().require()
                reports = [batch.for_target(str(i)) for i in range(count)]
                assert all(r.scope["assumptions"] == 1 for r in reports)
            elif mode == "expressions":
                reports = [2 * x + 3 * x - x for _ in range(count)]
                assert reports[-1].evaluate({x: 1}) == 4
            else:
                for i in range(count):
                    report = model.check.linear(claim, using=[source]).require()
                    if mode == "retained":
                        reports.append(report)
                    del report
                    if i % 100 == 0:
                        peak = max(peak, fd_count())
            peak = max(peak, fd_count())
            assert peak - baseline <= 8, (mode, baseline, peak)
            if mode == "retained":
                assert len({id(r.batch._facts) for r in reports}) == 1
                assert not hasattr(reports[0],"_store")
                assert mf.Report.replay(reports[-1].to_spec(), models=[model]).ok
            reports.clear()
            if mode == "batch":
                del batch
            assert fd_count() - baseline <= 2
            assert not unraisable, unraisable
            print(json.dumps(dict(mode=mode, count=count, baseline=baseline, peak=peak,
                                  remaining=fd_count(), automatic_gc=False)))
    gc.enable()
    gc.collect()
    assert not unraisable, unraisable


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]))
