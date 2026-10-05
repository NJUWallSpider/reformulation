"""Versioned exact certificate streams. Imported verdicts never grant trust."""

import json
import os
import tempfile
from pathlib import Path
from mipframe.frame.executor import size, MaterializationLimitError
from mipframe.check.certificate import InvalidCertificateError


def topology(root):
    order = []
    state = {}
    stack = [(root, False)]
    while stack:
        r, done = stack.pop()
        r._guard()
        key = id(r)
        if done:
            state[key] = 2
            order.append(r)
            continue
        if state.get(key) == 1:
            raise InvalidCertificateError("Cyclic report dependencies")
        if state.get(key) == 2:
            continue
        state[key] = 1
        stack.append((r, True))
        stack.extend((p, False) for p in reversed(r._parents))
    return order


def events(report):
    if getattr(report, "_batch", None) is not None:
        raise InvalidCertificateError("Legacy batch Report serialization is unsupported; export BatchProof instead")
    reports = topology(report)
    ids = {id(r): i for i, r in enumerate(reports)}
    yield dict(type="header", format="mipframe-proof", version=1, root=ids[id(report)])
    for i, r in enumerate(reports):
        if r._recipe is None:
            raise InvalidCertificateError(
                "This diagnostic report has no executable recipe; export its evidence Frames explicitly"
            )
        recipe = dict(r._recipe)
        if recipe.get("kind") in ("frame", "execution") and "frame_object" in recipe:
            recipe["frame"] = recipe.pop("frame_object").to_spec()
        if recipe.get("kind") == "algorithm_contract" and not recipe.get("encoded"):
            from mipframe.ops._runtime import recipe_arguments

            recipe["arguments"] = recipe_arguments(
                recipe["arguments"], lambda f: f.to_spec()["plan"]
            )
            recipe["encoded"] = True
        snapshot = r.scope.get("snapshot")
        yield dict(
            type="node",
            node=i,
            parents=[ids[id(p)] for p in r._parents],
            recipe=recipe,
            snapshot=snapshot,
            domain=r.scope.get("domain"),
            status=r.status,
        )
        if recipe["kind"] in ("frame", "indexed", "model_integrity", "execution", "algorithm_contract"):
            continue
        raise InvalidCertificateError('Numerical proofs must use the compact CertificateBatch serializer')


def report_spec(report):
    records = []
    total = 0
    for e in events(report):
        total += size(e)
        if total > 128 * 1024**2:
            raise MaterializationLimitError(
                "Certificate exceeds 128 MiB; use report.write(path)"
            )
        records.append(e)
    return dict(format="mipframe-proof", version=1, events=records)


def write_report(report, path):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            for e in events(report):
                out.write(json.dumps(e, ensure_ascii=False, allow_nan=False) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        return path
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def replay(spec_or_path, *, models, relations=None, memory_mb=128, disk_mb=None):
    from mipframe.check.replay import replay as restore
    return restore(spec_or_path, models=models, relations=relations,
                   memory_mb=memory_mb, disk_mb=disk_mb)
