"""Bounded proof import, dependency scheduling and checker-specific replay.

Imported verdicts are never trusted: each node executes its checker again.
"""
from collections.abc import Mapping
from fractions import Fraction
import json
from pathlib import Path

from mipframe.frame.base import Frame
from mipframe.frame.schema import DType, SchemaError, ref_type
from mipframe.linear.expr import LinExpr, bind_model
from mipframe.linear.claim import Claim
from mipframe.check.certificate import InvalidCertificateError
from mipframe.store.algorithm_store import AlgorithmStore


def proof_events(spec):
    if isinstance(spec, (str, Path)):
        with open(spec, encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)
    else:
        if spec.get("format") != "mipframe-proof" or spec.get("version") != 1:
            raise SchemaError("Unsupported proof specification")
        yield from spec["events"]


class ProofInput:
    """Normalize imported events into bounded, indexed staging tables."""

    def __init__(self, ctx, spec):
        self.ctx = ctx
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

        header = None
        for e in proof_events(spec):
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
        self.header = header
        self.nodes, self.claims, self.terms = nodes, claims, terms
        self.assumes, self.sources = assumes, sources
        self.values, self.variables, self.case_rows = values, variables, case_rows

    def ordered_nodes(self, reports):
        """Yield ready nodes; validate all parent references and reject cycles."""
        ctx = self.ctx
        pending = ctx.raw_table("id INTEGER PRIMARY KEY")
        ctx.execute(f"INSERT INTO {pending} SELECT id FROM {self.nodes}")
        while ctx.execute(f"SELECT 1 FROM {pending} LIMIT 1").fetchone():
            progress = False
            for ident, payload in ctx.execute(
                f"SELECT n.id,n.payload FROM {self.nodes} n JOIN {pending} p ON p.id=n.id ORDER BY n.id"
            ):
                node = json.loads(payload)
                parents = node["parents"]
                if any(
                    type(parent) is not int
                    or ctx.execute(f"SELECT 1 FROM {self.nodes} WHERE id=?", (parent,)).fetchone() is None
                    for parent in parents
                ):
                    raise InvalidCertificateError("Missing report dependency")
                if not all(parent in reports for parent in parents):
                    continue
                yield ident, node
                ctx.execute(f"DELETE FROM {pending} WHERE id=?", (ident,))
                progress = True
            if not progress:
                raise InvalidCertificateError("Cyclic report dependency graph")


class ReplayNode:
    """Reconstruct one node's claims and invoke its original checker."""

    def __init__(self, data, ident, node, reports, models, relations, options):
        self.data, self.ctx, self.ident = data, data.ctx, ident
        self.node, self.recipe = node, node["recipe"]
        self.parents, self.reports = node["parents"], reports
        self.models, self.relations, self.options = models, relations, options

    def frame(self, spec):
        from mipframe.frame.serialization import frame_from_spec
        return frame_from_spec(spec, models={m.uid: m for m in self.models}, relations=self.relations)

    def run(self):
        kind = self.recipe["kind"]
        if kind in ("execution", "algorithm_contract"):
            return self.execution()
        if kind == "frame":
            recipe = self.recipe
            return self.frame(recipe["frame"]).check(
                recipe["check"], recipe.get("columns"), key=recipe.get("key"),
                value=recipe.get("value"), memory_mb=self.options["memory_mb"],
            )
        self.owner = bind_model(self.node["snapshot"], self.models)
        if kind == "model_integrity":
            from mipframe.check.model import verify
            return verify(self.owner, runs=self.recipe["runs"])
        if kind == "indexed":
            return self.indexed()
        handlers = {
            "cases": self.cases, "identity": self.identity, "linear": self.linear,
            "infeasible": self.infeasible, "round": self.round,
            "enumerate": self.enumerate, "solution": self.solution,
        }
        if kind not in handlers:
            raise SchemaError("Unknown checker recipe")
        options = dict(self.options, domain=self.node.get("domain") or "auto")
        return handlers[kind](options)

    def execution(self):
        from mipframe.ops._runtime import restore
        from mipframe.check.report import Report
        recipe, ctx = self.recipe, self.ctx
        kind = recipe["kind"]
        try:
            if kind == "execution":
                len(self.frame(recipe["frame"]))  # Execute complete validation barriers.
            else:
                arguments = dict(recipe, algorithm_version=1)
                arguments["resources"] = dict(arguments["resources"], **self.options)
                restore(arguments, lambda plan: self.frame(dict(version=1, plan=plan)))
        except Exception as exc:
            if getattr(exc, "report", None) is None:
                raise
            return exc.report
        violations = ctx.publish({"reason": DType("str")}, [])
        result = Report(ctx, True, dict(operation=kind, scope="full declared inputs"),
                        violations, recipe=recipe)
        # This data assertion owns staging if input bindings changed.
        ctx.retained = True
        return result

    def indexed(self):
        from mipframe.ir.indexing.validation import restore_spec
        recipe, owner = self.recipe, self.owner
        indexed = getattr(owner, "_indexed_results", {}).get(recipe["run"])
        if indexed is None:
            raise SchemaError("The completed indexed run is not bound; load its saved snapshot")
        if "spec" in recipe:
            return indexed.validate(restore_spec(recipe["spec"], owner))
        if "selection" in recipe:
            return indexed.verify(self.frame(recipe["selection"]))
        return indexed.verify()

    def claim(self, cid):
        ctx, n, owner = self.ctx, self.ident, self.owner
        claims, terms = self.data.claims, self.data.terms
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
                    else owner.vars).loc[source["id"]],
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


    def assumptions(self):
        for (cid,) in self.ctx.execute(
            f"SELECT claim FROM {self.data.assumes} WHERE node=? ORDER BY claim", (self.ident,),
        ):
            yield self.claim(cid)

    def sources(self, direction="main", constraints=False):
        for (payload,) in self.ctx.execute(
            f"SELECT payload FROM {self.data.sources} WHERE node=? ORDER BY id", (self.ident,),
        ):
            data = json.loads(payload)
            if data["direction"] != direction:
                continue
            parent = data["parent"]
            if parent is not None and (type(parent) is not int or not 0 <= parent < len(self.parents)):
                raise InvalidCertificateError("Invalid parent report reference")
            source = self.claim(data["claim"]) if parent is None else self.reports[self.parents[parent]]
            multiplier = Fraction(data["multiplier"])
            if constraints:
                if multiplier != 1:
                    raise InvalidCertificateError("Enumeration source multiplier must equal one")
                yield source
            else:
                yield multiplier, source

    def cases(self, options):
        var = self.recipe["var"]
        if type(var) is not int or not 0 <= var < self.owner.shape[1]:
            raise InvalidCertificateError("Invalid case variable")
        return self.owner.check.cases(self.claim(self.recipe["target"]),
                                     var=self.owner.vars.loc[var], cases=CaseReports(self), **options)

    def identity(self, options):
        return self.owner.check.identity(self.claim(self.recipe["target"]).expr, 0,
                                        using=self.sources(), assume=self.assumptions(), **options)

    def linear(self, options):
        use = {d: self.sources(d) for d in ("le", "ge")} if self.recipe.get("double") else self.sources()
        return self.owner.check.linear(self.claim(self.recipe["target"]), using=use,
                                       assume=self.assumptions(), residual=self.recipe["residual"], **options)

    def infeasible(self, options):
        return self.owner.check.infeasible(using=self.sources(), assume=self.assumptions(),
                                           residual=self.recipe["residual"], **options)

    def round(self, options):
        return self.owner.check.round(self.claim(self.recipe["target"]),
                                     from_=self.reports[self.parents[self.recipe["parent"]]], **options)

    def enumerate(self, options):
        def read(fields, execution):
            for (var,) in self.ctx.execute(
                f"SELECT v FROM {self.data.variables} WHERE node=? ORDER BY v", (self.ident,),
            ):
                yield {"var": var}
        frame = Frame({"var": ref_type(self.owner, "var")}, key=("var",),
                      models=(self.owner,), source=read)
        return self.owner.check.enumerate(
            self.claim(self.recipe["target"]), vars=frame.refs("var"),
            using=self.sources(constraints=True), assume=self.assumptions(),
            max_states=self.recipe["max_states"], time_limit=self.recipe["time_limit"], **options,
        )

    def solution(self, options):
        def read(fields, execution):
            for var, value in self.ctx.execute(
                f"SELECT v,c FROM {self.data.values} WHERE node=? ORDER BY v", (self.ident,),
            ):
                row = dict(var=var, value=Fraction(value))
                yield {k: row[k] for k in fields}
        frame = Frame({"var": ref_type(self.owner, "var"), "value": DType("number")},
                      models=(self.owner,), source=read)
        return self.owner.check.solution(frame, missing=self.recipe["missing"], **options)


class CaseReports(Mapping):
    def __init__(self, replay):
        self.replay = replay

    def __len__(self):
        r = self.replay
        return r.ctx.execute(f"SELECT count(*) FROM {r.data.case_rows} WHERE node=?", (r.ident,)).fetchone()[0]

    def __iter__(self):
        return (key for key, _ in self.items())

    def __getitem__(self, key):
        for candidate, report in self.items():
            if candidate == key:
                return report
        raise KeyError(key)

    def items(self):
        r = self.replay
        for value, parent in r.ctx.execute(
            f"SELECT value,parent FROM {r.data.case_rows} WHERE node=? ORDER BY rowid", (r.ident,),
        ):
            if not 0 <= parent < len(r.parents):
                raise InvalidCertificateError("Invalid case parent reference")
            yield Fraction(value), r.reports[r.parents[parent]]


def replay(spec_or_path, *, models, relations=None, memory_mb=128, disk_mb=None):
    stream = proof_events(spec_or_path)
    try:
        first = next(stream, {})
    finally:
        stream.close()
    if first.get("batch") is True:
        from mipframe.check.batch import replay_batch
        return replay_batch(spec_or_path, models=models, relations=relations, memory_mb=memory_mb, disk_mb=disk_mb)
    model_list = tuple(models.values()) if isinstance(models, dict) else (models,) if hasattr(models, "uid") else tuple(models)
    ctx = AlgorithmStore(model_list, memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None)
    reports = {}
    try:
        data = ProofInput(ctx, spec_or_path)
        options = dict(memory_mb=memory_mb, disk_mb=disk_mb)
        for ident, node in data.ordered_nodes(reports):
            result = ReplayNode(data, ident, node, reports, model_list, relations, options).run()
            # Retired parents retain disk data, not a full per-report cache budget.
            if hasattr(result._store, "execute"):
                result._store.execute("PRAGMA cache_size=-1")
            reports[ident] = result
        if data.header["root"] not in reports:
            raise InvalidCertificateError("Missing root report")
        return reports[data.header["root"]]
    except BaseException:
        from mipframe.store.lifecycle import release_all
        release_all(report._store.close for report in reports.values())
        raise
    finally:
        # Replayed claims were copied into checker storage.
        if not ctx.retained:
            ctx.close()
