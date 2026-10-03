"""Exact set systems expressed as unique scalar membership relations."""

from mipframe.frame.schema import DType, SchemaError
from mipframe.ops._runtime import operation
from mipframe.store.algorithm_store import Membership
from mipframe.ops.grouping import classify, class_result
from mipframe.ops._kernels.union_find import DSU

RELATIONS = {
    "equal",
    "subset",
    "strict_subset",
    "superset",
    "strict_superset",
    "overlap",
    "disjoint",
}
MEMBER_OPS = {"intersection", "union", "left_only", "right_only"}


@operation("sets.equal_groups")
def equal_groups(ctx, S, *, set="set", member="member", value=None, sets=None):
    """
    MIPFRAME HELP BEGIN
    ops.sets.equal_groups [api.ops.sets.equal_groups]

    Classify exact equal supports or member-to-value maps.

    ops.sets.equal_groups(S, *, set='set', member='member', value=None, sets=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Classify exact equal supports or member-to-value maps.

    Inputs: Unique (set,member); optional value column and declared sets.

    Returns: Frame(set,class) with class the smallest original set key.

    Establishes: No hash-collision assumption. Equal support alone says nothing about row coefficients or bounds.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Failures: Duplicate set/member records, domain violations and resource limits fail the complete call.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.support, concept.resources
    MIPFRAME HELP END
    """
    system = Membership(ctx, S, set, member, sets=sets, value=value)
    classes = classify(ctx, system.sets, lambda s: system.stream(s, value is not None))
    return class_result(ctx, system.sets, classes, set)


@operation("sets.pairs")
def pairs(
    ctx,
    S,
    *,
    set="set",
    member="member",
    sets=None,
    left=None,
    right=None,
    candidates=None,
    relation="overlap",
    ordered=None,
    include_self=False,
):
    """
    MIPFRAME HELP BEGIN
    ops.sets.pairs [api.ops.sets.pairs]

    Compare exact set supports and their cardinalities.

    ops.sets.pairs(S, *, set='set', member='member', sets=None, left=None, right=None, candidates=None, relation='overlap', ordered=None, include_self=False, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Compare exact set supports and their cardinalities.

    Inputs: Unique (set,member); optional declared sets, left/right or directed candidates.

    Returns: Frame(left,right,left_n,right_n,intersection,left_only,right_only,union_n).

    Establishes: equal/subset/strict_subset/superset/strict_superset/overlap/disjoint. Empty sets participate. Symmetric same-domain pairs default unordered without self.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Failures: Duplicate set/member records, domain violations and resource limits fail the complete call.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.support, concept.resources
    MIPFRAME HELP END
    """
    if relation not in RELATIONS:
        raise ValueError("Unknown set relation")
    if (
        type(include_self) is not bool
        or ordered is not None
        and type(ordered) is not bool
    ):
        raise TypeError("ordered/include_self must be boolean")
    system = Membership(ctx, S, set, member, sets=sets)
    sym = relation in ("equal", "overlap", "disjoint")
    if candidates is not None:
        if left is not None or right is not None:
            raise ValueError("candidates and left/right are mutually exclusive")
        if ordered is False:
            raise ValueError("Explicit candidates retain their direction")
        work = system.pairs(candidates)
        pair_query = f"SELECT a,b FROM {work} ORDER BY a,b"
    else:
        l = system.sets.selection(left)
        r = system.sets.selection(right)
        same = (
            ctx.execute(
                f"SELECT 1 FROM (SELECT ordinal FROM {l} EXCEPT SELECT ordinal FROM {r}) LIMIT 1"
            ).fetchone()
            is None
            and ctx.execute(
                f"SELECT 1 FROM (SELECT ordinal FROM {r} EXCEPT SELECT ordinal FROM {l}) LIMIT 1"
            ).fetchone()
            is None
        )
        ordered = not (sym and same) if ordered is None else ordered
        if not ordered and (not sym or not same):
            raise ValueError(
                "Unordered pairs require a symmetric relation and identical domains"
            )
        conditions = []
        if not include_self:
            conditions.append("a!=b")
        if not ordered:
            conditions.append("a<=b" if include_self else "a<b")
        condition = " AND ".join(conditions) or "1"
        if relation == "equal":
            classes = classify(ctx, system.sets, system.stream)
            query = f"SELECT x.id AS a,y.id AS b FROM {classes} x JOIN {classes} y ON x.class=y.class JOIN {l} l ON l.ordinal=x.id JOIN {r} r ON r.ordinal=y.id"
            counts = ctx.raw_table(
                "class INTEGER PRIMARY KEY,l INTEGER DEFAULT 0,r INTEGER DEFAULT 0,both INTEGER DEFAULT 0"
            )
            for label, scope in (("l", l), ("r", r)):
                for c, n in ctx.execute(
                    f"SELECT c.class,count(*) FROM {classes} c JOIN {scope} d ON d.ordinal=c.id GROUP BY c.class"
                ):
                    ctx.execute(
                        f"INSERT INTO {counts}(class,{label}) VALUES(?,?) ON CONFLICT(class) DO UPDATE SET {label}=excluded.{label}",
                        (c, n),
                    )
            for c, n in ctx.execute(
                f"SELECT c.class,count(*) FROM {classes} c JOIN {l} l ON l.ordinal=c.id JOIN {r} r ON r.ordinal=c.id GROUP BY c.class"
            ):
                ctx.execute(f"UPDATE {counts} SET both=? WHERE class=?", (n, c))
            count = 0
            for nl, nr, shared in ctx.execute(f"SELECT l,r,both FROM {counts}"):
                count += (
                    (nl * nr - (0 if include_self else shared))
                    if ordered
                    else nl * (nl - 1) // 2 + (nl if include_self else 0)
                )
                ctx.enforce(count)
            pair_query = f"SELECT * FROM ({query}) WHERE {condition} ORDER BY a,b"
        elif relation == "overlap":
            work = ctx.raw_table("a INTEGER,b INTEGER,n INTEGER,PRIMARY KEY(a,b)")
            # The reverse index drives only observed member intersections.
            query = f"SELECT x.s AS a,y.s AS b FROM {system.edges} x JOIN {system.edges} y ON y.m=x.m JOIN {l} l ON l.ordinal=x.s JOIN {r} r ON r.ordinal=y.s"
            seen = 0
            for a, b in ctx.execute(f"SELECT * FROM ({query}) WHERE {condition}"):
                found = ctx.execute(
                    f"SELECT n FROM {work} WHERE a=? AND b=?", (a, b)
                ).fetchone()
                if found:
                    ctx.execute(f"UPDATE {work} SET n=n+1 WHERE a=? AND b=?", (a, b))
                else:
                    seen += 1
                    ctx.enforce(seen)
                    ctx.execute(f"INSERT INTO {work} VALUES(?,?,1)", (a, b))
            pair_query = f"SELECT a,b FROM {work} ORDER BY a,b"
            ctx.paths.add("inverted membership intersection accumulation")
        else:
            work = ctx.raw_table("a INTEGER,b INTEGER,PRIMARY KEY(a,b)")
            if relation == "disjoint":
                # Emit the complement row by row; never allocate the domain product.
                generated = (
                    (a, b)
                    for (a,) in ctx.execute(f"SELECT ordinal FROM {l} ORDER BY ordinal")
                    for (b,) in ctx.execute(
                        f"SELECT r.ordinal FROM {r} r WHERE NOT EXISTS(SELECT 1 FROM {system.edges} x JOIN {system.edges} y ON x.m=y.m WHERE x.s=? AND y.s=r.ordinal) ORDER BY r.ordinal",
                        (a,),
                    )
                )
                ctx.paths.add("indexed complement scan")
            else:
                reverse = relation in ("superset", "strict_superset")
                small, large = (r, l) if reverse else (l, r)
                strict = relation.startswith("strict_")

                def contains():
                    for (a,) in ctx.execute(
                        f"SELECT ordinal FROM {small} ORDER BY ordinal"
                    ):
                        n = system.size(a)
                        if n:
                            rare = ctx.execute(
                                f"SELECT e.m FROM {system.edges} e JOIN {system.frequency} f ON f.m=e.m WHERE e.s=? ORDER BY f.n,e.m LIMIT 1",
                                (a,),
                            ).fetchone()[0]
                            query = f"SELECT d.ordinal FROM {large} d JOIN {system.edges} e ON e.s=d.ordinal JOIN {system.sizes} z ON z.s=d.ordinal WHERE e.m=? AND z.n {'>' if strict else '>='} ? ORDER BY d.ordinal"
                            options = ctx.execute(query, (rare, n))
                        else:
                            options = ctx.execute(
                                f"SELECT d.ordinal FROM {large} d JOIN {system.sizes} z ON z.s=d.ordinal WHERE z.n {'>' if strict else '>='} 0 ORDER BY d.ordinal"
                            )
                        for (b,) in options:
                            if system.intersection(a, b) == n:
                                yield (b, a) if reverse else (a, b)

                generated = contains()
                ctx.paths.add("rarest-member candidate index + exact containment")
            count = 0
            for a, b in generated:
                if not include_self and a == b or not ordered and a > b:
                    continue
                count += 1
                ctx.enforce(count)
                ctx.execute(f"INSERT INTO {work} VALUES(?,?)", (a, b))
            pair_query = f"SELECT a,b FROM {work} ORDER BY a,b"
    schema = {
        "left": system.sets.typ,
        "right": system.sets.typ,
        **{
            n: DType("int")
            for n in (
                "left_n",
                "right_n",
                "intersection",
                "left_only",
                "right_only",
                "union_n",
            )
        },
    }

    def records():
        for a, b in ctx.execute(pair_query):
            if not include_self and a == b:
                continue
            an, bn, k = system.size(a), system.size(b), system.intersection(a, b)
            yes = {
                "equal": k == an == bn,
                "subset": k == an,
                "strict_subset": k == an and an < bn,
                "superset": k == bn,
                "strict_superset": k == bn and bn < an,
                "overlap": k > 0,
                "disjoint": k == 0,
            }[relation]
            if yes:
                yield dict(
                    left=system.sets.value(a),
                    right=system.sets.value(b),
                    left_n=an,
                    right_n=bn,
                    intersection=k,
                    left_only=an - k,
                    right_only=bn - k,
                    union_n=an + bn - k,
                )

    domain = system.sets.as_frame(set)
    return ctx.publish(
        schema,
        records(),
        key=("left", "right"),
        domains={"left": domain, "right": domain},
    )


def member_ids(system, a, b, op):
    """Two bounded sorted cursors, with no materialized Python set."""
    x = iter(system.stream(a))
    y = iter(system.stream(b))
    u = next(x, None)
    v = next(y, None)
    while u is not None or v is not None:
        if v is None or u is not None and u[0] < v[0]:
            if op in ("union", "left_only"):
                yield u[0]
            u = next(x, None)
        elif u is None or v[0] < u[0]:
            if op in ("union", "right_only"):
                yield v[0]
            v = next(y, None)
        else:
            if op in ("union", "intersection"):
                yield u[0]
            u = next(x, None)
            v = next(y, None)


@operation("sets.members")
def members(ctx, S, *, pairs, op="intersection", set="set", member="member", sets=None):
    """
    MIPFRAME HELP BEGIN
    ops.sets.members [api.ops.sets.members]

    Expand a specified pair relation into exact members.

    ops.sets.members(S, *, pairs, op='intersection', set='set', member='member', sets=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Expand a specified pair relation into exact members.

    Inputs: Unique membership and pairs(left,right).

    Returns: Frame(left,right,member).

    Establishes: intersection/union/left_only/right_only. Recomputes membership rather than trusting pair statistics.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Failures: Duplicate set/member records, domain violations and resource limits fail the complete call.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.support, concept.resources
    MIPFRAME HELP END
    """
    if op not in MEMBER_OPS:
        raise ValueError("Unknown member operation")
    system = Membership(ctx, S, set, member, sets=sets)
    work = system.pairs(pairs)

    def records():
        for a, b in ctx.execute(f"SELECT a,b FROM {work} ORDER BY a,b"):
            av, bv = system.sets.value(a), system.sets.value(b)
            for m in member_ids(system, a, b, op):
                yield dict(left=av, right=bv, member=system.members.value(m))

    domain = system.sets.as_frame(set)
    return ctx.publish(
        {
            "left": system.sets.typ,
            "right": system.sets.typ,
            "member": system.members.typ,
        },
        records(),
        key=("left", "right", "member"),
        domains={
            "left": domain,
            "right": domain,
            "member": system.members.as_frame(member),
        },
    )


@operation("sets.components")
def components(ctx, S, *, set="set", member="member", sets=None, vertices=None):
    """
    MIPFRAME HELP BEGIN
    ops.sets.components [api.ops.sets.components]

    Find hypergraph member components without clique expansion.

    ops.sets.components(S, *, set='set', member='member', sets=None, vertices=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Find hypergraph member components without clique expansion.

    Inputs: Unique membership; optional sets and full member vertices.

    Returns: Frame(member,component), retaining isolated declared members.

    Establishes: A structural component does not establish separability from excluded rows or the objective.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Failures: Duplicate set/member records, domain violations and resource limits fail the complete call.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.support, concept.resources
    MIPFRAME HELP END
    """
    if member == "component":
        raise SchemaError("Rename member to avoid the component column")
    system = Membership(ctx, S, set, member, sets=sets, vertices=vertices)
    ctx.enforce(system.members.n)
    dsu = DSU(system.members.n + 1, ctx)
    previous = None
    first = None
    for s, m in ctx.execute(f"SELECT s,m FROM {system.edges} ORDER BY s,m"):
        if s != previous:
            previous, first = s, m
        else:
            dsu.union(first, m)
    domain = system.members.as_frame(member)
    return ctx.publish(
        {member: system.members.typ, "component": system.members.typ},
        (
            {member: v, "component": system.members.value(dsu.representative(i))}
            for i, v in system.members.entries()
        ),
        key=(member,),
        domains={member: domain, "component": domain},
    )


@operation("sets.aggregate")
def aggregate(
    ctx,
    membership,
    values,
    *,
    set="set",
    member="member",
    by=(),
    value="value",
    agg="sum",
    sets=None,
):
    """
    MIPFRAME HELP BEGIN
    ops.sets.aggregate [api.ops.sets.aggregate]

    Sum exact member attributes per set and optional group.

    ops.sets.aggregate(membership, values, *, set='set', member='member', by=(), value='value', agg='sum', sets=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Sum exact member attributes per set and optional group.

    Inputs: membership(set,member), values unique on (member,*by).

    Returns: Frame(set,*by,value).

    Establishes: Finite exact values; absent attributes are sparse zero, explicit None fails. No-by empty sums are zero.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Failures: Duplicate set/member records, domain violations and resource limits fail the complete call.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.support, concept.resources
    MIPFRAME HELP END
    """
    from mipframe.ops._aggregate import aggregate as execute

    return execute(
        ctx,
        membership,
        values,
        set=set,
        member=member,
        by=by,
        value=value,
        agg=agg,
        sets=sets,
    )


@operation("sets.pair_aggregate")
def pair_aggregate(
    ctx,
    membership,
    pairs,
    values,
    *,
    set="set",
    member="member",
    op="union",
    by=(),
    value="value",
    agg="sum",
    sets=None,
):
    """
    MIPFRAME HELP BEGIN
    ops.sets.pair_aggregate [api.ops.sets.pair_aggregate]

    Aggregate members of pair unions/intersections/differences without double counting.

    ops.sets.pair_aggregate(membership, pairs, values, *, set='set', member='member', op='union', by=(), value='value', agg='sum', sets=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Aggregate members of pair unions/intersections/differences without double counting.

    Inputs: membership, pairs(left,right), values unique on (member,*by).

    Returns: Frame(left,right,*by,value).

    Establishes: Only sum; absent attributes are zero. by-groups are observed, including exact cancellations to zero.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Failures: Duplicate set/member records, domain violations and resource limits fail the complete call.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.support, concept.resources
    MIPFRAME HELP END
    """
    from mipframe.ops._aggregate import pair_aggregate as execute

    return execute(
        ctx,
        membership,
        pairs,
        values,
        set=set,
        member=member,
        op=op,
        by=by,
        value=value,
        agg=agg,
        sets=sets,
    )
