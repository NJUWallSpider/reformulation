"""Stream numerical Model to LP. Semantic views never override original coefficients."""

from mipframe._help.diagnostics import hint as _help_hint

import re
from mipframe.store.numbers import decimal
from mipframe.store.numbers import MIPFrameError
from mipframe.store.integrity import require_integrity as verify


def _export(
    m, path, rename_vars=None, rename_rows=None, row_order=None, reverse_terms=False, _charge=None
):
    m.guard()
    if not hasattr(m,"f"):verify(m)

    def name(v):
        if rename_vars:
            return rename_vars[v]
        s = m.variables[v][1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\],]*", s):
            raise MIPFrameError(f"LP-incompatible variable name {s!r}; supply rename_vars")
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

    class ChargedWriter:
        def __init__(self,stream):self.stream=stream
        def write(self,text):
            _charge.add(len(text.encode('utf8')))
            return self.stream.write(text)

    with open(path, "w",encoding='utf8') as stream:
        f=stream if _charge is None else ChargedWriter(stream)
        f.write(
            "Minimize\n obj:" if m.manifest["sense"] == "min" else "Maximize\n obj:"
        )
        vs = reversed(list(m.variables)) if reverse_terms else m.variables
        terms(f, ((v[0], m.num(v[5])) for v in vs))
        c = m.num(m.manifest["objective_constant"])
        if c:
            f.write((" +" if c > 0 else " -") + decimal(abs(c)))
        f.write("\nSubject To\n")
        for r in row_order if row_order is not None else (m.entity_ids('row') if hasattr(m,'entity_ids') else range(len(m.rows))):
            meta = m.rows[r]
            lo, hi = m.num(meta[2]), m.num(meta[3])
            label = rename_rows[r] if rename_rows else meta[1]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\],]*", label):
                raise MIPFrameError("LP-incompatible row name; supply rename_rows")
            if lo is None and hi is None:
                raise _help_hint(MIPFrameError("LP export cannot represent a completely free row"), 'limitation.export')
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
                if m.variable_type(v) == kind:
                    if not started:
                        f.write(title + "\n")
                        started = True
                    f.write(" " + name(v[0]) + "\n")
        f.write("End\n")


def export(m, path, **options):
    """Publish a complete exact LP atomically, retaining any previous file on failure."""
    import os
    import tempfile
    from pathlib import Path
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    os.close(fd)
    try:
        _export(m, temporary, **options)
        with open(temporary, "rb") as f:
            os.fsync(f.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise
