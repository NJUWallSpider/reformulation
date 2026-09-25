"""Incremental LP reader. Buffers one logical row (or the objective), not a file."""
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

def parse(path, sink):
    section = None
    pending = []
    row_number = 0
    seen_obj = seen_rows = ended = False
    seen_row_names = set()
    def flush():
        nonlocal pending, row_number
        if not pending:
            return
        tokens = pending
        pending = []
        label = None
        if len(tokens) >= 2 and tokens[1].text == ':':
            if tokens[0].kind != 'name':
                fail(tokens[0], 'invalid label')
            label = tokens[0].text
            tokens = tokens[2:]
        if not tokens:
            fail(pending[0] if pending else Token('', 0, 0, ''), 'empty expression')
        if section == 'obj':
            a,c = expression(tokens, sink.variable)
            sink.objective(a,c)
        elif section == 'rows':
            name = label or f'__row_{row_number}'
            if name in seen_row_names:
                fail(tokens[0], f'duplicate constraint name {name}')
            seen_row_names.add(name)
            a,lo,hi = constraint(tokens, sink.variable)
            sink.row(name,a,lo,hi,tokens[0].line)
            row_number += 1
    with open(path, encoding='utf-8') as f:
        for line_number, line in enumerate(f, 1):
            clean = line.split('\\', 1)[0].strip()
            if not clean:
                continue
            if ended:
                raise LPIRError(f'line {line_number}: content after End')
            header = HEADERS.get(re.sub(r'\s+', ' ', clean.rstrip(':')).lower())
            if header:
                flush()
                if header in ('min','max'):
                    if seen_obj or section:
                        raise LPIRError(f'line {line_number}: misplaced objective section')
                    sink.sense = header
                    seen_obj = True
                    section = 'obj'
                elif header == 'rows':
                    if not seen_obj or seen_rows:
                        raise LPIRError(f'line {line_number}: misplaced constraint section')
                    seen_rows = True
                    section = 'rows'
                elif header == 'end':
                    ended = True
                else:
                    if not seen_rows:
                        raise LPIRError(f'line {line_number}: declarations before constraints')
                    section = header
                continue
            if clean.lower() in ('sos','semi','semis','semi-continuous','semicontinuous','indicators'):
                raise LPIRError(f'line {line_number}: unsupported section {clean}')
            ts = lex(line, line_number)
            if section in ('obj','rows'):
                named = len(ts)>1 and ts[1].text == ':'
                if section == 'rows' and pending and named:
                    flush()
                elif section == 'rows' and pending and not (len(pending)>1 and pending[1].text==':'):
                    # Unnamed rows end once a complete numeric RHS is seen.
                    if any(t.text in ('<=','>=','=') for t in pending) and pending[-1].kind=='num' and ts[0].text not in ('+','-'):
                        flush()
                pending.extend(ts)
                if section == 'rows' and pending[-1].kind == 'num' and any(t.text in ('<=','>=','=') for t in pending):
                    flush()
            elif section in ('binary','integer'):
                for t in ts:
                    if t.kind != 'name':
                        fail(t, 'expected variable name in type declaration')
                    sink.set_kind(sink.variable(t.text,t.line), 'B' if section=='binary' else 'I')
            elif section == 'bounds':
                if len(ts)==2 and ts[0].kind=='name' and ts[1].text.lower()=='free':
                    sink.set_bound(sink.variable(ts[0].text,ts[0].line),None,None,True,True)
                    continue
                # Also accept reversed one-sided infinite bounds.
                if len(ts)>=3 and ts[0].kind=='name' and ts[1].text=='>=' and [t.text.lower() for t in ts[2:]] in (['-','inf'],['-','infinity']):
                    sink.set_bound(sink.variable(ts[0].text,ts[0].line),None,None,True,False)
                    continue
                if len(ts)>=3 and ts[-1].kind=='name' and ts[-2].text=='>=' and [t.text.lower() for t in ts[:-2]] in (['inf'],['infinity'],['+','inf'],['+','infinity']):
                    sink.set_bound(sink.variable(ts[-1].text,ts[-1].line),None,None,False,True)
                    continue
                # Infinity is supported only at bound endpoints; strip those sides.
                work = list(ts)
                low_free = high_free = False
                if len(work)>=4 and work[0].text=='-' and work[1].text.lower() in ('inf','infinity') and work[2].text=='<=':
                    low_free=True;work=work[3:]
                if len(work)>=3 and work[-1].text.lower() in ('inf','infinity'):
                    n=2 if work[-2].text=='<=' else 3 if len(work)>=3 and work[-2].text=='+' and work[-3].text=='<=' else 0
                    if n:
                        high_free=True;work=work[:-n]
                if len(work)==1 and work[0].kind=='name' and (low_free or high_free):
                    sink.set_bound(sink.variable(work[0].text,work[0].line),None,None,low_free,high_free)
                    continue
                a,lo,hi = constraint(work,sink.variable)
                if len(a)!=1:
                    fail(ts[0], 'bound must involve exactly one variable')
                v,c=next(iter(a.items()))
                if c<0:
                    lo,hi = (hi/c if hi is not None else None),(lo/c if lo is not None else None)
                else:
                    lo,hi = (lo/c if lo is not None else None),(hi/c if hi is not None else None)
                sink.set_bound(v,lo,hi,lo is not None or low_free,hi is not None or high_free)
            else:
                raise LPIRError(f'line {line_number}: expected section header')
    flush()
    if not seen_obj or not seen_rows or not ended:
        raise LPIRError('LP requires objective, Subject To, and End sections')
    sink.finish()
