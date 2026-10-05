"""Individual variable view with lazy incident constraints."""

from __future__ import annotations

from mipframe.frame.entity import _text


from mipframe.entity import Entity


class Var(Entity):
    """
    MIPFRAME HELP BEGIN
    Original variable domain, bounds and objective coefficient.

    Full contract: mf.help("api.Var")
    MIPFRAME HELP END
    """
    columns = ("source_line", "var", "name", "kind", "lb", "ub", "obj", "type", "nnz")

    @property
    def source_line(self):
        """
        One-based source line, or None for constructed entities.

        MIPFRAME HELP BEGIN
        Read the original source_line field or entity relation.

        Full contract: mf.help("api.Var.source_line")
        MIPFRAME HELP END
        """
        return self._owner.model.variables[self.id][6] or None

    def __init__(self, owner, ident: int):
        self._owner, self._id = owner, ident
        owner.model

    @property
    def id(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Read the original id field or entity relation.

        Full contract: mf.help("api.Var.id")
        MIPFRAME HELP END
        """
        self._owner.model
        return self._id

    @property
    def name(self) -> str:
        """
        MIPFRAME HELP BEGIN
        Read the original name field or entity relation.

        Full contract: mf.help("api.Var.name")
        MIPFRAME HELP END
        """
        return self._owner.model.variables[self.id][1]

    @property
    def var(self):
        self._owner.model
        return self

    @property
    def kind(self):
        """
        MIPFRAME HELP BEGIN
        Canonical C/I domain; binary is derived from effective integer bounds.

        Full contract: mf.help("api.Var.kind")
        MIPFRAME HELP END
        """
        return self._owner.model.variables[self.id][2]

    @property
    def is_binary(self):
        """
        MIPFRAME HELP BEGIN
        Whether the nonempty integer domain is contained in {0,1}, including fixed 0/1.

        Full contract: mf.help("api.Var.is_binary")
        MIPFRAME HELP END
        """
        from mipframe.frame.sources import binary

        return binary(self.kind, self.lb, self.ub)

    @property
    def type(self):
        """
        MIPFRAME HELP BEGIN
        Read the original type field or entity relation.

        Full contract: mf.help("api.Var.type")
        MIPFRAME HELP END
        """
        return "B" if self.is_binary else self.kind

    @property
    def terms(self):
        """
        MIPFRAME HELP BEGIN
        Original nonzero terms, not a dense row or column.

        Full contract: mf.help("api.Var.terms")
        MIPFRAME HELP END
        """
        from mipframe.frame.terms import TermFrame

        return TermFrame(self._owner, vars=self._owner.vars._select((self.id,)))

    @property
    def lb(self):
        """
        MIPFRAME HELP BEGIN
        Read the original lb field or entity relation.

        Full contract: mf.help("api.Var.lb")
        MIPFRAME HELP END
        """
        from math import inf

        m = self._owner.model
        value = m.variables[self.id][3]
        return -inf if value is None else m.num(value)

    @property
    def ub(self):
        """
        MIPFRAME HELP BEGIN
        Read the original ub field or entity relation.

        Full contract: mf.help("api.Var.ub")
        MIPFRAME HELP END
        """
        from math import inf

        m = self._owner.model
        value = m.variables[self.id][4]
        return inf if value is None else m.num(value)

    @property
    def obj(self):
        """
        MIPFRAME HELP BEGIN
        Read the original obj field or entity relation.

        Full contract: mf.help("api.Var.obj")
        MIPFRAME HELP END
        """
        m = self._owner.model
        return m.num(m.variables[self.id][5])

    @property
    def nnz(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Read the original nnz field or entity relation.

        Full contract: mf.help("api.Var.nnz")
        MIPFRAME HELP END
        """
        m = self._owner.model
        return m.degree("var",self.id)

    @property
    def rows(self):
        """
        MIPFRAME HELP BEGIN
        Read the original rows field or entity relation.

        Full contract: mf.help("api.Var.rows")
        MIPFRAME HELP END
        """
        return self._owner.vars._select((self.id,)).rows

    def __repr__(self):
        import json

        return (
            f"Var({json.dumps(self.name, ensure_ascii=False)})\n"
            f"  type     : {self.type}\n  bounds   : [{_text(self.lb)}, {_text(self.ub)}]\n"
            f"  obj      : {_text(self.obj)}\n  rows     : {self.nnz:,}"
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))

    def __hash__(self):
        self._owner.model
        return hash((self._owner.uid, self._owner.version, 'var', self.id))

    @property
    def expr(self):
        """
        MIPFRAME HELP BEGIN
        Original affine expression; row expressions contain no bound and constant zero.

        Full contract: mf.help("api.Var.expr")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import variable
        return variable(self)

    def __add__(self, other):
        return self.expr + other

    __radd__ = __add__

    def __sub__(self, other):
        return self.expr - other

    def __rsub__(self, other):
        return self.expr.__rsub__(other)

    def __mul__(self, other):
        return self.expr * other

    __rmul__ = __mul__

    def __pow__(self, exponent):
        return self.expr ** exponent

    def __neg__(self):
        return -self.expr

    def __le__(self, other):
        return self.expr <= other

    def __ge__(self, other):
        return self.expr >= other

    def eq(self, other):
        """
        MIPFRAME HELP BEGIN
        Construct a mathematical equality Claim; entity == retains identity semantics.

        Full contract: mf.help("api.Var.eq")
        MIPFRAME HELP END
        """
        return self.expr.eq(other)

    @property
    def lb_claim(self):
        """
        MIPFRAME HELP BEGIN
        Original finite lower side with snapshot/entity provenance.

        Full contract: mf.help("api.Var.lb_claim")
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'lb')

    @property
    def ub_claim(self):
        """
        MIPFRAME HELP BEGIN
        Original finite upper side with snapshot/entity provenance.

        Full contract: mf.help("api.Var.ub_claim")
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'ub')
