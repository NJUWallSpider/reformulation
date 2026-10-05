"""Exact coefficient/type/bound-class signatures with bounded metadata batches."""

from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from fractions import Fraction
from functools import lru_cache
from itertools import islice
import json

from mipframe.ir.algebraic import CoefficientPattern
from mipframe.ir.algebraic import _canonical_form
from mipframe.frame.entity import _text
from mipframe.options import options


def bound_class(lower, upper):
    """Classify finite Fraction/None bounds; fixed and boxed take precedence."""
    if lower is None:
        if upper is None:
            return "free"
        return "nonpositive" if upper <= 0 else "upper_bounded"
    if upper is None:
        return "nonnegative" if lower >= 0 else "lower_bounded"
    return "fixed" if lower == upper else "boxed"


class TypedPattern(tuple):
    """Sorted ((coefficient, variable type, bound class), multiplicity) pairs."""

    def __repr__(self):
        limit = options.display.max_cols
        parts = []
        for (coef, kind, bounds), count in islice(reversed(self), limit):
            coefficient = ("+" if coef > 0 else "") + _text(coef)
            token = f"({coefficient}, {kind}, {bounds})"
            if count != 1:
                token += f" × {count:,}"
            parts.append(token)
        if len(self) > limit:
            parts.append(f"... ({len(self) - limit:,} more tokens)")
        return "[" + ", ".join(parts) + "]"


@dataclass(frozen=True)
class _TypedKey:
    """
    MIPFRAME HELP BEGIN
    _TypedKey [api._TypedKey]

    Single-row pattern with variable-domain observations.

    _TypedKey [type]

    When to use: Single-row pattern with variable-domain observations.

    Does not establish: A signature, name or draft identity does not establish a model equivalence.

    See also: concept.frame, concept.identity
    MIPFRAME HELP END
    """
    sense: str
    nnz: int
    edge_multiset: TypedPattern
    lhs: Fraction | None
    rhs: Fraction | None
    numeric_kind: str = "exact"
    scale: Fraction = field(default=Fraction(1), compare=False, hash=False)

    @property
    def pattern(self):
        return self.edge_multiset

    @property
    def coeff_multiset(self):
        counts = Counter()
        for (coef, _, _), count in self.edge_multiset:
            counts[coef] += count
        return CoefficientPattern(sorted(counts.items()))

    def key(self):
        return json.dumps([
            "typed", self.numeric_kind, self.sense, self.nnz,
            [[str(c), kind, bounds, count] for (c, kind, bounds), count in self.edge_multiset],
            None if self.lhs is None else str(self.lhs),
            None if self.rhs is None else str(self.rhs),
        ], separators=(",", ":"))

    @classmethod
    def from_key(cls, key):
        signature, kind, sense, nnz, pairs, lo, hi = json.loads(key)
        if signature != "typed":
            raise ValueError("Not a typed signature")
        return cls(sense, nnz,
                   TypedPattern(((Fraction(c), typ, bounds), n) for c, typ, bounds, n in pairs),
                   None if lo is None else Fraction(lo),
                   None if hi is None else Fraction(hi), kind)


def typed_signature(owner, row_id):
    m = owner.model
    @lru_cache(maxsize=1024)
    def classify(ident):
        v=m.variables[ident]
        return m.variable_type(v),bound_class(m.num(v[3]),m.num(v[4]))

    values=Counter();nnz=0
    for ident,coefficient in m.iter_terms(row_id):
        kind,bounds=classify(ident)
        values[coefficient,kind,bounds]+=1
        nnz+=1
    sense, pairs, lo, hi, scale = _canonical_form(m, row_id, values)
    return _TypedKey(sense, nnz, TypedPattern(pairs), lo, hi, scale=scale)
