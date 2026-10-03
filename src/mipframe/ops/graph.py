"""Complete deterministic graph facts over scalar Frame endpoints."""

from types import SimpleNamespace
from sys import int_info
from mipframe.frame.schema import DType, normalize, SchemaError
from mipframe.store.algorithm_store import KeyIndex
from mipframe.ops._runtime import operation
from mipframe.ops._kernels.union_find import DSU
from mipframe.ops._kernels.graph import (
    Graph,
    scc as _scc,
    topological,
    search,
    path_edges,
)
from mipframe.check.algorithm import NotDAGError


@operation("graph.components")
def components(ctx, E, *, u="u", v="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    ops.graph.components [api.ops.graph.components]

    Compute undirected components of an endpoint relation.

    ops.graph.components(E, *, u='u', v='v', vertices=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Compute undirected components of an endpoint relation.

    Inputs: Frame with compatible nonmissing endpoint columns; optional declared vertices.

    Returns: Frame(vertex,component).

    Establishes: Includes isolated declared vertices.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.network, recipe.closure
    MIPFRAME HELP END
    """
    graph = Graph(ctx, E, u, v, vertices)
    dsu = DSU(graph.vertices.n + 1, ctx)
    for a, b in ctx.execute(f"SELECT u,v FROM {graph.edges} ORDER BY u,v"):
        dsu.union(a, b)
    domain = graph.vertices.as_frame("vertex")
    return ctx.publish(
        {"vertex": graph.vertices.typ, "component": graph.vertices.typ},
        (
            dict(vertex=value, component=graph.vertices.value(dsu.representative(i)))
            for i, value in graph.vertices.entries()
        ),
        key=("vertex",),
        domains={"vertex": domain, "component": domain},
    )


@operation("graph.scc")
def scc(ctx, E, *, tail="u", head="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    ops.graph.scc [api.ops.graph.scc]

    Compute directed strongly connected components.

    ops.graph.scc(E, *, tail='u', head='v', vertices=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Compute directed strongly connected components.

    Inputs: Frame with compatible nonmissing endpoint columns; optional declared vertices.

    Returns: Frame(vertex,scc).

    Establishes: Nonrecursive traversal; representative is minimum original key.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.network, recipe.closure
    MIPFRAME HELP END
    """
    graph = Graph(ctx, E, tail, head, vertices)
    labels = _scc(graph)
    domain = graph.vertices.as_frame("vertex")
    return ctx.publish(
        {"vertex": graph.vertices.typ, "scc": graph.vertices.typ},
        (
            dict(vertex=graph.vertices.value(v), scc=graph.vertices.value(c))
            for v, c in ctx.execute(f"SELECT v,component FROM {labels} ORDER BY v")
        ),
        key=("vertex",),
        domains={"vertex": domain, "scc": domain},
    )


@operation("graph.topological_order")
def topological_order(ctx, E, *, tail="u", head="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    ops.graph.topological_order [api.ops.graph.topological_order]

    Order a directed acyclic graph deterministically.

    ops.graph.topological_order(E, *, tail='u', head='v', vertices=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Order a directed acyclic graph deterministically.

    Inputs: Frame with compatible nonmissing endpoint columns; optional declared vertices.

    Returns: Frame(vertex,order,level).

    Establishes: Smallest available vertex first; level is longest predecessor depth. Cycles raise GraphCycleError with full SCC evidence.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.network, recipe.closure
    MIPFRAME HELP END
    """
    graph = Graph(ctx, E, tail, head, vertices)
    order = topological(graph)
    return ctx.publish(
        {"vertex": graph.vertices.typ, "order": DType("int"), "level": DType("int")},
        (
            dict(vertex=graph.vertices.value(v), order=p, level=l)
            for v, p, l in ctx.execute(
                f"SELECT v,position,level FROM {order} ORDER BY position"
            )
        ),
        key=("vertex",),
        domains={"vertex": graph.vertices.as_frame("vertex")},
    )


def closure_masks(graph):
    """Use SCC bitset DP only when its complete resident estimate fits."""
    ctx = graph.ctx
    labels = _scc(graph)
    index = KeyIndex(ctx, DType("int"), f"SELECT component AS value FROM {labels}")
    k = index.n
    estimate = (k + 4) * (
        64
        + ((k + int_info.bits_per_digit - 1) // int_info.bits_per_digit)
        * int_info.sizeof_digit
    )
    if not ctx.reserve(estimate):
        return None
    edges = ctx.raw_table("u INTEGER,v INTEGER,PRIMARY KEY(u,v)")
    ctx.execute(
        f"INSERT OR IGNORE INTO {edges} SELECT a.ordinal,b.ordinal FROM {graph.edges} e JOIN {labels} l ON l.v=e.u JOIN {labels} r ON r.v=e.v JOIN {index.table} a ON a.c0=CAST(l.component AS TEXT) JOIN {index.table} b ON b.c0=CAST(r.component AS TEXT) WHERE a.ordinal!=b.ordinal"
    )
    ctx.execute(f"CREATE INDEX {edges}_reverse ON {edges}(v,u)")
    dag = SimpleNamespace(ctx=ctx, edges=edges, vertices=index)
    dag.neighbors = lambda v: (
        row[0]
        for row in ctx.execute(f"SELECT v FROM {edges} WHERE u=? ORDER BY v", (v,))
    )
    order = topological(dag)
    masks = [0] * (k + 1)
    for (v,) in ctx.execute(f"SELECT v FROM {order} ORDER BY position DESC"):
        mask = 1 << (v - 1)
        for n in dag.neighbors(v):
            mask |= masks[n]
        masks[v] = mask
    ctx.paths.add("SCC condensation + budgeted bitset closure")
    return labels, index, masks


@operation("graph.reachability")
def reachability(
    ctx,
    E,
    *,
    tail="u",
    head="v",
    vertices=None,
    sources=None,
    targets=None,
    include_self=False,
):
    """
    MIPFRAME HELP BEGIN
    ops.graph.reachability [api.ops.graph.reachability]

    Compute exact reachable endpoint pairs.

    ops.graph.reachability(E, *, tail='u', head='v', vertices=None, sources=None, targets=None, include_self=False, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Compute exact reachable endpoint pairs.

    Inputs: Frame with compatible nonmissing endpoint columns; optional declared vertices.

    Returns: Frame(source,target).

    Establishes: sources/targets restrict returned endpoints, not intermediate vertices. include_self=False excludes all diagonals even in cycles.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.network, recipe.closure
    MIPFRAME HELP END
    """
    if type(include_self) is not bool:
        raise TypeError("include_self must be boolean")
    graph = Graph(ctx, E, tail, head, vertices)
    src = graph.vertices.selection(sources)
    dst = graph.vertices.selection(targets)
    out = ctx.raw_table("u INTEGER,v INTEGER,PRIMARY KEY(u,v)")
    count = 0

    def emit(u, v):
        nonlocal count
        if not include_self and u == v:
            return
        count += 1
        ctx.enforce(count)
        ctx.execute(f"INSERT INTO {out} VALUES(?,?)", (u, v))

    masks = closure_masks(graph) if sources is None and targets is None else None
    if masks is not None:
        labels, index, bitsets = masks
        for (u,) in ctx.execute(f"SELECT ordinal FROM {src} ORDER BY ordinal"):
            component = ctx.execute(
                f"SELECT component FROM {labels} WHERE v=?", (u,)
            ).fetchone()[0]
            mask = bitsets[index.lookup(component)]
            while mask:
                bit = mask & -mask
                code = bit.bit_length()
                mask -= bit
                component = index.value(code)
                for (v,) in ctx.execute(
                    f"SELECT v FROM {labels} WHERE component=? ORDER BY v", (component,)
                ):
                    emit(u, v)
    else:
        seen = None
        ctx.paths.add("source-target search with paged visited state")
        # No all-pairs closure is computed for an explicitly targeted query.
        if ctx.execute(f"SELECT 1 FROM {dst} LIMIT 1").fetchone():
            for (u,) in ctx.execute(f"SELECT ordinal FROM {src} ORDER BY ordinal"):
                seen = search(graph, u, seen=seen)
                for (v,) in ctx.execute(
                    f"SELECT s.v FROM {seen} s JOIN {dst} d ON d.ordinal=s.v ORDER BY s.v"
                ):
                    emit(u, v)
    domain = graph.vertices.as_frame("vertex")
    return ctx.publish(
        {"source": graph.vertices.typ, "target": graph.vertices.typ},
        (
            dict(source=graph.vertices.value(u), target=graph.vertices.value(v))
            for u, v in ctx.execute(f"SELECT u,v FROM {out} ORDER BY u,v")
        ),
        key=("source", "target"),
        domains={"source": domain, "target": domain},
    )


@operation("graph.path")
def path(ctx, E, *, source, target, tail="u", head="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    ops.graph.path [api.ops.graph.path]

    Return one stable shortest directed path.

    ops.graph.path(E, *, source, target, tail='u', head='v', vertices=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Return one stable shortest directed path.

    Inputs: Frame with compatible nonmissing endpoint columns; optional declared vertices.

    Returns: Frame(step,u,v), plus relations["status"].

    Establishes: Unreachable and source==target both have empty edge paths; status distinguishes unreachable from length zero.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.network, recipe.closure
    MIPFRAME HELP END
    """
    graph = Graph(ctx, E, tail, head, vertices)
    source = normalize(source, graph.vertices.typ)
    target = normalize(target, graph.vertices.typ)
    a, b = graph.vertices.lookup(source), graph.vertices.lookup(target)
    seen = search(graph, a, target=b)
    found = ctx.execute(f"SELECT depth FROM {seen} WHERE v=?", (b,)).fetchone()
    status = ctx.publish(
        {
            "source": graph.vertices.typ,
            "target": graph.vertices.typ,
            "reachable": DType("bool"),
            "length": DType("int", True),
        },
        [
            dict(
                source=source,
                target=target,
                reachable=found is not None,
                length=None if found is None else found[0],
            )
        ],
        key=("source", "target"),
        limited=False,
    )
    domain = graph.vertices.as_frame("vertex")
    return ctx.publish(
        {"step": DType("int"), "u": graph.vertices.typ, "v": graph.vertices.typ},
        (
            dict(step=step, u=graph.vertices.value(u), v=graph.vertices.value(v))
            for step, (u, v) in enumerate(path_edges(graph, seen, b))
        ),
        key=("step",),
        relations={"status": status},
        domains={"u": domain, "v": domain},
    )


@operation("graph.transitive_reduction")
def transitive_reduction(ctx, E, *, tail="u", head="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    ops.graph.transitive_reduction [api.ops.graph.transitive_reduction]

    Reduce a DAG while preserving reachability.

    ops.graph.transitive_reduction(E, *, tail='u', head='v', vertices=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Reduce a DAG while preserving reachability.

    Inputs: Frame with compatible nonmissing endpoint columns; optional declared vertices.

    Returns: Original input schema and all parallel source records for retained endpoint pairs.

    Establishes: Rejects cycles/self-loops. This is not a proof that original constraints can be deleted.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit.

    See also: recipe.network, recipe.closure
    MIPFRAME HELP END
    """
    graph = Graph(ctx, E, tail, head, vertices)
    order = topological(graph, error=NotDAGError)
    keep = ctx.raw_table("u INTEGER,v INTEGER,PRIMARY KEY(u,v)")
    covered = ctx.raw_table("v INTEGER PRIMARY KEY")
    queue = ctx.raw_table("seq INTEGER PRIMARY KEY,v INTEGER")
    for u, _ in graph.vertices.entries():
        ctx.execute(f"DELETE FROM {covered}")
        for (v,) in ctx.execute(
            f"SELECT e.v FROM {graph.edges} e JOIN {order} o ON o.v=e.v WHERE e.u=? ORDER BY o.position",
            (u,),
        ):
            if ctx.execute(f"SELECT 1 FROM {covered} WHERE v=?", (v,)).fetchone():
                continue
            ctx.execute(f"INSERT INTO {keep} VALUES(?,?)", (u, v))
            ctx.execute(f"DELETE FROM {queue}")
            ctx.execute(f"INSERT INTO {queue}(v) VALUES(?)", (v,))
            ctx.execute(f"INSERT INTO {covered} VALUES(?)", (v,))
            pos = 0
            while True:
                row = ctx.execute(
                    f"SELECT seq,v FROM {queue} WHERE seq>? ORDER BY seq LIMIT 1",
                    (pos,),
                ).fetchone()
                if row is None:
                    break
                pos, node = row
                for n in graph.neighbors(node):
                    if ctx.execute(
                        f"SELECT 1 FROM {covered} WHERE v=?", (n,)
                    ).fetchone():
                        continue
                    ctx.execute(f"INSERT INTO {covered} VALUES(?)", (n,))
                    ctx.execute(f"INSERT INTO {queue}(v) VALUES(?)", (n,))
    ctx.paths.add("topological successor coverage; original edge records retained")

    def records():
        for row in E._raw(tuple(E.schema), ctx):
            a, b = graph.vertices.lookup(row[tail]), graph.vertices.lookup(row[head])
            if ctx.execute(
                f"SELECT 1 FROM {keep} WHERE u=? AND v=?", (a, b)
            ).fetchone():
                yield row

    return ctx.publish(dict(E.schema), records(), key=E.key, domains=E.domains)
