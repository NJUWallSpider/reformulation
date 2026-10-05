from fractions import Fraction
from numbers import Integral
from math import inf

def exact(x):
    if type(x) is int:return x
    if isinstance(x, bool) or isinstance(x, float):
        from mipframe.core.errors import ExactArithmeticError
        raise ExactArithmeticError('Expected an exact finite number')
    if isinstance(x, Integral):
        return int(x)
    x = Fraction(x)
    return x.numerator if x.denominator == 1 else x


def bound(x):
    return x if x in (-inf, inf) else exact(x)
