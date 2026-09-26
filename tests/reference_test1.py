"""Strict parser for the linear LP syntax of test1; decimals are exact rationals."""

import re
from fractions import Fraction as F
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Row:
    name: str
    a: dict
    sense: str
    rhs: F


term = re.compile(r"([+-]?\s*(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+(x\d+)")


def expr(s):
    a = {}
    for m in term.finditer(s):
        v = int(m[2][1:])
        a[v] = a.get(v, F(0)) + F(m[1].replace(" ", ""))
    assert not term.sub("", s).strip(), repr(term.sub("", s))
    return {v: c for v, c in a.items() if c}


def read(path):
    t = Path(path).read_text()
    o, c = t.split("Subject to")
    c, b = c.split("Bounds")
    b, bi = b.split("Binaries")
    obj = expr(o.split("Obj:")[1])
    rows = []
    for m in re.finditer(r"(c\d+):\s*(.*?)(?=c\d+:|\Z)", c, re.S):
        lhs, sense, rhs = re.split(r"(<=|>=|=)", m[2])
        rows.append(Row(m[1], expr(lhs), sense, F(rhs.strip())))
    assert len(rows) == 4630
    bounds = {}
    for line in b.splitlines():
        if not line.strip():
            continue
        m = re.fullmatch(r"\s*(\S+) <= x(\d+)(?: <= (\S+))?\s*", line)
        assert m, line
        bounds[int(m[2])] = (F(m[1]), F(m[3]) if m[3] else None)
    binaries = set(map(int, re.findall(r"x(\d+)", bi)))
    assert not re.sub(r"x\d+|End|\s", "", bi)
    allvars = set(obj).union(*(r.a for r in rows), binaries, bounds)
    for v in allvars:
        bounds.setdefault(v, (F(0), F(1) if v in binaries else None))
    assert len(bounds) == 11090 and len(binaries) == 11025
    return obj, rows, bounds, binaries
