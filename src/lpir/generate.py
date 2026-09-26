"""Streaming disjoint-copy fixture; not representative of all large MIPs."""

from pathlib import Path
from math import ceil
from .numbers import LPIRError, decimal
from .validation import verify


def generate(m, path, copies=None, target_mb=None):
    verify(m)
    if (copies is None) == (target_mb is None):
        raise LPIRError("Specify exactly one of copies or target_mb")
    if copies is not None and copies < 1:
        raise LPIRError("copies must be positive")
    if target_mb is not None and target_mb <= 0:
        raise LPIRError("target_mb must be positive")
    target = int(target_mb * 1024 * 1024) if target_mb is not None else None
    copies = copies or max(1, ceil(target / max(1, m.manifest["source_bytes"])))

    def terms(f, iterator, d, empty=True):
        n = 0
        for v, c in iterator:
            if c:
                f.write(
                    (" +" if c > 0 else " -")
                    + decimal(abs(c))
                    + f" copy{d}_{m.variables[v][1]}"
                )
                n += 1
                if n % 16 == 0:
                    f.write("\n ")
        if not n and empty:
            f.write(" 0")

    def write(count):
        with open(path, "w") as f:
            f.write(
                "Minimize\n obj: 0\n"
                if m.manifest["sense"] == "min"
                else "Maximize\n obj: 0\n"
            )
            for d in range(count):
                terms(f, ((v[0], m.num(v[5])) for v in m.variables), d, False)
                f.write("\n")
            constant = m.num(m.manifest["objective_constant"]) * count
            if constant:
                f.write(
                    (" +" if constant > 0 else " -") + decimal(abs(constant)) + "\n"
                )
            f.write("Subject To\n")
            for d in range(count):
                for row in m.rows:
                    r = row[0]
                    lo, hi = m.num(row[2]), m.num(row[3])
                    if lo is None and hi is None:
                        raise LPIRError("Cannot emit a free LP row")
                    f.write(f" copy{d}_{row[1]}:")
                    if lo is not None and hi is not None and lo != hi:
                        f.write(" " + decimal(lo) + " <=")
                    iterator = (
                        m.iter_terms(r)
                        if hasattr(m, "iter_terms")
                        else m.row(r).items()
                    )
                    terms(f, iterator, d)
                    f.write(
                        " = " + decimal(lo)
                        if lo == hi
                        else " <= " + decimal(hi)
                        if hi is not None
                        else " >= " + decimal(lo)
                    )
                    f.write("\n")
            f.write("Bounds\n")
            for d in range(count):
                for v in m.variables:
                    name = f"copy{d}_{v[1]}"
                    lo = m.num(v[3])
                    hi = m.num(v[4])
                    if lo is None and hi is None:
                        f.write(f" {name} free\n")
                    elif lo is None:
                        f.write(f" -inf <= {name} <= {decimal(hi)}\n")
                    elif hi is None:
                        f.write(f" {decimal(lo)} <= {name}\n")
                    else:
                        f.write(f" {decimal(lo)} <= {name} <= {decimal(hi)}\n")
            for kind, title in [("B", "Binaries"), ("I", "Generals")]:
                started = False
                for d in range(count):
                    for v in m.variables:
                        if v[2] == kind:
                            if not started:
                                f.write(title + "\n")
                                started = True
                            f.write(f" copy{d}_{v[1]}\n")
            f.write("End\n")

    write(copies)
    while target is not None and Path(path).stat().st_size < target:
        copies = max(
            copies + 1, ceil(copies * target / max(1, Path(path).stat().st_size))
        )
        write(copies)
    return {
        "output": str(path),
        "copies": copies,
        "bytes": Path(path).stat().st_size,
        "warning": "Independent repeated blocks test repetition scalability, not arbitrary large MIP structure.",
    }
