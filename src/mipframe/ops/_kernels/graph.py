"""Integer graph traversal with bounded resident state and disk-backed queues."""

from mipframe.store.algorithm_store import KeyIndex, key_column
from mipframe.frame.schema import SchemaError, DType
from mipframe.check.algorithm import GraphCycleError
from mipframe.check.report import Report


class Graph:
    def __init__(self, ctx, frame, tail, head, vertices=None):
        self.ctx, self.source, self.tail, self.head = ctx, frame, tail, head
        names = tuple(dict.fromkeys((tail, head)))
        table, schema = ctx.stage_checked(frame, names, nonnull=names)
        self.typ = schema[tail]
        if not self.typ.compatible(schema[head]):
            raise SchemaError("Graph endpoints must have compatible scalar identities")
        a, b = names.index(tail), names.index(head)
        if vertices is not None:
            index = KeyIndex.domain(
                ctx, self.typ, vertices, "SELECT NULL AS value WHERE 0"
            )
        else:
            queries = []
            for name, col in ((tail, a), (head, b)):
                domain = frame.domains.get(name)
                if domain is None:
                    queries.append(f"SELECT c{col} AS value FROM {table}")
                else:
                    dn = key_column(domain)
                    if not self.typ.compatible(domain.schema[dn]):
                        raise SchemaError("Vertex domain type differs")
                    dt, _ = ctx.stage_checked(domain, (dn,), nonnull=(dn,))
                    queries.append(f"SELECT c0 AS value FROM {dt}")
            index = KeyIndex(ctx, self.typ, " UNION ".join(queries))
        index.validate(f"SELECT c{a} AS value FROM {table}", tail)
        index.validate(f"SELECT c{b} AS value FROM {table}", head)
        self.vertices = index
        self.edges = ctx.raw_table("u INTEGER,v INTEGER,PRIMARY KEY(u,v)")
        ctx.execute(
            f"INSERT OR IGNORE INTO {self.edges} SELECT u.ordinal,v.ordinal FROM {table} t JOIN {index.table} u ON u.c0=t.c{a} JOIN {index.table} v ON v.c0=t.c{b}"
        )
        ctx.execute(f"CREATE INDEX {self.edges}_reverse ON {self.edges}(v,u)")
        ctx.paths.add("integer forward/reverse adjacency indexes")

    def neighbors(self, v, reverse=False):
        a, b = ("v", "u") if reverse else ("u", "v")
        for (n,) in self.ctx.execute(
            f"SELECT {b} FROM {self.edges} WHERE {a}=? ORDER BY {b}", (v,)
        ):
            yield n


def scc(graph):
    ctx = graph.ctx
    visited = ctx.raw_table("v INTEGER PRIMARY KEY")
    stack = ctx.raw_table("seq INTEGER PRIMARY KEY,v INTEGER,last INTEGER")
    finish = ctx.raw_table("seq INTEGER PRIMARY KEY,v INTEGER")
    # Iterative DFS stores only a cursor position for each suspended vertex.
    for root, _ in graph.vertices.entries():
        if ctx.execute(f"SELECT 1 FROM {visited} WHERE v=?", (root,)).fetchone():
            continue
        ctx.execute(f"INSERT INTO {visited} VALUES(?)", (root,))
        ctx.execute(f"INSERT INTO {stack}(v,last) VALUES(?,-1)", (root,))
        while True:
            top = ctx.execute(
                f"SELECT seq,v,last FROM {stack} ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if top is None:
                break
            seq, v, last = top
            next_ = ctx.execute(
                f"SELECT v FROM {graph.edges} WHERE u=? AND v>? ORDER BY v LIMIT 1",
                (v, last),
            ).fetchone()
            if next_ is None:
                ctx.execute(f"DELETE FROM {stack} WHERE seq=?", (seq,))
                ctx.execute(f"INSERT INTO {finish}(v) VALUES(?)", (v,))
            else:
                n = next_[0]
                ctx.execute(f"UPDATE {stack} SET last=? WHERE seq=?", (n, seq))
                if not ctx.execute(
                    f"SELECT 1 FROM {visited} WHERE v=?", (n,)
                ).fetchone():
                    ctx.execute(f"INSERT INTO {visited} VALUES(?)", (n,))
                    ctx.execute(f"INSERT INTO {stack}(v,last) VALUES(?,-1)", (n,))
    labels = ctx.raw_table("v INTEGER PRIMARY KEY,component INTEGER")
    ctx.execute(f"CREATE INDEX {labels}_component ON {labels}(component,v)")
    for (root,) in ctx.execute(f"SELECT v FROM {finish} ORDER BY seq DESC"):
        if ctx.execute(f"SELECT 1 FROM {labels} WHERE v=?", (root,)).fetchone():
            continue
        minimum = root
        ctx.execute(f"INSERT INTO {labels} VALUES(?,?)", (root, root))
        ctx.execute(f"INSERT INTO {stack}(v) VALUES(?)", (root,))
        while True:
            top = ctx.execute(
                f"SELECT seq,v FROM {stack} ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if top is None:
                break
            seq, v = top
            ctx.execute(f"DELETE FROM {stack} WHERE seq=?", (seq,))
            for n in graph.neighbors(v, True):
                if ctx.execute(f"SELECT 1 FROM {labels} WHERE v=?", (n,)).fetchone():
                    continue
                minimum = min(minimum, n)
                ctx.execute(f"INSERT INTO {labels} VALUES(?,?)", (n, root))
                ctx.execute(f"INSERT INTO {stack}(v) VALUES(?)", (n,))
        ctx.execute(
            f"UPDATE {labels} SET component=? WHERE component=?", (minimum, root)
        )
    ctx.paths.add("nonrecursive SCC traversal with paged stacks")
    return labels


def search(graph, source, *, target=None, seen=None):
    ctx = graph.ctx
    if seen is None:
        seen = ctx.raw_table(
            "seq INTEGER PRIMARY KEY,v INTEGER UNIQUE,pred INTEGER,depth INTEGER"
        )
    else:
        ctx.execute(f"DELETE FROM {seen}")
    ctx.execute(f"INSERT INTO {seen}(v,pred,depth) VALUES(?,NULL,0)", (source,))
    position = 0
    while True:
        item = ctx.execute(
            f"SELECT seq,v,depth FROM {seen} WHERE seq>? ORDER BY seq LIMIT 1",
            (position,),
        ).fetchone()
        if item is None:
            break
        position, v, depth = item
        if target is not None and v == target:
            break
        for n in graph.neighbors(v):
            ctx.execute(
                f"INSERT OR IGNORE INTO {seen}(v,pred,depth) VALUES(?,?,?)",
                (n, v, depth + 1),
            )
    return seen


def path_edges(graph, seen, target):
    ctx = graph.ctx
    reverse = ctx.raw_table("seq INTEGER PRIMARY KEY,u INTEGER,v INTEGER")
    v = target
    while True:
        row = ctx.execute(f"SELECT pred FROM {seen} WHERE v=?", (v,)).fetchone()
        if row is None or row[0] is None:
            break
        ctx.execute(f"INSERT INTO {reverse}(u,v) VALUES(?,?)", (row[0], v))
        v = row[0]
    for u, v in ctx.execute(f"SELECT u,v FROM {reverse} ORDER BY seq DESC"):
        yield u, v


def cycle_error(graph, error=GraphCycleError):
    ctx = graph.ctx
    labels = scc(graph)
    cyclic = ctx.raw_table("component INTEGER PRIMARY KEY")
    ctx.execute(
        f"INSERT INTO {cyclic} SELECT component FROM {labels} GROUP BY component HAVING count(*)>1"
    )
    ctx.execute(
        f"INSERT OR IGNORE INTO {cyclic} SELECT l.component FROM {labels} l JOIN {graph.edges} e ON e.u=l.v AND e.v=l.v"
    )
    schema = {
        "component": graph.vertices.typ,
        "step": DType("int"),
        "u": graph.vertices.typ,
        "v": graph.vertices.typ,
    }

    def witnesses():
        seen = None
        for (root,) in ctx.execute(
            f"SELECT component FROM {cyclic} ORDER BY component"
        ):
            neighbor = ctx.execute(
                f"SELECT e.v FROM {graph.edges} e JOIN {labels} l ON l.v=e.v WHERE e.u=? AND l.component=? ORDER BY e.v LIMIT 1",
                (root, root),
            ).fetchone()[0]
            rv = graph.vertices.value(root)
            yield dict(component=rv, step=0, u=rv, v=graph.vertices.value(neighbor))
            if neighbor != root:
                seen = search(graph, neighbor, target=root, seen=seen)
                for step, (u, v) in enumerate(path_edges(graph, seen, root), 1):
                    yield dict(
                        component=rv,
                        step=step,
                        u=graph.vertices.value(u),
                        v=graph.vertices.value(v),
                    )

    witness = ctx.publish(schema, witnesses(), key=("component", "step"), limited=False)
    violations = ctx.publish(
        {"vertex": graph.vertices.typ, "component": graph.vertices.typ},
        (
            dict(vertex=graph.vertices.value(v), component=graph.vertices.value(c))
            for v, c in ctx.execute(
                f"SELECT l.v,l.component FROM {labels} l JOIN {cyclic} c ON c.component=l.component ORDER BY l.v"
            )
        ),
        key=("vertex",),
        relations={"witness": witness},
        limited=False,
    )
    report = Report(
        ctx,
        False,
        {
            "operation": ctx.operation,
            "scope": "full declared graph",
            "cyclic_vertices": len(violations),
            "violations": len(violations),
        },
        violations,
    )
    raise error(report)


def topological(graph, *, error=GraphCycleError):
    ctx = graph.ctx
    state = ctx.raw_table("v INTEGER PRIMARY KEY,degree INTEGER,level INTEGER")
    ready = ctx.raw_table("v INTEGER PRIMARY KEY")
    order = ctx.raw_table("v INTEGER PRIMARY KEY,position INTEGER UNIQUE,level INTEGER")
    ctx.execute(
        f"INSERT INTO {state} SELECT d.ordinal,count(e.u),0 FROM {graph.vertices.table} d LEFT JOIN {graph.edges} e ON e.v=d.ordinal GROUP BY d.ordinal"
    )
    ctx.execute(f"INSERT INTO {ready} SELECT v FROM {state} WHERE degree=0")
    count = 0
    while True:
        row = ctx.execute(f"SELECT v FROM {ready} ORDER BY v LIMIT 1").fetchone()
        if row is None:
            break
        v = row[0]
        ctx.execute(f"DELETE FROM {ready} WHERE v=?", (v,))
        level = ctx.execute(f"SELECT level FROM {state} WHERE v=?", (v,)).fetchone()[0]
        ctx.execute(f"INSERT INTO {order} VALUES(?,?,?)", (v, count, level))
        count += 1
        for n in graph.neighbors(v):
            ctx.execute(
                f"UPDATE {state} SET degree=degree-1,level=max(level,?) WHERE v=?",
                (level + 1, n),
            )
            ctx.execute(
                f"INSERT OR IGNORE INTO {ready} SELECT v FROM {state} WHERE v=? AND degree=0",
                (n,),
            )
    if count != graph.vertices.n:
        cycle_error(graph, error)
    ctx.paths.add("stable Kahn order with paged indegrees")
    return order
