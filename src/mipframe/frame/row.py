"""Individual constraints with bounded displays and lazy sparse term access."""

from __future__ import annotations

from collections import Counter
from heapq import nsmallest
from itertools import islice
import json
from math import inf
from typing import TYPE_CHECKING

from mipframe.frame.entity import TermFrame
from mipframe.frame.base import Frame
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
    Original row expression and two independent bounds.

    Full contract: mf.help("api.Row")
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
    columns = ("source_line", "row", "name", "lb", "ub", "nnz", "relation")

    @property
    def source_line(self):
        """
        One-based source line, or None for constructed entities.

        MIPFRAME HELP BEGIN
        Read the original source_line field or entity relation.

        Full contract: mf.help("api.Row.source_line")
        MIPFRAME HELP END
        """
        return self._owner.model.rows[self.id][4] or None

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
        Original affine expression; row expressions contain no bound and constant zero.

        Full contract: mf.help("api.Row.expr")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import row_expr
        return row_expr(self)

    @property
    def lb_claim(self):
        """
        MIPFRAME HELP BEGIN
        Original finite lower side with snapshot/entity provenance.

        Full contract: mf.help("api.Row.lb_claim")
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'lb')

    @property
    def ub_claim(self):
        """
        MIPFRAME HELP BEGIN
        Original finite upper side with snapshot/entity provenance.

        Full contract: mf.help("api.Row.ub_claim")
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'ub')

    @property
    def eq_claim(self):
        """
        MIPFRAME HELP BEGIN
        Original equality side; both finite row bounds must coincide.

        Full contract: mf.help("api.Row.eq_claim")
        MIPFRAME HELP END
        """
        from mipframe.linear.claim import source_claim
        return source_claim(self, 'eq')

    @property
    def id(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Read the original id field or entity relation.

        Full contract: mf.help("api.Row.id")
        MIPFRAME HELP END
        """
        self._owner.model
        return self._id

    @property
    def name(self) -> str:
        """
        MIPFRAME HELP BEGIN
        Read the original name field or entity relation.

        Full contract: mf.help("api.Row.name")
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
        Read the original lb field or entity relation.

        Full contract: mf.help("api.Row.lb")
        MIPFRAME HELP END
        """
        return self.lhs

    @property
    def ub(self):
        """
        MIPFRAME HELP BEGIN
        Read the original ub field or entity relation.

        Full contract: mf.help("api.Row.ub")
        MIPFRAME HELP END
        """
        return self.rhs

    @property
    def relation(self):
        """
        MIPFRAME HELP BEGIN
        Read the original relation field or entity relation.

        Full contract: mf.help("api.Row.relation")
        MIPFRAME HELP END
        """
        return self.sense

    @property
    def lhs(self):
        """
        MIPFRAME HELP BEGIN
        Read the original lhs field or entity relation.

        Full contract: mf.help("api.Row.lhs")
        MIPFRAME HELP END
        """
        m = self._owner.model
        return -inf if self._metadata[2] is None else m.num(self._metadata[2])

    @property
    def rhs(self):
        """
        MIPFRAME HELP BEGIN
        Read the original rhs field or entity relation.

        Full contract: mf.help("api.Row.rhs")
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
        Read the original nnz field or entity relation.

        Full contract: mf.help("api.Row.nnz")
        MIPFRAME HELP END
        """
        m = self._owner.model
        return m.degree("row",self.id)

    @property
    def terms(self) -> TermFrame:
        """
        MIPFRAME HELP BEGIN
        Original nonzero terms, not a dense row or column.

        Full contract: mf.help("api.Row.terms")
        MIPFRAME HELP END
        """
        return TermFrame(self._owner, self.id)

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        Read the original vars field or entity relation.

        Full contract: mf.help("api.Row.vars")
        MIPFRAME HELP END
        """
        return self._owner.rows._select((self.id,)).vars


    def _statistics(self):
        m = self._owner.model
        if self._stats is None:
            values=set();types=Counter()
            for var,coef in m.iter_terms(self.id):
                values.add(coef)
                types[m.variable_type(var)] += 1
            self._stats=(min(values,default=None),max(values,default=None),len(values),dict(types))
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
        for ident, coef in m.iter_terms(self.id):
            yield m.name('var',ident),ident,coef

    def formula(self, *, order: str = "abs_coef", max_terms: int = 16) -> Formula:
        """
        Return a bounded exact formula; absolute-coefficient ordering is default.

                        abs_coef and name scan the row with O(max_terms) selection memory; index
                        reads only the prefix. Equal magnitudes are ordered by variable ID.

        MIPFRAME HELP BEGIN
        Bounded exact Formula display with explicitly limited terms.

        Full contract: mf.help("api.Row.formula")
        MIPFRAME HELP END
        """
        m = self._owner.model
        max_terms = Frame._size(max_terms)
        if order not in ("abs_coef", "index", "name"):
            raise ValueError("order must be 'abs_coef', 'index', or 'name'")
        count = min(max_terms, self.nnz)
        if order == "name":
            chosen = nsmallest(
                count, self._named_terms(), key=lambda term: (term[0], term[1])
            )
            terms = [
                (ident, coef, name)
                for name, ident, coef in chosen
            ]
        else:
            source = m.iter_terms(self.id)
            chosen = (
                nsmallest(count, source, key=lambda term: (-abs(term[1]), term[0]))
                if order == "abs_coef"
                else list(islice(source, count))
            )
            terms = [(ident, coef, m.name('var',ident)) for ident, coef in chosen]
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
