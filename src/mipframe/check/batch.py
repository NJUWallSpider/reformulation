"""Batch linear certificates share one scratch database and normalized result tables."""

import json
import sqlite3
import weakref
from collections.abc import Mapping
from fractions import Fraction
from mipframe.store.algorithm_store import AlgorithmStore
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType, ref_type, SchemaError, encode, decode
from mipframe.linear.expr import LinExpr, owners
from mipframe.linear.claim import Claim, source_claim
from mipframe.linear.scalar import q
from mipframe.check.report import Report
from mipframe.check.certificate import ProofContext, InvalidCertificateError


def restore_json(x):
    if isinstance(x, dict):
        if set(x) in ({"rational"}, {"infinity"}, {"ref"}):
            return decode(x)
        return {k: restore_json(v) for k, v in x.items()}
    if isinstance(x, list):
        return [restore_json(v) for v in x]
    return x


# Every target uses the same normalized relations; no table or Python object per target.
TABLES = {
    "claims": "id INTEGER,relation TEXT,constant TEXT,axis TEXT,entity INTEGER,side TEXT",
    "terms": "claim INTEGER,v INTEGER,c TEXT",
    "assumptions": "claim INTEGER",
    "sources": "id INTEGER,direction TEXT,claim INTEGER,multiplier TEXT,parent INTEGER",
    "deps": "id INTEGER,kind TEXT,entity INTEGER,side TEXT,parent INTEGER",
}


class Batch:
    def __init__(self, owner, store):
        self.owner = owner
        self.s = store
        self.parents = {}
        self._parent_ids = {}
        self._parent_counts = {}
        self._parent_serial = 0
        self._models = {}
        self.result = store.raw_table(
            "id INTEGER PRIMARY KEY,key TEXT UNIQUE,status TEXT,metadata TEXT"
        )
        self.tables = {
            name: store.raw_table("target INTEGER," + definition)
            for name, definition in TABLES.items()
        }
        for name, t in self.tables.items():
            store.execute(f"CREATE INDEX {t}_target ON {t}(target)")
        self.frames = store.raw_table(
            "target INTEGER,name TEXT,schema TEXT,key TEXT,PRIMARY KEY(target,name)"
        )
        self.records = store.raw_table(
            "target INTEGER,name TEXT,ordinal INTEGER,payload TEXT,PRIMARY KEY(target,name,ordinal)"
        )

    def keys(self):
        for (k,) in self.s.execute(f"SELECT key FROM {self.result} ORDER BY id"):
            self.s.guard()
            yield k

    def capture(self, key, r):
        parents = []
        for parent in r._parents:
            i = self._parent_ids.get(id(parent))
            if i is None:
                i = self._parent_serial
                self._parent_serial += 1
                self._parent_ids[id(parent)] = i
                self.parents[i] = parent
                self._parent_counts[i] = 0
            self._parent_counts[i] += 1
            parents.append(i)
        result = None
        self._capture_target = None
        try:
            self.s.execute("SAVEPOINT capture_proof")
            result = self._capture(key, r, parents)
            self.s.disk_check()
            self.s.execute("RELEASE capture_proof")
            return result
        except BaseException as exc:
            try:
                self.s.db.execute("ROLLBACK TO capture_proof")
                self.s.db.execute("RELEASE capture_proof")
            except Exception as cleanup:
                exc.add_note(f"Certificate rollback also failed: {cleanup}")
            if self._capture_target is not None:
                self._models.pop(self._capture_target, None)
            self._release_parents(parents)
            raise

    def _release_parents(self, parents):
        for i in parents:
            self._parent_counts[i] -= 1
            if not self._parent_counts[i]:
                parent = self.parents.pop(i)
                self._parent_ids.pop(id(parent), None)
                del self._parent_counts[i]

    def _capture(self, key, r, parents):
        conclusion = r.conclusion
        extra_claim = None
        if isinstance(conclusion, Claim):
            claim = r._recipe.get("target")
            if claim is None:
                claim = r._store.execute(f"SELECT coalesce(max(id),0)+1 FROM {r._store.claims}").fetchone()[0]
                extra_claim = conclusion
            conclusion = dict(claim=claim)
        else:
            conclusion = dict(value=conclusion)
        data = dict(summary=r.summary, scope=r.scope, recipe=r._recipe,
                    parents=parents, method=r.method, conclusion=conclusion)
        target = self.s.execute(
            f"INSERT INTO {self.result}(key,status,metadata) VALUES(?,?,?)",
            (key, r.status, json.dumps(data, default=encode)),
        ).lastrowid
        self._capture_target = target
        for name, dest in self.tables.items():
            if self.s.db is r._store.db:
                self.s.execute(
                    f"INSERT INTO {dest} SELECT ?,* FROM {getattr(r._store,name)}",
                    (target,),
                )
            else:
                marks = ",".join("?" for _ in range(len(TABLES[name].split(",")) + 1))
                for row in r._store.execute(f"SELECT * FROM {getattr(r._store,name)}"):
                    self.s.execute(
                        f"INSERT INTO {dest} VALUES({marks})", (target, *row)
                    )
        if extra_claim is not None:
            self.s.execute(f"INSERT INTO {self.tables['claims']} VALUES(?,?,?,?,?,?,?)",
                           (target, claim, extra_claim.relation, str(extra_claim.expr.constant), None, None, None))
            for v, c in extra_claim.expr._items():
                self.s.execute(f"INSERT INTO {self.tables['terms']} VALUES(?,?,?,?)",
                               (target, claim, v, str(c)))
        for name, f in dict(
            r.evidence,
            dependencies=r.dependencies,
            violations=r.violations,
            counterexample=r.counterexample,
        ).items():
            self.s.execute(
                f"INSERT INTO {self.frames} VALUES(?,?,?,?)",
                (
                    target,
                    name,
                    json.dumps({n: t.to_spec() for n, t in f.schema.items()}),
                    json.dumps(f.key),
                ),
            )
            for i, record in enumerate(f._raw(tuple(f.schema), self.s)):
                self.s.check_size(record)
                self.s.execute(
                    f"INSERT INTO {self.records} VALUES(?,?,?,?)",
                    (target, name, i, json.dumps(record, default=encode)),
                )
        self._models[target] = r._store.models
        return target

    def one(self, key):
        rec = self.s.execute(
            f"SELECT id,status,metadata FROM {self.result} WHERE key=?", (key,)
        ).fetchone()
        if rec is None:
            raise KeyError(key)
        ident, status, metadata = rec
        meta = restore_json(json.loads(metadata))
        ctx = ProofContext.__new__(ProofContext)
        ctx._shared = self.s
        ctx.db, ctx.memory = self.s.db, self.s.memory
        ctx.owner = self.owner
        ctx.models = self._models[ident]
        # Scalar archives override this with a shared lease for the node. Every
        # Claim/Frame view keeps it, so dropping its Report cannot invalidate it.
        ctx._lease = self.lease(ident)
        for name, t in self.tables.items():
            columns = ",".join(x.strip().split()[0] for x in TABLES[name].split(","))
            setattr(ctx, name, f"(SELECT {columns} FROM {t} WHERE target={ident})")
        ctx.parents = [self.parents[i] for i in meta["parents"]]

        def frame(name):
            schema, key = self.s.execute(
                f"SELECT schema,key FROM {self.frames} WHERE target=? AND name=?",
                (ident, name),
            ).fetchone()
            schema = {n: DType(**v) for n, v in json.loads(schema).items()}

            def source(fields, execution):
                ctx.guard()
                for (payload,) in self.s.execute(
                    f"SELECT payload FROM {self.records} WHERE target=? AND name=? ORDER BY ordinal",
                    (ident, name),
                ):
                    ctx.guard()
                    row = restore_json(json.loads(payload))
                    yield {n: row[n] for n in fields}

            f = Frame(schema, models=ctx.models, source=source, key=json.loads(key))
            f._resource = ctx
            return f

        ev = {
            name: frame(name)
            for name, in self.s.execute(
                f"SELECT name FROM {self.frames} WHERE target=?", (ident,)
            )
            if name not in ("dependencies", "violations", "counterexample")
        }
        r = Report(
            ctx,
            status == "PROVED",
            meta["summary"],
            frame("violations"),
            status=status,
            conclusion=(ctx.claim(meta["conclusion"]["claim"])
                        if "claim" in meta["conclusion"] else meta["conclusion"]["value"]),
            scope=meta["scope"],
            method=meta["method"],
            dependencies=frame("dependencies"),
            evidence=ev,
            counterexample=frame("counterexample"),
            recipe=meta["recipe"],
        )
        r._verified = True
        r._parents = tuple(ctx.parents)
        r._assumption_reader = ctx.assumption_claims
        return r

    def lease(self, ident):
        return None

    def release(self, ident):
        """Discard one archived node after all its views and dependants release it."""
        if self.s.db is None:
            return
        rec = self.s.execute(f"SELECT metadata FROM {self.result} WHERE id=?", (ident,)).fetchone()
        if rec is None:
            return
        for table in (*self.tables.values(), self.frames, self.records):
            self.s.execute(f"DELETE FROM {table} WHERE target=?", (ident,))
        self.s.execute(f"DELETE FROM {self.result} WHERE id=?", (ident,))
        self._models.pop(ident, None)
        self._release_parents(json.loads(rec[0])["parents"])


    def report(self):
        total = self.s.execute(f"SELECT count(*) FROM {self.result}").fetchone()[0]
        bad = self.s.execute(
            f"SELECT count(*) FROM {self.result} WHERE status!='PROVED'"
        ).fetchone()[0]
        results = self.s.publish(
            {"target": DType("str"), "status": DType("str"), "domain": DType("str")},
            (
                dict(target=k, status=st, domain=d)
                for k, st, d in self.s.execute(
                    f"SELECT key,status,json_extract(metadata, '$.scope.domain') FROM {self.result} ORDER BY id"
                )
            ),
            key=("target",),
        )
        violations = results.where(status="INCONCLUSIVE")
        assumptions = self.s.execute(
            f"SELECT coalesce(sum(json_extract(metadata,'$.scope.assumptions')),0) FROM {self.result}"
        ).fetchone()[0]
        r = Report(
            self.s,
            not bad,
            dict(operation="linear_many", targets=total, inconclusive=bad),
            violations,
            status="INCONCLUSIVE" if bad else "PROVED",
            conclusion=dict(kind="linear_batch"),
            scope=dict(
                snapshot=dict(uid=self.owner.uid, version=self.owner.version),
                kind="linear_batch",
                arithmetic="exact",
                domain="per target",
                assumptions=assumptions,
                complete=True,
            ),
            method="linear_many",
            evidence={"results": results},
            recipe=dict(kind="linear_many"),
        )
        r._verified = True
        r._batch = self
        return r


class _Lease:
    def __init__(self, archive, ident):
        self.archive, self.ident = archive, ident

    def __del__(self):
        try:
            self.archive.discard(self.ident)
        except Exception:
            # Descriptor/storage failure during finalization is retried at the
            # next publication; Model.close still releases the entire archive.
            pass


class CertificateArchive(Batch):
    """One normalized store per live owner, with reference-owned proof nodes."""

    def __init__(self, owner, store):
        super().__init__(owner, store)
        self._leases = weakref.WeakValueDictionary()
        self._next_key = 0
        self._discarding = False
        self._capturing = False
        from collections import deque
        self._pending = deque()

    def lease(self, ident):
        lease = self._leases.get(ident)
        if lease is None:
            lease = self._leases[ident] = _Lease(self, ident)
        return lease

    def discard(self, ident):
        self._pending.append(ident)
        if self._discarding or self._capturing:
            return
        self._discarding = True
        try:
            while self._pending:
                pending = self._pending.popleft()
                try:
                    self.release(pending)
                except BaseException:
                    self._pending.appendleft(pending)
                    raise
            if self.s.db is not None:
                self.s.db.commit()
        finally:
            self._discarding = False

    def retain(self, report):
        if self._pending:
            pending = self._pending.popleft()
            self.discard(pending)
        self.s.memory = report._store.memory
        for pragma in ("cache_size", "temp.cache_size"):
            self.s.execute(f"PRAGMA {pragma}=-{max(1, self.s.memory // 8192)}")
        key = str(self._next_key)
        self._next_key += 1
        self._capturing = True
        try:
            with report._store.output_budget(self.s):
                self.capture(key, report)
            result = self.one(key)
            self.s.db.commit()
            return result
        finally:
            self._capturing = False
            if self._pending:
                pending = self._pending.popleft()
                self.discard(pending)


def retain_report(report):
    """Publish a scalar result, then let its caller release the scratch context."""
    owner = report._store.owner
    reference = getattr(owner, "_certificate_archive", None)
    archive = reference() if reference is not None else None
    created = archive is None or archive.s.db is None
    if created:
        store = AlgorithmStore((owner,), memory_mb=report._store.memory / 1024**2,
                               max_output_rows=None)
        try:
            store.db.execute("PRAGMA journal_mode=DELETE")
            store.db.execute("PRAGMA synchronous=OFF")
            archive = CertificateArchive(owner, store)
        except BaseException:
            store.close()
            raise
        owner._certificate_archive = weakref.ref(archive)
    try:
        return archive.retain(report)
    except BaseException:
        if created:
            archive.s.close()
        raise


def drop_transient(store, before):
    for (name,) in list(store.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name GLOB 'a[0-9]*' OR name GLOB 'f[0-9]*')"
    )):
        if int(name[1:]) > before:
            store.execute(f"DROP TABLE {name}")


class LinearBatch:
    """
    A streaming builder; successful run transfers storage to its Report.

    MIPFRAME HELP BEGIN
    LinearBatch [api.LinearBatch]

    Incremental linear certificate builder obtained through m.check.batch("linear").

    LinearBatch [type]

    When to use: Incremental linear certificate builder obtained through m.check.batch("linear").

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.Checker.batch, concept.report-resources
    MIPFRAME HELP END
    """

    def __init__(self, checker, *, residual, domain, memory_mb, disk_mb):
        if residual not in ("exact", "bounds") or domain not in ("auto", "relaxation", "integer"):
            raise ValueError("Invalid residual or domain")
        self._checker, self._residual, self._domain = checker, residual, domain
        self._store = None
        self._requests = None
        self._sealed = False
        try:
            self._store = AlgorithmStore((checker._owner,), memory_mb=memory_mb,
                                         disk_mb=disk_mb, max_output_rows=None)
            self._requests = Batch(checker._owner, self._store)
        except BaseException:
            self.close()
            raise

    def _guard(self):
        if self._sealed or self._store is None:
            raise ValueError("Batch builder is closed or has already run")
        self._store.guard()

    def add(self, *, key, target, using=(), assume=()):
        """
        Stage one keyed check; consume its inputs now without retaining expressions.

        MIPFRAME HELP BEGIN
        LinearBatch.add [api.LinearBatch.add]

        Stage one keyed target with the same using and assume arguments as check.linear.

        LinearBatch.add(*, key, target, using=(), assume=())

        When to use: Stage one keyed target with the same using and assume arguments as check.linear.

        Inputs: A unique nonempty string key, Claim target, original source Claims or verified Reports, optional exact multipliers and assumptions. Equality certificates also accept using={"le": ..., "ge": ...}.

        Returns: The builder for chaining. No proof is published by add.

        Establishes: Consumes generators and copies mathematical inputs into shared paged staging now; does not retain a list of target expressions. Parent proof dependencies remain live.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Duplicate keys, invalid sources and staging failures abort and close the builder. A closed or already-run builder rejects further additions.

        See also: api.Checker.batch, api.Checker.linear, api.LinearBatch.run, concept.conditions
        MIPFRAME HELP END
        """
        self._guard()
        s = self._store
        try:
            if not isinstance(key, str) or not key:
                raise SchemaError("Batch keys must be nonempty strings")
            if s.execute(f"SELECT 1 FROM {self._requests.result} WHERE key=?", (key,)).fetchone():
                raise SchemaError(f"Duplicate certificate target: {key}")
            before = s.serial
            ctx = ProofContext(self._checker._owner, "linear", domain=self._domain, _shared=s)
            ident = ctx.add_claim(target)
            ctx.assume(assume)
            double = isinstance(using, dict)
            if double:
                if target.relation != "==" or set(using) != {"le", "ge"}:
                    raise InvalidCertificateError("Directional certificates require an equality target and le/ge keys")
                for direction in ("le", "ge"):
                    ctx.ingest(using[direction], direction)
            else:
                ctx.ingest(using)
            staged = ctx.finish_report(
                "INCONCLUSIVE", ctx.claim(ident), {},
                recipe=dict(kind="linear", target=ident, residual=self._residual, double=double),
            )
            self._requests.capture(key, staged)
            drop_transient(s, before)
            s.disk_check()
            return self
        except BaseException:
            self.close()
            raise

    def run(self):
        """
        Verify every staged target and return the complete batch, sealing this builder.

        MIPFRAME HELP BEGIN
        LinearBatch.run [api.LinearBatch.run]

        Verify every staged linear target and seal the builder.

        LinearBatch.run()

        When to use: Verify every staged linear target and seal the builder.

        Returns: Report with evidence["results"] and for_target(key). An empty batch is PROVED.

        Establishes: Overall PROVED requires every target proved. Insufficient certificates yield per-target INCONCLUSIVE. All assumptions and domains remain attached to their own conclusions.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Errors or cancellation publish no partial result. run may be called only once.

        Successful run transfers result storage to the Report. The result, subreports and evidence views survive builder.close() or context exit while their source models remain open.

        See also: api.Report.for_target, api.Report.require, api.Report.replay, concept.report-resources
        MIPFRAME HELP END
        """
        from mipframe.check.accessor import Checker

        self._guard()
        s = self._store
        try:
            batch = Batch(self._checker._owner, s)
            last_target = 0
            while True:
                row = s.execute(f"SELECT id,key FROM {self._requests.result} "
                                "WHERE id>? ORDER BY id LIMIT 1", (last_target,)).fetchone()
                if row is None:
                    break
                last_target, key = row
                request = self._requests.one(key)
                before = s.serial
                ctx = ProofContext(self._checker._owner, "linear", domain=self._domain, _shared=s)

                def sources(direction):
                    for claim, multiplier, parent in request._store.execute(
                        f"SELECT claim,multiplier,parent FROM {request._store.sources} "
                        "WHERE direction=? ORDER BY id", (direction,)
                    ):
                        source = (request._store.claim(claim) if parent is None
                                  else request._parents[parent])
                        yield Fraction(multiplier), source

                using = ({d: sources(d) for d in ("le", "ge")}
                         if request._recipe["double"] else sources("main"))
                result = Checker.linear.__wrapped__(
                    self._checker, ctx, request.conclusion, using=using,
                    assume=request._assumption_reader(), residual=self._residual,
                )
                batch.capture(key, result)
                drop_transient(s, before)
            for table in (*self._requests.tables.values(), self._requests.frames,
                          self._requests.records, self._requests.result):
                s.execute(f"DROP TABLE {table}")
            result = batch.report()
            s.disk_check()
            self._sealed = True
            self._store = self._requests = None
            return result
        except BaseException:
            self.close()
            raise

    def close(self):
        """
        Discard unpublished work; a Report returned by run remains valid.

        MIPFRAME HELP BEGIN
        LinearBatch.close [api.LinearBatch.close]

        Release unpublished batch work and close the builder.

        LinearBatch.close()

        When to use: Release unpublished batch work and close the builder.

        Returns: None.

        Establishes: Idempotent. Reports already returned by run remain valid.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: api.Checker.batch, api.LinearBatch.run, concept.report-resources
        MIPFRAME HELP END
        """
        if self._store is not None:
            self._store.close()
        self._store = self._requests = None
        self._sealed = True

    def __enter__(self):
        self._guard()
        return self

    def __exit__(self, *exc):
        self.close()


def linear_many(
    checker,
    *,
    targets,
    terms,
    using=(),
    reports=None,
    residual="exact",
    domain="auto",
    memory_mb=128,
    disk_mb=None,
):
    from mipframe.check.accessor import Checker

    owner = checker._owner
    if residual not in ("exact", "bounds") or domain not in (
        "auto",
        "relaxation",
        "integer",
    ):
        raise ValueError("Invalid residual or domain")
    s = AlgorithmStore(
        (owner,), memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None
    )
    try:
        batch = Batch(owner, s)
        ts = s.raw_table(
            "id INTEGER PRIMARY KEY,key TEXT UNIQUE,relation TEXT,rhs TEXT"
        )
        tt = s.raw_table("target INTEGER,v INTEGER,c TEXT,PRIMARY KEY(target,v)")
        us = s.raw_table(
            "target INTEGER,axis TEXT,entity INTEGER,proof TEXT,side TEXT,multiplier TEXT,direction TEXT"
        )

        def records(frame, required):
            if not isinstance(frame, Frame) or not set(required) <= set(frame.schema):
                raise SchemaError(f"Expected columns {required}")
            owners((owner,), frame._models)
            yield from frame._raw(tuple(frame.schema), s)

        for t in records(targets, ("target", "relation", "rhs")):
            if (
                not isinstance(t["target"], str)
                or not t["target"]
                or t["relation"] not in ("<=", ">=", "==")
            ):
                raise SchemaError("Invalid target key or relation")
            try:
                s.execute(
                    f"INSERT INTO {ts}(key,relation,rhs) VALUES(?,?,?)",
                    (t["target"], t["relation"], str(q(t["rhs"]))),
                )
            except sqlite3.IntegrityError as exc:
                raise SchemaError(
                    f"Duplicate certificate target: {t['target']}"
                ) from exc

        def target(key):
            r = s.execute(f"SELECT id FROM {ts} WHERE key=?", (key,)).fetchone()
            if r is None:
                raise SchemaError("Unknown certificate target")
            return r[0]

        if not isinstance(terms, Frame):
            raise SchemaError("terms must be a Frame")
        if not ref_type(owner, "var").compatible(
            terms.schema.get("var", DType("null", True))
        ):
            raise SchemaError("Expected source VarRef terms")
        for t in records(terms, ("target", "var", "coef")):
            i = target(t["target"])
            v = t["var"]
            c = q(t["coef"])
            if type(v) is not int or not 0 <= v < owner.shape[1]:
                raise SchemaError("Invalid variable")
            old = s.execute(
                f"SELECT c FROM {tt} WHERE target=? AND v=?", (i, v)
            ).fetchone()
            c += Fraction(old[0]) if old else 0
            if c:
                s.execute(f"INSERT OR REPLACE INTO {tt} VALUES(?,?,?)", (i, v, str(c)))
            else:
                s.execute(f"DELETE FROM {tt} WHERE target=? AND v=?", (i, v))
        reports = {} if reports is None else reports
        if not isinstance(reports, Mapping) or any(
            not isinstance(k, str) or not k for k in reports
        ):
            raise SchemaError("reports must bind nonempty string keys to Reports")
        blocks = (using,) if isinstance(using, Frame) else using
        for block in blocks:
            if not isinstance(block, Frame):
                raise SchemaError("using must contain certificate Frames")
            for axis in ("row", "var"):
                if axis in block.schema and not ref_type(owner, axis).compatible(
                    block.schema[axis]
                ):
                    raise SchemaError("Wrong certificate source type")
            for rec in records(block, ("target", "multiplier")):
                i = target(rec["target"])
                selected = [
                    k for k in ("row", "var", "proof") if rec.get(k) is not None
                ]
                if len(selected) != 1:
                    raise SchemaError("Exactly one certificate source is required")
                axis = selected[0]
                a = q(rec["multiplier"])
                side = rec.get("side")
                d = rec.get("direction", "main")
                if d not in ("main", "le", "ge"):
                    raise SchemaError("Invalid direction")
                if axis == "proof":
                    if (
                        not isinstance(rec["proof"], str)
                        or rec["proof"] not in reports
                        or side is not None
                    ):
                        raise SchemaError("Invalid proof binding")
                    p = reports[rec["proof"]]
                    if (
                        not isinstance(p, Report)
                        or not p._verified
                        or not p.ok
                        or not isinstance(p.conclusion, Claim)
                    ):
                        raise InvalidCertificateError("Expected proved scalar Report")
                    owners((owner,), p.conclusion.expr._models)
                    claim = p.conclusion
                else:
                    entity = (owner.rows if axis == "row" else owner.vars).by_id(
                        rec[axis]
                    )
                    claim = source_claim(entity, side)
                if claim.relation != "==" and a < 0:
                    raise InvalidCertificateError("Negative inequality multiplier")
                s.execute(
                    f"INSERT INTO {us} VALUES(?,?,?,?,?,?,?)",
                    (
                        i,
                        axis,
                        None if axis == "proof" else rec[axis],
                        rec.get("proof"),
                        side,
                        str(a),
                        d,
                    ),
                )
        last_target = 0
        while True:
            row = s.execute(
                f"SELECT * FROM {ts} WHERE id>? ORDER BY id LIMIT 1", (last_target,)
            ).fetchone()
            if row is None:
                break
            i, key, relation, rhs = row
            last_target = i
            before = s.serial
            ctx = ProofContext(
                owner,
                "linear",
                domain=domain,
                memory_mb=memory_mb,
                disk_mb=disk_mb,
                _shared=s,
            )
            expr = LinExpr(
                owner,
                -Fraction(rhs),
                store=s,
                query=f"SELECT v,c FROM {tt} WHERE target={i} ORDER BY v",
            )
            claim = Claim(expr, relation)

            def sources(direction):
                for axis, entity, proof, side, a in s.execute(
                    f"SELECT axis,entity,proof,side,multiplier FROM {us} WHERE target=? AND direction=?",
                    (i, direction),
                ):
                    cl = (
                        reports[proof]
                        if axis == "proof"
                        else source_claim(
                            (owner.rows if axis == "row" else owner.vars).by_id(entity),
                            side,
                        )
                    )
                    yield Fraction(a), cl

            double = s.execute(
                f"SELECT 1 FROM {us} WHERE target=? AND direction!='main' LIMIT 1", (i,)
            ).fetchone()
            if (
                double
                and s.execute(
                    f"SELECT 1 FROM {us} WHERE target=? AND direction='main' LIMIT 1",
                    (i,),
                ).fetchone()
            ):
                raise InvalidCertificateError("Mixed certificate directions")
            certificate = (
                {k: sources(k) for k in ("le", "ge")} if double else sources("main")
            )
            r = Checker.linear.__wrapped__(
                checker, ctx, claim, using=certificate, residual=residual
            )
            batch.capture(key, r)
            # Drop only transient per-target tables. Batch capture allocates no new tables.
            drop_transient(s, before)
        s.disk_check()
        return batch.report()
    except BaseException:
        s.close()
        raise


def batch_events(report):
    from mipframe.check.serialization import events

    b = report._batch
    yield dict(
        type="header",
        format="mipframe-proof",
        version=1,
        batch=True,
        snapshot=dict(uid=b.owner.uid, version=b.owner.version),
    )
    count = 0
    for key in b.keys():
        yield dict(type="target_start", key=key)
        for event in events(b.one(key)):
            yield dict(type="target_event", event=event)
        yield dict(type="target_end")
        count += 1
    yield dict(type="batch_end", count=count)


def replay_batch(spec, *, models, relations=None, memory_mb=128, disk_mb=None):
    from pathlib import Path
    from tempfile import NamedTemporaryFile
    from mipframe.check.serialization import replay
    from mipframe.linear.expr import bind_model

    def stream():
        if isinstance(spec, (str, Path)):
            with open(spec) as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)
        else:
            yield from spec["events"]

    it = iter(stream())
    header = next(it)
    if header.get("version") != 1 or header.get("format") != "mipframe-proof":
        raise SchemaError("Invalid batch header")
    owner = bind_model(header["snapshot"], models)
    s = AlgorithmStore(
        (owner,), memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None
    )
    batch = Batch(owner, s)
    count = 0
    current = None
    key = None
    done = False
    try:
        for event in it:
            s.check_size(event)
            if done:
                raise SchemaError("Records after batch end")
            typ = event.get("type")
            if typ == "target_start" and current is None:
                key = event["key"]
                current = NamedTemporaryFile(mode="w+", suffix=".jsonl")
            elif typ == "target_event" and current is not None:
                current.write(json.dumps(event["event"]) + "\n")
            elif typ == "target_end" and current is not None:
                current.flush()
                r = replay(
                    current.name,
                    models=models,
                    relations=relations,
                    memory_mb=memory_mb,
                    disk_mb=disk_mb,
                )
                if not isinstance(r.conclusion, Claim):
                    raise InvalidCertificateError(
                        "Batch target must prove a scalar claim"
                    )
                batch.capture(key, r)
                current.close()
                current = None
                count += 1
                # Parent reports remain owned by Batch; release only the captured root workspace.
                r._store.close()
            elif typ == "batch_end" and current is None and event.get("count") == count:
                done = True
            else:
                raise SchemaError("Malformed batch certificate stream")
        if not done:
            raise SchemaError("Incomplete batch certificate")
        return batch.report()
    except BaseException:
        s.close()
        raise
    finally:
        if current is not None:
            current.close()
