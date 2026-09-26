"""Exact LP grammar primitives shared by the streaming parser."""
import re
from dataclasses import dataclass
from fractions import Fraction as F
from .numbers import LPIRError

NAME = r'[A-Za-z_][A-Za-z0-9_.\[\],]*'
LEX = re.compile(r'(?P<space>\s+)|(?P<num>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|(?P<name>'+NAME+r')|(?P<op><=|>=|[+\-=:])')

@dataclass
class Token:
    text: str
    line: int
    col: int
    kind: str

def fail(token, message):
    raise LPIRError(f'line {token.line}, column {token.col}: {message}')

def lex(line, number):
    line = line.split('\\', 1)[0]
    result = []
    pos = 0
    while pos < len(line):
        m = LEX.match(line, pos)
        if not m:
            raise LPIRError(f'line {number}, column {pos+1}: unsupported character/syntax {line[pos:pos+16]!r}')
        if m.lastgroup != 'space':
            result.append(Token(m[0], number, pos+1, m.lastgroup))
        pos = m.end()
    return result

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
