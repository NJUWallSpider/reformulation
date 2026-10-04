"""Budgeted integer aggregation of sparse neighborhood family patterns."""
from types import SimpleNamespace
import numpy as np
from mipframe.store.query_store import QueryStore, SQLIndex


def source_index(source, store):
    """Deduplicate arbitrary source selections within this query's own budget."""
    index = source.ids
    if isinstance(index, range) or getattr(index, "unique", False):
        return index
    table = store.table("ordinal INTEGER PRIMARY KEY,id INTEGER UNIQUE")
    store.db.executemany(
        f"INSERT OR IGNORE INTO {table}(id) VALUES(?)", ((int(i),) for i in index)
    )
    return SQLIndex(store, f"SELECT id FROM {table} ORDER BY ordinal", unique=True)


def build_patterns(source, depth, memory_mb):
    from mipframe.store.incidence import default_depth

    store = QueryStore(source._owner, memory_mb)
    block = max(1, store.memory // 256)
    try:
        owner = source._owner
        kind = "var" if source._entity_axis == "row" else "row"
        depth = default_depth(owner, kind) if depth is None else depth
        if type(depth) is not int or depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        from mipframe.ir.signatures import refinement
        result = refinement(owner,depth=depth)
        colors = result._store.array(result._config, kind, depth)
        family_db = result.partition(kind,depth).db

        def ordinal(color):
            return (
                int(color)
                if kind == "var"
                else family_db.execute(
                    "SELECT position FROM families WHERE color=?", (int(color),)
                ).fetchone()[0]
            )

        work = store.table("color INTEGER PRIMARY KEY, n INTEGER")
        nodes = store.table(
            "node INTEGER PRIMARY KEY,parent INTEGER,color INTEGER,n INTEGER,UNIQUE(parent,color,n)"
        )
        members = store.table("ordinal INTEGER PRIMARY KEY,id INTEGER UNIQUE,node INTEGER")
        for ident in source_index(source, store):
            store.execute(f"DELETE FROM {work}")
            for ids, _ in owner.model.sparse.blocks(source._entity_axis, ident, block):
                values, amounts = np.unique(colors[ids], return_counts=True)
                store.db.executemany(
                    f"INSERT INTO {work} VALUES(?,?) ON CONFLICT(color) DO UPDATE SET n=n+excluded.n",
                    ((ordinal(c), int(n)) for c, n in zip(values, amounts)),
                )
            parent = 0
            for color, n in store.execute(f"SELECT color,n FROM {work} ORDER BY color"):
                store.execute(
                    f"INSERT OR IGNORE INTO {nodes}(parent,color,n) VALUES(?,?,?)",
                    (parent, color, n),
                )
                parent = store.execute(
                    f"SELECT node FROM {nodes} WHERE parent=? AND color=? AND n=?",
                    (parent, color, n),
                ).fetchone()[0]
            store.execute(f"INSERT INTO {members}(id,node) VALUES(?,?)", (int(ident), parent))
        # Lexicographic trie walk assigns a bounded-memory, exact pattern order.
        store.execute(f"CREATE INDEX {nodes}_parent ON {nodes}(parent,color,n)")
        order = store.table("node INTEGER PRIMARY KEY, rank INTEGER")
        stack = store.table(
            "level INTEGER PRIMARY KEY,node INTEGER,color INTEGER,n INTEGER"
        )
        store.execute(f"INSERT INTO {stack} VALUES(0,0,-1,-1)")
        rank, level = 0, 0
        store.execute(f"INSERT INTO {order} VALUES(0,0)")
        while level >= 0:
            parent, color, n = store.execute(
                f"SELECT node,color,n FROM {stack} WHERE level=?", (level,)
            ).fetchone()
            child = store.execute(
                f"SELECT node,color,n FROM {nodes} WHERE parent=? AND (color,n)>(?,?) ORDER BY color,n LIMIT 1",
                (parent, color, n),
            ).fetchone()
            if child is None:
                store.execute(f"DELETE FROM {stack} WHERE level=?", (level,))
                level -= 1
            else:
                rank += 1
                store.execute(
                    f"UPDATE {stack} SET color=?,n=? WHERE level=?", (*child[1:], level)
                )
                store.execute(f"INSERT INTO {order} VALUES(?,?)", (child[0], rank))
                level += 1
                store.execute(
                    f"INSERT INTO {stack} VALUES(?,?, -1,-1)", (level, child[0])
                )
        store.execute(f"CREATE INDEX {members}_node ON {members}(node,ordinal)")
        query = f"SELECT m.node,count(*) AS count FROM {members} m JOIN {order} o USING(node) GROUP BY m.node ORDER BY count DESC,o.rank"
        return SimpleNamespace(
            _store=store, _nodes=nodes, _members=members, _query=query, depth=depth
        )
    except BaseException:
        store.close()
        raise
