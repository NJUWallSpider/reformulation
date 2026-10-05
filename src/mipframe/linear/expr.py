"""Immutable, canonical sparse affine expressions and bounded exact operations."""

from collections.abc import Mapping
from fractions import Fraction
from itertools import zip_longest
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType, Ref, SchemaError, ref_type, encode, decode
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
    if ident not in owner.vars.ids:
        raise SchemaError("Variable ID outside model")
    return ident




class LinExpr:
    """
    MIPFRAME HELP BEGIN
    Immutable sparse affine expression; use variable arithmetic, row.expr or m.linexpr.

    Full contract: mf.help("api.LinExpr")
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
            for v,c in self._store.items():
                self._guard()
                yield v,c
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
            for v in self._store.ids:
                self._guard()
                yield int(v)
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
        Read or construct affine expression constant.

        Full contract: mf.help("api.LinExpr.constant")
        MIPFRAME HELP END
        """
        self._guard()
        return self._constant

    @property
    def terms(self):
        """
        MIPFRAME HELP BEGIN
        Canonical sorted nonzero expression terms; an ordinary Frame, not original TermFrame.

        Full contract: mf.help("api.LinExpr.terms")
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

        frame = Frame(
            {"var": ref_type(self._owner, "var"), "coef": DType("number")},
            key=("var",),
            models=self._models,
            source=read,
        )
        frame._array_scan = read
        return frame

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        Read or construct affine expression vars.

        Full contract: mf.help("api.LinExpr.vars")
        MIPFRAME HELP END
        """
        return self.terms.refs("var")

    def _coerce(self, other):
        return coerce(self._owner, other)

    def __add__(self, other):
        from mipframe.edit.draft import DraftExpr, DraftVar
        if isinstance(other, (DraftExpr, DraftVar)): return other + self
        from mipframe.check.expressions import active_pool
        pool = active_pool(self._owner)
        if pool is not None and not isinstance(other,LinExpr) and not hasattr(other,"_owner"):
            return pool.shift(self,q(other))
        other = self._coerce(other)
        if pool is not None:
            return pool.add(self, other)
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
        from mipframe.check.expressions import active_pool
        pool=active_pool(self._owner)
        if pool is not None and not isinstance(other,LinExpr) and not hasattr(other,"_owner"):
            return pool.shift(self,-q(other))
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
        from mipframe.check.expressions import active_pool
        pool = active_pool(self._owner)
        if pool is not None:
            return pool.scale(self, scalar)
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
        Construct an unproved mathematical <= Claim.

        Full contract: mf.help("api.LinExpr.le")
        MIPFRAME HELP END
        """
        return self <= other

    def ge(self, other):
        """
        MIPFRAME HELP BEGIN
        Construct an unproved mathematical >= Claim.

        Full contract: mf.help("api.LinExpr.ge")
        MIPFRAME HELP END
        """
        return self >= other

    def eq(self, other):
        """
        MIPFRAME HELP BEGIN
        Construct an unproved mathematical == Claim.

        Full contract: mf.help("api.LinExpr.eq")
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
        Simultaneous one-level affine substitution; right sides are not recursively substituted.

        Full contract: mf.help("api.LinExpr.substitute")
        MIPFRAME HELP END
        """
        if not isinstance(mapping, Mapping):
            raise SchemaError("Substitution requires a Var/VarRef mapping")
        replacements = {}
        models = self._models
        for key,value in mapping.items():
            ident = var_id(self._owner,key)
            if ident in replacements:raise SchemaError('Duplicate substitution variable')
            expression = self._coerce(value)
            models = owners(models,expression._models)
            replacements[ident] = expression
        constant = self.constant
        for v,c in self._items():
            if v in replacements:constant += c*replacements[v].constant
        def terms():
            for v,c in self._items():
                if v in replacements:
                    for w,a in replacements[v]._items():yield w,c*a
                else:yield v,c
        return materialize(self._owner,terms(),constant,models=models,memory_mb=memory_mb,disk_mb=disk_mb)

    def evaluate(
        self, values, *, value="value", missing="error", memory_mb=128, disk_mb=None
    ):
        """
        MIPFRAME HELP BEGIN
        Evaluate an affine expression at exact values without checking feasibility.

        Full contract: mf.help("api.LinExpr.evaluate")
        MIPFRAME HELP END
        """
        if missing not in ("error", "zero"):
            raise ValueError("missing must be error or zero")
        from mipframe.data.exact_sparse import ExactSparse
        from mipframe.check.kernel import facts
        f = facts(self._owner)
        if isinstance(values,Frame):
            if ('var' not in values.schema or value not in values.schema
                or not ref_type(self._owner,'var').compatible(values.schema['var'])
                or values.schema[value].kind not in ('number','int')):
                raise SchemaError('Expected a snapshot-qualified Frame(var, value)')
            owners(self._models,values._models)
            records = ((r['var'],r[value]) for r in values._iterate(('var',value),memory_mb))
        elif isinstance(values,Mapping):
            records = ((var_id(self._owner,k),x) for k,x in values.items())
        else:raise SchemaError('Expected a Frame or a Var/VarRef mapping')
        def validated():
            for v,c in records:
                if type(v) is not int:raise SchemaError('Invalid variable ID')
                try:f.validate('var',v)
                except ValueError as error:raise SchemaError('Variable ID outside model') from error
                yield v,q(c)
        data = ExactSparse(memory_mb,disk_mb)
        try:
            try:data.build(validated(),duplicates=True)
            except ValueError as error:raise SchemaError(str(error)) from error
            inputs = iter(data.items());item = next(inputs,None);total = self.constant
            for v,c in self._items():
                while item is not None and item[0]<v:item=next(inputs,None)
                if item is None or item[0]!=v:
                    if missing=='error':raise SchemaError(f'Missing value for variable ID {v}')
                else:total += c*item[1];data.check(total)
            return total
        finally:data.close()

    def formula(self, *, max_terms=12):
        """
        MIPFRAME HELP BEGIN
        Bounded Formula display; does not verify the expression.

        Full contract: mf.help("api.LinExpr.formula")
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
            name = self._owner.vars.loc[v].name
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
        Export the versioned exact expression representation.

        Full contract: mf.help("api.LinExpr.to_spec")
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
    from mipframe.check.expressions import active_pool
    pool = active_pool(var._owner)
    if pool is not None:
        return pool.expression(pool.terms(((ident, 1),)))
    return LinExpr(
        var._owner,
        items=lambda: iter(((ident, Fraction(1)),)),
        ids=lambda: iter((ident,)),
    )


def row_expr(row):
    owner, ident = row._owner, row.id
    from mipframe.check.expressions import active_pool
    pool = active_pool(owner)
    if pool is not None:
        return pool.expression(pool.row(ident))

    def read():
        yield from owner.model.iter_terms(ident)

    def ids():
        yield from map(int,owner.model.support(ident))

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


def materialize(owner, items, constant=0, *, models=(), memory_mb=None, disk_mb=None):
    from mipframe.data.exact_sparse import ExactSparse
    from mipframe.options import options
    from mipframe.check.kernel import facts
    from mipframe.frame.executor import size
    from itertools import chain
    models = owners((owner,),models)
    for model in models:model.model
    f = facts(owner)
    data = ExactSparse(options.query.memory_mb if memory_mb is None else memory_mb,disk_mb)
    try:
        constant = q(constant);data.check(constant)
        def validated():
            for v,c in items:
                if type(v) is not int:raise SchemaError('Invalid variable ID in expression')
                try:f.validate('var',v)
                except ValueError as error:raise SchemaError('Invalid variable ID in expression') from error
                yield v,q(c)
        iterator = iter(validated());values = {};used = 0
        for v,c in iterator:
            data.check(c)
            old = values.pop(v,None)
            if old is not None:used-=size(old)+104;c+=old;data.check(c)
            if c:values[v]=c;used+=size(c)+104
            if used>min(65536,data.work.memory//4):break
        else:
            records = tuple(sorted(values.items()));data.close()
            return LinExpr(owner,constant,items=lambda:iter(records),models=models)
        data.build(chain(values.items(),iterator))
        from mipframe.store.lifecycle import register
        for model in models:register(model,data)
        return LinExpr(owner,constant,store=data,models=models)
    except BaseException:
        data.close()
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
    models = owners((owner,),source._models)
    name = 'var' if T is None else var
    if name not in source.schema or not ref_type(owner,'var').compatible(source.schema[name]):
        raise SchemaError('Expression requires a variable Ref column')
    from mipframe.check.expressions import active_pool
    pool=active_pool(owner)
    if T is None:
        if getattr(vars,'_entity_axis',None)!='var':raise SchemaError('vars requires a VarFrame')
        a = q(1 if coef=='coef' else coef)
        if pool is not None:return pool.frame(source,name,None,constant,scale=a)
        records = ((r[name],a) for r in source._iterate((name,),memory_mb))
    else:
        if coef not in T.schema:raise SchemaError('Missing coefficient column')
        if T.schema[coef].kind not in ('number','int'):raise SchemaError('Coefficients must have an exact numeric type')
        if pool is not None:return pool.frame(source,name,coef,constant)
        records = ((r[name],r[coef]) for r in source._iterate((name,coef),memory_mb))
    return materialize(owner,records,constant,models=models,memory_mb=memory_mb,disk_mb=disk_mb)
