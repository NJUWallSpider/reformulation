"""Exact literals for algebra and certificates, never implicit binary floats."""

from decimal import Decimal
from fractions import Fraction
from mipframe.store.numbers import MIPFrameError


class ExactArithmeticError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    ExactArithmeticError [api.ExactArithmeticError]

    Diagnostic exception; inspect original message and any attached complete Report.

    ExactArithmeticError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.exact
    MIPFRAME HELP END
    """
    pass


class NonlinearExpressionError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    NonlinearExpressionError [api.NonlinearExpressionError]

    Diagnostic exception; inspect original message and any attached complete Report.

    NonlinearExpressionError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: error.expressions
    MIPFRAME HELP END
    """
    pass


def q(value):
    """
    MIPFRAME HELP BEGIN
    q [api.q]

    Parse an exact finite rational scalar.

    q(value)

    When to use: Parse an exact finite rational scalar.

    Inputs: int, Fraction, finite Decimal or decimal/scientific/fraction string.

    Returns: Fraction.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Failures: Float, bool, NaN and infinite algebraic scalars raise ExactArithmeticError.

    Illustrative example (requires caller-provided inputs):
    mf.q("43/39")

    See also: concept.exact
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
