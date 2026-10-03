"""Versioned exact certificate streams. Imported verdicts never grant trust."""

import json
import os
import tempfile
from pathlib import Path
from fractions import Fraction
from mipframe.frame.schema import SchemaError, encode
from mipframe.frame.executor import size, MaterializationLimitError
from mipframe.linear.expr import LinExpr, bind_model
from mipframe.linear.claim import Claim
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
        from mipframe.check.batch import batch_events
        yield from batch_events(report)
        return
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
        if recipe["kind"] in ("frame", "indexed", "execution", "algorithm_contract"):
            continue
        ctx = r._store
        for ident, rel, c, axis, entity, side in ctx.execute(
            f"SELECT * FROM {ctx.claims} ORDER BY id"
        ):
            yield dict(
                type="claim",
                node=i,
                id=ident,
                relation=rel,
                constant=c,
                axis=axis,
                entity=entity,
                side=side,
            )
        for cl, v, c in ctx.execute(f"SELECT * FROM {ctx.terms} ORDER BY claim,v"):
            yield dict(type="term", node=i, claim=cl, var=v, coef=c)
        for (ident,) in ctx.execute(
            f"SELECT claim FROM {ctx.assumptions} ORDER BY claim"
        ):
            yield dict(type="assumption", node=i, claim=ident)
        for ident, d, cl, a, p in ctx.execute(
            f"SELECT * FROM {ctx.sources} ORDER BY id"
        ):
            yield dict(
                type="source",
                node=i,
                id=ident,
                direction=d,
                claim=cl,
                multiplier=a,
                parent=p,
            )
        if recipe["kind"] == "solution":
            for row in r.evidence["values"]._raw(("var", "value"), ctx):
                yield dict(
                    type="value", node=i, var=row["var"], value=str(row["value"])
                )
        if recipe["kind"] == "enumerate":
            for row in r.evidence["variables"]._raw(("var",), ctx):
                yield dict(type="variable", node=i, var=row["var"])
        if recipe["kind"] == "cases":
            for row in r.evidence["cases"]._raw(("value", "parent"), ctx):
                yield dict(type="case", node=i, value=str(row["value"]), parent=row["parent"])


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
    if isinstance(spec_or_path, (str, Path)):
        with open(spec_or_path) as f:
            first = next((json.loads(line) for line in f if line.strip()), {})
    else:
        first = next(iter(spec_or_path.get("events", ())), {})
    if first.get("batch") is True:
        from mipframe.check.batch import replay_batch
        return replay_batch(spec_or_path, models=models, relations=relations, memory_mb=memory_mb, disk_mb=disk_mb)
    from mipframe.store.algorithm_store import AlgorithmStore
    from mipframe.frame.base import Frame
    from mipframe.frame.schema import DType, ref_type
    from mipframe.frame.materialized import stored_frame

    model_list = (
        tuple(models.values())
        if isinstance(models, dict)
        else (models,) if hasattr(models, "uid") else tuple(models)
    )
    ctx = AlgorithmStore(
        model_list, memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None
    )
    reports = {}
    try:
        nodes = ctx.raw_table("id INTEGER PRIMARY KEY,payload TEXT")
        claims = ctx.raw_table(
            "node INTEGER,id INTEGER,payload TEXT,PRIMARY KEY(node,id)"
        )
        terms = ctx.raw_table(
            "node INTEGER,claim INTEGER,v INTEGER,c TEXT,PRIMARY KEY(node,claim,v)"
        )
        assumes = ctx.raw_table("node INTEGER,claim INTEGER,PRIMARY KEY(node,claim)")
        sources = ctx.raw_table(
            "node INTEGER,id INTEGER,payload TEXT,PRIMARY KEY(node,id)"
        )
        values = ctx.raw_table("node INTEGER,v INTEGER,c TEXT,PRIMARY KEY(node,v)")
        variables = ctx.raw_table("node INTEGER,v INTEGER,PRIMARY KEY(node,v)")
        case_rows = ctx.raw_table("node INTEGER,value TEXT,parent INTEGER,PRIMARY KEY(node,value)")

        def stream():
            if isinstance(spec_or_path, (str, Path)):
                with open(spec_or_path, encoding="utf-8") as inp:
                    for line in inp:
                        if line.strip():
                            yield json.loads(line)
            else:
                if (
                    spec_or_path.get("format") != "mipframe-proof"
                    or spec_or_path.get("version") != 1
                ):
                    raise SchemaError("Unsupported proof specification")
                yield from spec_or_path["events"]

        header = None
        for e in stream():
            ctx.check_size(e)
            typ = e.get("type")
            if typ == "header":
                if (
                    header is not None
                    or e.get("format") != "mipframe-proof"
                    or e.get("version") != 1
                ):
                    raise SchemaError("Invalid proof header")
                header = e
                continue
            n = e.get("node")
            if type(n) is not int or n < 0:
                raise SchemaError("Invalid proof node ID")
            if typ == "node":
                ctx.execute(f"INSERT INTO {nodes} VALUES(?,?)", (n, json.dumps(e)))
            elif typ == "claim":
                ctx.execute(
                    f"INSERT INTO {claims} VALUES(?,?,?)", (n, e["id"], json.dumps(e))
                )
            elif typ == "term":
                from mipframe.linear.scalar import q

                v = e["var"]
                if type(v) is not int or v < 0:
                    raise SchemaError("Invalid variable ID")
                ctx.execute(
                    f"INSERT INTO {terms} VALUES(?,?,?,?)",
                    (n, e["claim"], v, str(q(e["coef"]))),
                )
            elif typ == "assumption":
                ctx.execute(f"INSERT INTO {assumes} VALUES(?,?)", (n, e["claim"]))
            elif typ == "source":
                ctx.execute(
                    f"INSERT INTO {sources} VALUES(?,?,?)", (n, e["id"], json.dumps(e))
                )
            elif typ == "value":
                from mipframe.linear.scalar import q

                ctx.execute(
                    f"INSERT INTO {values} VALUES(?,?,?)",
                    (n, e["var"], str(q(e["value"]))),
                )
            elif typ == "variable":
                ctx.execute(f"INSERT INTO {variables} VALUES(?,?)", (n, e["var"]))
            elif typ == "case":
                from mipframe.linear.scalar import q

                value = q(e["value"])
                parent = e["parent"]
                if value.denominator != 1 or type(parent) is not int or parent < 0:
                    raise InvalidCertificateError("Invalid case record")
                if ctx.execute(f"SELECT 1 FROM {case_rows} WHERE node=? AND value=?", (n, str(value))).fetchone():
                    raise InvalidCertificateError("Duplicate case record")
                ctx.execute(f"INSERT INTO {case_rows} VALUES(?,?,?)", (n, str(value), parent))
            else:
                raise SchemaError("Unknown certificate record")
        if header is None:
            raise SchemaError("Missing proof header")
        # Topological order is validated, rather than trusted from serialized statuses.
        pending = ctx.raw_table("id INTEGER PRIMARY KEY")
        ctx.execute(f"INSERT INTO {pending} SELECT id FROM {nodes}")
        while ctx.execute(f"SELECT 1 FROM {pending} LIMIT 1").fetchone():
            progress = False
            for n, payload in ctx.execute(
                f"SELECT n.id,n.payload FROM {nodes} n JOIN {pending} p ON p.id=n.id ORDER BY n.id"
            ):
                node = json.loads(payload)
                ps = node["parents"]
                if any(
                    type(p) is not int
                    or ctx.execute(f"SELECT 1 FROM {nodes} WHERE id=?", (p,)).fetchone()
                    is None
                    for p in ps
                ):
                    raise InvalidCertificateError("Missing report dependency")
                if not all(p in reports for p in ps):
                    continue
                recipe = node["recipe"]
                kind = recipe["kind"]
                opts = dict(memory_mb=memory_mb, disk_mb=disk_mb)
                if kind in ("execution", "algorithm_contract"):
                    from mipframe.frame.serialization import frame_from_spec
                    from mipframe.ops._runtime import restore

                    def build(plan):
                        return frame_from_spec(
                            dict(version=1, plan=plan),
                            models={m.uid: m for m in model_list},
                            relations=relations,
                        )

                    try:
                        if kind == "execution":
                            f = build(recipe["frame"]["plan"])
                            len(f)  # Execute complete validation barriers.
                        else:
                            arguments = dict(recipe)
                            arguments["algorithm_version"] = 1
                            arguments["resources"] = dict(
                                arguments["resources"],
                                memory_mb=memory_mb,
                                disk_mb=disk_mb,
                            )
                            f = restore(arguments, build)
                    except Exception as exc:
                        if getattr(exc, "report", None) is None:
                            raise
                        result = exc.report
                    else:
                        from mipframe.check.report import Report

                        violations = ctx.publish({"reason": DType("str")}, [])
                        result = Report(
                            ctx,
                            True,
                            dict(operation=kind, scope="full declared inputs"),
                            violations,
                            recipe=recipe,
                        )
                        # This data assertion owns the staging context if input bindings changed.
                        ctx.retained = True
                elif kind == "frame":
                    from mipframe.frame.serialization import frame_from_spec

                    f = frame_from_spec(
                        recipe["frame"],
                        models={m.uid: m for m in model_list},
                        relations=relations,
                    )
                    result = f.check(
                        recipe["check"],
                        recipe.get("columns"),
                        key=recipe.get("key"),
                        value=recipe.get("value"),
                        memory_mb=memory_mb,
                    )
                elif kind == "indexed":
                    owner = bind_model(node["snapshot"], model_list)
                    indexed = getattr(owner, "_indexed_results", {}).get(recipe["run"])
                    if indexed is None:
                        raise SchemaError(
                            "The completed indexed run is not bound; load its saved snapshot"
                        )
                    result = (
                        indexed.validate(recipe["spec"])
                        if "spec" in recipe
                        else indexed.verify()
                    )
                else:
                    owner = bind_model(node["snapshot"], model_list)

                    def claim(cid):
                        got = ctx.execute(
                            f"SELECT payload FROM {claims} WHERE node=? AND id=?",
                            (n, cid),
                        ).fetchone()
                        if got is None:
                            raise InvalidCertificateError("Missing claim record")
                        data = json.loads(got[0])
                        bad = ctx.execute(
                            f"SELECT 1 FROM {terms} WHERE node=? AND claim=? AND v>=? LIMIT 1",
                            (n, cid, owner.shape[1]),
                        ).fetchone()
                        if bad:
                            raise SchemaError("Variable outside snapshot")
                        e = LinExpr(
                            owner,
                            Fraction(data["constant"]),
                            store=ctx,
                            query=f"SELECT v,c FROM {terms} WHERE node={n} AND claim={int(cid)} ORDER BY v",
                        )
                        source = (
                            None
                            if data["axis"] is None
                            else dict(
                                axis=data["axis"], id=data["entity"], side=data["side"]
                            )
                        )
                        result = Claim(e, data["relation"], _source=source)
                        if source is not None:
                            from mipframe.linear.claim import source_claim

                            if source["axis"] not in ("row", "var"):
                                raise InvalidCertificateError("Invalid source axis")
                            actual = source_claim(
                                (
                                    owner.rows
                                    if source["axis"] == "row"
                                    else owner.vars
                                ).by_id(source["id"]),
                                source["side"],
                            )
                            if (
                                actual.relation != result.relation
                                or actual.expr != result.expr
                            ):
                                raise InvalidCertificateError(
                                    "Imported claim disagrees with the original model side"
                                )
                        return result

                    def assumption_items():
                        for (cid,) in ctx.execute(
                            f"SELECT claim FROM {assumes} WHERE node=? ORDER BY claim",
                            (n,),
                        ):
                            yield claim(cid)

                    def source_items(direction="main", constraints=False):
                        for (payload,) in ctx.execute(
                            f"SELECT payload FROM {sources} WHERE node=? ORDER BY id",
                            (n,),
                        ):
                            data = json.loads(payload)
                            if data["direction"] != direction:
                                continue
                            p = data["parent"]
                            if p is not None and (
                                type(p) is not int or not 0 <= p < len(ps)
                            ):
                                raise InvalidCertificateError(
                                    "Invalid parent report reference"
                                )
                            src = claim(data["claim"]) if p is None else reports[ps[p]]
                            if constraints:
                                if Fraction(data["multiplier"]) != 1:
                                    raise InvalidCertificateError(
                                        "Enumeration source multiplier must equal one"
                                    )
                                yield src
                            else:
                                yield Fraction(data["multiplier"]), src

                    options = dict(opts, domain=node.get("domain") or "auto")
                    check = owner.check
                    if kind == "cases":
                        from collections.abc import Mapping

                        class Cases(Mapping):
                            def __len__(self):
                                return ctx.execute(f"SELECT count(*) FROM {case_rows} WHERE node=?", (n,)).fetchone()[0]

                            def __iter__(self):
                                return (k for k, _ in self.items())

                            def __getitem__(self, key):
                                for k, value in self.items():
                                    if k == key:
                                        return value
                                raise KeyError(key)

                            def items(self):
                                for k, p in ctx.execute(f"SELECT value,parent FROM {case_rows} WHERE node=? ORDER BY rowid", (n,)):
                                    if not 0 <= p < len(ps):
                                        raise InvalidCertificateError("Invalid case parent reference")
                                    yield Fraction(k), reports[ps[p]]

                        v = recipe["var"]
                        if type(v) is not int or not 0 <= v < owner.shape[1]:
                            raise InvalidCertificateError("Invalid case variable")
                        result = check.cases(claim(recipe["target"]), var=owner.vars.by_id(v), cases=Cases(), **options)
                    elif kind == "identity":
                        result = check.identity(
                            claim(recipe["target"]).expr,
                            0,
                            using=source_items(),
                            assume=assumption_items(),
                            **options,
                        )
                    elif kind == "linear":
                        use = (
                            {d: source_items(d) for d in ("le", "ge")}
                            if recipe.get("double")
                            else source_items()
                        )
                        result = check.linear(
                            claim(recipe["target"]),
                            using=use,
                            assume=assumption_items(),
                            residual=recipe["residual"],
                            **options,
                        )
                    elif kind == "infeasible":
                        result = check.infeasible(
                            using=source_items(),
                            assume=assumption_items(),
                            residual=recipe["residual"],
                            **options,
                        )
                    elif kind == "round":
                        result = check.round(
                            claim(recipe["target"]),
                            from_=reports[ps[recipe["parent"]]],
                            **options,
                        )
                    elif kind == "enumerate":

                        def read(fields, execution):
                            for (v,) in ctx.execute(
                                f"SELECT v FROM {variables} WHERE node=? ORDER BY v",
                                (n,),
                            ):
                                yield {"var": v}

                        f = Frame(
                            {"var": ref_type(owner, "var")},
                            key=("var",),
                            models=(owner,),
                            source=read,
                        )
                        result = check.enumerate(
                            claim(recipe["target"]),
                            vars=f.refs("var"),
                            using=source_items(constraints=True),
                            assume=assumption_items(),
                            max_states=recipe["max_states"],
                            time_limit=recipe["time_limit"],
                            **options,
                        )
                    elif kind == "solution":

                        def read(fields, execution):
                            for v, c in ctx.execute(
                                f"SELECT v,c FROM {values} WHERE node=? ORDER BY v",
                                (n,),
                            ):
                                row = dict(var=v, value=Fraction(c))
                                yield {k: row[k] for k in fields}

                        f = Frame(
                            {"var": ref_type(owner, "var"), "value": DType("number")},
                            models=(owner,),
                            source=read,
                        )
                        result = check.solution(f, missing=recipe["missing"], **options)
                    else:
                        raise SchemaError("Unknown checker recipe")
                # Retired parent databases retain disk data, not a full per-report cache budget.
                result._store.execute("PRAGMA cache_size=-1")
                reports[n] = result
                ctx.execute(f"DELETE FROM {pending} WHERE id=?", (n,))
                progress = True
            if not progress:
                raise InvalidCertificateError("Cyclic report dependency graph")
        if header["root"] not in reports:
            raise InvalidCertificateError("Missing root report")
        result = reports[header["root"]]
        # Replayed claims are copied into the checker's store, so the staging DB can close.
        return result
    except BaseException:
        for r in reports.values():
            r._store.close()
        raise
    finally:
        if not ctx.retained:
            ctx.close()
