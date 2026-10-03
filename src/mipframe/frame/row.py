"""Individual constraints with bounded displays and lazy sparse term access."""

from __future__ import annotations

from collections import Counter
from heapq import nsmallest
from itertools import islice
import json
from math import inf
from typing import TYPE_CHECKING

from mipframe.frame.entity import TermFrame
from mipframe.frame.entity import _Frame
from mipframe.frame.entity import _sense
from mipframe.frame.entity import _text

if TYPE_CHECKING:
    from mipframe.model import Model


class Formula(str):
    """A string that displays directly, including line breaks, in notebooks."""

    def __repr__(self):
        return str(self)

    def _repr_pretty_(self, printer, cycle):
        printer.text(str(self))


from mipframe.entity import Entity


class Row(Entity):
    """
    MIPFRAME HELP BEGIN
    Row [api.Row]

    Original row expression and two independent bounds.

    Row [type]

    When to use: Original row expression and two independent bounds.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.frame, api.Row.expr
    MIPFRAME HELP END
    """
    _fields = (
        "sense",
        "lhs",
        "rhs",
        "coef_min",
        "coef_max",
        "unique_coef",
        "var_types",
    )
    columns = ("row", "name", "lb", "ub", "nnz", "relation")

    def __init__(self, owner: Model, ident: int):
        self._owner = owner
        owner.model
        self._id = ident
        self._cached_metadata = None
        self._stats = None

    @property
    def _metadata(self):
        self._owner.model
        if self._cached_metadata is None:
            self._cached_metadata = self._owner.model.rows[self._id]
        return self._cached_metadata

    @property
    def expr(self):
        """
        MIPFRAME HELP BEGIN
        Row.expr [api.Row.expr]

        Original affine expression; row expressions contain no bound and constant zero.

        Row.expr [property]

        When to use: Original affine expression; row expressions contain no bound and constant zero.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import row_expr
        return row_expr(self)

    @property
    def lb_claim(self):
        """
        MIPFRAME HELP BEGIN
        Row.lb_claim [api.Row.lb_claim]

        Original finite lower side with snapshot/entity provenance.

        Row.lb_claim [property]

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
        Row.ub_claim [api.Row.ub_claim]

        Original finite upper side with snapshot/entity provenance.

        Row.ub_claim [property]

        When to use: Original finite upper side with snapshot/entity provenance.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'ub')

    @property
    def eq_claim(self):
        """
        MIPFRAME HELP BEGIN
        Row.eq_claim [api.Row.eq_claim]

        Original equality side; both finite row bounds must coincide.

        Row.eq_claim [property]

        When to use: Original equality side; both finite row bounds must coincide.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'eq')

    @property
    def id(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Row.id [api.Row.id]

        Read the original id field or entity relation.

        Row.id [property]

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
        Row.name [api.Row.name]

        Read the original name field or entity relation.

        Row.name [property]

        When to use: Read the original name field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self._metadata[1]

    @property
    def row(self):
        self._owner.model
        return self

    @property
    def lb(self):
        """
        MIPFRAME HELP BEGIN
        Row.lb [api.Row.lb]

        Read the original lb field or entity relation.

        Row.lb [property]

        When to use: Read the original lb field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self.lhs

    @property
    def ub(self):
        """
        MIPFRAME HELP BEGIN
        Row.ub [api.Row.ub]

        Read the original ub field or entity relation.

        Row.ub [property]

        When to use: Read the original ub field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self.rhs

    @property
    def relation(self):
        """
        MIPFRAME HELP BEGIN
        Row.relation [api.Row.relation]

        Read the original relation field or entity relation.

        Row.relation [property]

        When to use: Read the original relation field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self.sense

    @property
    def lhs(self):
        """
        MIPFRAME HELP BEGIN
        Row.lhs [api.Row.lhs]

        Read the original lhs field or entity relation.

        Row.lhs [property]

        When to use: Read the original lhs field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        m = self._owner.model
        return -inf if self._metadata[2] is None else m.num(self._metadata[2])

    @property
    def rhs(self):
        """
        MIPFRAME HELP BEGIN
        Row.rhs [api.Row.rhs]

        Read the original rhs field or entity relation.

        Row.rhs [property]

        When to use: Read the original rhs field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        m = self._owner.model
        return inf if self._metadata[3] is None else m.num(self._metadata[3])

    @property
    def sense(self) -> str:
        self._owner.model
        return _sense(self._metadata[2], self._metadata[3])

    @property
    def nnz(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Row.nnz [api.Row.nnz]

        Read the original nnz field or entity relation.

        Row.nnz [property]

        When to use: Read the original nnz field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        m = self._owner.model
        return int(m.row_ptr[self.id + 1] - m.row_ptr[self.id])

    @property
    def terms(self) -> TermFrame:
        """
        MIPFRAME HELP BEGIN
        Row.terms [api.Row.terms]

        Original nonzero terms, not a dense row or column.

        Row.terms [property]

        When to use: Original nonzero terms, not a dense row or column.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return TermFrame(self._owner, self.id)

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        Row.vars [api.Row.vars]

        Read the original vars field or entity relation.

        Row.vars [property]

        When to use: Read the original vars field or entity relation.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        return self._owner.rows._select((self.id,)).neighbors()

    def signature(self, signature="algebratic", *, depth=None, **options):
        if signature in ("structural", "exact_structural", "exact_strctural"):
            from mipframe.ir.signatures import StructuralSignature
            from mipframe.ir.signatures import signature_result

            result = signature_result(self._owner, signature, depth, options)
            return StructuralSignature(result, "row", self.id)
        if depth is not None or options:
            raise ValueError(
                "depth and refinement options require signature='structural'"
            )
        from mipframe.ir.algebraic import signature_method

        return signature_method(signature)(self._owner, self.id)

    def _statistics(self):
        m = self._owner.model
        if self._stats is None:
            start, stop = int(m.row_ptr[self.id]), int(m.row_ptr[self.id + 1])
            seen = set()
            lower = upper = None
            types = Counter()
            for ptr in range(start, stop):
                ident = int(m.coeff_id[ptr])
                if ident not in seen:
                    seen.add(ident)
                    value = m.num(ident)
                    lower = value if lower is None else min(lower, value)
                    upper = value if upper is None else max(upper, value)
            # Small SQL batches avoid constructing a model-sized variable map.
            for begin in range(start, stop, 900):
                ids = [int(v) for v in m.col_idx[begin : min(begin + 900, stop)]]
                marks = ",".join("?" for _ in ids)
                from mipframe.frame.sources import binary

                for kind, lo, hi in m.db.execute(
                    f"SELECT kind,lo,hi FROM variables WHERE id IN ({marks})", ids
                ):
                    bound_lo = -inf if lo is None else m.num(lo)
                    bound_hi = inf if hi is None else m.num(hi)
                    types["B" if binary(kind, bound_lo, bound_hi) else kind] += 1
            self._stats = (lower, upper, len(seen), dict(types))
        return self._stats

    @property
    def coef_min(self):
        return self._statistics()[0]

    @property
    def coef_max(self):
        return self._statistics()[1]

    @property
    def unique_coef(self) -> int:
        return self._statistics()[2]

    @property
    def var_types(self) -> dict[str, int]:
        return dict(self._statistics()[3])

    def __repr__(self):
        lower, upper, unique, types = self._statistics()
        bounds = []
        if self.sense in (">=", "range", "free"):
            bounds.append(("lhs", _text(self.lhs)))
        if self.sense in ("<=", "=", "range", "free"):
            bounds.append(("rhs", _text(self.rhs)))
        fields = [
            ("sense", self.sense),
            *bounds,
            ("nonzeros", f"{self.nnz:,}"),
            (
                "var types",
                " | ".join(
                    f"{kind} {types[kind]:,}"
                    for kind in ("B", "I", "C")
                    if types.get(kind)
                )
                or "—",
            ),
            ("coef range", f"[{_text(lower)}, {_text(upper)}]"),
            ("unique coef", f"{unique:,}"),
        ]
        return f"Row({json.dumps(self.name, ensure_ascii=False)})\n" + "\n".join(
            f"  {label:<12}: {value}" for label, value in fields
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))

    def _named_terms(self):
        m = self._owner.model
        start, stop = int(m.row_ptr[self.id]), int(m.row_ptr[self.id + 1])
        for begin in range(start, stop, 900):
            ids = [int(v) for v in m.col_idx[begin : min(begin + 900, stop)]]
            marks = ",".join("?" for _ in ids)
            names = dict(
                m.db.execute(
                    f"SELECT id, name FROM variables WHERE id IN ({marks})", ids
                )
            )
            for ptr, ident in enumerate(ids, begin):
                yield names[ident], ident, ptr

    def formula(self, *, order: str = "abs_coef", max_terms: int = 16) -> Formula:
        """
        Return a bounded exact formula; absolute-coefficient ordering is default.

                        abs_coef and name scan the row with O(max_terms) selection memory; index
                        reads only the prefix. Equal magnitudes are ordered by variable ID.

        MIPFRAME HELP BEGIN
        Row.formula [api.Row.formula]

        Bounded exact Formula display with explicitly limited terms.

        Row.formula(*, order: 'str' = 'abs_coef', max_terms: 'int' = 16) -> 'Formula'

        When to use: Bounded exact Formula display with explicitly limited terms.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Absent finite source bounds raise MissingBoundError; closed models reject live access.

        See also: concept.frame, concept.proofs
        MIPFRAME HELP END
        """
        m = self._owner.model
        max_terms = _Frame._size(max_terms)
        if order not in ("abs_coef", "index", "name"):
            raise ValueError("order must be 'abs_coef', 'index', or 'name'")
        count = min(max_terms, self.nnz)
        if order == "name":
            chosen = nsmallest(
                count, self._named_terms(), key=lambda term: (term[0], term[1])
            )
            terms = [
                (ident, m.num(int(m.coeff_id[ptr])), name)
                for name, ident, ptr in chosen
            ]
        else:
            source = m.iter_terms(self.id)
            chosen = (
                nsmallest(count, source, key=lambda term: (-abs(term[1]), term[0]))
                if order == "abs_coef"
                else list(islice(source, count))
            )
            terms = [(ident, coef, m.variables[ident][1]) for ident, coef in chosen]
        parts = []
        for _, coef, name in terms:
            # Quote names with formula delimiters so the preview stays readable.
            if any(c.isspace() or c in '+-*/<=>"' for c in name):
                name = json.dumps(name, ensure_ascii=False)
            term = name if abs(coef) == 1 else f"{_text(abs(coef))} {name}"
            if parts:
                sign = " - " if coef < 0 else " + "
            else:
                sign = "-" if coef < 0 else ""
            parts.append(sign + term)
        expression = "".join(parts)
        truncated = count < self.nnz
        if truncated:
            expression = expression + " + ..." if expression else "..."
        elif not expression:
            expression = "0"
        if self.sense in ("range", "free"):
            expression = f"{_text(self.lhs)} <= {expression} <= {_text(self.rhs)}"
        elif self.sense == ">=":
            expression += f" >= {_text(self.lhs)}"
        else:
            expression += f" {self.sense} {_text(self.rhs)}"
        if truncated:
            expression += f"\n[{count:,} of {self.nnz:,} terms shown]"
        return Formula(expression)
