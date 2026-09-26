"""JSON CLI over the same public Python API."""

import argparse, json, sys
from pathlib import Path
from .api import IR
from .numbers import LPIRError


def main(argv=None):
    p = argparse.ArgumentParser(prog="lpir")
    subs = p.add_subparsers(dest="command", required=True)
    b = subs.add_parser("build")
    b.add_argument("source")
    b.add_argument("--out", required=True)
    for opt in ("rhs", "bounds", "ranges"):
        b.add_argument("--" + opt)
    b.add_argument("--memory-mb", type=float, default=128)
    b.add_argument("--mps-format", choices=["auto", "fixed", "free"])
    for name in (
        "overview",
        "describe",
        "inspect",
        "query",
        "render",
        "verify",
        "export",
        "generate",
        "analyze",
        "validate",
        "probe",
    ):
        q = subs.add_parser(name)
        q.add_argument("artifact")
        q.add_argument(
            "--raw-ids",
            action="store_true",
            help="Show internal identities instead of artifact-local short references",
        )
        if name == "inspect":
            q.add_argument("kind")
            q.add_argument("--key")
        if name == "query":
            q.add_argument("kind", nargs="?")
            q.add_argument("--key")
            q.add_argument("--handle")
            q.add_argument("--spec", help="JSON string, or @path to JSON file")
        if name == "analyze":
            q.add_argument("kind")
            q.add_argument("--scope")
            q.add_argument("--handle")
        if name == "validate":
            q.add_argument("--spec", required=True)
        if name in ("inspect", "query", "analyze", "probe", "describe", "render"):
            q.add_argument("--offset", type=int, default=0)
            q.add_argument("--limit", type=int, default=20)
        if name in ("query", "analyze", "probe", "validate"):
            q.add_argument("--seconds", type=float, default=30)
            q.add_argument("--memory-mb", type=float, default=128)
        if name in ("query", "analyze", "validate"):
            q.add_argument("--candidates", type=int, default=1000000)
        if name in ("render", "describe"):
            q.add_argument("--max-bytes", type=int, default=20000)
        if name == "render":
            q.add_argument("--format", choices=["markdown"], default="markdown")
            q.add_argument("--out")
        if name in ("export", "generate"):
            q.add_argument("--out", required=True)
        if name == "generate":
            group = q.add_mutually_exclusive_group(required=True)
            group.add_argument("--copies", type=int)
            group.add_argument("--target-mb", type=float)
    a = p.parse_args(argv)

    def obj(value):
        return json.loads(
            Path(value[1:]).read_text() if value.startswith("@") else value
        )

    try:
        if a.command == "build":
            result = IR.build(
                a.source,
                a.out,
                memory_mb=a.memory_mb,
                **{
                    k: getattr(a, k)
                    for k in ("rhs", "bounds", "ranges", "mps_format")
                    if getattr(a, k) is not None
                },
            ).overview()
        else:
            ir = IR(a.artifact)
            c = a.command
            if c == "describe":
                result = ir.describe(
                    offset=a.offset,
                    limit=a.limit,
                    max_bytes=a.max_bytes,
                    raw_ids=a.raw_ids,
                )
            elif c == "overview":
                result = ir.overview()
            elif c == "inspect":
                result = ir.inspect(a.kind, a.key, a.offset, a.limit)
            elif c == "query":
                if a.spec:
                    result = ir.query(
                        spec=obj(a.spec),
                        offset=a.offset,
                        limit=a.limit,
                        budget={
                            "seconds": a.seconds,
                            "memory_mb": a.memory_mb,
                            "candidates": a.candidates,
                        },
                    )
                else:
                    result = ir.query(
                        a.kind,
                        key=a.key,
                        offset=a.offset,
                        limit=a.limit,
                        handle=a.handle,
                    )
            elif c == "analyze":
                result = ir.analyze(
                    a.kind,
                    scope=obj(a.scope) if a.scope else None,
                    handle=a.handle,
                    offset=a.offset,
                    limit=a.limit,
                    budget={
                        "seconds": a.seconds,
                        "memory_mb": a.memory_mb,
                        "candidates": a.candidates,
                    },
                )
            elif c == "validate":
                result = ir.validate(
                    obj(a.spec),
                    budget={
                        "seconds": a.seconds,
                        "memory_mb": a.memory_mb,
                        "candidates": a.candidates,
                    },
                )
            elif c == "probe":
                result = ir.probe(
                    seconds=a.seconds,
                    memory_mb=a.memory_mb,
                    offset=a.offset,
                    limit=a.limit,
                )
            elif c == "verify":
                result = ir.verify()
            elif c == "render":
                text = ir.render(
                    offset=a.offset,
                    limit=a.limit,
                    max_bytes=a.max_bytes,
                    raw_ids=a.raw_ids,
                )
                if a.out:
                    Path(a.out).write_text(text)
                    result = {"output": a.out, "bytes": len(text.encode())}
                else:
                    print(text, end="")
                    return
            elif c == "export":
                ir.export(a.out)
                result = {"output": a.out}
            else:
                from .generate import generate

                result = generate(ir.model, a.out, a.copies, a.target_mb)
        if (
            a.command
            in ("overview", "inspect", "query", "analyze", "validate", "probe")
            and not a.raw_ids
            and hasattr(ir, "readable")
        ):
            result = ir.readable(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (LPIRError, OSError, KeyError, ValueError, TypeError, AttributeError) as e:
        print(
            json.dumps({"error": str(e), "type": type(e).__name__}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(2)
