"""Exact LP grammar primitives shared by the streaming parser."""
from dataclasses import dataclass
from fractions import Fraction as F
from mipframe.store.numbers import MIPFrameError


@dataclass
class Token:
    text: str
    line: int
    col: int
    kind: str

def fail(token, message):
    raise MIPFrameError(f'line {token.line}, column {token.col}: {message}')

def expression(ts, var):
    a = {}
    constant = F(0)
    k = 0
    while k < len(ts):
        sign = 1
        if ts[k].text in ('+', '-'):
            sign = -1 if ts[k].text == '-' else 1
            k += 1
        elif k:
            fail(ts[k], 'expected + or - between terms')
        if k == len(ts):
            fail(ts[-1], 'incomplete term')
        token = ts[k]
        if token.kind == 'num':
            c = sign * F(token.text)
            k += 1
            if k < len(ts) and ts[k].kind == 'name':
                v = var(ts[k].text, ts[k].line)
                a[v] = a.get(v, F(0)) + c
                k += 1
            else:
                constant += c
        elif token.kind == 'name':
            if token.text.lower() in ('inf','infinity'):
                fail(token, 'infinity allowed only at bound endpoints')
            v = var(token.text, token.line)
            a[v] = a.get(v, F(0)) + sign
            k += 1
        else:
            fail(token, 'expected a linear term')
    return {v:c for v,c in a.items() if c}, constant

def constraint(ts, var):
    positions = [i for i,t in enumerate(ts) if t.text in ('=', '<=', '>=')]
    if len(positions) == 1:
        k = positions[0]
        if k == 0 or k == len(ts)-1:
            fail(ts[k], 'missing side of constraint')
        a, ac = expression(ts[:k], var)
        b, bc = expression(ts[k+1:], var)
        for v,c in b.items():
            a[v] = a.get(v, F(0)) - c
        a = {v:c for v,c in a.items() if c}
        rhs = bc-ac
        sense = ts[k].text
        return a, rhs if sense != '<=' else None, rhs if sense != '>=' else None
    if len(positions) == 2:
        p,q = positions
        if not p or q == len(ts)-1 or p+1 == q:
            fail(ts[0], 'incomplete ranged constraint')
        if ts[p].text != ts[q].text or ts[p].text == '=':
            fail(ts[p], 'ranged constraints must use two <= or two >=')
        left, lc = expression(ts[:p], var)
        mid, mc = expression(ts[p+1:q], var)
        right, rc = expression(ts[q+1:], var)
        if left or right:
            fail(ts[0], 'ranged endpoints must be constants')
        return (mid, lc-mc, rc-mc) if ts[p].text == '<=' else (mid, rc-mc, lc-mc)
    fail(ts[0], 'expected one comparison or a ranged constraint')

HEADERS = {
    'minimize':'min', 'minimum':'min', 'min':'min',
    'maximize':'max', 'maximum':'max', 'max':'max',
    'subject to':'rows', 'such that':'rows', 's.t.':'rows', 'st':'rows', 's.t':'rows',
    'bounds':'bounds', 'bound':'bounds',
    'binaries':'binary', 'binary':'binary', 'bin':'binary',
    'generals':'integer', 'general':'integer', 'integers':'integer', 'integer':'integer', 'gen':'integer',
    'end':'end',
}
