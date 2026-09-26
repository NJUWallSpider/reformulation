"""Stream numerical IR to LP. Semantic views never override original coefficients."""

import re
from .numbers import decimal, LPIRError
from .validation import verify


def export(
    m, path, rename_vars=None, rename_rows=None, row_order=None, reverse_terms=False
):
    verify(m)

    def name(v):
        if rename_vars:
            return rename_vars[v]
        s = m.variables[v][1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\],]*", s):
            raise LPIRError(f"LP-incompatible variable name {s!r}; supply rename_vars")
        return s

    def terms(f, iterator):
        count = 0
        for v, c in iterator:
            if not c:
                continue
            f.write((" +" if c > 0 else " -") + decimal(abs(c)) + " " + name(v))
            count += 1
            if count % 16 == 0:
                f.write("\n ")
        if not count:
            f.write(" 0")

    with open(path, "w") as f:
        f.write(
            "Minimize\n obj:" if m.manifest["sense"] == "min" else "Maximize\n obj:"
        )
        vs = reversed(m.variables) if reverse_terms else m.variables
        terms(f, ((v[0], m.num(v[5])) for v in vs))
        c = m.num(m.manifest["objective_constant"])
        if c:
            f.write((" +" if c > 0 else " -") + decimal(abs(c)))
        f.write("\nSubject To\n")
        for r in row_order if row_order is not None else range(len(m.rows)):
            meta = m.rows[r]
            lo, hi = m.num(meta[2]), m.num(meta[3])
            label = rename_rows[r] if rename_rows else meta[1]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\],]*", label):
                raise LPIRError("LP-incompatible row name; supply rename_rows")
            if lo is None and hi is None:
                raise LPIRError("LP export cannot represent a completely free row")
            f.write(" " + label + ":")
            if lo is not None and hi is not None and lo != hi:
                f.write(" " + decimal(lo) + " <=")
            iterator = m.iter_terms(r)
            if reverse_terms:
                iterator = reversed(list(iterator))
            terms(f, iterator)
            f.write(
                " = " + decimal(lo)
                if lo == hi
                else " <= " + decimal(hi)
                if hi is not None
                else " >= " + decimal(lo)
            )
            f.write("\n")
        f.write("Bounds\n")
        for v in m.variables:
            lo, hi = m.num(v[3]), m.num(v[4])
            n = name(v[0])
            if lo is None and hi is None:
                f.write(f" {n} free\n")
            elif lo is None:
                f.write(f" -inf <= {n} <= {decimal(hi)}\n")
            elif hi is None:
                f.write(f" {decimal(lo)} <= {n}\n")
            else:
                f.write(f" {decimal(lo)} <= {n} <= {decimal(hi)}\n")
        for kind, title in [("B", "Binaries"), ("I", "Generals")]:
            started = False
            for v in m.variables:
                if v[2] == kind:
                    if not started:
                        f.write(title + "\n")
                        started = True
                    f.write(" " + name(v[0]) + "\n")
        f.write("End\n")
