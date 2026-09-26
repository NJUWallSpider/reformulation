"""Bounded-lookahead LP reader; expressions and objectives spill directly to SQLite."""

from collections import deque
from fractions import Fraction as F
from decimal import Decimal, InvalidOperation
from .parser import Token, HEADERS, constraint, fail
from .numbers import LPIRError


class Chars:
    def __init__(self, f):
        self.f = f
        self.buf = ""
        self.i = 0
        self.line = 1
        self.col = 1

    def peek(self):
        if self.i == len(self.buf):
            self.buf = self.f.read(16384)
            self.i = 0
        return self.buf[self.i : self.i + 1]

    def pop(self):
        c = self.peek()
        self.i += bool(c)
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return c


def tokens(f):
    c = Chars(f)
    while c.peek():
        x = c.peek()
        line, col = c.line, c.col
        if x in " \t\r":
            c.pop()
            continue
        if x == "\\":
            while c.peek() and c.peek() != "\n":
                c.pop()
            continue
        if x == "\n":
            c.pop()
            yield Token("\n", line, col, "newline")
            continue
        s = c.pop()
        if (x.isascii() and x.isalpha()) or x == "_":
            while c.peek() and (
                (c.peek().isascii() and c.peek().isalnum()) or c.peek() in "_.[],"
            ):
                s += c.pop()
                if len(s) > 65536:
                    raise LPIRError(f"line {line}: name exceeds 64KiB token limit")
            kind = "name"
        elif x.isdigit() or x == ".":
            while c.peek() and (c.peek().isdigit() or c.peek() == "."):
                s += c.pop()
                if len(s) > 4096:
                    raise LPIRError(
                        f"line {line}, column {col}: numeric token exceeds working precision budget"
                    )
            if c.peek() and c.peek() in "eE":
                s += c.pop()
                if c.peek() and c.peek() in "+-":
                    s += c.pop()
                while c.peek() and c.peek().isdigit():
                    s += c.pop()
                    if len(s) > 4096:
                        raise LPIRError(
                            f"line {line}, column {col}: numeric token exceeds working precision budget"
                        )
            try:
                digits = Decimal(s).as_tuple()
                if len(digits.digits) + abs(digits.exponent) > 4096:
                    raise LPIRError(
                        f"line {line}, column {col}: expanded decimal exceeds 4096-digit working precision budget"
                    )
                F(s)
            except (ValueError, InvalidOperation) as e:
                raise LPIRError(f"line {line}, column {col}: invalid number {s}") from e
            kind = "num"
        elif x in "+-=:<>":
            if x in "<>":
                if c.pop() != "=":
                    raise LPIRError(
                        f"line {line}, column {col}: unsupported syntax; expected <= or >="
                    )
                s += "="
            kind = "op"
        else:
            raise LPIRError(f"line {line}, column {col}: unsupported syntax {x!r}")
        yield Token(s, line, col, kind)
    yield Token("", c.line, c.col, "eof")


class Stream:
    def __init__(self, f):
        self.it = iter(tokens(f))
        self.q = deque()

    def peek(self, k=0):
        while len(self.q) <= k:
            self.q.append(next(self.it))
        return self.q[k]

    def pop(self):
        t = self.peek()
        self.q.popleft()
        return t

    def nl(self):
        while self.peek().kind == "newline":
            self.pop()

    def line(self):
        out = []
        while self.peek().kind not in ("newline", "eof"):
            out.append(self.pop())
            if len(out) > 1024:
                fail(out[0], "declaration exceeds 1024 tokens")
        self.nl()
        return out

    def header(self):
        t = self.peek().text.lower()
        return t in HEADERS or t in (
            "subject",
            "such",
            "s.t.",
            "sos",
            "semi",
            "semis",
            "semi-continuous",
            "semicontinuous",
            "indicators",
        )


def expr(s, w, r, factor=1, rhs=False):
    c = F(0)
    n = 0
    first = True
    while True:
        if s.peek().kind == "newline":
            s.nl()
            if rhs and not first and n == 0:
                break
        t = s.peek()
        if (
            t.kind == "eof"
            or s.header()
            or t.text in ("=", "<=", ">=")
            or (t.kind == "name" and s.peek(1).text == ":")
        ):
            break
        sign = 1
        if t.text in ("+", "-"):
            sign = -1 if s.pop().text == "-" else 1
            s.nl()
        elif not first:
            fail(t, "expected + or - between terms")
        t = s.pop()
        first = False
        if t.kind == "num":
            a = sign * F(t.text)
            # HiGHS wraps at token boundaries, including between a coefficient
            # and its variable. A scalar RHS still ends at a physical newline:
            # consuming it here could absorb the next unnamed constraint.
            if not rhs:
                s.nl()
            if (
                s.peek().kind == "name"
                and not s.header()
                and s.peek(1).text != ":"
            ):
                v = s.pop()
                w.term(r, w.variable(v.text, v.line), factor * a)
                n += 1
            else:
                c += a
        elif t.kind == "name":
            if t.text.lower() in ("inf", "infinity"):
                fail(t, "infinity only allowed in variable bounds")
            w.term(r, w.variable(t.text, t.line), F(factor * sign))
            n += 1
        else:
            fail(t, "expected linear term")
    if first:
        fail(s.peek(), "missing expression")
    return c, n


def bounds(ts, w):
    if len(ts) == 2 and ts[1].text.lower() == "free":
        w.set_bound(w.variable(ts[0].text, ts[0].line), None, None, True, True)
        return
    # Infinite endpoints are stripped before invoking the exact small-expression parser.
    lo_free = hi_free = False
    work = list(ts)
    if (
        len(work) >= 3
        and work[0].text == "-"
        and work[1].text.lower() in ("inf", "infinity")
        and work[2].text == "<="
    ):
        lo_free = True
        work = work[3:]
    if len(work) >= 3 and work[-1].text.lower() in ("inf", "infinity"):
        if work[-2].text == "<=":
            hi_free = True
            work = work[:-2]
        elif work[-2].text == "+" and work[-3].text == "<=":
            hi_free = True
            work = work[:-3]
        elif work[-2].text == "-" and work[-3].text == ">=":
            lo_free = True
            work = work[:-3]
    if (
        len(work) >= 4
        and work[0].text == "+"
        and work[1].text.lower() in ("inf", "infinity")
        and work[2].text == ">="
    ):
        hi_free = True
        work = work[3:]
    if (
        len(work) >= 3
        and work[0].text.lower() in ("inf", "infinity")
        and work[1].text == ">="
    ):
        hi_free = True
        work = work[2:]
    if len(work) == 1 and work[0].kind == "name" and (lo_free or hi_free):
        w.set_bound(
            w.variable(work[0].text, work[0].line), None, None, lo_free, hi_free
        )
        return
    a, lo, hi = constraint(work, w.variable)
    if len(a) != 1:
        fail(ts[0], "bound must involve one variable")
    v, c = next(iter(a.items()))
    if c < 0:
        lo, hi = (
            (hi / c if hi is not None else None),
            (lo / c if lo is not None else None),
        )
    else:
        lo, hi = (
            (lo / c if lo is not None else None),
            (hi / c if hi is not None else None),
        )
    w.set_bound(v, lo, hi, lo is not None or lo_free, hi is not None or hi_free)


def parse(path, w, **options):
    if options:
        raise LPIRError(f"Unsupported LP options: {sorted(options)}")
    with open(path, encoding="utf-8") as f:
        s = Stream(f)
        section = None
        seen_obj = seen_rows = ended = False
        while True:
            s.nl()
            t = s.peek()
            if t.kind == "eof":
                break
            if ended:
                fail(t, "content after End")
            if s.header():
                ts = s.line()
                key = " ".join(t.text.lower() for t in ts).rstrip(":")
                header = HEADERS.get(key)
                # HiGHS emits an empty `semi` section even for linear models
                # without semicontinuous variables. Accept only that empty
                # declaration; nonempty special domains remain unsupported.
                if key == "semi" and seen_rows and s.header():
                    continue
                if header is None:
                    fail(ts[0], f"unsupported section {key}")
                if header in ("min", "max"):
                    if seen_obj or section:
                        fail(ts[0], "misplaced objective section")
                    seen_obj = True
                    section = "obj"
                    w.sense = header
                elif header == "rows":
                    if not seen_obj or seen_rows:
                        fail(ts[0], "misplaced constraint section")
                    seen_rows = True
                    section = "rows"
                elif header == "end":
                    ended = True
                else:
                    if not seen_rows:
                        fail(ts[0], "declarations before constraints")
                    section = header
                continue
            if section == "obj":
                if s.peek(1).text == ":":
                    s.pop()
                    s.pop()
                c, _ = expr(s, w, -1)
                w.constant = w.number(c)
                if not s.header() and s.peek().kind != "eof":
                    fail(s.peek(), "expected constraint section")
            elif section == "rows":
                name = f"__row_{w.rows}"
                if s.peek(1).text == ":":
                    name = s.pop().text
                    s.pop()
                r = w.new_row(name, t.line)
                lc, ln = expr(s, w, r)
                op = s.pop()
                if op.text not in ("=", "<=", ">="):
                    fail(op, "expected comparison")
                rc, rn = expr(s, w, r, -1, True)
                if s.peek().text in ("<=", ">=", "="):
                    op2 = s.pop()
                    if op2.text != op.text or op.text == "=" or ln:
                        fail(
                            op2,
                            "ranged endpoints must be constants and directions match",
                        )
                    ec, en = expr(s, w, r, 1, True)
                    if en:
                        fail(op2, "ranged endpoints must be constants")
                    w.db.execute(
                        "UPDATE stage SET value=CASE WHEN substr(value,1,1)='-' THEN substr(value,2) ELSE '-'||value END WHERE r=?",
                        (r,),
                    )
                    lo, hi = (
                        (lc - rc, ec - rc) if op.text == "<=" else (ec - rc, lc - rc)
                    )
                else:
                    rhs = rc - lc
                    lo = rhs if op.text != "<=" else None
                    hi = rhs if op.text != ">=" else None
                w.row_bounds(r, lo, hi)
            elif section in ("binary", "integer"):
                t = s.pop()
                if t.kind != "name":
                    fail(t, "expected variable name")
                w.set_kind(
                    w.variable(t.text, t.line), "B" if section == "binary" else "I"
                )
            elif section == "bounds":
                bounds(s.line(), w)
            else:
                fail(t, "expected section header")
        if not seen_obj or not seen_rows or not ended:
            raise LPIRError("LP requires objective, Subject To, and End sections")
