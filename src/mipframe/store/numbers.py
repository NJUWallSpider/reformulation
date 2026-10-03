"""Exact finite decimals. Fractions are created only for queried/active rows."""
from decimal import Decimal
from fractions import Fraction

class MIPFrameError(ValueError):
    """
    MIPFRAME HELP BEGIN
    MIPFrameError [api.MIPFrameError]

    Diagnostic exception; inspect original message and any attached complete Report.

    MIPFrameError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.errors
    MIPFRAME HELP END
    """
    def __init__(self, *args):
        super().__init__(*args)
        from mipframe._help.diagnostics import annotate
        annotate(self)

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
