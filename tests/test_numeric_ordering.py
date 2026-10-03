"""SQLite numeric ordering preserves exact equality, precision and infinities."""

from fractions import Fraction
from itertools import product
import random
import sqlite3

from mipframe.store.numbers import compare_numeric_text


def test_numeric_collation_matches_exact_order():
    rng = random.Random(42)
    values = [
        '-inf', 'inf', '0', '-0', '1', '2/2', '0.1', '1/10',
        '-3/7', '-6/14', '1e-100', str(10**400),
        f'{10**100 + 1}/{10**100}', f'{10**100 + 2}/{10**100}',
    ]
    values += [str(Fraction(rng.randrange(-10**30, 10**30), rng.randrange(1, 10**20)))
               for _ in range(40)]

    def number(text):
        return float(text) if text in ('inf', '-inf') else Fraction(text)

    for a, b in product(values, repeat=2):
        left, right = number(a), number(b)
        assert compare_numeric_text(a, b) == (left > right) - (left < right)

    with sqlite3.connect(':memory:') as db:
        db.create_collation('exact', compare_numeric_text)
        db.execute('CREATE TABLE numbers(value TEXT COLLATE exact)')
        db.executemany('INSERT INTO numbers VALUES(?)', ((v,) for v in values))
        actual = [number(v) for v, in db.execute('SELECT value FROM numbers ORDER BY value')]
        assert actual == sorted(map(number, values))
        distinct = db.execute('SELECT count(DISTINCT value) FROM numbers').fetchone()[0]
        assert distinct == len(set(map(number, values)))
