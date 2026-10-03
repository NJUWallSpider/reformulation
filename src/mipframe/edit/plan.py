"""Transactional, source-relative edit declarations backed by paged SQLite."""

from contextlib import contextmanager
from fractions import Fraction
from math import inf
import uuid
from mipframe.store.algorithm_store import AlgorithmStore
from mipframe.store.numbers import MIPFrameError
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType, Ref, SchemaError, ref_type
from mipframe.linear.scalar import q
from mipframe.linear.expr import coerce, owners
from mipframe.linear.claim import Claim
from mipframe.check.report import Report, ValidationError
from mipframe.edit.draft import (
    DraftExpr,
    DraftClaim,
    DraftVar,
    DraftRef,
    EDITS,
    draft_type,
)

UNSET = object()


class EditConflictError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    EditConflictError [api.EditConflictError]

    Diagnostic exception; inspect original message and any attached complete Report.

    EditConflictError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.Edit
    MIPFRAME HELP END
    """
    pass


class EditValidationError(ValidationError):
    """
    MIPFRAME HELP BEGIN
    EditValidationError [api.EditValidationError]

    Diagnostic exception; inspect original message and any attached complete Report.

    EditValidationError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.Edit.build
    MIPFRAME HELP END
    """
    pass


class NonIsolatedVariableError(EditValidationError):
    """
    MIPFRAME HELP BEGIN
    NonIsolatedVariableError [api.NonIsolatedVariableError]

    Diagnostic exception; inspect original message and any attached complete Report.

    NonIsolatedVariableError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: limitation.elimination
    MIPFRAME HELP END
    """
    pass


class UndischargedAssumptionError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    UndischargedAssumptionError [api.UndischargedAssumptionError]

    Diagnostic exception; inspect original message and any attached complete Report.

    UndischargedAssumptionError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.conditions
    MIPFRAME HELP END
    """
    pass


def bound(x, side):
    if type(x) is float and x == (-inf if side == "lb" else inf):
        return x
    return q(x)


def num(x):
    return float(x) if x in ("inf", "-inf") else Fraction(x)


class Edit:
    """
    MIPFRAME HELP BEGIN
    Edit [api.Edit]

    Source-relative simultaneous modifications producing a new independent snapshot.

    Edit [type]

    When to use: Source-relative simultaneous modifications producing a new independent snapshot.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: recipe.edits
    MIPFRAME HELP END
    """
    def __init__(self, owner, *, memory_mb=128, disk_mb=None):
        self._owner = owner
        self.uid = str(uuid.uuid4())
        self.memory_mb = memory_mb
        self.disk_mb = disk_mb
        self._store = AlgorithmStore(
            (owner,), memory_mb=memory_mb, disk_mb=disk_mb, max_output_rows=None
        )
        self._store.db.execute("PRAGMA journal_mode=DELETE")
        self._sealed = False
        self._proofs = []
        self._prepared = None
        schema = """
        CREATE TABLE operations(id INTEGER PRIMARY KEY,kind TEXT);
        CREATE TABLE fields(op INTEGER,field TEXT,PRIMARY KEY(op,field));
        CREATE TABLE subjects(op INTEGER,axis TEXT,id INTEGER,PRIMARY KEY(op,axis,id));
        CREATE TABLE proofs(op INTEGER,p INTEGER,PRIMARY KEY(op,p));
        CREATE TABLE changes(axis TEXT,id INTEGER,field TEXT,value TEXT,PRIMARY KEY(axis,id,field));
        CREATE TABLE dropped(axis TEXT,id INTEGER,PRIMARY KEY(axis,id));
        CREATE TABLE new_vars(id INTEGER PRIMARY KEY,batch INTEGER,label TEXT,name TEXT,kind TEXT,lb TEXT,ub TEXT,obj TEXT,UNIQUE(batch,label));
        CREATE TABLE new_rows(id INTEGER PRIMARY KEY,batch INTEGER,label TEXT,name TEXT,lb TEXT,ub TEXT,UNIQUE(batch,label));
        CREATE TABLE new_terms(r INTEGER,ns INTEGER,v INTEGER,c TEXT,PRIMARY KEY(r,ns,v));
        CREATE TABLE objective(ns INTEGER,v INTEGER,c TEXT,PRIMARY KEY(ns,v));
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE replaces(old INTEGER,new INTEGER,PRIMARY KEY(old,new));
        """
        try:
            for statement in schema.split(";"):
                if statement.strip():
                    self._store.execute(statement)
            self._store.disk_check()
        except BaseException:
            self._store.close()
            raise
        EDITS[self.uid] = self

    def _guard(self, write=False):
        self._store.guard()
        if write and self._sealed:
            raise EditConflictError(
                "Edit already built; create an Edit on the new snapshot"
            )

    @contextmanager
    def _transaction(self):
        self._guard(True)
        s = self._store
        count = len(self._proofs)
        s.execute("SAVEPOINT edit_call")
        try:
            yield
            s.disk_check()
            s.execute("RELEASE edit_call")
        except BaseException as exc:
            s.db.execute("ROLLBACK TO edit_call")
            s.db.execute("RELEASE edit_call")
            del self._proofs[count:]
            import sqlite3

            if isinstance(exc, sqlite3.IntegrityError):
                raise SchemaError(
                    "Duplicate edit input key or invalid input record"
                ) from exc
            raise
        if self._prepared is not None:
            self._prepared.close()
            self._prepared = None

    def _op(self, kind, because=None):
        op = self._store.execute(
            "INSERT INTO operations(kind) VALUES(?)", (kind,)
        ).lastrowid
        if because is not None:
            proofs = (because,) if isinstance(because, Report) else because
            attached = False
            for proof in proofs:
                self._check_proof(proof)
                attached = True
                p = next((i for i, r in enumerate(self._proofs) if r is proof), None)
                if p is None:
                    p = len(self._proofs)
                    self._proofs.append(proof)
                self._store.execute("INSERT OR IGNORE INTO proofs VALUES(?,?)", (op, p))
            if not attached:
                from mipframe.check.certificate import InvalidCertificateError

                raise InvalidCertificateError(
                    "because must contain at least one Report"
                )
        return op

    def _check_proof(self, p):
        from mipframe.check.certificate import InvalidCertificateError

        if not isinstance(p, Report) or not p._verified or p.status != "PROVED":
            raise InvalidCertificateError(
                "because requires a verified PROVED mathematical Report"
            )
        if p.scope.get("snapshot") != dict(
            uid=self._owner.uid, version=self._owner.version
        ):
            raise InvalidCertificateError(
                "Evidence belongs to a different source snapshot"
            )
        if p.scope.get("assumptions", 0):
            raise UndischargedAssumptionError(
                "Conditional evidence must be rechecked on a branch snapshot"
            )
        if p.scope.get("arithmetic") != "exact" or not (
            isinstance(p.conclusion, Claim) or getattr(p, "_batch", None)
        ):
            raise InvalidCertificateError("This Report does not prove linear claims")
        from mipframe.check.serialization import topology

        topology(p)

    def _selected(self, selection, axis):
        from mipframe.frame.entity import RowFrame, VarFrame
        from mipframe.frame.row import Row
        from mipframe.frame.var import Var

        cls = RowFrame if axis == "row" else VarFrame
        entity = Row if axis == "row" else Var
        if isinstance(selection, entity):
            owners((self._owner,), (selection._owner,))
            yield selection.id
            return
        if not isinstance(selection, cls):
            raise SchemaError(f"Expected source {axis} entity or Frame")
        owners((self._owner,), selection._models)
        yield from selection.ids

    def _frame(self, f, required):
        if not isinstance(f, Frame) or not set(required) <= set(f.schema):
            raise SchemaError(f"Expected Frame columns {required}")
        for m in f._models:
            owners((self._owner,), (m,))
        return f._raw(tuple(f.schema), self._store)

    def _name(self, name, axis, ident):
        if name is not None:
            if not isinstance(name, str) or not name:
                raise SchemaError("Names must be nonempty strings")
            return name
        # Generated names avoid all original names, even if subsequently dropped.
        table = "rows" if axis == "row" else "variables"
        new = "new_rows" if axis == "row" else "new_vars"
        name = f"edit_{axis}_{ident}"
        suffix = 0
        while (
            self._owner.model.db.execute(
                f"SELECT 1 FROM {table} WHERE name=?", (name,)
            ).fetchone()
            or self._store.execute(
                f"SELECT 1 FROM {new} WHERE name=?", (name,)
            ).fetchone()
        ):
            suffix += 1
            name = f"edit_{axis}_{ident}_{suffix}"
        return name

    def _label(self, label):
        if not isinstance(label, str) or not label:
            raise SchemaError("label must be a nonempty string")
        return label

    def _batch_frame(self, table, batch, axis):
        schema = {"label": DType("str"), axis: draft_type(self, axis)}
        f = self._store.publish(
            schema,
            (
                {"label": label, axis: ident}
                for ident, label in self._store.execute(
                    f"SELECT id,label FROM {table} WHERE batch=? ORDER BY id", (batch,)
                )
            ),
            key=("label",),
        )
        return f

    def add_vars(self, specs):
        """
        MIPFRAME HELP BEGIN
        Edit.add_vars [api.Edit.add_vars]

        Stage new variables and return Edit-local handles.

        Edit.add_vars(specs)

        When to use: Stage new variables and return Edit-local handles.

        Inputs: specs(label,name?,kind?,lb?,ub?,obj?); defaults C,0,+inf,0.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            op = self._op("add_vars")
            for r in self._frame(specs, ("label",)):
                label = self._label(r["label"])
                kind = r.get("kind", "C")
                if kind not in ("C", "I"):
                    raise SchemaError("kind must be C or I")
                ident = self._store.execute(
                    "SELECT coalesce(max(id)+1,0) FROM new_vars"
                ).fetchone()[0]
                self._store.execute(
                    "INSERT INTO new_vars VALUES(?,?,?,?,?,?,?,?)",
                    (
                        ident,
                        op,
                        label,
                        self._name(r.get("name"), "var", ident),
                        kind,
                        str(bound(r.get("lb", 0), "lb")),
                        str(bound(r.get("ub", inf), "ub")),
                        str(q(r.get("obj", 0))),
                    ),
                )
            result = self._batch_frame("new_vars", op, "var")
        return result

    def _endpoint(self, raw, typ, draft=False):
        if draft:
            if (
                typ is None
                or typ.kind != "draft_ref"
                or typ.uid != self.uid
                or typ.axis != "var"
            ):
                raise SchemaError("Expected a variable from this Edit")
            if not self._store.execute(
                "SELECT 1 FROM new_vars WHERE id=?", (raw,)
            ).fetchone():
                raise SchemaError("Unknown draft variable")
        else:
            if typ is None or not typ.compatible(ref_type(self._owner, "var")):
                raise SchemaError("Expected source VarRef")
            if type(raw) is not int or not 0 <= raw < self._owner.shape[1]:
                raise SchemaError("Invalid source variable ID")
        return int(draft), raw

    def _term(self, r, ns, v, c):
        c = q(c)
        self._store.check_size(c)
        old = self._store.execute(
            "SELECT c FROM new_terms WHERE r=? AND ns=? AND v=?", (r, ns, v)
        ).fetchone()
        c += Fraction(old[0]) if old else 0
        if c:
            self._store.execute(
                "INSERT OR REPLACE INTO new_terms VALUES(?,?,?,?)", (r, ns, v, str(c))
            )
        else:
            self._store.execute(
                "DELETE FROM new_terms WHERE r=? AND ns=? AND v=?", (r, ns, v)
            )

    def _add_rows(self, rows, terms, because):
        op = self._op("add_rows", because)
        for r in self._frame(rows, ("label",)):
            ident = self._store.execute(
                "SELECT coalesce(max(id)+1,0) FROM new_rows"
            ).fetchone()[0]
            self._store.execute(
                "INSERT INTO new_rows VALUES(?,?,?,?,?,?)",
                (
                    ident,
                    op,
                    self._label(r["label"]),
                    self._name(r.get("name"), "row", ident),
                    str(bound(r.get("lb", -inf), "lb")),
                    str(bound(r.get("ub", inf), "ub")),
                ),
            )
        for t in self._frame(terms, ("label", "coef")):
            found = self._store.execute(
                "SELECT id FROM new_rows WHERE batch=? AND label=?", (op, t["label"])
            ).fetchone()
            if found is None:
                raise SchemaError("Term label is outside this row batch")
            a, b = t.get("var"), t.get("draft_var")
            if (a is None) == (b is None):
                raise SchemaError("Terms require exactly one variable endpoint")
            ns, v = self._endpoint(
                b if a is None else a,
                terms.schema.get("draft_var" if a is None else "var"),
                a is None,
            )
            self._term(found[0], ns, v, t["coef"])
        return op

    def add_rows(self, *, rows, terms, because=None):
        """
        MIPFRAME HELP BEGIN
        Edit.add_rows [api.Edit.add_rows]

        Stage a complete batch of new rows atomically.

        Edit.add_rows(*, rows, terms, because=None)

        When to use: Stage a complete batch of new rows atomically.

        Inputs: rows(label,name?,lb?,ub?); terms(label,var,coef), or nullable var/draft_var endpoints.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            op = self._add_rows(rows, terms, because)
            result = self._batch_frame("new_rows", op, "row")
        return result

    def _expr(self, expr):
        if isinstance(expr, DraftVar):
            expr = expr.expr
        if isinstance(expr, DraftExpr):
            if expr._edit is not self:
                raise SchemaError("Different draft Edit")
            return expr
        expr = coerce(self._owner, expr)
        return DraftExpr.create(
            self, (((0, v), c) for v, c in expr._items()), expr.constant
        )

    def add_row(self, expr, *, lb=UNSET, ub=UNSET, name=None, because=None):
        """
        MIPFRAME HELP BEGIN
        Edit.add_row [api.Edit.add_row]

        Stage a new row with a new identity.

        Edit.add_row(expr, *, lb=<unchanged>, ub=<unchanged>, name=None, because=None)

        When to use: Stage a new row with a new identity.

        Inputs: Claim or LinExpr with explicit lb/ub; optional name and because.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            if isinstance(expr, (Claim, DraftClaim)):
                if lb is not UNSET or ub is not UNSET:
                    raise SchemaError("Do not mix Claim and bounds")
                rel = expr.relation
                expr = expr.expr
                lb = 0 if rel in (">=", "==") else -inf
                ub = 0 if rel in ("<=", "==") else inf
            else:
                lb = -inf if lb is UNSET else lb
                ub = inf if ub is UNSET else ub
            expr = self._expr(expr)
            lo = bound(lb, "lb")
            hi = bound(ub, "ub")
            op = self._op("add_rows", because)
            ident = self._store.execute(
                "SELECT coalesce(max(id)+1,0) FROM new_rows"
            ).fetchone()[0]
            self._store.execute(
                "INSERT INTO new_rows VALUES(?,?,?,?,?,?)",
                (
                    ident,
                    op,
                    "row",
                    self._name(name, "row", ident),
                    str(lo - expr.constant),
                    str(hi - expr.constant),
                ),
            )
            for (ns, v), c in expr._items():
                self._term(ident, ns, v, c)
        return DraftRef(self.uid, "row", ident)

    def _drop(self, selection, axis, because=None):
        op = self._op("drop_" + axis + "s", because)
        for ident in self._selected(selection, axis):
            if self._store.execute(
                "SELECT 1 FROM changes WHERE axis=? AND id=?", (axis, ident)
            ).fetchone():
                raise EditConflictError(
                    "Cannot delete and modify the same source entity"
                )
            self._store.execute(
                "INSERT OR IGNORE INTO dropped VALUES(?,?)", (axis, ident)
            )
            self._store.execute(
                "INSERT OR IGNORE INTO subjects VALUES(?,?,?)", (op, axis, ident)
            )
        return op

    def drop_rows(self, R, *, because=None):
        """
        MIPFRAME HELP BEGIN
        Edit.drop_rows [api.Edit.drop_rows]

        Stage deletion of source rows.

        Edit.drop_rows(R, *, because=None)

        When to use: Stage deletion of source rows.

        Inputs: Source Row/RowFrame; because must cover every lost finite side if provided.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            self._drop(R, "row", because)
        return self

    def drop_vars(self, V):
        """
        MIPFRAME HELP BEGIN
        Edit.drop_vars [api.Edit.drop_vars]

        Remove only finally unreferenced variables.

        Edit.drop_vars(V)

        When to use: Remove only finally unreferenced variables.

        Inputs: Source Var/VarFrame; final row degree and objective coefficient must be zero.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            self._drop(V, "var")
        return self

    def _set(self, selection, axis, updates, because):
        op = self._op("set_" + axis, because)
        s = self._store
        chosen = s.raw_table("id INTEGER PRIMARY KEY")
        for ident in self._selected(selection, axis):
            if s.execute(
                "SELECT 1 FROM dropped WHERE axis=? AND id=?", (axis, ident)
            ).fetchone():
                raise EditConflictError("Cannot modify a deleted entity")
            s.execute(f"INSERT OR IGNORE INTO {chosen} VALUES(?)", (ident,))
            s.execute("INSERT OR IGNORE INTO subjects VALUES(?,?,?)", (op, axis, ident))
        for field, value in updates.items():
            if value is UNSET:
                continue
            s.execute("INSERT INTO fields VALUES(?,?)", (op, field))

            def valid(x):
                if field == "kind":
                    if x not in ("C", "I"):
                        raise SchemaError("kind must be C or I")
                    return x
                return str(bound(x, field))

            if isinstance(value, Frame):
                if not ref_type(self._owner, axis).compatible(
                    value.schema.get(axis, DType("null", True))
                ):
                    raise SchemaError("Wrong bound key type")
                aligned = s.raw_table("id INTEGER PRIMARY KEY,value TEXT")
                for rec in self._frame(value, (axis, "value")):
                    s.execute(
                        f"INSERT INTO {aligned} VALUES(?,?)",
                        (rec[axis], valid(rec["value"])),
                    )
                for a, b in ((aligned, chosen), (chosen, aligned)):
                    if s.execute(
                        f"SELECT id FROM {a} EXCEPT SELECT id FROM {b} LIMIT 1"
                    ).fetchone():
                        raise SchemaError("Bound keys must match the selection exactly")
                vals = s.execute(f"SELECT id,value FROM {aligned} ORDER BY id")
            else:
                x = valid(value)
                vals = (
                    (i, x) for i, in s.execute(f"SELECT id FROM {chosen} ORDER BY id")
                )
            for ident, x in vals:
                old = s.execute(
                    "SELECT value FROM changes WHERE axis=? AND id=? AND field=?",
                    (axis, ident, field),
                ).fetchone()
                if old and old[0] != x:
                    raise EditConflictError("Conflicting values for the same field")
                s.execute(
                    "INSERT OR IGNORE INTO changes VALUES(?,?,?,?)",
                    (axis, ident, field, x),
                )
        return op

    def set_var_bounds(self, V, *, lb=UNSET, ub=UNSET, because=None):
        """
        MIPFRAME HELP BEGIN
        Edit.set_var_bounds [api.Edit.set_var_bounds]

        Change source variable bounds.

        Edit.set_var_bounds(V, *, lb=<unchanged>, ub=<unchanged>, because=None)

        When to use: Change source variable bounds.

        Inputs: Source variables; scalar or exactly key-aligned Frame(var,value) per side.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            self._set(V, "var", dict(lb=lb, ub=ub), because)
        return self

    def set_row_bounds(self, R, *, lb=UNSET, ub=UNSET, because=None):
        """
        MIPFRAME HELP BEGIN
        Edit.set_row_bounds [api.Edit.set_row_bounds]

        Change source row bounds without changing its expression.

        Edit.set_row_bounds(R, *, lb=<unchanged>, ub=<unchanged>, because=None)

        When to use: Change source row bounds without changing its expression.

        Inputs: Source rows; scalar or exactly key-aligned Frame(row,value) per side.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            self._set(R, "row", dict(lb=lb, ub=ub), because)
        return self

    def fix(self, V, value, *, because=None):
        """
        MIPFRAME HELP BEGIN
        Edit.fix [api.Edit.fix]

        Set both source variable bounds to one exact value.

        Edit.fix(V, value, *, because=None)

        When to use: Set both source variable bounds to one exact value.

        Inputs: Source variables, value and optional verified evidence.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        return self.set_var_bounds(V, lb=value, ub=value, because=because)

    def set_domains(self, V, *, kind):
        """
        MIPFRAME HELP BEGIN
        Edit.set_domains [api.Edit.set_domains]

        Set the canonical variable domain.

        Edit.set_domains(V, *, kind)

        When to use: Set the canonical variable domain.

        Inputs: Source variables; kind C/I or key-aligned value Frame.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        with self._transaction():
            self._set(V, "var", dict(kind=kind), None)
        return self

    def set_objective(self, expr, *, sense):
        """
        MIPFRAME HELP BEGIN
        Edit.set_objective [api.Edit.set_objective]

        Replace the entire objective independent of staging order.

        Edit.set_objective(expr, *, sense)

        When to use: Replace the entire objective independent of staging order.

        Inputs: Affine source/draft expression and min/max sense; includes its constant.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        if sense not in ("min", "max"):
            raise SchemaError("Objective sense must be min or max")
        with self._transaction():
            e = self._expr(expr)
            s = self._store
            old = s.execute("SELECT value FROM settings WHERE key='sense'").fetchone()
            if old:
                from itertools import zip_longest

                stored = (
                    ((ns, v), Fraction(c))
                    for ns, v, c in s.execute(
                        "SELECT ns,v,c FROM objective ORDER BY ns,v"
                    )
                )
                if (
                    old[0] != sense
                    or num(
                        s.execute(
                            "SELECT value FROM settings WHERE key='constant'"
                        ).fetchone()[0]
                    )
                    != e.constant
                    or any(a != b for a, b in zip_longest(stored, e._items()))
                ):
                    raise EditConflictError("Conflicting objective replacements")
            else:
                self._op("set_objective")
                s.execute("INSERT INTO settings VALUES(?,?)", ("sense", sense))
                s.execute(
                    "INSERT INTO settings VALUES(?,?)", ("constant", str(e.constant))
                )
                for (ns, v), c in e._items():
                    s.execute("INSERT INTO objective VALUES(?,?,?)", (ns, v, str(c)))
        return self

    def replace_rows(self, old, *, rows, terms, correspondence, because=None):
        """
        MIPFRAME HELP BEGIN
        Edit.replace_rows [api.Edit.replace_rows]

        Stage explicit row replacement relationships.

        Edit.replace_rows(old, *, rows, terms, correspondence, because=None)

        When to use: Stage explicit row replacement relationships.

        Inputs: old source rows, new rows/terms and correspondence(old_row,new_label).

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        because = because or {}
        with self._transaction():
            drop = self._drop(old, "row", because.get("drop"))
            batch = self._add_rows(rows, terms, because.get("add"))
            if not ref_type(self._owner, "row").compatible(
                correspondence.schema.get("old_row", DType("null", True))
            ):
                raise SchemaError("Expected old_row Ref")
            for rec in self._frame(correspondence, ("old_row", "new_label")):
                r = self._store.execute(
                    "SELECT id FROM new_rows WHERE batch=? AND label=?",
                    (batch, rec["new_label"]),
                ).fetchone()
                if (
                    r is None
                    or not self._store.execute(
                        "SELECT 1 FROM subjects WHERE op=? AND id=?",
                        (drop, rec["old_row"]),
                    ).fetchone()
                ):
                    raise SchemaError("Replacement correspondence outside selection")
                self._store.execute(
                    "INSERT INTO replaces VALUES(?,?)", (rec["old_row"], r[0])
                )
            for query, args in [
                (
                    "SELECT id FROM subjects WHERE op=? EXCEPT SELECT old FROM replaces",
                    (drop,),
                ),
                (
                    "SELECT id FROM new_rows WHERE batch=? EXCEPT SELECT new FROM replaces",
                    (batch,),
                ),
            ]:
                if self._store.execute(query, args).fetchone():
                    raise SchemaError(
                        "Replacement correspondence must cover both selections"
                    )
            result = self._batch_frame("new_rows", batch, "row")
        return result

    def _prepare(self):
        self._guard()
        if self._prepared is None:
            from mipframe.edit.prepare import Prepared

            self._prepared = Prepared(self)
        return self._prepared

    def summary(self):
        """
        MIPFRAME HELP BEGIN
        Edit.summary [api.Edit.summary]

        Validate and summarize the staged plan without building a Model.

        Edit.summary()

        When to use: Validate and summarize the staged plan without building a Model.

        Inputs: No arguments; reports whether build is possible.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        from mipframe.frame.row import Formula

        p = self._prepare()
        max_row = self._store.execute(
            f"SELECT coalesce(max(n),0) FROM {p.rows}"
        ).fetchone()[0]
        max_var = self._store.execute(
            f"SELECT coalesce(max(n),0) FROM {p.vars}"
        ).fetchone()[0]
        return Formula(
            f"Planned edits\n  source: {self._owner.shape[0]:,} rows × {self._owner.shape[1]:,} vars; {self._owner.nnz:,} nnz\n  target: {p.nrows:,} rows × {p.nvars:,} vars; {p.nnz:,} nnz\n  max row nnz: {max_row:,}; max column nnz: {max_var:,}\n  build checks: {p.errors:,} errors; {p.infeasible:,} directly infeasible facts\n  semantics: {p.semantic}"
        )

    def build(self, *, allow_infeasible=False):
        """
        MIPFRAME HELP BEGIN
        Edit.build [api.Edit.build]

        Atomically build an independent edited snapshot and prepare its own IR/indexed.

        Edit.build(*, allow_infeasible=False)

        When to use: Atomically build an independent edited snapshot and prepare its own IR/indexed.

        Inputs: allow_infeasible=False rejects directly visible impossible bounds/empty rows.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        self._guard(True)
        if type(allow_infeasible) is not bool:
            raise TypeError("allow_infeasible must be boolean")
        p = self._prepare()
        p.require(allow_infeasible)
        from mipframe.edit.build import build

        result = build(self, p, allow_infeasible)
        self._sealed = True
        return result

    def close(self):
        """
        MIPFRAME HELP BEGIN
        Edit.close [api.Edit.close]

        Release the Edit and live draft resources.

        Edit.close()

        When to use: Release the Edit and live draft resources.

        Inputs: No arguments.

        Returns: New staging result, Formula summary or independent Model as appropriate.

        Establishes: All operations are source-relative and simultaneous. Each staging call is atomic; conflicting writes are rejected.

        Does not establish: Without evidence, no equivalence is asserted. New rows cannot serve as source facts before an intermediate build.

        Failures: Supplied because must be proved, exact, unconditional, same-snapshot and cover the change; invalid evidence is never silently ignored.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Successful build seals the Edit. Failure publishes no model; source and previous results remain intact.
        Changed variable coordinates have no automatic projection-equivalence claim. LP and MIP semantic inclusions are reported separately.
        For deleting facts, proof chains must close to final retained source facts; self-proof and ungrounded cycles fail.
        Use native save to retain lineage; historical saved status is not a new live proof.

        See also: recipe.edits, concept.conditions
        MIPFRAME HELP END
        """
        self._store.close()

    def __enter__(self):
        self._guard()
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self):
        self._guard()
        return (
            f'<Edit: {"built" if self._sealed else "open"}; source {self._owner.shape}>'
        )
