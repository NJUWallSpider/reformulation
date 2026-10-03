"""Exact linear fixed/free MPS reader. Named vector selection is explicit."""

from fractions import Fraction as F
from decimal import Decimal
from decimal import InvalidOperation
from mipframe.store.numbers import MIPFrameError


def _fixed_fields(line, section):
    """Return fields only if the entire record fits the fixed-column layout.

    Free-format writers often pad short names just like fixed MPS, then emit
    longer names or full-precision numbers later in the same section. Checking
    every separator and the end of the record prevents silently slicing those
    tokens. Blank column/vector names remain valid fixed-format continuations;
    embedded spaces inside fixed name fields remain part of the name.
    """
    text = line.rstrip("\r\n")
    if "\t" in text:
        return None
    if section == "ROWS":
        spans = ((1, 3), (4, 12))
        gaps = ((0, 1), (3, 4), (12, len(text)))
    elif section == "BOUNDS":
        spans = ((1, 3), (4, 12), (14, 22), (24, 36))
        gaps = ((0, 1), (3, 4), (12, 14), (22, 24), (36, len(text)))
    elif section in ("COLUMNS", "RHS", "RANGES"):
        spans = ((4, 12), (14, 22), (24, 36), (39, 47), (49, 61))
        gaps = ((0, 4), (12, 14), (22, 24), (36, 39), (47, 49), (61, len(text)))
    else:
        return None
    if any(text[a:b].strip() for a, b in gaps):
        return None
    fields = [text[a:b].strip() for a, b in spans]
    if section == "ROWS":
        return fields if all(fields) else None
    if section == "BOUNDS":
        if not fields[0] or not fields[2]:
            return None
        return fields if fields[3] else fields[:3]
    if not fields[1] or not fields[2] or bool(fields[3]) != bool(fields[4]):
        return None
    return fields if fields[3] else fields[:3]


def parse(path, w, rhs=None, bounds=None, ranges=None, mps_format="auto"):
    if mps_format not in ("auto", "fixed", "free"):
        raise MIPFrameError("mps_format must be auto, fixed or free")
    db = w.db
    db.executescript("""CREATE TABLE mps_rows(name TEXT PRIMARY KEY,r INTEGER,type TEXT,rhs TEXT DEFAULT '0',rng TEXT);
    CREATE TABLE mps_bounds(v INTEGER PRIMARY KEY);
    CREATE TABLE mps_sets(section TEXT,name TEXT,PRIMARY KEY(section,name));""")
    section = None
    objective = None
    intmode = False
    ended = False
    lastcol = None
    lastset = {}
    selected = {"RHS": rhs, "BOUNDS": bounds, "RANGES": ranges}

    def number(s):
        try:
            token = s.replace("D", "E").replace("d", "e")
            d = Decimal(token).as_tuple()
            if (
                not isinstance(d.exponent, int)
                or len(d.digits) + abs(d.exponent) > 4096
            ):
                raise MIPFrameError(
                    f"line {lineno}: decimal exceeds working precision budget"
                )
            return F(token)
        except (ValueError, InvalidOperation) as e:
            raise MIPFrameError(f"line {lineno}: invalid MPS number {s}") from e

    def pick(sec, name):
        db.execute("INSERT OR IGNORE INTO mps_sets VALUES(?,?)", (sec, name))
        names = [
            x[0]
            for x in db.execute("SELECT name FROM mps_sets WHERE section=?", (sec,))
        ]
        if selected[sec] is None and len(names) > 1:
            raise MIPFrameError(
                f"line {lineno}: multiple {sec} vectors; select one explicitly"
            )
        return selected[sec] is None or selected[sec] == name

    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(iter(lambda: f.readline(65537), ""), 1):
            if len(line) > 65536:
                raise MIPFrameError(f"line {lineno}: MPS record exceeds 64KiB")
            if not line.strip() or line.startswith("*"):
                continue
            if ended:
                raise MIPFrameError(f"line {lineno}: content after ENDATA")
            p = line.split()
            if not line[0].isspace():
                head = p[0].upper()
                if head not in (
                    "NAME",
                    "OBJSENSE",
                    "OBJNAME",
                    "ROWS",
                    "COLUMNS",
                    "RHS",
                    "RANGES",
                    "BOUNDS",
                    "ENDATA",
                ):
                    raise MIPFrameError(f"line {lineno}: unsupported MPS section {head}")
                section = head
                if head == "ENDATA":
                    ended = True
                    continue
                if head == "OBJNAME" and len(p) > 1:
                    objective = p[1]
                if head == "OBJSENSE" and len(p) > 1:
                    if p[1].upper() not in ("MIN", "MAX"):
                        raise MIPFrameError(f"line {lineno}: invalid objective sense")
                    w.sense = p[1].lower()
                continue
            if section == "OBJSENSE":
                if p[0].upper() not in ("MIN", "MAX"):
                    raise MIPFrameError(f"line {lineno}: invalid objective sense")
                w.sense = p[0].lower()
                continue
            if section == "OBJNAME":
                objective = p[0]
                continue
            # Marker alignment does not determine coefficient record layout.
            # Preserve the last column across markers for fixed continuations.
            if section == "COLUMNS" and any(x.strip("'") == "MARKER" for x in p):
                marker = p[-1].strip("'")
                if (
                    marker not in ("INTORG", "INTEND")
                    or (marker == "INTORG") == intmode
                ):
                    raise MIPFrameError(f"line {lineno}: invalid integer marker")
                intmode = marker == "INTORG"
                continue
            if mps_format != "free":
                fields = _fixed_fields(line, section)
                if fields is not None:
                    p = fields
                elif mps_format == "fixed":
                    raise MIPFrameError(
                        f"line {lineno}: invalid fixed-format {section} record"
                    )
            if section == "ROWS":
                if len(p) != 2 or p[0] not in ("N", "E", "L", "G"):
                    raise MIPFrameError(f"line {lineno}: invalid ROWS record")
                typ, name = p
                if typ == "N" and objective is None:
                    objective = name
                r = -1 if name == objective else w.new_row(name, lineno)
                try:
                    db.execute(
                        "INSERT INTO mps_rows(name,r,type) VALUES(?,?,?)",
                        (name, r, typ),
                    )
                except Exception as e:
                    raise MIPFrameError(f"line {lineno}: duplicate MPS row") from e
            elif section == "COLUMNS":
                if not p[0]:
                    p[0] = lastcol
                if len(p) not in (3, 5) or not p[0]:
                    raise MIPFrameError(f"line {lineno}: malformed COLUMNS record")
                lastcol = p[0]
                v = w.variable(p[0], lineno)
                if intmode:
                    kind = db.execute(
                        "SELECT kind FROM variables WHERE id=?", (v,)
                    ).fetchone()[0]
                    if kind == "C":
                        w.set_kind(v, "I")
                        w.set_bound(v, None, F(1), False, True)
                for k in range(1, len(p), 2):
                    row = db.execute(
                        "SELECT r FROM mps_rows WHERE name=?", (p[k],)
                    ).fetchone()
                    if row is None:
                        raise MIPFrameError(f"line {lineno}: unknown row {p[k]}")
                    w.term(row[0], v, number(p[k + 1]))
            elif section in ("RHS", "RANGES"):
                if len(p) not in (3, 5):
                    raise MIPFrameError(f"line {lineno}: malformed {section} record")
                if not p[0]:
                    p[0] = lastset.get(section, "")
                lastset[section] = p[0]
                if not pick(section, p[0]):
                    continue
                for k in range(1, len(p), 2):
                    row = db.execute(
                        "SELECT r FROM mps_rows WHERE name=?", (p[k],)
                    ).fetchone()
                    if row is None:
                        raise MIPFrameError(f"line {lineno}: unknown row {p[k]}")
                    val = number(p[k + 1])
                    field = "rhs" if section == "RHS" else "rng"
                    db.execute(
                        f"UPDATE mps_rows SET {field}=? WHERE name=?", (str(val), p[k])
                    )
                    if row[0] == -1:
                        if section == "RANGES":
                            raise MIPFrameError(f"line {lineno}: range on objective")
                        w.constant = w.number(-val)
            elif section == "BOUNDS":
                if len(p) not in (3, 4):
                    raise MIPFrameError(f"line {lineno}: malformed BOUNDS record")
                typ, name, var = p[:3]
                if typ not in ("LO", "UP", "FX", "FR", "MI", "PL", "BV", "LI", "UI"):
                    raise MIPFrameError(f"line {lineno}: unsupported bound type {typ}")
                if not pick(section, name):
                    continue
                if typ in ("LO", "UP", "FX", "LI", "UI") and len(p) != 4:
                    raise MIPFrameError(f"line {lineno}: bound value missing")
                v = w.variable(var, lineno)
                val = number(p[3]) if len(p) == 4 else None
                if typ == "BV":
                    w.set_kind(v, "B")
                    w.set_bound(v, F(0), F(1), True, True)
                elif typ == "FR":
                    w.set_bound(v, None, None, True, True)
                elif typ == "MI":
                    w.set_bound(v, None, None, True, False)
                elif typ == "PL":
                    w.set_bound(v, None, None, False, True)
                elif typ == "FX":
                    w.set_bound(v, val, val, True, True)
                else:
                    if typ in ("LI", "UI"):
                        w.set_kind(v, "I")
                    if typ in ("LO", "LI"):
                        w.set_bound(v, val, None, True, False)
                    else:
                        w.set_bound(v, None, val, False, True)
            else:
                raise MIPFrameError(f"line {lineno}: unexpected MPS record")
    if not ended or intmode or objective is None:
        raise MIPFrameError("MPS requires objective row, balanced markers, and ENDATA")
    for sec, name in selected.items():
        if (
            name is not None
            and not db.execute(
                "SELECT 1 FROM mps_sets WHERE section=? AND name=?", (sec, name)
            ).fetchone()
        ):
            raise MIPFrameError(f"Unknown {sec} vector {name}")
    for name, r, typ, rhsvalue, rng in db.execute("SELECT * FROM mps_rows"):
        if r < 0:
            continue
        b = F(rhsvalue)
        lo = b if typ in ("E", "G") else None
        hi = b if typ in ("E", "L") else None
        if rng is not None:
            a = F(rng)
            if typ == "G":
                hi = b + abs(a)
            elif typ == "L":
                lo = b - abs(a)
            elif typ == "E":
                lo, hi = (b, b + a) if a >= 0 else (b + a, b)
            else:
                raise MIPFrameError("Range on free row unsupported")
        w.row_bounds(r, lo, hi)
    for table in ("mps_rows", "mps_bounds", "mps_sets"):
        db.execute(f"DROP TABLE {table}")
