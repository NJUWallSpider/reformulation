"""Exact finite decimals. Fractions are created only for queried/active rows."""
from fractions import Fraction
import operator
from math import isinf


def exact_binary(op, a, b):
    """Arithmetic on exact finite scalars and signed infinite bounds.

    Handle infinities before Python can coerce a large integer to float or
    create NaN. Every finite quotient remains a Fraction.
    """
    ai = isinstance(a, float) and isinf(a)
    bi = isinstance(b, float) and isinf(b)
    if not ai and not bi:
        return (Fraction(a) / Fraction(b) if op == 'div' else
                {'add': operator.add, 'sub': operator.sub, 'mul': operator.mul}[op](a, b))
    if op == 'sub':
        return exact_binary('add', a, -b)
    if op == 'add' and not (ai and bi and (a > 0) != (b > 0)):
        return a if ai else b
    if op == 'mul' and a != 0 and b != 0:
        return float('inf') if (a > 0) == (b > 0) else -float('inf')
    if op == 'div':
        if b == 0:
            raise ZeroDivisionError('division by zero')
        if not ai:
            return Fraction(0)
        if not bi:
            return float('inf') if (a > 0) == (b > 0) else -float('inf')
    from mipframe.linear.scalar import ExactArithmeticError
    raise ExactArithmeticError(f'Indeterminate exact arithmetic: {a} {op} {b}')

from mipframe.core.errors import MIPFrameError

def compare_numeric_text(a, b):
    """Compare exact SQLite scalars without constructing temporary Fractions.

    Stored finite scalars are integers or canonical rational strings. Decimal
    lookup values still use Fraction's parser; infinities represent open bounds.
    """
    if a == b:
        return 0
    if a == '-inf' or b == 'inf':
        return -1
    if a == 'inf' or b == '-inf':
        return 1

    def parts(text):
        numerator, slash, denominator = text.partition('/')
        try:
            n, d = int(numerator), int(denominator) if slash else 1
            if d <= 0:
                raise ValueError
            return n, d
        except ValueError:
            value = Fraction(text)
            return value.numerator, value.denominator

    an, ad = parts(a)
    bn, bd = parts(b)
    left, right = an * bd, bn * ad
    return (left > right) - (left < right)

def fraction(value):
    return value if isinstance(value, Fraction) else Fraction(value)

def decimal(value):
    value = fraction(value)
    d = value.denominator
    a = b = 0
    while d % 2 == 0:
        d //= 2
        a += 1
    while d % 5 == 0:
        d //= 5
        b += 1
    if d != 1:
        from mipframe._help.diagnostics import hint
        raise hint(MIPFrameError('Cannot export non-terminating rational as exact LP decimal'), 'limitation.export')
    k = max(a, b)
    n = value.numerator * 2**(k-a) * 5**(k-b)
    if k == 0:
        return str(n)
    s = str(abs(n)).zfill(k+1)
    return ('-' if n < 0 else '') + s[:-k] + '.' + s[-k:]
