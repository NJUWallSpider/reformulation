"""Exact whole-graph classes: refinement proposes, bijections establish identity."""

import hashlib
from mipframe.ops._runtime import operation
from mipframe.store.algorithm_store import KeyIndex
from mipframe.frame.schema import DType, SchemaError


def labels_tuple(value):
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(x, str) for x in value
    ):
        raise TypeError("Labels must be column names")
    return tuple(value)


def refine(ctx, nodes, edges):
    """Refine across all graph instances; exact token comparison resolves hash collisions."""
    features = ctx.raw_table(
        "g INTEGER,v INTEGER,d INTEGER,c INTEGER,l INTEGER,n INTEGER,PRIMARY KEY(g,v,d,c,l)"
    )
    hashes = ctx.raw_table("old INTEGER,digest TEXT,g INTEGER,v INTEGER,color INTEGER")
    ctx.execute(f"CREATE INDEX {hashes}_bucket ON {hashes}(old,digest)")
    previous = ctx.execute(f"SELECT count(DISTINCT color) FROM {nodes}").fetchone()[0]
    while True:
        ctx.execute(f"DELETE FROM {features}")
        ctx.execute(f"DELETE FROM {hashes}")
        for direction, left, right in ((0, "u", "v"), (1, "v", "u")):
            ctx.execute(
                f"INSERT INTO {features} SELECT e.g,e.{left},?,n.color,e.label,count(*) FROM {edges} e JOIN {nodes} n ON n.g=e.g AND n.v=e.{right} GROUP BY e.g,e.{left},n.color,e.label",
                (direction,),
            )
        colors = 0
        for g, v, old in ctx.execute(f"SELECT g,v,color FROM {nodes} ORDER BY g,v"):
            ctx.tick()
            h = hashlib.sha256()
            for feature in ctx.execute(
                f"SELECT d,c,l,n FROM {features} WHERE g=? AND v=? ORDER BY d,c,l",
                (g, v),
            ):
                h.update(repr(feature).encode())
            digest = h.hexdigest()
            color = None
            for rg, rv, rc in ctx.execute(
                f"SELECT g,v,color FROM {hashes} WHERE old=? AND digest=?",
                (old, digest),
            ):
                query = f"SELECT d,c,l,n FROM {features} WHERE g=? AND v=? EXCEPT SELECT d,c,l,n FROM {features} WHERE g=? AND v=?"
                if (
                    ctx.execute(query, (g, v, rg, rv)).fetchone() is None
                    and ctx.execute(query, (rg, rv, g, v)).fetchone() is None
                ):
                    color = rc
                    break
            if color is None:
                colors += 1
                color = colors
                ctx.execute(
                    f"INSERT INTO {hashes} VALUES(?,?,?,?,?)",
                    (old, digest, g, v, color),
                )
            ctx.execute(f"UPDATE {nodes} SET next=? WHERE g=? AND v=?", (color, g, v))
        ctx.execute(f"UPDATE {nodes} SET color=next")
        if colors == previous:
            break
        previous = colors


def match(ctx, nodes, edges, g, r, mapping, order, stack):
    """Paged, nonrecursive color-compatible bijection search."""
    histogram = f"SELECT color,count(*) FROM {nodes} WHERE g=? GROUP BY color EXCEPT SELECT color,count(*) FROM {nodes} WHERE g=? GROUP BY color"
    if (
        ctx.execute(histogram, (g, r)).fetchone()
        or ctx.execute(histogram, (r, g)).fetchone()
    ):
        return False
    ctx.execute(f"DELETE FROM {order}")
    ctx.execute(
        f"INSERT INTO {order}(v,color) SELECT v,color FROM {nodes} WHERE g=? ORDER BY (SELECT count(*) FROM {nodes} n WHERE n.g=? AND n.color={nodes}.color),v",
        (g, g),
    )
    count = ctx.execute(f"SELECT count(*) FROM {order}").fetchone()[0]
    ctx.execute(f"DELETE FROM {mapping}")
    ctx.execute(f"DELETE FROM {stack}")
    ctx.execute(f"INSERT INTO {stack} VALUES(1,0)")
    depth = 1
    while depth > 0:
        if depth > count:
            # A separate final equality check includes every edge and nonedge.
            for a, b in ((g, r), (r, g)):
                x, y = ("a", "b") if a == g else ("b", "a")
                if ctx.execute(
                    f"SELECT 1 FROM {edges} e JOIN {mapping} p ON p.{x}=e.u JOIN {mapping} q ON q.{x}=e.v WHERE e.g=? AND NOT EXISTS(SELECT 1 FROM {edges} t WHERE t.g=? AND t.u=p.{y} AND t.v=q.{y} AND t.label=e.label) LIMIT 1",
                    (a, b),
                ).fetchone():
                    raise RuntimeError("Internal isomorphism verification failed")
            return True
        source, color = ctx.execute(
            f"SELECT v,color FROM {order} WHERE d=?", (depth,)
        ).fetchone()
        last = ctx.execute(f"SELECT last FROM {stack} WHERE d=?", (depth,)).fetchone()[
            0
        ]
        candidate = ctx.execute(
            f"SELECT v FROM {nodes} n WHERE g=? AND color=? AND v>? AND NOT EXISTS(SELECT 1 FROM {mapping} m WHERE m.b=n.v) ORDER BY v LIMIT 1",
            (r, color, last),
        ).fetchone()
        if candidate is None:
            ctx.execute(f"DELETE FROM {stack} WHERE d=?", (depth,))
            depth -= 1
            ctx.execute(f"DELETE FROM {mapping} WHERE d=?", (depth,))
            continue
        ctx.tick()
        target = candidate[0]
        ctx.execute(f"UPDATE {stack} SET last=? WHERE d=?", (target, depth))
        ctx.execute(f"INSERT INTO {mapping} VALUES(?,?,?)", (depth, source, target))
        good = True
        for left, right in (("u", "v"), ("v", "u")):
            # Including the newly inserted pair checks loops as well.
            if ctx.execute(
                f"SELECT 1 FROM {mapping} m LEFT JOIN {edges} a ON a.g=? AND a.{left}=? AND a.{right}=m.a LEFT JOIN {edges} b ON b.g=? AND b.{left}=? AND b.{right}=m.b WHERE a.label IS NOT b.label LIMIT 1",
                (g, source, r, target),
            ).fetchone():
                good = False
                break
        if good:
            depth += 1
            ctx.execute(f"INSERT INTO {stack} VALUES(?,0)", (depth,))
        else:
            ctx.execute(f"DELETE FROM {mapping} WHERE d=?", (depth,))
    return False


@operation("alg.graph.isomorphism_classes", search=True)
def isomorphism_classes(
    ctx,
    E,
    *,
    vertices,
    graph="graph",
    u="u",
    v="v",
    graphs=None,
    directed=False,
    vertex_labels=(),
    edge_labels=(),
    max_states=None,
    time_limit=None,
):
    """
    MIPFRAME HELP BEGIN
    alg.graph.isomorphism_classes [api.alg.graph.isomorphism_classes]

    Classify complete explicitly labeled graph instances by exact isomorphism.

    alg.graph.isomorphism_classes(E, *, vertices, graph='graph', u='u', v='v', graphs=None, directed=False, vertex_labels=(), edge_labels=(), max_states=None, time_limit=None, memory_mb=128, max_output_rows=1000000, disk_mb=None)

    When to use: Classify complete explicitly labeled graph instances by exact isomorphism.

    Inputs: E(graph,u,v,*edge_labels); vertices(graph,vertex,*vertex_labels) declares each graph separately. Optional single-column graphs includes empty graphs; explicit directed and label names.

    Returns: Frame(graph,class); relations["mapping"] has graph,vertex,representative_vertex; search metadata describes the exhaustive scope.

    Establishes: Representative is the smallest graph key. A complete label/adjacency-preserving bijection is verified, not inferred from stable colors. State/time exhaustion raises SearchBudgetExceeded without a partial successful Frame.

    Does not establish: No MIP proof, automatic symmetry claim or approved cut is produced.

    Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit. max_output_rows defaults to 1,000,000; exceeding it fails the whole call. None explicitly removes that limit. Output counts membership plus summary/mapping rows. State/time limits are separate; resource failures never silently truncate an object.

    Loops and isolates participate. Duplicate endpoint labels must agree. None is an exact label; vertex IDs are identities, not labels. Not subgraph matching, automorphism enumeration, or a MIP symmetry certificate.

    See also: concept.search, recipe.conflicts
    MIPFRAME HELP END
    """
    if (
        graph in ("class", "vertex", "representative_vertex")
        or u == v
        or graph in (u, v)
    ):
        raise SchemaError(
            "Graph and endpoint columns must be distinct; graph cannot use a reserved result column"
        )
    if type(directed) is not bool:
        raise TypeError("directed must be boolean")
    vertex_labels, edge_labels = labels_tuple(vertex_labels), labels_tuple(edge_labels)
    vt, vs = ctx.stage_checked(
        vertices,
        (graph, "vertex", *vertex_labels),
        unique=(graph, "vertex"),
        nonnull=(graph, "vertex"),
    )
    et, es = ctx.stage_checked(E, (graph, u, v, *edge_labels), nonnull=(graph, u, v))
    if (
        not vs[graph].compatible(es[graph])
        or not vs["vertex"].compatible(es[u])
        or not vs["vertex"].compatible(es[v])
    ):
        raise SchemaError("Graph and vertex identities must have compatible types")
    groups = KeyIndex.domain(
        ctx,
        vs[graph],
        graphs if graphs is not None else vertices.domains.get(graph),
        f"SELECT c0 AS value FROM {vt} UNION SELECT c0 AS value FROM {et}",
    )
    groups.validate(f"SELECT c0 AS value FROM {vt}", graph)
    groups.validate(f"SELECT c0 AS value FROM {et}", graph)
    keys = KeyIndex(ctx, vs["vertex"], f"SELECT c1 AS value FROM {vt}")
    # Edges cannot invent vertices, even when that key exists in another graph.
    for col in (1, 2):
        schema = {graph: vs[graph], "vertex": vs["vertex"]}
        bad = f"SELECT DISTINCT e.c0,e.c{col} FROM {et} e WHERE NOT EXISTS(SELECT 1 FROM {vt} v WHERE v.c0=e.c0 AND v.c1=e.c{col})"
        ctx.invalid(
            "Edge endpoint absent from its declared graph",
            schema,
            ctx.rows(bad, schema),
        )
    nodes = ctx.raw_table(
        "g INTEGER,v INTEGER,color INTEGER,next INTEGER,PRIMARY KEY(g,v)"
    )
    vl = ",".join(f"t.c{i}" for i in range(2, len(vs))) or "1"
    ctx.execute(
        f"INSERT INTO {nodes}(g,v,color) SELECT g.ordinal,v.ordinal,dense_rank() OVER(ORDER BY {vl}) FROM {vt} t JOIN {groups.table} g ON g.c0=t.c0 JOIN {keys.table} v ON v.c0=t.c1"
    )
    raw = ctx.raw_table("g INTEGER,u INTEGER,v INTEGER,label INTEGER")
    el = ",".join(f"t.c{i}" for i in range(3, len(es))) or "1"
    ctx.execute(
        f"INSERT INTO {raw} SELECT g.ordinal,u.ordinal,v.ordinal,dense_rank() OVER(ORDER BY {el}) FROM {et} t JOIN {groups.table} g ON g.c0=t.c0 JOIN {keys.table} u ON u.c0=t.c1 JOIN {keys.table} v ON v.c0=t.c2"
    )
    if not directed:
        ctx.execute(f"INSERT INTO {raw} SELECT g,v,u,label FROM {raw} WHERE u!=v")
    bad = f"SELECT g,u,v,count(DISTINCT label) FROM {raw} GROUP BY g,u,v HAVING count(DISTINCT label)>1"
    count_name = "label_count"
    while count_name in (graph, u, v):
        count_name = "_" + count_name
    schema = {
        graph: vs[graph],
        u: vs["vertex"],
        v: vs["vertex"],
        count_name: DType("int"),
    }
    ctx.invalid(
        "Parallel edges have conflicting labels",
        schema,
        (
            {graph: groups.value(g), u: keys.value(a), v: keys.value(b), count_name: n}
            for g, a, b, n in ctx.execute(bad)
        ),
    )
    edges = ctx.raw_table(
        "g INTEGER,u INTEGER,v INTEGER,label INTEGER,PRIMARY KEY(g,u,v)"
    )
    ctx.execute(f"INSERT INTO {edges} SELECT DISTINCT * FROM {raw}")
    ctx.execute(f"CREATE INDEX {edges}_reverse ON {edges}(g,v,u)")
    ctx.scope_info = f"{groups.n} declared graph instances; directed={directed}"
    ctx.paths.add("exact joint color refinement + paged bijection backtracking")
    ctx.ready()
    refine(ctx, nodes, edges)
    classes = ctx.raw_table("g INTEGER PRIMARY KEY,r INTEGER")
    mappings = ctx.raw_table("g INTEGER,a INTEGER,b INTEGER,PRIMARY KEY(g,a)")
    scratch = ctx.raw_table("d INTEGER PRIMARY KEY,a INTEGER UNIQUE,b INTEGER UNIQUE")
    order = ctx.raw_table("d INTEGER PRIMARY KEY,v INTEGER,color INTEGER")
    stack = ctx.raw_table("d INTEGER PRIMARY KEY,last INTEGER")
    for g, _ in groups.entries():
        representative = None
        for (r,) in ctx.execute(f"SELECT g FROM {classes} WHERE g=r ORDER BY g"):
            if match(ctx, nodes, edges, g, r, scratch, order, stack):
                representative = r
                ctx.execute(f"INSERT INTO {mappings} SELECT ?,a,b FROM {scratch}", (g,))
                break
        if representative is None:
            representative = g
            ctx.execute(
                f"INSERT INTO {mappings} SELECT g,v,v FROM {nodes} WHERE g=?", (g,)
            )
        ctx.execute(f"INSERT INTO {classes} VALUES(?,?)", (g, representative))
        ctx.enforce(
            ctx.execute(f"SELECT count(*) FROM {mappings}").fetchone()[0]
            + ctx.execute(f"SELECT count(*) FROM {classes}").fetchone()[0]
        )
    status = ctx.finish(
        found=groups.n,
        retained=groups.n,
        details="whole simple graphs; exact label-preserving bijections; not a MIP symmetry proof",
    )
    maps = ctx.publish(
        {
            graph: vs[graph],
            "vertex": vs["vertex"],
            "representative_vertex": vs["vertex"],
        },
        (
            {
                graph: groups.value(g),
                "vertex": keys.value(a),
                "representative_vertex": keys.value(b),
            }
            for g, a, b in ctx.execute(f"SELECT g,a,b FROM {mappings} ORDER BY g,a")
        ),
        key=(graph, "vertex"),
        relations={"search": status},
    )
    return ctx.publish(
        {graph: vs[graph], "class": vs[graph]},
        (
            {graph: groups.value(g), "class": groups.value(r)}
            for g, r in ctx.execute(f"SELECT g,r FROM {classes} ORDER BY g")
        ),
        key=(graph,),
        domains={graph: groups.as_frame(graph), "class": groups.as_frame(graph)},
        relations={"mapping": maps, "search": status},
    )
