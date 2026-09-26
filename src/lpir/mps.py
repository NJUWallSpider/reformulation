"""Exact linear fixed/free MPS reader. Named vector selection is explicit."""

from fractions import Fraction as F
from decimal import Decimal, InvalidOperation
from .numbers import LPIRError


def parse(path, w, rhs=None, bounds=None, ranges=None, mps_format="auto"):
    if mps_format not in ("auto", "fixed", "free"):
        raise LPIRError("mps_format must be auto, fixed or free")
    db = w.db
    db.executescript("""CREATE TABLE mps_rows(name TEXT PRIMARY KEY,r INTEGER,type TEXT,rhs TEXT DEFAULT '0',rng TEXT);
    CREATE TABLE mps_bounds(v INTEGER PRIMARY KEY);
    CREATE TABLE mps_sets(section TEXT,name TEXT,PRIMARY KEY(section,name));""")
    section = None
    objective = None
    intmode = False
    ended = False
    lastcol = None
    fixed_layout = False
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
                raise LPIRError(
                    f"line {lineno}: decimal exceeds working precision budget"
                )
            return F(token)
        except (ValueError, InvalidOperation) as e:
            raise LPIRError(f"line {lineno}: invalid MPS number {s}") from e

    def pick(sec, name):
        db.execute("INSERT OR IGNORE INTO mps_sets VALUES(?,?)", (sec, name))
        names = [
            x[0]
            for x in db.execute("SELECT name FROM mps_sets WHERE section=?", (sec,))
        ]
        if selected[sec] is None and len(names) > 1:
            raise LPIRError(
                f"line {lineno}: multiple {sec} vectors; select one explicitly"
            )
        return selected[sec] is None or selected[sec] == name

    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(iter(lambda: f.readline(65537), ""), 1):
            if len(line) > 65536:
                raise LPIRError(f"line {lineno}: MPS record exceeds 64KiB")
            if not line.strip() or line.startswith("*"):
                continue
            if ended:
                raise LPIRError(f"line {lineno}: content after ENDATA")
            p = line.split()
            raw_p = list(p)
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
                    raise LPIRError(f"line {lineno}: unsupported MPS section {head}")
                section = head
                fixed_layout = False
                if head == "ENDATA":
                    ended = True
                    continue
                if head == "OBJNAME" and len(p) > 1:
                    objective = p[1]
                if head == "OBJSENSE" and len(p) > 1:
                    if p[1].upper() not in ("MIN", "MAX"):
                        raise LPIRError(f"line {lineno}: invalid objective sense")
                    w.sense = p[1].lower()
                continue
            if section == "OBJSENSE":
                if p[0].upper() not in ("MIN", "MAX"):
                    raise LPIRError(f"line {lineno}: invalid objective sense")
                w.sense = p[0].lower()
                continue
            if section == "OBJNAME":
                objective = p[0]
                continue
            fixed = mps_format == "fixed" or (
                mps_format == "auto"
                and len(line) > 14
                and line[3:4] == " "
                and line[12:14] == "  "
                and bool(line[4:12].strip())
                and (
                    section not in ("COLUMNS", "RHS", "RANGES")
                    or bool(line[14:22].strip())
                )
            )
            fixed = fixed or (mps_format == "auto" and fixed_layout)
            fixed_layout = fixed
            if fixed:
                if section == "ROWS":
                    p = [line[1:3].strip(), line[4:12].strip()]
                elif section == "BOUNDS":
                    p = [line[1:3].strip(), line[4:12].strip(), line[14:22].strip()] + (
                        [line[24:36].strip()] if line[24:36].strip() else []
                    )
                elif section in ("COLUMNS", "RHS", "RANGES"):
                    p = [line[4:12].strip(), line[14:22].strip(), line[24:36].strip()]
                    if line[39:47].strip():
                        p.extend([line[39:47].strip(), line[49:61].strip()])
            if any(x.strip("'") == "MARKER" for x in raw_p):
                p = raw_p
            if section == "ROWS":
                if len(p) != 2 or p[0] not in ("N", "E", "L", "G"):
                    raise LPIRError(f"line {lineno}: invalid ROWS record")
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
                    raise LPIRError(f"line {lineno}: duplicate MPS row") from e
            elif section == "COLUMNS":
                if any(x.strip("'") == "MARKER" for x in p):
                    marker = p[-1].strip("'")
                    if (
                        marker not in ("INTORG", "INTEND")
                        or (marker == "INTORG") == intmode
                    ):
                        raise LPIRError(f"line {lineno}: invalid integer marker")
                    intmode = marker == "INTORG"
                    continue
                if not p[0]:
                    p[0] = lastcol
                if len(p) not in (3, 5) or not p[0]:
                    raise LPIRError(f"line {lineno}: malformed COLUMNS record")
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
                        raise LPIRError(f"line {lineno}: unknown row {p[k]}")
                    w.term(row[0], v, number(p[k + 1]))
            elif section in ("RHS", "RANGES"):
                if len(p) not in (3, 5):
                    raise LPIRError(f"line {lineno}: malformed {section} record")
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
                        raise LPIRError(f"line {lineno}: unknown row {p[k]}")
                    val = number(p[k + 1])
                    field = "rhs" if section == "RHS" else "rng"
                    db.execute(
                        f"UPDATE mps_rows SET {field}=? WHERE name=?", (str(val), p[k])
                    )
                    if row[0] == -1:
                        if section == "RANGES":
                            raise LPIRError(f"line {lineno}: range on objective")
                        w.constant = w.number(-val)
            elif section == "BOUNDS":
                if len(p) not in (3, 4):
                    raise LPIRError(f"line {lineno}: malformed BOUNDS record")
                typ, name, var = p[:3]
                if typ not in ("LO", "UP", "FX", "FR", "MI", "PL", "BV", "LI", "UI"):
                    raise LPIRError(f"line {lineno}: unsupported bound type {typ}")
                if not pick(section, name):
                    continue
                if typ in ("LO", "UP", "FX", "LI", "UI") and len(p) != 4:
                    raise LPIRError(f"line {lineno}: bound value missing")
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
                raise LPIRError(f"line {lineno}: unexpected MPS record")
    if not ended or intmode or objective is None:
        raise LPIRError("MPS requires objective row, balanced markers, and ENDATA")
    for sec, name in selected.items():
        if (
            name is not None
            and not db.execute(
                "SELECT 1 FROM mps_sets WHERE section=? AND name=?", (sec, name)
            ).fetchone()
        ):
            raise LPIRError(f"Unknown {sec} vector {name}")
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
                raise LPIRError("Range on free row unsupported")
        w.row_bounds(r, lo, hi)
    for table in ("mps_rows", "mps_bounds", "mps_sets"):
        db.execute(f"DROP TABLE {table}")
