"""Exact finite decimals. Fractions are created only for queried/active rows."""
from decimal import Decimal
from fractions import Fraction

class LPIRError(ValueError):
    pass

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
        raise LPIRError('Cannot export non-terminating rational as exact LP decimal')
    k = max(a, b)
    n = value.numerator * 2**(k-a) * 5**(k-b)
    if k == 0:
        return str(n)
    s = str(abs(n)).zfill(k+1)
    return ('-' if n < 0 else '') + s[:-k] + '.' + s[-k:]

def encoding(value):
    t = Decimal(decimal(value)).as_tuple()
    m = int(''.join(map(str, t.digits))) * (-1 if t.sign else 1)
    e = t.exponent
    if not m:
        return '0', 0
    while m % 10 == 0:
        m //= 10
        e += 1
    return str(m), e

def from_encoding(m, e):
    return Fraction(int(m)*10**e) if e >= 0 else Fraction(int(m), 10**(-e))
