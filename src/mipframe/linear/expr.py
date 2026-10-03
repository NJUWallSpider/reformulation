"""Immutable, canonical sparse affine expressions and bounded exact operations."""

from collections.abc import Mapping
from fractions import Fraction
from itertools import zip_longest
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType, Ref, SchemaError, ref_type, encode, decode
from mipframe.store.algorithm_store import AlgorithmStore
from mipframe.linear.scalar import q, NonlinearExpressionError


def owners(*groups):
    result = []
    identity = None
    for group in groups:
        for m in group:
            current = (m.uid, m.version)
            if identity is not None and current != identity:
                raise SchemaError(
                    "Expressions require the same snapshot uid and version"
                )
            identity = current
            if not any(m is x for x in result):
                result.append(m)
    return tuple(result)


def attach(ctx, models):
    ctx.models = owners(ctx.models, models)
    for model in models:
        model._query_stores.add(ctx)


def context(models, memory_mb=None, disk_mb=None):
    from mipframe.options import options

    return AlgorithmStore(
        models,
        memory_mb=options.query.memory_mb if memory_mb is None else memory_mb,
        disk_mb=disk_mb,
        max_output_rows=None,
    )


def var_id(owner, key):
    from mipframe.frame.var import Var

    if isinstance(key, Var):
        owners((owner,), (key._owner,))
        ident = key.id
    elif (
        isinstance(key, Ref)
        and key.kind == "var"
        and (key.uid, key.version) == (owner.uid, owner.version)
    ):
        ident = key.id
    else:
        raise SchemaError(
            "Expected a Var or VarRef of this snapshot, not a name or integer"
        )
    if not 0 <= ident < owner.shape[1]:
        raise SchemaError("Variable ID outside model")
    return ident


def vector(ctx, owner, values, value="value"):
    """Validate the complete input before use, retaining only one exact scalar per ID."""
    table = ctx.raw_table("v INTEGER PRIMARY KEY,c TEXT")
    if isinstance(values, Frame):
        if (
            "var" not in values.schema
            or value not in values.schema
            or not ref_type(owner, "var").compatible(values.schema["var"])
        ):
            raise SchemaError("Expected a snapshot-qualified Frame(var, value)")
        if values.schema[value].kind not in ("number", "int"):
            raise SchemaError("Vector values must have an exact numeric type")
        attach(ctx, values._models)
        records = ((r["var"], r[value]) for r in values._raw(("var", value), ctx))
    elif isinstance(values, Mapping):

        def records_():
            for key, x in values.items():
                if hasattr(key, "_owner"):
                    attach(ctx, (key._owner,))
                yield var_id(owner, key), x

        records = records_()
    else:
        raise SchemaError("Expected a Frame or a Var/VarRef mapping")
    import sqlite3

    for ident, x in records:
        if type(ident) is not int or not 0 <= ident < owner.shape[1]:
            raise SchemaError("Variable ID outside model")
        scalar = q(x)
        ctx.check_size(scalar)
        try:
            ctx.execute(f"INSERT INTO {table} VALUES(?,?)", (ident, str(scalar)))
        except sqlite3.IntegrityError as exc:
            raise SchemaError("Duplicate vector variable") from exc
    return table


class LinExpr:
    """
    MIPFRAME HELP BEGIN
    LinExpr [api.LinExpr]

    Immutable sparse affine expression; use variable arithmetic, row.expr or m.linexpr.

    LinExpr [type]

    When to use: Immutable sparse affine expression; use variable arithmetic, row.expr or m.linexpr.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.proofs, concept.exact
    MIPFRAME HELP END
    """
    def __setattr__(self, name, value):
        if not name.startswith("_"):
            raise AttributeError("LinExpr is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        owner,
        constant=0,
        *,
        items=None,
        store=None,
        query=None,
        models=None,
        ids=None,
    ):
        self._owner = owner
        self._models = owners((owner,), models or ())
        self._constant = q(constant)
        self._source = items
        self._id_source = ids
        self._store, self._query = store, query

    def _guard(self):
        for m in self._models:
            m.model
        if self._store is not None:
            self._store.guard()

    def _items(self, ctx=None):
        self._guard()
        if ctx is not None:
            attach(ctx, self._models)
        if self._store is not None:
            from contextlib import nullcontext

            with ctx.reader(self._store) if hasattr(ctx, "reader") else nullcontext():
                iterator = iter(self._store.execute(self._query))
                while True:
                    self._guard()
                    row = next(iterator, None)
                    if row is None:
                        break
                    v, c = row
                    yield int(v), Fraction(c)
        elif self._source is not None:
            it = iter(self._source())
            while True:
                self._guard()
                try:
                    yield next(it)
                except StopIteration:
                    break

    def _ids(self, ctx=None):
        self._guard()
        if ctx is not None:
            attach(ctx, self._models)
        if self._store is not None:
            iterator = iter(self._store.execute(f"SELECT v FROM ({self._query})"))
            while True:
                self._guard()
                item = next(iterator, None)
                if item is None:
                    return
                yield int(item[0])
        elif self._id_source is not None:
            iterator = iter(self._id_source())
            while True:
                self._guard()
                item = next(iterator, None)
                if item is None:
                    return
                yield int(item)
        else:
            for v, c in self._items(ctx):
                yield v

    @property
    def constant(self):
        """
        MIPFRAME HELP BEGIN
        LinExpr.constant [api.LinExpr.constant]

        Read or construct affine expression constant.

        LinExpr.constant [property]

        When to use: Read or construct affine expression constant.

        Inputs: Exact finite scalars and compatible snapshot identities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: Expression manipulation alone does not establish a valid model constraint.

        See also: concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        self._guard()
        return self._constant

    @property
    def terms(self):
        """
        MIPFRAME HELP BEGIN
        LinExpr.terms [api.LinExpr.terms]

        Canonical sorted nonzero expression terms; an ordinary Frame, not original TermFrame.

        LinExpr.terms [property]

        When to use: Canonical sorted nonzero expression terms; an ordinary Frame, not original TermFrame.

        Inputs: Exact finite scalars and compatible snapshot identities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: Expression manipulation alone does not establish a valid model constraint.

        See also: concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        self._guard()

        def read(fields, ctx):
            if "coef" not in fields:
                for v in self._ids(ctx):
                    yield {n: v for n in fields}
                return
            for v, c in self._items(ctx):
                yield {n: v if n == "var" else c for n in fields}

        return Frame(
            {"var": ref_type(self._owner, "var"), "coef": DType("number")},
            key=("var",),
            models=self._models,
            source=read,
        )

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        LinExpr.vars [api.LinExpr.vars]

        Read or construct affine expression vars.

        LinExpr.vars [property]

        When to use: Read or construct affine expression vars.

        Inputs: Exact finite scalars and compatible snapshot identities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: Expression manipulation alone does not establish a valid model constraint.

        See also: concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        return self.terms.refs("var")

    def _coerce(self, other):
        return coerce(self._owner, other)

    def __add__(self, other):
        from mipframe.edit.draft import DraftExpr, DraftVar
        if isinstance(other, (DraftExpr, DraftVar)): return other + self
        other = self._coerce(other)
        return materialize(
            self._owner,
            merge(self._items(), other._items()),
            self.constant + other.constant,
            models=owners(self._models, other._models),
        )

    __radd__ = __add__

    def __neg__(self):
        return self * -1

    def __sub__(self, other):
        from mipframe.edit.draft import DraftExpr, DraftVar
        if isinstance(other, (DraftExpr, DraftVar)): return -other + self
        return self + -self._coerce(other)

    def __rsub__(self, other):
        return self._coerce(other) - self

    def __mul__(self, scalar):
        from mipframe.edit.draft import DraftExpr, DraftVar
        if isinstance(scalar, (LinExpr, DraftExpr, DraftVar)) or hasattr(scalar, "_owner"):
            raise NonlinearExpressionError(
                "Only multiplication by an exact scalar is linear"
            )
        scalar = q(scalar)
        if not scalar:
            self._guard()
            return LinExpr(self._owner, 0, models=self._models)
        return materialize(
            self._owner,
            ((v, c * scalar) for v, c in self._items()),
            self.constant * scalar,
            models=self._models,
        )

    __rmul__ = __mul__

    def __pow__(self, exponent):
        raise NonlinearExpressionError("Powers are not supported by LinExpr")

    def __le__(self, other):
        from mipframe.linear.claim import Claim

        from mipframe.edit.draft import DraftExpr, DraftClaim
        difference = self - other
        return DraftClaim(difference, "<=") if isinstance(difference, DraftExpr) else Claim(difference, "<=")

    def __ge__(self, other):
        from mipframe.linear.claim import Claim

        from mipframe.edit.draft import DraftExpr, DraftClaim
        difference = self - other
        return DraftClaim(difference, ">=") if isinstance(difference, DraftExpr) else Claim(difference, ">=")

    def le(self, other):
        """
        MIPFRAME HELP BEGIN
        LinExpr.le [api.LinExpr.le]

        Construct an unproved mathematical <= Claim.

        LinExpr.le(other)

        When to use: Construct an unproved mathematical <= Claim.

        Inputs: Exact finite scalar, Var or compatible affine expression from the same snapshot.

        Returns: Claim, or DraftClaim when the other operand contains draft variables.

        Establishes: Equivalent to the <= operator.

        Does not establish: Does not add a row, prove the proposition or permit implicit bool.

        Failures: Inexact scalars, cross-snapshot operands and closed owners are rejected.

        Illustrative example (requires caller-provided inputs):
        claim = s.le(U)

        See also: error.expressions, concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        return self <= other

    def ge(self, other):
        """
        MIPFRAME HELP BEGIN
        LinExpr.ge [api.LinExpr.ge]

        Construct an unproved mathematical >= Claim.

        LinExpr.ge(other)

        When to use: Construct an unproved mathematical >= Claim.

        Inputs: Exact finite scalar, Var or compatible affine expression from the same snapshot.

        Returns: Claim, or DraftClaim when the other operand contains draft variables.

        Establishes: Equivalent to the >= operator.

        Does not establish: Does not add a row, prove the proposition or permit implicit bool.

        Failures: Inexact scalars, cross-snapshot operands and closed owners are rejected.

        Illustrative example (requires caller-provided inputs):
        claim = s.ge(U)

        See also: error.expressions, concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        return self >= other

    def eq(self, other):
        """
        MIPFRAME HELP BEGIN
        LinExpr.eq [api.LinExpr.eq]

        Construct an unproved mathematical == Claim.

        LinExpr.eq(other)

        When to use: Construct an unproved mathematical == Claim.

        Inputs: Exact finite scalar, Var or compatible affine expression from the same snapshot.

        Returns: Claim, or DraftClaim when the other operand contains draft variables.

        Establishes: Mathematical equality; Python == retains expression equality semantics.

        Does not establish: Does not add a row, prove the proposition or permit implicit bool.

        Failures: Inexact scalars, cross-snapshot operands and closed owners are rejected.

        Illustrative example (requires caller-provided inputs):
        claim = s.eq(U)

        See also: error.expressions, concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import Claim

        from mipframe.edit.draft import DraftExpr, DraftClaim
        difference = self - other
        return DraftClaim(difference, "==") if isinstance(difference, DraftExpr) else Claim(difference, "==")

    def __eq__(self, other):
        self._guard()
        if not isinstance(other, LinExpr):
            return False
        other._guard()
        return (
            (self._owner.uid, self._owner.version)
            == (other._owner.uid, other._owner.version)
            and self.constant == other.constant
            and all(a == b for a, b in zip_longest(self._items(), other._items()))
        )

    __hash__ = None

    def __bool__(self):
        raise TypeError("LinExpr has no truth value")

    def substitute(self, mapping, *, memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        LinExpr.substitute [api.LinExpr.substitute]

        Simultaneous one-level affine substitution; right sides are not recursively substituted.

        LinExpr.substitute(mapping, *, memory_mb=128, disk_mb=None)

        When to use: Simultaneous one-level affine substitution; right sides are not recursively substituted.

        Inputs: Exact finite scalars and compatible snapshot identities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: Expression manipulation alone does not establish a valid model constraint.

        See also: concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        if not isinstance(mapping, Mapping):
            raise SchemaError("Substitution requires a Var/VarRef mapping")
        ctx = context(self._models, memory_mb, disk_mb)
        try:
            table = ctx.raw_table("v INTEGER PRIMARY KEY,c TEXT")
            replacements = ctx.raw_table("v INTEGER PRIMARY KEY,constant TEXT")
            terms = ctx.raw_table("v INTEGER,w INTEGER,c TEXT,PRIMARY KEY(v,w)")
            for key, value in mapping.items():
                ident = var_id(self._owner, key)
                if hasattr(key, "_owner"):
                    attach(ctx, (key._owner,))
                e = self._coerce(value)
                attach(ctx, e._models)
                ctx.execute(
                    f"INSERT INTO {replacements} VALUES(?,?)", (ident, str(e.constant))
                )
                for w, c in e._items(ctx):
                    ctx.execute(
                        f"INSERT INTO {terms} VALUES(?,?,?)", (ident, w, str(c))
                    )
            constant = self.constant
            for v, c in self._items(ctx):
                replacement = ctx.execute(
                    f"SELECT constant FROM {replacements} WHERE v=?", (v,)
                ).fetchone()
                if replacement is None:
                    add(ctx, table, v, c)
                else:
                    constant += c * Fraction(replacement[0])
                    ctx.check_size(constant)
                    for w, a in ctx.execute(
                        f"SELECT w,c FROM {terms} WHERE v=? ORDER BY w", (v,)
                    ):
                        add(ctx, table, w, c * Fraction(a))
            from mipframe.linear.storage import finish_expression
            return finish_expression(self._owner, ctx, table, constant)
        except BaseException:
            ctx.close()
            raise

    def evaluate(
        self, values, *, value="value", missing="error", memory_mb=128, disk_mb=None
    ):
        """
        MIPFRAME HELP BEGIN
        LinExpr.evaluate [api.LinExpr.evaluate]

        Evaluate an affine expression at exact values without checking feasibility.

        LinExpr.evaluate(values, *, value='value', missing='error', memory_mb=128, disk_mb=None)

        When to use: Evaluate an affine expression at exact values without checking feasibility.

        Inputs: Exact finite scalars and compatible snapshot identities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: Expression manipulation alone does not establish a valid model constraint.

        See also: concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        if missing not in ("error", "zero"):
            raise ValueError("missing must be error or zero")
        ctx = context(self._models, memory_mb, disk_mb)
        try:
            table = vector(ctx, self._owner, values, value)
            total = self.constant
            for v, c in self._items(ctx):
                found = ctx.execute(f"SELECT c FROM {table} WHERE v=?", (v,)).fetchone()
                if found is None and missing == "error":
                    raise SchemaError(f"Missing value for variable ID {v}")
                if found is not None:
                    total += c * Fraction(found[0])
                    ctx.check_size(total)
            return total
        finally:
            ctx.close()

    def formula(self, *, max_terms=12):
        """
        MIPFRAME HELP BEGIN
        LinExpr.formula [api.LinExpr.formula]

        Bounded Formula display; does not verify the expression.

        LinExpr.formula(*, max_terms=12)

        When to use: Bounded Formula display; does not verify the expression.

        Inputs: Exact finite scalars and compatible snapshot identities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: Expression manipulation alone does not establish a valid model constraint.

        See also: concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        from mipframe.frame.row import Formula

        if type(max_terms) is not int or max_terms < 0:
            raise ValueError("max_terms must be a nonnegative integer")
        import json

        parts = []

        def append(coef, term):
            sign = (
                (" - " if coef < 0 else " + ") if parts else ("-" if coef < 0 else "")
            )
            parts.append(sign + term)

        for i, (v, c) in enumerate(self._items()):
            if i == max_terms:
                parts.append(" + ..." if parts else "...")
                break
            name = self._owner.vars.by_id(v).name
            if any(ch.isspace() or ch in '+-*/<=>"' for ch in name):
                name = json.dumps(name, ensure_ascii=False)
            append(c, name if abs(c) == 1 else f"{abs(c)}*{name}")
        if self.constant:
            append(self.constant, str(abs(self.constant)))
        return Formula("".join(parts) or "0")

    def __repr__(self):
        return "LinExpr(" + str(self.formula()) + ")"

    def _repr_pretty_(self, p, cycle):
        p.text(repr(self))

    def to_spec(self):
        """
        MIPFRAME HELP BEGIN
        LinExpr.to_spec [api.LinExpr.to_spec]

        Export the versioned exact expression representation.

        LinExpr.to_spec()

        When to use: Export the versioned exact expression representation.

        Inputs: Exact finite scalars and compatible snapshot identities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: Expression manipulation alone does not establish a valid model constraint.

        See also: concept.proofs, concept.exact
        MIPFRAME HELP END
        """
        from mipframe.frame.executor import size, MaterializationLimitError

        terms = []
        used = 0
        for v, c in self._items():
            item = [v, encode(c)]
            used += size(item)
            if used > 128 * 1024**2:
                raise MaterializationLimitError(
                    "Expression spec exceeds 128 MiB; use Report.write for a streaming certificate"
                )
            terms.append(item)
        return dict(
            version=1,
            snapshot=dict(uid=self._owner.uid, version=self._owner.version),
            constant=encode(self.constant),
            terms=terms,
        )

    @classmethod
    def from_spec(cls, spec, *, models):
        if spec.get("version") != 1:
            raise SchemaError("Unsupported expression specification version")
        owner = bind_model(spec["snapshot"], models)
        return materialize(
            owner,
            ((v, q(decode(c))) for v, c in spec["terms"]),
            q(decode(spec["constant"])),
        )


def bind_model(identity, models):
    candidates = models.values() if isinstance(models, Mapping) else models
    if hasattr(models, "uid"):
        candidates = (models,)
    for m in candidates:
        if m.uid == identity["uid"]:
            if m.version != identity["version"]:
                raise SchemaError("Snapshot version mismatch")
            return m
    raise SchemaError("Unbound snapshot identity")


def coerce(owner, value):
    from mipframe.frame.var import Var

    if isinstance(value, Var):
        value = variable(value)
    if isinstance(value, LinExpr):
        owners((owner,), value._models)
        return value
    return LinExpr(owner, q(value))


def variable(var):
    ident = var.id
    return LinExpr(
        var._owner,
        items=lambda: iter(((ident, Fraction(1)),)),
        ids=lambda: iter((ident,)),
    )


def row_expr(row):
    owner, ident = row._owner, row.id

    def read():
        m = owner.model
        for p in range(int(m.row_ptr[ident]), int(m.row_ptr[ident + 1])):
            owner.model
            yield int(m.col_idx[p]), m.num(int(m.coeff_id[p]))

    def ids():
        m = owner.model
        for p in range(int(m.row_ptr[ident]), int(m.row_ptr[ident + 1])):
            owner.model
            yield int(m.col_idx[p])

    return LinExpr(owner, items=read, ids=ids)


def merge(left, right):
    sentinel = object()
    a, b = next(left, sentinel), next(right, sentinel)
    while a is not sentinel or b is not sentinel:
        if b is sentinel or a is not sentinel and a[0] < b[0]:
            yield a
            a = next(left, sentinel)
        elif a is sentinel or b[0] < a[0]:
            yield b
            b = next(right, sentinel)
        else:
            c = a[1] + b[1]
            if c:
                yield a[0], c
            a, b = next(left, sentinel), next(right, sentinel)


def add(ctx, table, v, c):
    c = q(c)
    old = ctx.execute(f"SELECT c FROM {table} WHERE v=?", (v,)).fetchone()
    if old:
        c += Fraction(old[0])
    ctx.check_size(c)
    if c:
        ctx.execute(f"INSERT OR REPLACE INTO {table} VALUES(?,?)", (v, str(c)))
    elif old:
        ctx.execute(f"DELETE FROM {table} WHERE v=?", (v,))


def from_table(owner, ctx, table, constant):
    ctx.check_size(constant)
    ctx.disk_check()
    return LinExpr(
        owner,
        constant,
        store=ctx,
        query=f"SELECT v,c FROM {table} ORDER BY v",
        models=ctx.models,
    )


def materialize(owner, items, constant=0, *, models=(), memory_mb=None, disk_mb=None):
    # Most agent-authored expressions have only a few terms. Canonicalize those
    # in bounded memory instead of allocating a SQLite connection per operator.
    if disk_mb is None:
        from mipframe.options import options
        from mipframe.store.query_store import memory_bytes
        from mipframe.frame.executor import size, QueryMemoryError

        models = owners((owner,), models)
        for model in models:
            model.model
        memory = memory_bytes(options.query.memory_mb if memory_mb is None else memory_mb)
        constant = q(constant)
        if size(constant) > memory // 2:
            raise QueryMemoryError("An expression constant exceeds the working-memory budget")
        values, used = {}, 0
        items = iter(items)
        for v, c in items:
            if type(v) is not int or not 0 <= v < owner.shape[1]:
                raise SchemaError("Invalid variable ID in expression")
            c = q(c)
            if size(c) > memory // 2:
                raise QueryMemoryError("An expression coefficient exceeds the working-memory budget")
            old = values.pop(v, None)
            if old is not None:
                used -= size(v) + size(old) + 72
                c += old
            if size(c) > memory // 2:
                raise QueryMemoryError("An expression coefficient exceeds the working-memory budget")
            if c:
                values[v] = c
                used += size(v) + size(c) + 72
            if used > min(65536, memory // 4):
                from itertools import chain

                items = chain(values.items(), items)
                break
        else:
            data = tuple(sorted(values.items()))
            return LinExpr(owner, constant, items=lambda: iter(data), models=models)
    ctx = context(owners((owner,), models), memory_mb, disk_mb)
    try:
        table = ctx.raw_table("v INTEGER PRIMARY KEY,c TEXT")
        for v, c in items:
            if type(v) is not int or not 0 <= v < owner.shape[1]:
                raise SchemaError("Invalid variable ID in expression")
            add(ctx, table, v, c)
        from mipframe.linear.storage import finish_expression
        return finish_expression(owner, ctx, table, q(constant))
    except BaseException:
        ctx.close()
        raise


def linexpr(
    owner,
    T=None,
    *,
    vars=None,
    var="var",
    coef="coef",
    constant=0,
    memory_mb=128,
    disk_mb=None,
):
    if (T is None) == (vars is None):
        if T is None and vars is None:
            return materialize(
                owner, (), constant, memory_mb=memory_mb, disk_mb=disk_mb
            )
        raise ValueError("Specify T or vars, not both")
    source = vars if T is None else T
    if not isinstance(source, Frame):
        raise SchemaError("linexpr requires source model Frames, not draft handles")
    ctx = context(owners((owner,), source._models), memory_mb, disk_mb)
    try:
        table = ctx.raw_table("v INTEGER PRIMARY KEY,c TEXT")
        name = "var" if T is None else var
        if name not in source.schema or not ref_type(owner, "var").compatible(
            source.schema[name]
        ):
            raise SchemaError("Expression requires a variable Ref column")
        if T is None:
            if getattr(vars, "_entity_axis", None) != "var":
                raise SchemaError("vars requires a VarFrame")
            a = q(1 if coef == "coef" else coef)
            for record in source._raw((name,), ctx):
                add(ctx, table, record[name], a)
        else:
            if coef not in T.schema:
                raise SchemaError("Missing coefficient column")
            if T.schema[coef].kind not in ("number", "int"):
                raise SchemaError("Coefficients must have an exact numeric type")
            for record in T._raw((name, coef), ctx):
                v = record[name]
                if type(v) is not int or not 0 <= v < owner.shape[1]:
                    raise SchemaError("Variable ID outside model")
                add(ctx, table, v, record[coef])
        from mipframe.linear.storage import finish_expression
        return finish_expression(owner, ctx, table, q(constant))
    except BaseException:
        ctx.close()
        raise
