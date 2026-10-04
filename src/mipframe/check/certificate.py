"""Paged exact claims, provenance, assumptions and sparse certificate accumulation."""

from fractions import Fraction
from itertools import zip_longest
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType, SchemaError, ref_type
from mipframe.linear.expr import (
    LinExpr,
    coerce,
    context,
    attach,
    owners,
    add,
    from_table,
)
from mipframe.linear.claim import Claim, source_claim
from mipframe.linear.scalar import q
from mipframe.check.report import Report
from mipframe.store.numbers import MIPFrameError
from mipframe.store.search_store import SearchStore


class InvalidCertificateError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.InvalidCertificateError")
    MIPFRAME HELP END
    """
    def __init__(self, message, violations=None):
        super().__init__(message)
        self.violations = violations


class UnsupportedEnumerationDomain(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.UnsupportedEnumerationDomain")
    MIPFRAME HELP END
    """
    pass


class ProofData:
    """Read-only claim access shared by workspaces and archived proof views."""
    def __init__(self, owner, runtime, models):
        self.owner, self._runtime, self.models = owner, runtime, tuple(models)

    @property
    def db(self):
        return self._runtime.db

    @property
    def memory(self):
        return self._runtime.memory

    @property
    def query_context(self):
        self.guard()
        return self._runtime

    def guard(self):
        for model in self.models:
            model.model
        self._runtime.guard()

    def execute(self, sql, args=()):
        self.guard()
        return self._runtime.execute(sql, args)

    def close(self):
        # A view owns its lease, never the shared runtime.
        pass

    def claim(self, ident):
        rel, c, axis, entity, side = self.execute(
            f"SELECT relation,constant,axis,entity,side FROM {self.claims} WHERE id=?",
            (ident,),
        ).fetchone()
        e = LinExpr(
            self.owner,
            Fraction(c),
            store=self,
            query=f"SELECT v,c FROM {self.terms} WHERE claim={int(ident)} ORDER BY v",
            models=self.models,
        )
        return Claim(
            e,
            rel,
            _source=None if axis is None else dict(axis=axis, id=entity, side=side),
        )

    def assumption_claims(self):
        for (ident,) in self.execute(
            f"SELECT claim FROM {self.assumptions} ORDER BY claim"
        ):
            yield self.claim(ident)


class ArchivedProof(ProofData):
    """A complete, reference-owned view of one normalized archived proof."""
    def __init__(self, owner, runtime, models, tables, parents, lease):
        super().__init__(owner, runtime, models)
        self._lease, self.parents = lease, list(parents)
        for name, query in tables.items():
            setattr(self, name, query)


class ProofContext(ProofData):
    def __init__(
        self,
        owner,
        method,
        *,
        memory_mb=128,
        disk_mb=None,
        domain="auto",
        runtime=None,
    ):
        self._owns_runtime = runtime is None
        if domain not in ('auto', 'relaxation', 'integer'):
            raise ValueError('domain must be auto, relaxation or integer')
        if runtime is None:
            runtime = SearchStore((owner,), memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None)
        super().__init__(owner, runtime, (owner,))
        runtime.operation = method
        self.owner, self.method, self.operation, self.requested_domain = (
            owner,
            method,
            method,
            domain,
        )
        self.proof_domain = "integer" if domain == "integer" else "relaxation"
        self.claims = self.raw_table(
            "id INTEGER PRIMARY KEY,relation TEXT,constant TEXT,axis TEXT,entity INTEGER,side TEXT"
        )
        self.terms = self.raw_table(
            "claim INTEGER,v INTEGER,c TEXT,PRIMARY KEY(claim,v)"
        )
        self.assumptions = self.raw_table("claim INTEGER PRIMARY KEY")
        self.sources = self.raw_table(
            "id INTEGER PRIMARY KEY,direction TEXT,claim INTEGER,multiplier TEXT,parent INTEGER"
        )
        self.deps = self.raw_table(
            "id INTEGER PRIMARY KEY,kind TEXT,entity INTEGER,side TEXT,parent INTEGER,UNIQUE(kind,entity,side,parent)"
        )
        self.intervals = self.raw_table("v INTEGER PRIMARY KEY,lo TEXT,hi TEXT")
        self.parents = []
        self.contradiction = None
        self.bounds_evidence = self.raw_table(
            "id INTEGER PRIMARY KEY,direction TEXT,v INTEGER,c TEXT,side TEXT,bound TEXT,contribution TEXT"
        )

    def publish(self, schema, rows, **kwargs):
        self.guard()
        result = self._runtime.publish(schema, rows, **kwargs)
        result._models = self.models
        return result

    def raw_table(self, definition):
        return self._runtime.raw_table(definition)

    def close(self):
        runtime = getattr(self, '_runtime', None)
        if runtime is not None and self._owns_runtime:
            runtime.close()

    def _close_connection(self):
        if self._owns_runtime:
            self._runtime._close_connection()

    def check_size(self, value):
        return self._runtime.check_size(value)

    def disk_check(self):
        return self._runtime.disk_check()

    def reader(self, store):
        return self._runtime.reader(store)

    def output_budget(self, store):
        return self._runtime.output_budget(store)

    def configure(self, **options):
        return self._runtime.configure(**options)

    def clock(self):
        return self._runtime.clock()

    def tick(self):
        return self._runtime.tick()

    searching = property(lambda self: self._runtime.searching,
                         lambda self, value: setattr(self._runtime, 'searching', value))
    prepared = property(lambda self: self._runtime.prepared,
                        lambda self, value: setattr(self._runtime, 'prepared', value))
    scope_info = property(lambda self: self._runtime.scope_info,
                          lambda self, value: setattr(self._runtime, 'scope_info', value))
    states = property(lambda self: self._runtime.states)
    started = property(lambda self: self._runtime.started,
                       lambda self, value: setattr(self._runtime, 'started', value))

    def add_claim(self, claim):
        if not isinstance(claim, Claim):
            raise InvalidCertificateError("Expected a Claim")
        owners((self.owner,), claim.expr._models)
        attach(self, claim.expr._models)
        src = claim._source or {}
        cur = self.execute(
            f"INSERT INTO {self.claims}(relation,constant,axis,entity,side) VALUES(?,?,?,?,?)",
            (
                claim.relation,
                str(claim.expr.constant),
                src.get("axis"),
                src.get("id"),
                src.get("side"),
            ),
        )
        ident = cur.lastrowid
        for v, c in claim.expr._items(self):
            self.check_size(c)
            self.execute(f"INSERT INTO {self.terms} VALUES(?,?,?)", (ident, v, str(c)))
        return ident

    def equal_claim(self, a, b):
        ar, ac = self.execute(
            f"SELECT relation,constant FROM {self.claims} WHERE id=?", (a,)
        ).fetchone()
        br, bc = self.execute(
            f"SELECT relation,constant FROM {self.claims} WHERE id=?", (b,)
        ).fetchone()
        if (ar, ac) != (br, bc):
            return False
        return not any(
            self.execute(
                f"SELECT v,c FROM {self.terms} WHERE claim=? EXCEPT SELECT v,c FROM {self.terms} WHERE claim=? LIMIT 1",
                (x, y),
            ).fetchone()
            for x, y in ((a, b), (b, a))
        )

    def assume(self, claims):
        for claim in claims:
            ident = self.add_claim(claim)
            if not any(
                self.equal_claim(ident, old)
                for old, in self.execute(f"SELECT claim FROM {self.assumptions}")
            ):
                self.execute(f"INSERT INTO {self.assumptions} VALUES(?)", (ident,))
        return self

    def dependency(self, kind, entity=-1, side="", parent=-1):
        self.execute(
            f"INSERT OR IGNORE INTO {self.deps}(kind,entity,side,parent) VALUES(?,?,?,?)",
            (kind, entity, side, parent),
        )

    def register_parent(self, report):
        """Retain verified provenance without asserting its conclusion or assumptions."""
        if not isinstance(report, Report):
            raise InvalidCertificateError("Expected a verified Report")
        report._guard()
        from mipframe.check.serialization import topology

        topology(report)
        if (
            not report._verified
            or report.status != "PROVED"
            or not isinstance(report.conclusion, Claim)
        ):
            raise InvalidCertificateError(
                "A source report must have a verified PROVED linear conclusion"
            )
        owners((self.owner,), report.conclusion.expr._models)
        attach(self, report._store.models)
        if report.scope.get("arithmetic") != "exact":
            raise InvalidCertificateError(
                "Exact certificates require exact dependencies"
            )
        if report.scope.get("snapshot") != dict(uid=self.owner.uid, version=self.owner.version):
            raise InvalidCertificateError("Evidence belongs to a different source snapshot")
        if report.scope.get("domain") == "integer":
            if self.requested_domain == "relaxation":
                raise InvalidCertificateError(
                    "Integer-only evidence cannot prove a relaxation conclusion"
                )
            self.proof_domain = "integer"
        # Intern only references to parent reports; coefficient and source tables stay on disk.
        for i, old in enumerate(self.parents):
            if old is report:
                return i
        i = len(self.parents)
        self.parents.append(report)
        return i

    def parent(self, report):
        i = self.register_parent(report)
        self.assume(report._assumption_reader())
        self.dependency("report", parent=i)
        return i

    def source(self, source, multiplier, direction):
        multiplier = q(multiplier)
        parent = None
        if isinstance(source, Report):
            parent = self.parent(source)
            source = source.conclusion
        ident = self.add_claim(source)
        if source.relation != "==" and multiplier < 0:
            raise InvalidCertificateError("Inequality multipliers must be nonnegative")
        if source._source is not None:
            src = source._source
            axis = src["axis"]
            if axis not in ("row", "var"):
                raise InvalidCertificateError("Invalid source axis")
            entity = (self.owner.rows if axis == "row" else self.owner.vars).loc[src["id"]]
            actual = source_claim(entity, src["side"])
            if actual.relation != source.relation or not expressions_equal(
                actual.expr, source.expr, self
            ):
                raise InvalidCertificateError(
                    "Source does not match the original model side"
                )
            self.dependency(axis, src["id"], src["side"])
        elif parent is None and not any(
            self.equal_claim(ident, a)
            for a, in self.execute(f"SELECT claim FROM {self.assumptions}")
        ):
            raise InvalidCertificateError(
                "Unproved Claim must be explicitly declared in assume"
            )
        self.execute(
            f"INSERT INTO {self.sources}(direction,claim,multiplier,parent) VALUES(?,?,?,?)",
            (direction, ident, str(multiplier), parent),
        )

    def ingest(self, using, direction="main", *, constraints=False):
        if isinstance(using, (Claim, Report, Frame)):
            using = (using,)
        for item in using:
            if isinstance(item, Frame):
                fields = tuple(
                    n for n in ("row", "var", "side", "multiplier") if n in item.schema
                )
                if (
                    "side" not in fields
                    or not any(n in fields for n in ("row", "var"))
                    or not constraints
                    and "multiplier" not in fields
                ):
                    raise SchemaError(
                        "Certificate Frame requires row/var, side and multiplier"
                    )
                for axis in ("row", "var"):
                    if axis in fields and not ref_type(self.owner, axis).compatible(
                        item.schema[axis]
                    ):
                        raise SchemaError("Certificate Ref axis/snapshot mismatch")
                attach(self, item._models)
                errors = self.raw_table(
                    "ordinal INTEGER PRIMARY KEY,reason TEXT,row_id INTEGER,var_id INTEGER"
                )
                for ordinal, row in enumerate(item._raw(fields, self)):
                    from mipframe.linear.claim import MissingBoundError
                    from mipframe.linear.scalar import ExactArithmeticError

                    try:
                        present = [
                            axis for axis in ("row", "var") if row.get(axis) is not None
                        ]
                        if len(present) != 1:
                            raise SchemaError(
                                "Each source record requires exactly one row or var"
                            )
                        axis = present[0]
                        ent = (
                            self.owner.rows if axis == "row" else self.owner.vars).loc[row[axis]]
                        if (
                            constraints
                            and "multiplier" in row
                            and q(row["multiplier"]) != 1
                        ):
                            raise InvalidCertificateError(
                                "Enumeration takes constraints, not multipliers"
                            )
                        self.source(
                            source_claim(ent, row["side"]),
                            row.get("multiplier", 1),
                            direction,
                        )
                    except (
                        InvalidCertificateError,
                        SchemaError,
                        MissingBoundError,
                        ExactArithmeticError,
                        KeyError,
                        IndexError,
                    ) as exc:
                        self.execute(
                            f"INSERT INTO {errors} VALUES(?,?,?,?)",
                            (ordinal, str(exc), row.get("row"), row.get("var")),
                        )
                if self.execute(f"SELECT 1 FROM {errors} LIMIT 1").fetchone():
                    violations = self.publish(
                        {
                            "record": DType("int"),
                            "reason": DType("str"),
                            "row_id": DType("int", True),
                            "var_id": DType("int", True),
                        },
                        (
                            dict(record=i, reason=r, row_id=rid, var_id=vid)
                            for i, r, rid, vid in self.execute(
                                f"SELECT * FROM {errors} ORDER BY ordinal"
                            )
                        ),
                    )
                    raise InvalidCertificateError(
                        "Invalid certificate records; see violations for the complete input errors",
                        violations,
                    )
            elif isinstance(item, tuple) and len(item) == 2:
                if constraints:
                    raise InvalidCertificateError(
                        "Enumeration takes constraints, not multiplier tuples"
                    )
                self.source(item[1], item[0], direction)
            else:
                self.source(item, 1, direction)

    def prepare_assumptions(self):
        for (ident,) in self.execute(
            f"SELECT claim FROM {self.assumptions} ORDER BY claim"
        ):
            cl = self.claim(ident)
            it = cl.expr._items(self)
            first = next(it, None)
            if first is None:
                if not satisfied(cl.relation, cl.expr.constant):
                    self.contradiction = "constant assumption is false"
                continue
            if next(it, None) is not None:
                continue
            v, c = first
            b = -cl.expr.constant / c
            lo, hi = self.interval(v)
            if cl.relation == "==":
                lo, hi = max(lo, b), min(hi, b)
            elif (cl.relation == "<=" and c > 0) or (cl.relation == ">=" and c < 0):
                hi = min(hi, b)
            else:
                lo = max(lo, b)
            self.execute(
                f"INSERT OR REPLACE INTO {self.intervals} VALUES(?,?,?)",
                (v, str(lo), str(hi)),
            )
            if lo > hi:
                self.contradiction = f"empty conditional interval for variable ID {v}"

    def interval(self, v):
        row = self.execute(
            f"SELECT lo,hi FROM {self.intervals} WHERE v=?", (v,)
        ).fetchone()
        if row is not None:
            return number(row[0]), number(row[1])
        var = self.owner.vars.loc[v]
        lo, hi = var.lb, var.ub
        # These exact model sides participate in conditional interval intersection.
        self.dependency("var", v, "lb")
        self.dependency("var", v, "ub")
        if lo > hi:
            self.contradiction = f"empty model interval for variable ID {v}"
        return lo, hi

    def fixed(self, v):
        row = self.execute(
            f"SELECT lo,hi FROM {self.intervals} WHERE v=?", (v,)
        ).fetchone()
        if row is not None:
            lo, hi = number(row[0]), number(row[1])
            if lo == hi and not isinstance(lo, float):
                return lo
        return None

    def accumulate(self, sources):
        # Reserve a conservative fraction of the call budget. Large exact values
        # and wide residuals spill to the same exact paged kernel.
        import sys
        table = self.raw_table("v INTEGER PRIMARY KEY,c TEXT")
        limit = self.memory // 8
        values, used, spilled = {}, 0, False
        constant = Fraction(0)
        conditional = self.execute(f"SELECT 1 FROM {self.intervals} LIMIT 1").fetchone() is not None

        def weight(c):
            return 256 + sys.getsizeof(c.numerator) + sys.getsizeof(c.denominator)

        for factor, expr in sources:
            constant += factor * expr.constant
            self.check_size(constant)
            for v, c in expr._items(self):
                c = c if factor == 1 else -c if factor == -1 else factor * c
                fixed = self.fixed(v) if conditional else None
                if fixed is not None:
                    constant += c * fixed
                    self.check_size(constant)
                elif spilled:
                    add(self, table, v, c)
                elif c:
                    old = values.pop(v, None)
                    if old is not None:
                        used -= weight(old)
                        c += old
                    self.check_size(c)
                    if c:
                        values[v] = c
                        used += weight(c)
                    if used > limit:
                        for key, value in values.items():
                            self.execute(f"INSERT INTO {table} VALUES(?,?)", (key, str(value)))
                        values.clear()
                        spilled = True
        for v, c in values.items():
            self.execute(f"INSERT INTO {table} VALUES(?,?)", (v, str(c)))
        return table, constant

    def source_terms(self, direction="main", *, oriented=True):
        for ident, mult in self.execute(
            f"SELECT claim,multiplier FROM {self.sources} WHERE direction=? ORDER BY id",
            (direction,),
        ):
            cl = self.claim(ident)
            sign = -1 if oriented and cl.relation == ">=" else 1
            yield sign * Fraction(mult), cl.expr

    def expr(self, table, constant=0):
        return from_table(self.owner, self, table, constant)

    def bound(self, table, constant, direction, *, upper=True):
        total = constant
        finite = True
        for v, c in self.execute(f"SELECT v,c FROM {table} ORDER BY v"):
            c = Fraction(c)
            side = "ub" if (c > 0) == upper else "lb"
            column = "hi" if side == "ub" else "lo"
            conditional = self.execute(
                f"SELECT {column} FROM {self.intervals} WHERE v=?", (v,)
            ).fetchone()
            if conditional is None:
                b = getattr(self.owner.vars.loc[v], side)
                self.dependency("var", v, side)
            else:
                b = number(conditional[0])
            contribution = None if isinstance(b, float) else c * b
            self.execute(
                f"INSERT INTO {self.bounds_evidence}(direction,v,c,side,bound,contribution) VALUES(?,?,?,?,?,?)",
                (
                    direction,
                    v,
                    str(c),
                    side,
                    str(b),
                    None if contribution is None else str(contribution),
                ),
            )
            if contribution is None:
                finite = False
            else:
                total += contribution
                self.check_size(total)
        return total if finite else None

    def finish_report(
        self,
        status,
        conclusion,
        summary,
        *,
        kind="implication",
        evidence=None,
        counterexample=None,
        recipe=None,
        local=False,
        complete=True,
    ):
        evidence = dict(evidence or {})
        empty = self.publish({"reason": DType("str")}, [], limited=False)
        from mipframe.check.evidence import normalized_evidence
        normalized = normalized_evidence(self)
        dependencies = normalized.pop("dependencies")
        evidence.update(normalized)
        scope = dict(
            kind=kind,
            domain=self.proof_domain,
            arithmetic="exact",
            complete=complete,
            local=local,
            snapshot=dict(uid=self.owner.uid, version=self.owner.version),
            numeric_provenance=dict(self.owner.source)["numeric_provenance"],
            assumptions=self.execute(f"SELECT count(*) FROM {self.assumptions}").fetchone()[0],
        )
        if self.method in ("enumerate", "cases"):
            scope["exhaustive"] = summary["exhaustive"]
        report = Report(
            self,
            status == "PROVED",
            summary,
            empty,
            status=status,
            conclusion=conclusion,
            scope=scope,
            method=self.method,
            evidence=evidence,
            dependencies=dependencies,
            counterexample=counterexample,
            recipe=recipe,
        )
        report._verified = True
        report._parents = tuple(self.parents)
        report._assumption_reader = self.assumption_claims
        self.searching = False
        self.disk_check()
        self.db.commit()
        return report


def number(x):
    return float(x) if x in ("inf", "-inf") else Fraction(x)


def satisfied(relation, value):
    return (
        value <= 0
        if relation == "<="
        else value >= 0 if relation == ">=" else value == 0
    )


def expressions_equal(a, b, ctx):
    return a.constant == b.constant and all(
        x == y for x, y in zip_longest(a._items(ctx), b._items(ctx))
    )
