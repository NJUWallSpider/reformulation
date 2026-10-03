"""Exact sparse reduction recovery and bounded numeric verification."""

from fractions import Fraction as F
import json
from mipframe.ir.indexing.store import ident
from mipframe.ir.indexing.discovery import evidence
from mipframe.store.numbers import MIPFrameError


def operator_ast(t, axis, invariant):
    return {
        "op": "range",
        "lower": {"parameter": "lower", "indices": ["p", "k"]},
        "upper": {"parameter": "upper", "indices": ["p", "k"]},
        "expression": {
            "op": "sub",
            "left": {"tensor": "y", "indices": ["p"]},
            "right": {
                "op": "sum",
                "index": "j",
                "domain": {"tensor": t, "fixed_axis": axis, "fixed_index": "k"},
                "term": {
                    "op": "mul",
                    "left": {
                        "parameter": "A",
                        "indices": ["p", "j"] if invariant else ["p", "k", "j"],
                    },
                    "right": {
                        "tensor": t,
                        "indices": ["k", "j"] if axis == 0 else ["j", "k"],
                    },
                },
            },
        },
    }


def recover(s, run, edges):
    m = s.owner.model
    for (txt,) in s.db.execute(
        "SELECT record FROM cards WHERE run=? AND kind='tensors' ORDER BY id", (run,)
    ):
        tensor = json.loads(txt)
        t = tensor["id"]
        bundle = s.table(
            "axis INTEGER,r INTEGER,anchor INTEGER,k INTEGER,scale TEXT,lo TEXT,hi TEXT,PRIMARY KEY(axis,r)"
        )
        slices = s.table(
            "r INTEGER,j0 INTEGER,j1 INTEGER,value TEXT,PRIMARY KEY(r,j0,j1)"
        )
        # Full CSR row length is used even for explicitly restricted variable scopes.
        for r, n, amin, amax, bmin, bmax in s.db.execute(
            f"SELECT e.r,count(*),min(c.a),max(c.a),min(c.b),max(c.b) FROM {edges} e JOIN coordinates c ON c.v=e.v AND c.t=? GROUP BY e.r",
            (t,),
        ):
            if int(m.row_ptr[r + 1] - m.row_ptr[r]) != n + 1:
                continue
            if amin != amax and bmin != bmax:
                continue
            anchor = None
            scale = None
            # Force the row's short CSR support to drive this join. Without
            # CROSS JOIN SQLite can scan every tensor variable for every row.
            inside = iter(s.db.execute(
                f"SELECT e.v FROM {edges} e CROSS JOIN coordinates c ON c.v=e.v AND c.t=? WHERE e.r=? ORDER BY e.v",
                (t, r),
            ))
            current = next(inside, None)
            for pos in range(int(m.row_ptr[r]), int(m.row_ptr[r + 1])):
                v = int(m.col_idx[pos])
                if current is not None and v == current[0]:
                    current = next(inside, None)
                else:
                    if anchor is not None:
                        break
                    anchor = v
                    scale = m.num(int(m.coeff_id[pos]))
            else:
                if anchor is None:
                    continue
                row = m.rows[r]
                lo, hi = m.num(row[2]), m.num(row[3])
                if scale < 0:
                    lo, hi = hi, lo
                lo = None if lo is None else str(lo / scale)
                hi = None if hi is None else str(hi / scale)
                for axis, low, high in ((0, amin, amax), (1, bmin, bmax)):
                    if low == high:
                        s.db.execute(
                            f"INSERT INTO {bundle} VALUES(?,?,?,?,?,?,?)",
                            (axis, r, anchor, low, str(scale), lo, hi),
                        )
                s.db.executemany(
                    f"INSERT INTO {slices} VALUES(?,?,?,?)",
                    ((r, a, b, str(-m.num(int(m.coeff_id[pos])) / scale))
                     for a, b, pos in s.db.execute(
                         f"SELECT c.a,c.b,e.pos FROM {edges} e CROSS JOIN coordinates c ON c.v=e.v AND c.t=? WHERE e.r=?",
                         (t, r),
                     )),
                )
        for axis in (0, 1):
            count = s.db.execute(
                f"SELECT count(*) FROM {bundle} WHERE axis=?", (axis,)
            ).fetchone()[0]
            if not count:
                continue
            duplicate = s.db.execute(
                f"SELECT anchor,k,count(*) FROM {bundle} WHERE axis=? GROUP BY anchor,k HAVING count(*)>1 LIMIT 1",
                (axis,),
            ).fetchone()
            if duplicate:
                evidence(
                    s,
                    run,
                    "duplicate row coordinates",
                    tensor=t,
                    axis=axis,
                    counterexample=list(duplicate),
                )
                continue
            anchors = s.table("anchor INTEGER PRIMARY KEY,p INTEGER")
            s.db.execute(
                f"INSERT INTO {anchors} SELECT anchor,row_number() OVER(ORDER BY anchor)-1 FROM {bundle} WHERE axis=? GROUP BY anchor",
                (axis,),
            )
            np = s.db.execute(f"SELECT count(*) FROM {anchors}").fetchone()[0]
            nk = tensor["shape"][axis]
            nj = tensor["shape"][1 - axis]
            o = ident("operator_", [t, axis])
            jcol = "j1" if axis == 0 else "j0"
            # Compare complete sparse vectors through exact SQL EXCEPT, including zeros.
            invariant = count == np * nk
            for (anchor,) in s.db.execute(f"SELECT anchor FROM {anchors}"):
                base = None
                for (r,) in s.db.execute(
                    f"SELECT r FROM {bundle} WHERE axis=? AND anchor=? ORDER BY k",
                    (axis, anchor),
                ):
                    if base is None:
                        base = r
                        continue
                    for left, right in ((base, r), (r, base)):
                        if s.db.execute(
                            f"SELECT {jcol},value FROM {slices} WHERE r=? EXCEPT SELECT {jcol},value FROM {slices} WHERE r=? LIMIT 1",
                            (left, right),
                        ).fetchone():
                            invariant = False
            for r, p, k, anchor, scale, lo, hi in s.db.execute(
                f"SELECT b.r,a.p,b.k,b.anchor,b.scale,b.lo,b.hi FROM {bundle} b JOIN {anchors} a ON a.anchor=b.anchor WHERE axis=? ORDER BY p,k",
                (axis,),
            ):
                s.db.execute(
                    "INSERT INTO bindings VALUES(?,?,?,?,?,?,?,?)",
                    (o, r, p, k, anchor, scale, lo, hi),
                )
                s.db.execute(
                    f"INSERT OR IGNORE INTO kernel SELECT ?,?,?,{jcol},value FROM {slices} WHERE r=?",
                    (o, p, -1 if invariant else k, r),
                )
            card = dict(
                id=o,
                tensor=t,
                fixed_axis=axis,
                rows=count,
                shape=[np, nk],
                kernel_shape=[np, nj] if invariant else [np, nk, nj],
                axis_invariant=invariant,
                complete_row_product=count == np * nk,
                kind="broadcast_reduction" if invariant else "indexed_reduction",
                ast=operator_ast(t, axis, invariant),
                formula=f"lower[p,k] <= y[p] - Sum_j A[{'p,j' if invariant else 'p,k,j'}] * X[{'k,j' if axis == 0 else 'j,k'}] <= upper[p,k]",
                verification="verified",
            )
            errors = check_operator(s, card)
            if errors:
                raise MIPFrameError(
                    "Internal indexed operator expansion failure: " + str(errors)
                )
            card["kernel_statistics"] = kernel_stats(s, card)
            s.put(run, "operators", o, card)
            s.discard(anchors)
        s.discard(bundle)
        s.discard(slices)


def check_operator(s, card, *, kernel=None, rows=None, coordinates=None, ast=None):
    """Replay each full original row, streaming merge against an exact SQL expansion."""
    if ast is not None and ast != operator_ast(
        card["tensor"], card["fixed_axis"], card["axis_invariant"]
    ):
        return [dict(reason="unsupported or changed indexed AST")]
    if card["ast"] != operator_ast(
        card["tensor"], card["fixed_axis"], card["axis_invariant"]
    ):
        return [dict(reason="AST disagrees with expression")]
    m = s.owner.model
    o = card["id"]
    axis = card["fixed_axis"]
    t = card["tensor"]
    work = s.table("v INTEGER PRIMARY KEY,value TEXT")
    kt = s.table("p INTEGER,k INTEGER,j INTEGER,value TEXT,PRIMARY KEY(p,k,j)")
    bt = s.table(
        "r INTEGER PRIMARY KEY,p INTEGER,k INTEGER,anchor INTEGER,scale TEXT,lo TEXT,hi TEXT,UNIQUE(p,k)"
    )
    ct = s.table("v INTEGER PRIMARY KEY,a INTEGER,b INTEGER,UNIQUE(a,b)")
    try:
        if kernel is None:
            s.db.execute(
                f"INSERT INTO {kt} SELECT p,k,j,value FROM kernel WHERE o=?", (o,)
            )
        else:
            for x in kernel:
                p, j = x["p"], x["j"]
                k = x.get("k")
                k = -1 if k is None else k
                if (
                    type(p) != int
                    or type(j) != int
                    or type(k) != int
                    or not 0 <= p < card["shape"][0]
                    or not 0 <= j < card["kernel_shape"][-1]
                    or (
                        k != -1
                        if card["axis_invariant"]
                        else not 0 <= k < card["shape"][1]
                    )
                ):
                    return [dict(reason="out-of-domain kernel entry")]
                s.db.execute(
                    f"INSERT INTO {kt} VALUES(?,?,?,?)", (p, k, j, str(F(x["value"])))
                )
        if rows is None:
            s.db.execute(
                f"INSERT INTO {bt} SELECT r,p,k,anchor,scale,lo,hi FROM bindings WHERE o=?",
                (o,),
            )
        else:
            for x in rows:
                s.db.execute(
                    f"INSERT INTO {bt} VALUES(?,?,?,?,?,?,?)",
                    (
                        _ref_id(s, x.get("row", x.get("row_id")), "row"),
                        x["p"],
                        x["k"],
                        _ref_id(s, x["anchor"], "var"),
                        str(x["scale"]),
                        None if x["lower"] is None else str(x["lower"]),
                        None if x["upper"] is None else str(x["upper"]),
                    ),
                )
        if coordinates is None:
            s.db.execute(
                f"INSERT INTO {ct} SELECT v,a,b FROM coordinates WHERE t=?", (t,)
            )
        else:
            for x in coordinates:
                s.db.execute(
                    f"INSERT INTO {ct} VALUES(?,?,?)",
                    (x["variable"], x["axis0"], x["axis1"]),
                )
        if s.db.execute(f"SELECT count(*) FROM {bt}").fetchone()[0] != card["rows"]:
            return [dict(reason="operator row coverage mismatch")]
        if (
            s.db.execute(
                f"SELECT p FROM {bt} GROUP BY p HAVING count(DISTINCT anchor)<>1 LIMIT 1"
            ).fetchone()
            or s.db.execute(
                f"SELECT anchor FROM {bt} GROUP BY anchor HAVING count(DISTINCT p)<>1 LIMIT 1"
            ).fetchone()
        ):
            return [dict(reason="anchor mapping mismatch")]
        for r, p, k, anchor, scale, lo, hi in s.db.execute(
            f"SELECT * FROM {bt} ORDER BY p,k"
        ):
            if (
                not 0 <= r < len(m.rows)
                or not 0 <= p < card["shape"][0]
                or not 0 <= k < card["shape"][1]
            ):
                return [dict(reason="row or index outside domain", row_id=r)]
            scale = F(scale)
            if not scale:
                return [dict(reason="zero scale", row_id=r)]
            s.db.execute(f"DELETE FROM {work}")
            s.db.execute(f"INSERT INTO {work} VALUES(?,?)", (anchor, str(scale)))
            # Resolve the sparse coordinates in SQLite as one indexed join,
            # rather than issuing several Python/SQL round trips per coefficient.
            match = "c.a=? AND c.b=e.j" if axis == 0 else "c.a=e.j AND c.b=?"
            expanded = f"FROM {kt} e LEFT JOIN {ct} c ON {match} WHERE e.p=? AND e.k=?"
            args = (k, p, -1 if card["axis_invariant"] else k)
            for (value,) in s.db.execute(
                f"SELECT e.value {expanded} AND c.v IS NULL", args
            ):
                if F(value):
                    return [dict(reason="kernel on missing coordinate", row_id=r)]

            def coefficients():
                for v, text in s.db.execute(
                    f"SELECT c.v,e.value {expanded} AND c.v IS NOT NULL", args
                ):
                    value = -F(text) * scale
                    if value:
                        # Both tables have unique coordinates, so the anchor
                        # is the only possible repeated variable in a row.
                        if v == anchor:
                            value += scale
                        yield v, str(value)

            s.db.executemany(
                f"INSERT OR REPLACE INTO {work} VALUES(?,?)", coefficients()
            )
            s.db.execute(f"DELETE FROM {work} WHERE value='0'")
            actual = m.iter_terms(r)
            expected = iter(s.db.execute(f"SELECT v,value FROM {work} ORDER BY v"))
            for v, c in actual:
                x = next(expected, None)
                if x is None or (v, c) != (x[0], F(x[1])):
                    return [dict(reason="coefficient mismatch", row_id=r, variable=v)]
            if next(expected, None) is not None:
                return [dict(reason="extra coefficient", row_id=r)]
            low = None if lo is None else F(lo) * scale
            high = None if hi is None else F(hi) * scale
            if scale < 0:
                low, high = high, low
            raw = m.rows[r]
            if (low, high) != (m.num(raw[2]), m.num(raw[3])):
                return [dict(reason="bound mismatch", row_id=r)]
        return []
    except (
        ValueError,
        KeyError,
        TypeError,
        __import__("sqlite3").IntegrityError,
    ) as error:
        return [dict(reason="invalid candidate", detail=str(error))]
    finally:
        for table in (work, kt, bt, ct):
            s.discard(table)


def kernel_stats(s, card):
    o = card["id"]
    np = card["shape"][0]
    nj = card["kernel_shape"][-1]
    total = np * nj * (1 if card["axis_invariant"] else card["shape"][1])
    count = s.db.execute("SELECT count(*) FROM kernel WHERE o=?", (o,)).fetchone()[0]
    low = high = None
    integer = True
    for (txt,) in s.db.execute("SELECT value FROM kernel WHERE o=?", (o,)):
        v = F(txt)
        low = v if low is None else min(low, v)
        high = v if high is None else max(high, v)
        integer &= v.denominator == 1
    if count < total:
        low = min(low or F(0), F(0))
        high = max(high or F(0), F(0))
    stats = dict(
        integer=integer,
        min=str(low or 0),
        max=str(high or 0),
        zeros=total - count,
        stored_nonzeros=count,
        implicit_zero=True,
    )
    if card["axis_invariant"] and np == nj:
        valid = True
        for p in range(np):
            mx = None
            which = None
            ties = 0
            n = 0
            for j, txt in s.db.execute(
                "SELECT j,value FROM kernel WHERE o=? AND p=? ORDER BY j", (o, p)
            ):
                v = F(txt)
                n += 1
                if mx is None or v > mx:
                    mx = v
                    which = j
                    ties = 1
                elif v == mx:
                    ties += 1
            missing = nj - n
            if missing and (mx is None or mx <= 0):
                if mx == 0:
                    ties += missing
                else:
                    mx = F(0)
                    ties = missing
                    which = None
                if ties == 1 and which is None:
                    # Stream the gap instead of constructing a dense vector.
                    which = 0
                    for (j,) in s.db.execute(
                        "SELECT j FROM kernel WHERE o=? AND p=? ORDER BY j", (o, p)
                    ):
                        if j != which:
                            break
                        which += 1
            if (
                ties != 1
                or s.db.execute(
                    "SELECT 1 FROM alignment WHERE o=? AND j=?", (o, which)
                ).fetchone()
            ):
                valid = False
                break
            s.db.execute("INSERT INTO alignment VALUES(?,?,?)", (o, p, which))
        if valid:
            symmetric = True
            diagonal = None
            constant = True
            for p, j, txt in s.db.execute(
                "SELECT p,j,value FROM kernel WHERE o=?", (o,)
            ):
                pi = s.db.execute(
                    "SELECT p FROM alignment WHERE o=? AND j=?", (o, j)
                ).fetchone()[0]
                pj = s.db.execute(
                    "SELECT j FROM alignment WHERE o=? AND p=?", (o, p)
                ).fetchone()[0]
                other = s.db.execute(
                    "SELECT value FROM kernel WHERE o=? AND p=? AND j=?", (o, pi, pj)
                ).fetchone()
                if F(txt) != F(other[0] if other else 0):
                    symmetric = False
            for p, j in s.db.execute(
                "SELECT p,j FROM alignment WHERE o=? ORDER BY p", (o,)
            ):
                value = s.db.execute(
                    "SELECT value FROM kernel WHERE o=? AND p=? AND j=?", (o, p, j)
                ).fetchone()
                v = F(value[0] if value else 0)
                if diagonal is None:
                    diagonal = v
                elif diagonal != v:
                    constant = False
            stats["alignment"] = dict(
                basis="unique row maximum bijection",
                symmetric_after_mapping=symmetric,
                diagonal_constant=str(diagonal) if constant else None,
            )
        else:
            s.db.execute("DELETE FROM alignment WHERE o=?", (o,))
    return stats


def _ref_id(s, value, axis):
    from mipframe.frame.schema import Ref
    from mipframe.frame.schema import SchemaError
    if isinstance(value, Ref):
        if (value.uid,value.version,value.kind)!=(s.owner.uid,s.owner.version,axis):
            raise SchemaError('Indexed record references another snapshot or axis')
        return value.id
    if type(value) is not int:
        raise SchemaError('Indexed identity requires an original integer ID or Ref')
    return value
