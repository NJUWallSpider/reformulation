"""Shared reports and integer pattern aggregation for the Frame executor."""

from mipframe._help.diagnostics import hint as _help_hint

import numpy as np
from types import SimpleNamespace
from mipframe.store.numbers import MIPFrameError
from mipframe.store.query_store import QueryStore
from mipframe.store.query_store import SQLIndex


class CardinalityError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    CardinalityError [api.CardinalityError]

    Diagnostic exception; inspect original message and any attached complete Report.

    CardinalityError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: error.join
    MIPFRAME HELP END
    """
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


class ValidationError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    ValidationError [api.ValidationError]

    Diagnostic exception; inspect original message and any attached complete Report.

    ValidationError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.proofs
    MIPFRAME HELP END
    """
    def __init__(self, report):
        super().__init__("Validation failed: " + str(report.summary))
        self.report = report


def setup(relation, memory_mb):
    store = QueryStore(relation._owner, memory_mb)
    try:
        if hasattr(relation, "_ensure"):
            relation._ensure(store)
        filters = tuple(
            (target.prepared(store), exclude) for target, exclude in relation._filters
        )
        return store, filters, max(1, store.memory // 256)
    except BaseException:
        store.close()
        raise


def source_index(relation, store):
    """Deduplicate arbitrary source selections within this query's own budget."""
    if hasattr(relation, "ident"):
        return (relation.ident,)
    index = relation._frame.ids
    if isinstance(index, range) or getattr(index, "unique", False):
        return index
    table = store.table("ordinal INTEGER PRIMARY KEY,id INTEGER UNIQUE")
    store.db.executemany(
        f"INSERT OR IGNORE INTO {table}(id) VALUES(?)", ((int(i),) for i in index)
    )
    return SQLIndex(store, f"SELECT id FROM {table} ORDER BY ordinal", unique=True)


class Report:
    """
    One live immutable report for data assertions and mathematical certificates.

    MIPFRAME HELP BEGIN
    Report [api.Report]

    Immutable checker outcome with scope, dependencies and complete pageable evidence.

    Report [type]

    When to use: Immutable checker outcome with scope, dependencies and complete pageable evidence.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.proofs
    MIPFRAME HELP END
    """

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            raise AttributeError("Reports are read-only")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        store,
        ok,
        summary,
        violations,
        *,
        status=None,
        conclusion=None,
        scope=None,
        method=None,
        dependencies=None,
        evidence=None,
        counterexample=None,
        recipe=None,
    ):
        from types import MappingProxyType

        self._store, self._summary, self._violations = store, dict(summary), violations
        self._status = status or ("PROVED" if ok else "REFUTED")
        if self._status not in ("PROVED", "REFUTED", "INCONCLUSIVE"):
            raise ValueError("Unknown Report status")
        self._conclusion = (
            conclusion
            if conclusion is not None
            else dict(kind="relation", operation=summary.get("operation"))
        )
        self._scope = dict(
            scope
            or dict(
                kind="relation",
                domain=None,
                arithmetic="exact",
                complete=True,
                selection=summary.get("scope", "declared input"),
            )
        )
        if "snapshot" not in self._scope:
            self._scope["snapshots"] = tuple(
                dict(uid=m.uid, version=m.version) for m in violations._models
            )
            self._scope["assumptions"] = 0
        self._method = method or summary.get("operation", "relation check")
        self._dependencies = dependencies
        self._evidence = MappingProxyType(dict(evidence or {}))
        self._counterexample = counterexample
        self._recipe = recipe
        self._verified = False
        self._parents = ()
        self._assumption_reader = lambda: iter(())

    def _guard(self):
        self._store.guard()

    @property
    def status(self):
        """
        MIPFRAME HELP BEGIN
        Report.status [api.Report.status]

        Read report status with the original conclusion and scope.

        Report.status [property]

        When to use: Read report status with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        self._guard()
        return self._status

    @property
    def ok(self):
        """
        MIPFRAME HELP BEGIN
        Report.ok [api.Report.ok]

        Read report ok with the original conclusion and scope.

        Report.ok [property]

        When to use: Read report ok with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        return self.status == "PROVED"

    @property
    def summary(self):
        """
        MIPFRAME HELP BEGIN
        Report.summary [api.Report.summary]

        Read report summary with the original conclusion and scope.

        Report.summary [property]

        When to use: Read report summary with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        from copy import deepcopy

        self._guard()
        return deepcopy(self._summary)

    @property
    def scope(self):
        """
        MIPFRAME HELP BEGIN
        Report.scope [api.Report.scope]

        Read report scope with the original conclusion and scope.

        Report.scope [property]

        When to use: Read report scope with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        from copy import deepcopy

        self._guard()
        return deepcopy(self._scope)

    @property
    def conclusion(self):
        """
        MIPFRAME HELP BEGIN
        Report.conclusion [api.Report.conclusion]

        Read report conclusion with the original conclusion and scope.

        Report.conclusion [property]

        When to use: Read report conclusion with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        from copy import deepcopy

        self._guard()
        return (
            deepcopy(self._conclusion)
            if isinstance(self._conclusion, dict)
            else self._conclusion
        )

    @property
    def method(self):
        """
        MIPFRAME HELP BEGIN
        Report.method [api.Report.method]

        Read report method with the original conclusion and scope.

        Report.method [property]

        When to use: Read report method with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        self._guard()
        return self._method

    @property
    def violations(self):
        """
        MIPFRAME HELP BEGIN
        Report.violations [api.Report.violations]

        Read report violations with the original conclusion and scope.

        Report.violations [property]

        When to use: Read report violations with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        self._guard()
        return self._violations

    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        Report.evidence [api.Report.evidence]

        Read report evidence with the original conclusion and scope.

        Report.evidence [property]

        When to use: Read report evidence with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        self._guard()
        return self._evidence

    def _empty(self):
        from mipframe.frame.base import Frame

        return Frame(
            {
                "reason": __import__("mipframe.frame.schema", fromlist=["DType"]).DType(
                    "str"
                )
            },
            models=self._violations._models,
            source=lambda fields, ctx: iter(()),
            known_count=0,
        )

    @property
    def dependencies(self):
        """
        MIPFRAME HELP BEGIN
        Report.dependencies [api.Report.dependencies]

        Read report dependencies with the original conclusion and scope.

        Report.dependencies [property]

        When to use: Read report dependencies with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        self._guard()
        return self._dependencies if self._dependencies is not None else self._empty()

    @property
    def counterexample(self):
        """
        MIPFRAME HELP BEGIN
        Report.counterexample [api.Report.counterexample]

        Read report counterexample with the original conclusion and scope.

        Report.counterexample [property]

        When to use: Read report counterexample with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        self._guard()
        return (
            self._counterexample if self._counterexample is not None else self._empty()
        )

    def __bool__(self):
        raise _help_hint(TypeError("Report has no truth value; inspect status or use require()"), 'error.expressions')

    def require(self):
        """
        MIPFRAME HELP BEGIN
        Report.require [api.Report.require]

        Raise ValidationError carrying this report unless status is PROVED.

        Report.require()

        When to use: Raise ValidationError carrying this report unless status is PROVED.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        if not self.ok:
            raise ValidationError(self)
        return self

    def explain(self):
        """
        MIPFRAME HELP BEGIN
        Report.explain [api.Report.explain]

        Read report explain with the original conclusion and scope.

        Report.explain()

        When to use: Read report explain with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.frame.row import Formula
        from itertools import islice

        lines = [repr(self)]
        for i, parent in enumerate(islice(self._parents, 8)):
            lines.append(
                f"  depends on #{i+1}: {parent.status}; {parent.method}; domain={parent.scope.get('domain')}"
            )
            lines.append("    " + repr(parent.conclusion))
        if len(self._parents) > 8:
            lines.append("  dependencies: ... (see dependencies Frame)")
        return Formula("\n".join(lines))

    def for_target(self, key):
        """
        MIPFRAME HELP BEGIN
        Report.for_target [api.Report.for_target]

        Select one conclusion from a batch Report, retaining shared evidence storage.

        Report.for_target(key)

        When to use: Select one conclusion from a batch Report, retaining shared evidence storage.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        self._guard()
        if not getattr(self, "_batch", None): raise TypeError("for_target requires a batch report")
        return self._batch.one(key)

    def to_spec(self):
        """
        MIPFRAME HELP BEGIN
        Report.to_spec [api.Report.to_spec]

        Read report to_spec with the original conclusion and scope.

        Report.to_spec()

        When to use: Read report to_spec with the original conclusion and scope.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.check.serialization import report_spec

        return report_spec(self)

    def write(self, path):
        """
        MIPFRAME HELP BEGIN
        Report.write [api.Report.write]

        Atomically export the exact certificate package, not the whole model.

        Report.write(path)

        When to use: Atomically export the exact certificate package, not the whole model.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.check.serialization import write_report

        return write_report(self, path)

    @classmethod
    def replay(
        cls, spec_or_path, *, models, relations=None, memory_mb=128, disk_mb=None
    ):
        """
        MIPFRAME HELP BEGIN
        Report.replay [api.Report.replay]

        Bind dependencies and rerun certificate verification; never trust saved status.

        Report.replay(spec_or_path, *, models, relations=None, memory_mb=128, disk_mb=None)

        When to use: Bind dependencies and rerun certificate verification; never trust saved status.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.check.serialization import replay

        return replay(
            spec_or_path,
            models=models,
            relations=relations,
            memory_mb=memory_mb,
            disk_mb=disk_mb,
        )

    def __repr__(self):
        from itertools import islice

        self._guard()
        lines = [
            f"<Report: {self.status}; {self.method}>",
            f"  scope: {self._scope.get('kind', 'relation')}; domain: {self._scope.get('domain')}; arithmetic: {self._scope.get('arithmetic')}",
        ]
        if self._scope.get("local"):
            lines.append(
                "  local premises only; counterexample extension to the model is not established"
            )
        assumptions = list(islice(self._assumption_reader(), 5))
        for a in assumptions[:4]:
            lines.append("  assume: " + repr(a))
        if len(assumptions) > 4:
            lines.append("  assume: ...")
        if self._conclusion is not None and not isinstance(self._conclusion, dict):
            lines.append("  conclusion: " + repr(self._conclusion))
        for k, v in islice(self.summary.items(), 12):
            lines.append(f"  {k}: {str(v)[:300]}")
        return "\n".join(lines)

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


ValidationReport = Report


def family_counts(relation, depth, memory_mb, *, patterns):
    from mipframe.store.incidence import default_depth

    store, filters, block = setup(relation, memory_mb)
    try:
        owner, kind = relation._owner, relation.target_kind
        depth = default_depth(owner, kind) if depth is None else depth
        if type(depth) is not int or depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        result = owner.ir._result
        result._check_depth(depth)
        colors = result._store.array(result._config, kind, depth)
        families = (
            result._row_families(depth)
            if kind == "row"
            else result._var_families(depth)
        )
        # Store canonical display ordinals, not text labels, in aggregation keys.
        family_db = families._store.db

        def ordinal(color):
            return (
                int(color)
                if kind == "var"
                else family_db.execute(
                    "SELECT position FROM families WHERE color=?", (int(color),)
                ).fetchone()[0]
            )

        def label(code):
            owner.model
            return f"{'F' if kind == 'row' else 'V'}{code}"

        work = store.table("color INTEGER PRIMARY KEY, n INTEGER")
        nodes = (
            store.table(
                "node INTEGER PRIMARY KEY,parent INTEGER,color INTEGER,n INTEGER,UNIQUE(parent,color,n)"
            )
            if patterns
            else None
        )
        members = (
            store.table("ordinal INTEGER PRIMARY KEY,id INTEGER UNIQUE,node INTEGER")
            if patterns
            else None
        )
        for source in source_index(relation, store):
            if patterns:
                store.execute(f"DELETE FROM {work}")
            for ids, _ in relation._blocks(source, block, filters):
                values, amounts = np.unique(colors[ids], return_counts=True)
                store.db.executemany(
                    f"INSERT INTO {work} VALUES(?,?) ON CONFLICT(color) DO UPDATE SET n=n+excluded.n",
                    ((ordinal(c), int(n)) for c, n in zip(values, amounts)),
                )
            if patterns:
                parent = 0
                for color, n in store.execute(
                    f"SELECT color,n FROM {work} ORDER BY color"
                ):
                    store.execute(
                        f"INSERT OR IGNORE INTO {nodes}(parent,color,n) VALUES(?,?,?)",
                        (parent, color, n),
                    )
                    parent = store.execute(
                        f"SELECT node FROM {nodes} WHERE parent=? AND color=? AND n=?",
                        (parent, color, n),
                    ).fetchone()[0]
                store.execute(
                    f"INSERT INTO {members}(id,node) VALUES(?,?)", (int(source), parent)
                )
        if not patterns:
            raise ValueError("Edge frequencies use Terms joined to family_membership")
        # Lexicographic trie walk assigns a bounded-memory, exact pattern order.
        store.execute(f"CREATE INDEX {nodes}_parent ON {nodes}(parent,color,n)")
        order = store.table("node INTEGER PRIMARY KEY, rank INTEGER")
        stack = store.table(
            "level INTEGER PRIMARY KEY,node INTEGER,color INTEGER,n INTEGER"
        )
        store.execute(f"INSERT INTO {stack} VALUES(0,0,-1,-1)")
        rank, level = 0, 0
        store.execute(f"INSERT INTO {order} VALUES(0,0)")
        while level >= 0:
            parent, color, n = store.execute(
                f"SELECT node,color,n FROM {stack} WHERE level=?", (level,)
            ).fetchone()
            child = store.execute(
                f"SELECT node,color,n FROM {nodes} WHERE parent=? AND (color,n)>(?,?) ORDER BY color,n LIMIT 1",
                (parent, color, n),
            ).fetchone()
            if child is None:
                store.execute(f"DELETE FROM {stack} WHERE level=?", (level,))
                level -= 1
            else:
                rank += 1
                store.execute(
                    f"UPDATE {stack} SET color=?,n=? WHERE level=?", (*child[1:], level)
                )
                store.execute(f"INSERT INTO {order} VALUES(?,?)", (child[0], rank))
                level += 1
                store.execute(
                    f"INSERT INTO {stack} VALUES(?,?, -1,-1)", (level, child[0])
                )
        store.execute(f"CREATE INDEX {members}_node ON {members}(node,ordinal)")
        query = f"SELECT m.node,count(*) AS count FROM {members} m JOIN {order} o USING(node) GROUP BY m.node ORDER BY count DESC,o.rank"
        return SimpleNamespace(
            _store=store, _nodes=nodes, _members=members, _query=query, depth=depth
        )
    except BaseException:
        store.close()
        raise
