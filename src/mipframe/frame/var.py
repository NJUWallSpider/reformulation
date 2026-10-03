"""Individual variable view with lazy incident constraints."""

from __future__ import annotations

from mipframe.frame.entity import _text


from mipframe.entity import Entity


class Var(Entity):
    """
    MIPFRAME HELP BEGIN
    Var [api.Var]

    Original variable domain, bounds and objective coefficient.

    Var [type]

    When to use: Original variable domain, bounds and objective coefficient.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.frame, api.Var.expr
    MIPFRAME HELP END
    """
    columns = ("var", "name", "kind", "lb", "ub", "obj", "type", "nnz")

    def __init__(self, owner, ident: int):
        self._owner, self._id = owner, ident
        owner.model

    @property
    def id(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Var.id [api.Var.id]

        Read the original id field or entity relation.

        Var.id [property]

        When to use: Read the original id field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        self._owner.model
        return self._id

    @property
    def name(self) -> str:
        """
        MIPFRAME HELP BEGIN
        Var.name [api.Var.name]

        Read the original name field or entity relation.

        Var.name [property]

        When to use: Read the original name field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
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
        Var.kind [api.Var.kind]

        Canonical C/I domain; binary is derived from effective integer bounds.

        Var.kind [property]

        When to use: Canonical C/I domain; binary is derived from effective integer bounds.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self._owner.model.variables[self.id][2]

    @property
    def is_binary(self):
        """
        MIPFRAME HELP BEGIN
        Var.is_binary [api.Var.is_binary]

        Whether the nonempty integer domain is contained in {0,1}, including fixed 0/1.

        Var.is_binary [property]

        When to use: Whether the nonempty integer domain is contained in {0,1}, including fixed 0/1.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.frame.sources import binary

        return binary(self.kind, self.lb, self.ub)

    @property
    def type(self):
        """
        MIPFRAME HELP BEGIN
        Var.type [api.Var.type]

        Read the original type field or entity relation.

        Var.type [property]

        When to use: Read the original type field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return "B" if self.is_binary else self.kind

    @property
    def terms(self):
        """
        MIPFRAME HELP BEGIN
        Var.terms [api.Var.terms]

        Original nonzero terms, not a dense row or column.

        Var.terms [property]

        When to use: Original nonzero terms, not a dense row or column.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.frame.terms import TermFrame

        return TermFrame(self._owner, vars=self._owner.vars._select((self.id,)))

    @property
    def lb(self):
        """
        MIPFRAME HELP BEGIN
        Var.lb [api.Var.lb]

        Read the original lb field or entity relation.

        Var.lb [property]

        When to use: Read the original lb field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
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
        Var.ub [api.Var.ub]

        Read the original ub field or entity relation.

        Var.ub [property]

        When to use: Read the original ub field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
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
        Var.obj [api.Var.obj]

        Read the original obj field or entity relation.

        Var.obj [property]

        When to use: Read the original obj field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        m = self._owner.model
        return m.num(m.variables[self.id][5])

    @property
    def nnz(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Var.nnz [api.Var.nnz]

        Read the original nnz field or entity relation.

        Var.nnz [property]

        When to use: Read the original nnz field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        m = self._owner.model
        return int(m.col_ptr[self.id + 1] - m.col_ptr[self.id])

    @property
    def rows(self):
        """
        MIPFRAME HELP BEGIN
        Var.rows [api.Var.rows]

        Read the original rows field or entity relation.

        Var.rows [property]

        When to use: Read the original rows field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self._owner.vars._select((self.id,)).neighbors()

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
        Var.expr [api.Var.expr]

        Original affine expression; row expressions contain no bound and constant zero.

        Var.expr [property]

        When to use: Original affine expression; row expressions contain no bound and constant zero.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
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
        Var.eq [api.Var.eq]

        Construct a mathematical equality Claim; entity == retains identity semantics.

        Var.eq(other)

        When to use: Construct a mathematical equality Claim; entity == retains identity semantics.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self.expr.eq(other)

    @property
    def lb_claim(self):
        """
        MIPFRAME HELP BEGIN
        Var.lb_claim [api.Var.lb_claim]

        Original finite lower side with snapshot/entity provenance.

        Var.lb_claim [property]

        When to use: Original finite lower side with snapshot/entity provenance.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'lb')

    @property
    def ub_claim(self):
        """
        MIPFRAME HELP BEGIN
        Var.ub_claim [api.Var.ub_claim]

        Original finite upper side with snapshot/entity provenance.

        Var.ub_claim [property]

        When to use: Original finite upper side with snapshot/entity provenance.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'ub')
