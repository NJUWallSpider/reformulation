"""Paged search ownership, explicit stopping and normalized object publication."""

from fractions import Fraction
from math import isfinite
from time import monotonic
from mipframe.store.algorithm_store import AlgorithmStore
from mipframe.frame.schema import DType
from mipframe.check.algorithm import SearchBudgetExceeded


class SearchStopped(Exception):
    """Internal, caught only after full candidate-input validation."""


def nonnegative(value, name, *, optional=False):
    if optional and value is None:
        return
    if type(value) is not int or value < 0:
        raise ValueError(
            f"{name} must be a nonnegative integer" + (" or None" if optional else "")
        )


class SearchStore(AlgorithmStore):
    def __init__(self, *args, **kwargs):
        self.started = monotonic()
        self.deadline = None
        self.searching = False
        self.prepared = False
        self.stopped = None
        self.states = 0
        self.published_rows = 0
        super().__init__(*args, **kwargs)
        # Candidate clock stops can interrupt an object insertion. Unlike the
        # unpublished ops scratch DB, this store needs real savepoint rollback.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=OFF")

    def configure(self, *, candidate, max_states, time_limit):
        nonnegative(max_states, "max_states", optional=True)
        if time_limit is not None and (
            isinstance(time_limit, bool)
            or not isinstance(time_limit, (int, float))
            or not isfinite(time_limit)
            or time_limit <= 0
        ):
            raise ValueError("time_limit must be finite and positive or None")
        self.candidate, self.max_states = candidate, max_states
        self.time_limit = time_limit
        self.deadline = None if time_limit is None else self.started + time_limit
        self.searching = True
        self.scope_info = "full declared domain"

    def stop(self, reason):
        self.stopped = reason
        if self.candidate and self.prepared:
            raise SearchStopped(reason)
        raise SearchBudgetExceeded(
            self.operation,
            reason,
            self.states,
            self.max_states,
            self.time_limit,
            self.scope_info,
        )

    def clock(self):
        if (
            self.searching
            and self.deadline is not None
            and monotonic() >= self.deadline
        ):
            self.stop("time_limit")

    def execute(self, sql, args=()):
        self.clock()
        return super().execute(sql, args)

    def _progress(self):
        # SQLITE_INTERRUPT during a write can roll back the *whole* transaction,
        # destroying earlier candidates as well as this object. Prepared candidate
        # searches therefore stop at statement boundaries, not inside SQLite.
        if self.searching and self.prepared and self.candidate:
            return super()._progress()
        # Exhaustive/preparation failure discards the entire unpublished store.
        try:
            self.clock()
        except (SearchStopped, SearchBudgetExceeded) as exc:
            self._quota_error = exc
            return 1
        return super()._progress()

    def tick(self):
        self.guard()
        self.clock()
        if self.max_states is not None and self.states >= self.max_states:
            self.stop("max_states")
        self.states += 1

    def ready(self):
        self.clock()  # An incomplete input stage must never yield a candidate result.
        self.prepared = True

    def finish(self, *, found, retained, details=""):
        self.clock()
        if self.candidate:
            self.searching = False
        self.search_info = dict(
            algorithm=self.operation,
            algorithm_version=1,
            mode="candidate" if self.candidate else "exhaustive",
            stop_reason=self.stopped
            or ("heuristic_finished" if self.candidate else "exhausted"),
            states=self.states,
            found=found,
            retained=retained,
            scope=self.scope_info,
            details=details,
            memory_bytes=self.memory,
            disk_bytes=self.disk_limit,
            max_output_rows=self.limit,
            max_states=self.max_states,
            time_limit=None if self.time_limit is None else str(self.time_limit),
            ranking=(
                "best found; global optimum not established"
                if self.candidate
                else "complete within declared scope"
            ),
        )
        schema = {
            k: (
                DType("int", v is None)
                if k
                in (
                    "algorithm_version",
                    "states",
                    "found",
                    "retained",
                    "max_states",
                    "memory_bytes",
                    "disk_bytes",
                    "max_output_rows",
                )
                else DType("str", v is None)
            )
            for k, v in self.search_info.items()
        }
        return self.publish(schema, [self.search_info], limited=False)

    def publish(self, schema, rows, *, limited=True, **kwargs):
        def checked():
            for row in rows:
                if limited:
                    self.published_rows += 1
                    self.enforce(self.published_rows)
                yield row

        result = super().publish(schema, checked(), limited=False, **kwargs)
        if hasattr(self, "search_info"):
            result._search_provenance = (dict(self.search_info),)
        return result


class Objects:
    """Disk-backed verified objects. Hashes filter; complete sequences establish identity."""

    def __init__(self, ctx, graph, kind, top=None):
        self.ctx, self.graph, self.kind, self.top = ctx, graph, kind, top
        self.objects = ctx.raw_table(
            "id INTEGER PRIMARY KEY,n INTEGER,score TEXT COLLATE EXACT,digest TEXT,keep INTEGER"
        )
        self.members = ctx.raw_table("o INTEGER,p INTEGER,v INTEGER,PRIMARY KEY(o,p)")
        ctx.execute(f"CREATE INDEX {self.objects}_digest ON {self.objects}(digest)")
        self.found = 0
        self.output_rows = 0

    def less(self, a, b):
        """Canonical vertex sequence order without collecting either object."""
        row = self.ctx.execute(
            f"SELECT x.v,y.v FROM {self.members} x JOIN {self.members} y ON x.p=y.p WHERE x.o=? AND y.o=? AND x.v!=y.v ORDER BY x.p LIMIT 1",
            (a, b),
        ).fetchone()
        if row is not None:
            return row[0] < row[1]
        return (
            self.ctx.execute(
                f"SELECT n FROM {self.objects} WHERE id=?", (a,)
            ).fetchone()[0]
            < self.ctx.execute(
                f"SELECT n FROM {self.objects} WHERE id=?", (b,)
            ).fetchone()[0]
        )

    def add(self, sequence):
        # A clock stop during verification/storage must discard this entire object.
        self.ctx.execute("SAVEPOINT candidate_object")
        found, output_rows = self.found, self.output_rows
        try:
            self._add(sequence)
            self.ctx.execute("RELEASE candidate_object")
        except SearchStopped:
            self.ctx.db.execute("ROLLBACK TO candidate_object")
            self.ctx.db.execute("RELEASE candidate_object")
            self.found, self.output_rows = found, output_rows
            raise

    def _add(self, sequence):
        import hashlib

        ctx = self.ctx
        ident = ctx.execute(
            f"INSERT INTO {self.objects}(n,score,keep) VALUES(0,'0',1)"
        ).lastrowid
        digest = hashlib.sha256()
        score, n = Fraction(0), 0
        for n, v in enumerate(sequence, 1):
            ctx.execute(f"INSERT INTO {self.members} VALUES(?,?,?)", (ident, n - 1, v))
            digest.update(str(v).encode() + b",")
            score += self.graph.weight(v)
            ctx.check_size(score)
        sig = digest.hexdigest()
        ctx.execute(
            f"UPDATE {self.objects} SET n=?,score=?,digest=? WHERE id=?",
            (n, str(score), sig, ident),
        )
        duplicate = ctx.execute(
            f"SELECT o.id FROM {self.objects} o WHERE o.id!=? AND o.digest=? AND o.n=? AND NOT EXISTS(SELECT 1 FROM {self.members} a JOIN {self.members} b ON a.p=b.p AND b.o=o.id WHERE a.o=? AND a.v!=b.v) LIMIT 1",
            (ident, sig, n, ident),
        ).fetchone()
        if duplicate:
            ctx.execute(f"DELETE FROM {self.members} WHERE o=?", (ident,))
            ctx.execute(f"DELETE FROM {self.objects} WHERE id=?", (ident,))
            return
        self.found += 1
        self.output_rows += n + 1
        if self.top is None:
            # Both summary and member rows count; fail as soon as the exact size is known.
            ctx.enforce(self.output_rows)
        elif (
            ctx.execute(f"SELECT count(*) FROM {self.objects} WHERE keep=1").fetchone()[
                0
            ]
            > self.top
        ):
            worst_score = ctx.execute(
                f"SELECT score FROM {self.objects} WHERE keep=1 ORDER BY score LIMIT 1"
            ).fetchone()[0]
            worst = None
            for (o,) in ctx.execute(
                f"SELECT id FROM {self.objects} WHERE keep=1 AND score=?",
                (worst_score,),
            ):
                if worst is None or self.less(worst, o):
                    worst = o
            ctx.execute(f"UPDATE {self.objects} SET keep=0 WHERE id=?", (worst,))

    def publish(self, details=""):
        ctx, graph = self.ctx, self.graph
        # Candidate publication is atomic even after its exploration clock expired.
        # Exhaustive execution continues to enforce its deadline through publication.
        if ctx.candidate:
            ctx.searching = False
        order = ctx.raw_table("rank INTEGER PRIMARY KEY,o INTEGER UNIQUE")
        source = ctx.raw_table(
            "p INTEGER PRIMARY KEY,o INTEGER,score TEXT COLLATE EXACT"
        )
        target = ctx.raw_table(
            "p INTEGER PRIMARY KEY,o INTEGER,score TEXT COLLATE EXACT"
        )
        ctx.execute(
            f"INSERT INTO {source}(o,score) SELECT id,score FROM {self.objects} WHERE keep=1 ORDER BY id"
        )
        rank = ctx.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
        # External merge sort keeps only two scalar heads resident, including ties
        # between arbitrarily long member sequences.
        width = 1
        while width < rank:
            ctx.execute(f"DELETE FROM {target}")
            for start in range(1, rank + 1, 2 * width):
                a, b = start, min(start + width, rank + 1)
                ae, be = b, min(start + 2 * width, rank + 1)
                while a < ae or b < be:
                    left = (
                        ctx.execute(
                            f"SELECT o,score FROM {source} WHERE p=?", (a,)
                        ).fetchone()
                        if a < ae
                        else None
                    )
                    right = (
                        ctx.execute(
                            f"SELECT o,score FROM {source} WHERE p=?", (b,)
                        ).fetchone()
                        if b < be
                        else None
                    )
                    choose_left = (
                        right is None
                        or left is not None
                        and (
                            Fraction(left[1]) > Fraction(right[1])
                            or Fraction(left[1]) == Fraction(right[1])
                            and self.less(left[0], right[0])
                        )
                    )
                    item = left if choose_left else right
                    ctx.execute(f"INSERT INTO {target}(o,score) VALUES(?,?)", item)
                    if choose_left:
                        a += 1
                    else:
                        b += 1
            source, target = target, source
            width *= 2
        ctx.execute(f"INSERT INTO {order} SELECT p,o FROM {source} ORDER BY p")
        status = ctx.finish(found=self.found, retained=rank, details=details)
        kind = self.kind
        size_name = "size" if kind == "clique" else "length"
        summary = ctx.publish(
            {kind: DType("int"), size_name: DType("int"), "score": DType("number")},
            (
                {kind: r, size_name: n, "score": Fraction(s)}
                for r, n, s in ctx.execute(
                    f"SELECT r.rank,o.n,o.score FROM {order} r JOIN {self.objects} o ON o.id=r.o ORDER BY r.rank"
                )
            ),
            key=(kind,),
            relations={"search": status},
        )
        schema = {kind: DType("int")}
        if kind == "cycle":
            schema["position"] = DType("int")
        schema["vertex"] = graph.vertices.typ

        def rows():
            for r, p, v in ctx.execute(
                f"SELECT r.rank,m.p,m.v FROM {order} r JOIN {self.members} m ON m.o=r.o ORDER BY r.rank,m.p"
            ):
                yield {
                    kind: r,
                    **({"position": p} if kind == "cycle" else {}),
                    "vertex": graph.vertices.value(v),
                }

        result = ctx.publish(
            schema,
            rows(),
            key=(kind, "position" if kind == "cycle" else "vertex"),
            domains={"vertex": graph.domain()},
            relations={"summary": summary, "search": status},
        )
        return result
