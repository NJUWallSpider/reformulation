"""Exact additive attributes, without materializing pair-by-union membership."""

from fractions import Fraction
from mipframe.frame.schema import DType, SchemaError
from mipframe.frame.executor import unpack
from mipframe.store.algorithm_store import Membership
from mipframe.ops.sets import MEMBER_OPS


class Values:
    def __init__(self, ctx, system, frame, by, value):
        member = system.member_name
        self.by = (by,) if isinstance(by, str) else tuple(by)
        names = (member, *self.by, value)
        table, schema = ctx.stage_checked(
            frame, names, unique=(member, *self.by), nonnull=(member,)
        )
        if not system.members.typ.compatible(schema[member]):
            raise SchemaError("Attribute member identity differs")
        if schema[value].kind not in ("int", "number"):
            raise SchemaError("Attribute values must be exact numbers")
        field = len(names) - 1
        invalid = (
            f"SELECT "
            + ",".join(f"c{i}" for i in range(len(names)))
            + f" FROM {table} WHERE c{field} IS NULL OR c{field} IN ('inf','-inf')"
        )
        if ctx.execute(f"SELECT 1 FROM ({invalid}) LIMIT 1").fetchone():
            ctx.invalid(
                "Attribute values must be finite and nonmissing",
                schema,
                ctx.rows(invalid, schema),
            )
        self.schema = {n: schema[n] for n in self.by}
        self.groups = ctx.table(self.schema)
        if self.by:
            cols = ",".join(f"c{i + 1}" for i in range(len(self.by)))
            dest = ",".join(f"c{i}" for i in range(len(self.by)))
            ctx.execute(
                f"INSERT INTO {self.groups}({dest}) SELECT DISTINCT {cols} FROM {table} ORDER BY {cols}"
            )
            ctx.execute(f"CREATE INDEX {self.groups}_keys ON {self.groups}({dest})")
            match = " AND ".join(f"g.c{i} IS v.c{i + 1}" for i in range(len(self.by)))
        else:
            ctx.execute(f"INSERT INTO {self.groups} DEFAULT VALUES")
            match = "1"
        self.table = ctx.raw_table(
            "m INTEGER,g INTEGER,value TEXT COLLATE EXACT,PRIMARY KEY(m,g)"
        )
        ctx.execute(
            f"INSERT INTO {self.table} SELECT m.ordinal,g.ordinal,v.c{field} FROM {table} v JOIN {system.members.table} m ON m.c0=v.c0 JOIN {self.groups} g ON {match}"
        )
        self.cache = ctx.raw_table(
            "s INTEGER,g INTEGER,n INTEGER,value TEXT COLLATE EXACT,PRIMARY KEY(s,g)"
        )
        ctx.execute(
            f"INSERT INTO {self.cache} SELECT e.s,v.g,count(*),exact_sum(v.value) FROM {system.edges} e JOIN {self.table} v ON v.m=e.m GROUP BY e.s,v.g"
        )
        self.ctx = ctx
        ctx.paths.add("indexed exact attribute sums + presence counts")

    def group(self, g):
        columns = ",".join(f"c{i}" for i in range(len(self.by))) or "1"
        return next(
            self.ctx.rows(
                f"SELECT {columns} FROM {self.groups} WHERE ordinal=?",
                self.schema,
                (g,),
            )
        )

    def stats(self, s, g):
        row = self.ctx.execute(
            f"SELECT n,value FROM {self.cache} WHERE s=? AND g=?", (s, g)
        ).fetchone()
        return (0, Fraction(0)) if row is None else (row[0], Fraction(row[1]))


def aggregate(ctx, membership, values, *, set, member, by, value, agg, sets):
    if agg != "sum":
        raise ValueError("Only exact sum is supported")
    system = Membership(ctx, membership, set, member, sets=sets)
    weights = Values(ctx, system, values, by, value)
    if set in (*weights.by, "value") or "value" in weights.by:
        raise SchemaError("Rename colliding output columns")
    schema = {set: system.sets.typ, **weights.schema, "value": DType("number")}

    def records():
        for s, sv in system.sets.entries():
            if not weights.by:
                yield {set: sv, "value": weights.stats(s, 1)[1]}
            else:
                for g, number in ctx.execute(
                    f"SELECT g,value FROM {weights.cache} WHERE s=? ORDER BY g", (s,)
                ):
                    yield {set: sv, **weights.group(g), "value": Fraction(number)}

    return ctx.publish(
        schema,
        records(),
        key=(set, *weights.by),
        domains={set: system.sets.as_frame(set)},
    )


def pair_aggregate(
    ctx, membership, pairs, values, *, set, member, op, by, value, agg, sets
):
    if agg != "sum":
        raise ValueError("Only exact sum is supported")
    if op not in MEMBER_OPS:
        raise ValueError("Unknown pair operation")
    system = Membership(ctx, membership, set, member, sets=sets)
    work = system.pairs(pairs)
    weights = Values(ctx, system, values, by, value)
    if any(n in ("left", "right", "value") for n in weights.by):
        raise SchemaError("Rename colliding output columns")
    common = ctx.raw_table("g INTEGER PRIMARY KEY,n INTEGER,value TEXT COLLATE EXACT")
    schema = {
        "left": system.sets.typ,
        "right": system.sets.typ,
        **weights.schema,
        "value": DType("number"),
    }

    def records():
        for a, b in ctx.execute(f"SELECT a,b FROM {work} ORDER BY a,b"):
            ctx.execute(f"DELETE FROM {common}")
            ctx.execute(
                f"INSERT INTO {common} SELECT v.g,count(*),exact_sum(v.value) FROM {system.edges} a JOIN {system.edges} b ON a.m=b.m JOIN {weights.table} v ON v.m=a.m WHERE a.s=? AND b.s=? GROUP BY v.g",
                (a, b),
            )
            groups = (
                [(1,)]
                if not weights.by
                else ctx.execute(
                    f"SELECT g FROM {weights.cache} WHERE s=? UNION SELECT g FROM {weights.cache} WHERE s=? ORDER BY g",
                    (a, b),
                )
            )
            av, bv = system.sets.value(a), system.sets.value(b)
            for (g,) in groups:
                na, va = weights.stats(a, g)
                nb, vb = weights.stats(b, g)
                c = ctx.execute(
                    f"SELECT n,value FROM {common} WHERE g=?", (g,)
                ).fetchone()
                nc, vc = (0, Fraction(0)) if c is None else (c[0], Fraction(c[1]))
                n, v = {
                    "union": (na + nb - nc, va + vb - vc),
                    "intersection": (nc, vc),
                    "left_only": (na - nc, va - vc),
                    "right_only": (nb - nc, vb - vc),
                }[op]
                if n or not weights.by:
                    yield dict(left=av, right=bv, **weights.group(g), value=v)

    domain = system.sets.as_frame(set)
    ctx.paths.add("additive inclusion-exclusion; pair intersections only")
    return ctx.publish(
        schema,
        records(),
        key=("left", "right", *weights.by),
        domains={"left": domain, "right": domain},
    )
