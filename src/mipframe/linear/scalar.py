"""Exact literals for algebra and certificates, never implicit binary floats."""

from decimal import Decimal
from fractions import Fraction
from mipframe.store.numbers import MIPFrameError


class ExactArithmeticError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.ExactArithmeticError")
    MIPFRAME HELP END
    """
    pass


class NonlinearExpressionError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.NonlinearExpressionError")
    MIPFRAME HELP END
    """
    pass


def q(value):
    """
    MIPFRAME HELP BEGIN
    Parse an exact finite rational scalar.

    Full contract: mf.help("api.q")
    MIPFRAME HELP END
    """
    if isinstance(value, bool) or not isinstance(value, (int, Fraction, Decimal, str)):
        raise ExactArithmeticError(
            'Use an int, Fraction, Decimal or mf.q("0.1"); Python floats are not exact literals'
        )
    try:
        return Fraction(value)
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        raise ExactArithmeticError("Expected a finite exact rational") from exc
