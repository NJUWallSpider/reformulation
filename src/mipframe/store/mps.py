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


def parse(path, w, rhs=None, bounds=None, ranges=None, mps_format="auto", preserve_source=True):
    if mps_format not in ("auto", "fixed", "free"):
        raise MIPFrameError("mps_format must be auto, fixed or free")
    mps_rows = _Rows(w)
    sets = {k: set() for k in ("RHS", "BOUNDS", "RANGES")}
    section = None
    objective = None
    intmode = False
    ended = False
    lastcol = None
    lastset = {}
    selected = {"RHS": rhs, "BOUNDS": bounds, "RANGES": ranges}

    number_cache={}

    def number(s):
        if s in number_cache:return number_cache[s]
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
            value=F(token)
            if value.denominator==1:value=value.numerator
            if len(number_cache)<4096:number_cache[s]=value
            return value
        except (ValueError, InvalidOperation) as e:
            raise MIPFrameError(f"line {lineno}: invalid MPS number {s}") from e

    def pick(sec, name):
        sets[sec].add(name)
        names = sets[sec]
        if selected[sec] is None and len(names) > 1:
            raise MIPFrameError(
                f"line {lineno}: multiple {sec} vectors; select one explicitly"
            )
        return selected[sec] is None or selected[sec] == name

    from mipframe.store.source_mps import SourceCapture,HEADER,FIXED,MARKER,CONTINUATION
    with SourceCapture(path,w.path,preserve=preserve_source) as f:
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
                f.mark(section=head,flags=HEADER)
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
            if section == "COLUMNS" and len(p)>1 and p[1].strip("'") == "MARKER":
                f.mark(flags=MARKER)
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
                    f.mark(flags=FIXED | (CONTINUATION if not fields[0] else 0))
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
                if name in mps_rows:
                    raise MIPFrameError(f"line {lineno}: duplicate MPS row")
                mps_rows[name] = [r, typ, 0, None]
                f.mark(rows=[r])
            elif section == "COLUMNS":
                if not p[0]:
                    p[0] = lastcol
                if len(p) not in (3, 5) or not p[0]:
                    raise MIPFrameError(f"line {lineno}: malformed COLUMNS record")
                lastcol = p[0]
                v = w.variable(p[0], lineno)
                f.record[4]=v
                if intmode:
                    kind = w.kind(v)
                    if kind == "C":
                        w.set_kind(v, "I")
                        w.set_bound(v, None, F(1), False, True)
                for k in range(1, len(p), 2):
                    row_id = mps_rows.row_id(p[k])
                    if row_id is None:
                        raise MIPFrameError(f"line {lineno}: unknown row {p[k]}")
                    f.record[5+k//2]=row_id
                    w.term(row_id, v, number(p[k + 1]))
            elif section in ("RHS", "RANGES"):
                if len(p) not in (3, 5):
                    raise MIPFrameError(f"line {lineno}: malformed {section} record")
                if not p[0]:
                    p[0] = lastset.get(section, "")
                lastset[section] = p[0]
                chosen=pick(section,p[0])
                f.mark(selected=chosen,rows=[mps_rows.get(p[k],[-2])[0] for k in range(1,len(p),2)])
                if not chosen:
                    continue
                for k in range(1, len(p), 2):
                    row = mps_rows.get(p[k])
                    if row is None:
                        raise MIPFrameError(f"line {lineno}: unknown row {p[k]}")
                    val = number(p[k + 1])
                    field = "rhs" if section == "RHS" else "rng"
                    row[2 if field == "rhs" else 3] = val
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
                chosen=pick(section,name)
                f.mark(selected=chosen)
                if not chosen:
                    continue
                if typ in ("LO", "UP", "FX", "LI", "UI") and len(p) != 4:
                    raise MIPFrameError(f"line {lineno}: bound value missing")
                v = w.variable(var, lineno)
                f.mark(var=v)
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
    w.source_digest=f.digest.hexdigest()
    w.source_bytes=f.offset
    import json
    (w.path/'source_info.json').write_text(json.dumps(dict(version=1,objective=objective,
        selected={sec: selected[sec] if selected[sec] is not None else next(iter(sets[sec]),None) for sec in sets},
        mps_format=mps_format,preserved=preserve_source)))
    if not ended or intmode or objective is None:
        raise MIPFrameError("MPS requires objective row, balanced markers, and ENDATA")
    for sec, name in selected.items():
        if (
            name is not None
            and name not in sets[sec]
        ):
            raise MIPFrameError(f"Unknown {sec} vector {name}")
    for r, typ, rhsvalue, rng in mps_rows.values():
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


class _Rows:
    """MPS metadata borrows the import name index and uses mutable typed columns."""
    def __init__(self,writer):
        from mipframe.data.buffers import Buffer
        self.writer=writer;self.objective=None
        self.types=Buffer(writer.work,'u1');self.rhs=Buffer(writer.work,'<u8');self.ranges=Buffer(writer.work,'<u8')
        self.types.append(0);self.rhs.append(writer.number(0));self.ranges.append(2**64-1)
    def __getitem__(self,name):
        ident=self.row_id(name)
        if ident is None:raise KeyError(name)
        return _Row(self,ident+1)
    def row_id(self,name):
        if name==self.objective:return -1
        ident=self.writer.row_names.get(name)
        return ident if ident is not None and ident+1<len(self.types) else None
    def get(self,name,default=None):
        try:return self[name]
        except KeyError:return default
    def __contains__(self,name):return self.get(name) is not None
    def __setitem__(self,name,record):
        r,kind,rhs,rng=record
        if r<0:self.objective=name;return
        if r+1!=len(self.types):raise ValueError('MPS rows must use dense IDs')
        self.types.append('NELG'.index(kind));self.rhs.append(self.writer.number(rhs));self.ranges.append(2**64-1 if rng is None else self.writer.number(rng))
    def values(self):
        # Parsing is complete. Iterate columns directly without constructing a
        # record proxy and decoding four fields through it for every row.
        self.types=self.types.finish();self.rhs=self.rhs.finish();self.ranges=self.ranges.finish()
        for i in range(0 if self.objective is not None else 1,len(self.types)):
            rhs=int(self.rhs[i]);rng=int(self.ranges[i])
            yield i-1,'NELG'[int(self.types[i])],self.writer.num(rhs),None if rng==2**64-1 else self.writer.num(rng)


class _Row:
    def __init__(self,owner,index):self.owner,self.index=owner,index
    def __iter__(self):return (self[i] for i in range(4))
    def __getitem__(self,i):
        if i==0:return self.index-1
        if i==1:return 'NELG'[int(self.owner.types[self.index])]
        value=int((self.owner.rhs if i==2 else self.owner.ranges)[self.index])
        return None if value==2**64-1 else self.owner.writer.num(value)
    def __setitem__(self,i,value):
        (self.owner.rhs if i==2 else self.owner.ranges)[self.index]=2**64-1 if value is None else self.owner.writer.number(value)
