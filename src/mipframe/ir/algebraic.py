"""Exact, variable-identity-independent row signatures with counted coefficients."""

from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from fractions import Fraction
from itertools import islice
import json
from math import gcd
from math import lcm

from mipframe.frame.entity import _sense
from mipframe.frame.entity import _text
from mipframe.options import options


class CoefficientPattern(tuple):
    """Sorted (exact coefficient, multiplicity) pairs, never expanded for display."""

    def __repr__(self):
        limit = options.display.max_cols
        shown = islice(reversed(self), limit)
        parts = []
        for coef, count in shown:
            text = ("+" if coef > 0 else "") + _text(coef)
            if count != 1:
                text += f" × {count:,}"
            parts.append(text)
        if len(self) > limit:
            parts.append(f"... ({len(self) - limit:,} more coefficients)")
        return "[" + ", ".join(parts) + "]"


@dataclass(frozen=True)
class _AlgebraicKey:
    """
    MIPFRAME HELP BEGIN
    _AlgebraicKey [api._AlgebraicKey]

    Canonical exact single-row coefficient pattern, distinct from the original row formula.

    _AlgebraicKey [type]

    When to use: Canonical exact single-row coefficient pattern, distinct from the original row formula.

    Does not establish: A signature, name or draft identity does not establish a model equivalence.

    See also: concept.frame, concept.identity
    MIPFRAME HELP END
    """
    sense: str
    nnz: int
    coeff_multiset: CoefficientPattern
    lhs: Fraction | None
    rhs: Fraction | None
    numeric_kind: str = "exact"
    # original coefficient = scale * canonical coefficient; excluded from identity.
    scale: Fraction = field(default=Fraction(1), compare=False, hash=False)

    @property
    def pattern(self):
        return self.coeff_multiset

    def key(self):
        return json.dumps([
            self.numeric_kind, self.sense, self.nnz,
            [[str(c), n] for c, n in self.coeff_multiset],
            None if self.lhs is None else str(self.lhs),
            None if self.rhs is None else str(self.rhs),
        ], separators=(",", ":"))

    @classmethod
    def from_key(cls, key):
        kind, sense, nnz, pairs, lo, hi = json.loads(key)
        return cls(sense, nnz, CoefficientPattern((Fraction(c), n) for c, n in pairs),
                   None if lo is None else Fraction(lo),
                   None if hi is None else Fraction(hi), kind)


def canonical_ratio(coefficients):
    """
    Positive rational gcd; reject floats rather than silently approximate them.

    MIPFRAME HELP BEGIN
    Compute a canonical exact coefficient ratio for signature observations.

    Full contract: mf.help("api.canonical_ratio")
    MIPFRAME HELP END
    """
    scale = Fraction(0)
    for value in coefficients:
        if isinstance(value, float):
            raise TypeError("canonical_ratio requires exact numbers, not float")
        value = abs(Fraction(value))
        if value:
            scale = Fraction(gcd(scale.numerator, value.numerator),
                             lcm(scale.denominator, value.denominator))
    return scale or Fraction(1)


def _canonical_form(m, row_id, values):
    """Normalize counted edge tokens, retaining metadata through sign changes."""
    scale = canonical_ratio(token[0] for token in values)
    _, _, lo_id, hi_id, _ = m.rows[row_id]
    lo, hi = m.num(lo_id), m.num(hi_id)
    sense = _sense(lo, hi)
    if sense == ">=":
        scale = -scale
        lo, hi = hi, lo
        sense = "<="
    lo = None if lo is None else lo / scale
    hi = None if hi is None else hi / scale
    pairs = tuple(sorted(((token[0] / scale, *token[1:]), n) for token, n in values.items()))
    if sense == "=":
        negative = tuple(sorted(((-token[0], *token[1:]), n) for token, n in pairs))
        # RHS breaks a sign tie for symmetric coefficient histograms.
        if (negative, -hi) < (pairs, hi):
            pairs, lo, hi, scale = negative, -hi, -lo, -scale
    return sense, pairs, lo, hi, scale


def algebraic_signature(owner, row_id):
    m = owner.model
    start, stop = int(m.row_ptr[row_id]), int(m.row_ptr[row_id + 1])
    # Count canonical number IDs before converting to fractions: O(unique coefficients).
    counts = Counter(int(n) for n in m.coeff_id[start:stop])
    values = Counter()
    for ident, count in counts.items():
        values[(m.num(ident),)] += count
    sense, pairs, lo, hi, scale = _canonical_form(m, row_id, values)
    pattern = CoefficientPattern((token[0], n) for token, n in pairs)
    return _AlgebraicKey(sense, stop - start, pattern, lo, hi,
                              scale=scale)


def _typed_signature(owner, row_id):
    from mipframe.ir.typed import typed_signature
    return typed_signature(owner, row_id)


def _decode_typed(key):
    from mipframe.ir.typed import _TypedKey
    return _TypedKey.from_key(key)


# Dispatch point for future signature kinds; retain the spelling requested by the API.
_SIGNATURES = {
    "algebratic": (algebraic_signature, _AlgebraicKey.from_key),
    "algebraic": (algebraic_signature, _AlgebraicKey.from_key),
    "typed": (_typed_signature, _decode_typed),
}


def signature_method(name):
    if not isinstance(name, str) or name not in _SIGNATURES:
        raise ValueError("signature must be 'algebratic' (alias: 'algebraic') or 'typed'")
    return _SIGNATURES[name][0]


def signature_decoder(name):
    signature_method(name)
    return _SIGNATURES[name][1]
