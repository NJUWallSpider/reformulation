"""Verified graph searches; candidate discovery does not prove a MIP inequality."""

from mipframe.ops._runtime import operation
from mipframe.store.search_store import Objects, SearchStopped, nonnegative
from mipframe.alg._graph import SearchGraph


def minimum(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


@operation("alg.graph.clique_candidates", search=True)
def clique_candidates(
    ctx,
    E,
    *,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    score=None,
    value="value",
    top=100,
    min_size=2,
    max_states=1_000_000,
    time_limit=None,
):
    """
    Deterministic greedy maximal cliques, ranked among the objects found.

    MIPFRAME HELP BEGIN
    Find greedy inclusion-maximal cliques, ranked among those found.

    Full contract: mf.help("api.alg.graph.clique_candidates")
    MIPFRAME HELP END
    """
    minimum(min_size, "min_size")
    nonnegative(top, "top")
    graph = SearchGraph(
        ctx, E, u, v, vertices, scope, directed=False, score=score, value=value
    )
    objects = Objects(ctx, graph, "clique", top)
    current = ctx.raw_table("v INTEGER PRIMARY KEY")
    available = ctx.raw_table("v INTEGER PRIMARY KEY")
    ctx.ready()
    try:
        for (root,) in ctx.execute(f"SELECT v FROM {graph.priority} ORDER BY rank"):
            ctx.tick()
            ctx.execute(f"DELETE FROM {current}")
            ctx.execute(f"DELETE FROM {available}")
            ctx.execute(f"INSERT INTO {current} VALUES(?)", (root,))
            ctx.execute(
                f"INSERT INTO {available} SELECT v FROM {graph.edges} WHERE u=?",
                (root,),
            )
            while (
                row := ctx.execute(
                    f"SELECT a.v FROM {available} a JOIN {graph.priority} p ON p.v=a.v ORDER BY p.rank LIMIT 1"
                ).fetchone()
            ) is not None:
                ctx.tick()
                x = row[0]
                ctx.execute(f"INSERT INTO {current} VALUES(?)", (x,))
                ctx.execute(
                    f"DELETE FROM {available} WHERE NOT EXISTS(SELECT 1 FROM {graph.edges} e WHERE e.u=? AND e.v={available}.v)",
                    (x,),
                )
            n = ctx.execute(f"SELECT count(*) FROM {current}").fetchone()[0]
            if n >= min_size:
                graph.verify_clique(current)
                objects.add(
                    r[0] for r in ctx.execute(f"SELECT v FROM {current} ORDER BY v")
                )
    except SearchStopped:
        pass
    return objects.publish(
        f"greedy maximal cliques; min_size={min_size}; ignored self-loops={graph.ignored_loops}"
    )


@operation("alg.graph.maximal_cliques", search=True)
def maximal_cliques(
    ctx,
    E,
    *,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    min_size=2,
    max_states=None,
    time_limit=None,
):
    """
    All inclusion-maximal cliques in the induced scope, or an exception.

    MIPFRAME HELP BEGIN
    Enumerate every inclusion-maximal clique in the declared scope.

    Full contract: mf.help("api.alg.graph.maximal_cliques")
    MIPFRAME HELP END
    """
    minimum(min_size, "min_size")
    graph = SearchGraph(ctx, E, u, v, vertices, scope, directed=False)
    objects = Objects(ctx, graph, "clique")
    # Bron--Kerbosch state is entirely paged, including suspended P/X sets.
    sets = ctx.raw_table("d INTEGER,v INTEGER,k INTEGER,PRIMARY KEY(d,v)")
    branches = ctx.raw_table("d INTEGER,v INTEGER,PRIMARY KEY(d,v)")
    current = ctx.raw_table("d INTEGER PRIMARY KEY,v INTEGER UNIQUE")
    ctx.execute(f"INSERT INTO {sets} SELECT 0,ordinal,0 FROM {graph.active}")
    depth, entering = 0, True
    ctx.ready()
    while depth >= 0:
        if entering:
            ctx.tick()
            count = ctx.execute(
                f"SELECT count(*) FROM {sets} WHERE d=?", (depth,)
            ).fetchone()[0]
            if count == 0:
                if depth >= min_size:
                    graph.verify_clique(current)
                    objects.add(
                        r[0] for r in ctx.execute(f"SELECT v FROM {current} ORDER BY v")
                    )
            else:
                pivot = ctx.execute(
                    f"SELECT s.v FROM {sets} s LEFT JOIN {graph.edges} e ON e.u=s.v AND e.v IN (SELECT v FROM {sets} WHERE d=? AND k=0) WHERE s.d=? GROUP BY s.v ORDER BY count(e.v) DESC,s.v LIMIT 1",
                    (depth, depth),
                ).fetchone()[0]
                ctx.execute(
                    f"INSERT INTO {branches} SELECT d,v FROM {sets} s WHERE d=? AND k=0 AND NOT EXISTS(SELECT 1 FROM {graph.edges} e WHERE e.u=? AND e.v=s.v)",
                    (depth, pivot),
                )
        next_ = ctx.execute(
            f"SELECT v FROM {branches} WHERE d=? ORDER BY v LIMIT 1", (depth,)
        ).fetchone()
        if next_ is None:
            ctx.execute(f"DELETE FROM {sets} WHERE d=?", (depth,))
            ctx.execute(f"DELETE FROM {current} WHERE d=?", (depth,))
            depth -= 1
            entering = False
            continue
        x = next_[0]
        ctx.execute(f"DELETE FROM {branches} WHERE d=? AND v=?", (depth, x))
        ctx.execute(f"UPDATE {sets} SET k=1 WHERE d=? AND v=?", (depth, x))
        ctx.execute(
            f"INSERT INTO {sets} SELECT ?,s.v,s.k FROM {sets} s JOIN {graph.edges} e ON e.u=? AND e.v=s.v WHERE s.d=?",
            (depth + 1, x, depth),
        )
        depth += 1
        ctx.execute(f"INSERT INTO {current} VALUES(?,?)", (depth, x))
        entering = True
    return objects.publish(
        f"all maximal cliques; min_size={min_size}; ignored self-loops={graph.ignored_loops}"
    )


def cycles(ctx, graph, objects, *, minimum_length, maximum_length, lengths, chordless):
    path = ctx.raw_table("p INTEGER PRIMARY KEY,v INTEGER UNIQUE,last INTEGER")
    # Only strongly connected components can contain a directed cycle. The same
    # index gives connected components for the symmetric undirected adjacency.
    from types import SimpleNamespace
    from mipframe.ops._kernels.graph import scc

    selected = SimpleNamespace(
        entries=lambda: (
            (r[0], None)
            for r in ctx.execute(f"SELECT ordinal FROM {graph.active} ORDER BY ordinal")
        )
    )
    labels = scc(
        SimpleNamespace(
            ctx=ctx, vertices=selected, edges=graph.edges, neighbors=graph.neighbors
        )
    )
    ctx.ready()
    for (root,) in ctx.execute(f"SELECT v FROM {graph.priority} ORDER BY rank"):
        component = ctx.execute(
            f"SELECT component FROM {labels} WHERE v=?", (root,)
        ).fetchone()[0]
        if (
            ctx.execute(
                f"SELECT count(*) FROM {labels} WHERE component=?", (component,)
            ).fetchone()[0]
            < minimum_length
        ):
            continue
        ctx.tick()
        ctx.execute(f"INSERT INTO {path} VALUES(0,?,0)", (root,))
        depth = 0
        while depth >= 0:
            node, last = ctx.execute(
                f"SELECT v,last FROM {path} WHERE p=?", (depth,)
            ).fetchone()
            nxt = ctx.execute(
                f"SELECT e.v,p.rank FROM {graph.edges} e JOIN {graph.priority} p ON p.v=e.v JOIN {labels} l ON l.v=e.v WHERE e.u=? AND p.rank>? AND e.v>=? AND l.component=? ORDER BY p.rank LIMIT 1",
                (node, last, root, component),
            ).fetchone()
            if nxt is None:
                ctx.execute(f"DELETE FROM {path} WHERE p=?", (depth,))
                depth -= 1
                continue
            x, rank = nxt
            ctx.tick()
            ctx.execute(f"UPDATE {path} SET last=? WHERE p=?", (rank, depth))
            n = depth + 1
            if x == root:
                if (
                    n < minimum_length
                    or maximum_length is not None
                    and n > maximum_length
                    or lengths is not None
                    and n not in lengths
                ):
                    continue
                if (
                    not graph.directed
                    and ctx.execute(f"SELECT v FROM {path} WHERE p=1").fetchone()[0]
                    > node
                ):
                    continue
                if graph.verify_cycle(path, n, chordless):
                    objects.add(
                        r[0] for r in ctx.execute(f"SELECT v FROM {path} ORDER BY p")
                    )
            elif (maximum_length is None or n < maximum_length) and not ctx.execute(
                f"SELECT 1 FROM {path} WHERE v=?", (x,)
            ).fetchone():
                depth += 1
                ctx.execute(f"INSERT INTO {path} VALUES(?,?,0)", (depth, x))


@operation("alg.graph.odd_cycle_candidates", search=True)
def odd_cycle_candidates(
    ctx,
    E,
    *,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    lengths=(5, 7, 9),
    chordless=True,
    score=None,
    value="value",
    top=100,
    max_states=1_000_000,
    time_limit=None,
):
    """
    MIPFRAME HELP BEGIN
    Find verified undirected odd cycles of specified lengths.

    Full contract: mf.help("api.alg.graph.odd_cycle_candidates")
    MIPFRAME HELP END
    """
    nonnegative(top, "top")
    if (
        not isinstance(lengths, (tuple, list))
        or not lengths
        or any(type(n) is not int or n < 3 or n % 2 == 0 for n in lengths)
    ):
        raise ValueError("lengths must contain odd integers >= 3")
    if type(chordless) is not bool:
        raise TypeError("chordless must be boolean")
    lengths = tuple(sorted(set(lengths)))
    graph = SearchGraph(
        ctx, E, u, v, vertices, scope, directed=False, score=score, value=value
    )
    objects = Objects(ctx, graph, "cycle", top)
    try:
        cycles(
            ctx,
            graph,
            objects,
            minimum_length=min(lengths),
            maximum_length=max(lengths),
            lengths=lengths,
            chordless=chordless,
        )
    except SearchStopped:
        pass
    return objects.publish(
        f"odd cycles; lengths={lengths}; chordless={chordless}; ignored self-loops={graph.ignored_loops}"
    )


@operation("alg.graph.simple_cycles", search=True)
def simple_cycles(
    ctx,
    E,
    *,
    directed,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    min_length=None,
    max_length=None,
    chordless=False,
    max_states=None,
    time_limit=None,
):
    """
    MIPFRAME HELP BEGIN
    Enumerate all simple cycles in an explicitly directed or undirected graph.

    Full contract: mf.help("api.alg.graph.simple_cycles")
    MIPFRAME HELP END
    """
    if type(directed) is not bool or type(chordless) is not bool:
        raise TypeError("directed and chordless must be boolean")
    if directed and chordless:
        raise ValueError("chordless=True is only supported for undirected graphs")
    min_length = (1 if directed else 3) if min_length is None else min_length
    minimum(min_length, "min_length")
    if not directed and min_length < 3:
        raise ValueError("Undirected cycles have length >= 3")
    if max_length is not None:
        minimum(max_length, "max_length")
        if max_length < min_length:
            raise ValueError("max_length must be >= min_length")
    graph = SearchGraph(ctx, E, u, v, vertices, scope, directed=directed)
    objects = Objects(ctx, graph, "cycle")
    cycles(
        ctx,
        graph,
        objects,
        minimum_length=min_length,
        maximum_length=max_length,
        lengths=None,
        chordless=chordless,
    )
    return objects.publish(
        f"simple cycles; directed={directed}; lengths={min_length}..{max_length}; chordless={chordless}"
    )


from mipframe.alg._isomorphism import isomorphism_classes
