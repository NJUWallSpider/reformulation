"""Paged finite integer DFS shared by exhaustive discovery and certificate checking."""

from fractions import Fraction


def number(x):
    return float(x) if x in ("inf", "-inf") else Fraction(x)


def walk(ctx, bounds, constraints, coefficients, visit):
    """visit receives a SQL assignment table; False stops after a verified leaf.

    Bounds use consecutive 1-based v keys. No feasible assignment list is retained.
    """
    assignment = ctx.raw_table("v INTEGER PRIMARY KEY,x TEXT")
    n = ctx.execute(f"SELECT count(*) FROM {bounds}").fetchone()[0]

    def feasible():
        for r, lower, upper in ctx.execute(
            f"SELECT r,lo,hi FROM {constraints} ORDER BY r"
        ):
            low, high = Fraction(0), Fraction(0)
            for c, lo, hi, x in ctx.execute(
                f"SELECT c.c,b.lo,b.hi,a.x FROM {coefficients} c JOIN {bounds} b ON b.v=c.v LEFT JOIN {assignment} a ON a.v=c.v WHERE c.r=? ORDER BY c.v",
                (r,),
            ):
                c = Fraction(c)
                a, b = (int(lo), int(hi)) if x is None else (int(x), int(x))
                low += min(c * a, c * b)
                high += max(c * a, c * b)
                ctx.check_size((low, high))
            if (
                number(lower) > number(upper)
                or high < number(lower)
                or low > number(upper)
            ):
                return False
        return True

    cursors = ctx.raw_table("v INTEGER PRIMARY KEY,next TEXT")
    ctx.execute(f"INSERT INTO {cursors} SELECT v,lo FROM {bounds}")
    empty = ctx.execute(
        f"SELECT 1 FROM {bounds} WHERE lo COLLATE EXACT > hi COLLATE EXACT LIMIT 1"
    ).fetchone()
    ctx.tick()
    if empty or not feasible():
        return True
    depth = 1
    while depth > 0:
        if depth > n:
            if visit(assignment) is False:
                return False
            depth -= 1
            ctx.execute(f"DELETE FROM {assignment} WHERE v=?", (depth,))
            continue
        nxt, high = ctx.execute(
            f"SELECT c.next,b.hi FROM {cursors} c JOIN {bounds} b ON b.v=c.v WHERE c.v=?",
            (depth,),
        ).fetchone()
        x, high = int(nxt), int(high)
        if x > high:
            ctx.execute(
                f"UPDATE {cursors} SET next=(SELECT lo FROM {bounds} WHERE v=?) WHERE v=?",
                (depth, depth),
            )
            depth -= 1
            ctx.execute(f"DELETE FROM {assignment} WHERE v=?", (depth,))
            continue
        ctx.tick()
        ctx.check_size(x + 1)
        ctx.execute(f"UPDATE {cursors} SET next=? WHERE v=?", (str(x + 1), depth))
        ctx.execute(f"INSERT OR REPLACE INTO {assignment} VALUES(?,?)", (depth, str(x)))
        if feasible():
            depth += 1
        else:
            ctx.execute(f"DELETE FROM {assignment} WHERE v=?", (depth,))
    return True
