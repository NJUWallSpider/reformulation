"""A proposition is not evidence; model sides carry independently checked origins."""

from mipframe._help.diagnostics import hint as _help_hint

from mipframe.linear.expr import row_expr, variable, LinExpr
from mipframe.store.numbers import MIPFrameError


class MissingBoundError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.MissingBoundError")
    MIPFRAME HELP END
    """
    pass


class Claim:
    """
    MIPFRAME HELP BEGIN
    Construct an unproved affine proposition with <=, >=, LinExpr.le/ge or .eq().

    Full contract: mf.help("api.Claim")
    MIPFRAME HELP END
    """
    def __init__(self, expr, relation, *, _source=None):
        if not isinstance(expr, LinExpr) or relation not in ("<=", ">=", "=="):
            raise TypeError("Claim requires a LinExpr and <=, >= or ==")
        self._expr, self._relation, self._source = expr, relation, _source

    def __setattr__(self, n, v):
        if not n.startswith("_"):
            raise AttributeError("Claim is immutable")
        object.__setattr__(self, n, v)

    @property
    def expr(self):
        self._expr._guard()
        return self._expr

    @property
    def relation(self):
        self._expr._guard()
        return self._relation

    def __bool__(self):
        raise _help_hint(TypeError("Claim has no truth value; use m.check"), 'error.expressions')

    def __repr__(self):
        return f"Claim({self.expr.formula()} {self.relation} 0)"

    def _repr_pretty_(self, p, cycle):
        p.text(repr(self))

    def to_spec(self):
        return dict(
            version=1,
            expr=self.expr.to_spec(),
            relation=self.relation,
            source=None if self._source is None else dict(self._source),
        )

    @classmethod
    def from_spec(cls, spec, *, models):
        if spec.get("version") != 1:
            raise ValueError("Unsupported claim specification version")
        e = LinExpr.from_spec(spec["expr"], models=models)
        if spec.get("source") is not None:
            src = spec["source"]
            if src["axis"] not in ("row", "var"):
                raise ValueError("Invalid source axis")
            entity = (e._owner.rows if src["axis"] == "row" else e._owner.vars).loc[src["id"]]
            actual = source_claim(entity, src["side"])
            if actual.relation != spec["relation"] or actual.expr != e:
                raise ValueError("Claim source does not match the original model side")
            return actual
        return cls(e, spec["relation"])


def source_claim(entity, side):
    from mipframe.frame.row import Row

    axis = "row" if isinstance(entity, Row) else "var"
    if side not in ("lb", "ub", "eq") or side == "eq" and axis != "row":
        raise MissingBoundError("Invalid model side")
    bound = entity.lb if side == "lb" else entity.ub
    if isinstance(bound, float) or side == "eq" and entity.lb != entity.ub:
        raise MissingBoundError(
            "Claim requires a finite bound; eq requires a finite equality row"
        )
    e = row_expr(entity) if axis == "row" else variable(entity)
    # Preserve lazy CSR access when forming a source claim.
    result = LinExpr(
        e._owner,
        -bound,
        items=lambda: e._items(),
        models=e._models,
        ids=lambda: e._ids(),
    )
    return Claim(
        result,
        {"lb": ">=", "ub": "<=", "eq": "=="}[side],
        _source=dict(axis=axis, id=entity.id, side=side),
    )
