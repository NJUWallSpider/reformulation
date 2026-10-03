"""Shared typed, induced, disk-backed search graph and independent verifiers."""

from fractions import Fraction
from mipframe.ops._kernels.graph import Graph
from mipframe.frame.schema import DType, SchemaError
from mipframe.frame.executor import unpack
from mipframe.frame.materialized import stored_frame


class SearchGraph(Graph):
    def __init__(
        self, ctx, E, u, v, vertices, scope, *, directed, score=None, value="value"
    ):
        super().__init__(ctx, E, u, v, vertices)
        self.active = self.vertices.selection(scope)
        self.directed = directed
        self.size = ctx.execute(f"SELECT count(*) FROM {self.active}").fetchone()[0]
        ctx.scope_info = f"{'induced selection' if scope is not None else 'full declared domain'}: {self.size} of {self.vertices.n} vertices"
        scoped = ctx.raw_table("u INTEGER,v INTEGER,PRIMARY KEY(u,v)")
        ctx.execute(
            f"INSERT INTO {scoped} SELECT e.u,e.v FROM {self.edges} e JOIN {self.active} a ON a.ordinal=e.u JOIN {self.active} b ON b.ordinal=e.v"
        )
        self.ignored_loops = 0
        if not directed:
            self.ignored_loops = ctx.execute(
                f"SELECT count(*) FROM {scoped} WHERE u=v"
            ).fetchone()[0]
            ctx.execute(f"DELETE FROM {scoped} WHERE u=v")
            ctx.execute(f"INSERT OR IGNORE INTO {scoped} SELECT v,u FROM {scoped}")
        self.edges = scoped
        ctx.execute(f"CREATE INDEX {scoped}_reverse ON {scoped}(v,u)")
        self.weights = ctx.raw_table("v INTEGER PRIMARY KEY,w TEXT COLLATE EXACT")
        if score is None:
            ctx.execute(
                f"INSERT INTO {self.weights} SELECT ordinal,'1' FROM {self.active}"
            )
        else:
            table, schema = ctx.stage_checked(
                score, ("vertex", value), unique=("vertex",), nonnull=("vertex",)
            )
            if not self.vertices.typ.compatible(schema["vertex"]):
                raise SchemaError("Score vertex type or snapshot differs")
            if schema[value].kind not in ("int", "number"):
                raise SchemaError("Scores must be finite exact numbers")
            self.vertices.validate(f"SELECT c0 AS value FROM {table}", "vertex")
            bad = f"SELECT c0,c1 FROM {table} WHERE c1 IS NULL OR c1 IN ('inf','-inf') ORDER BY ordinal"
            ctx.invalid(
                "Scores must be finite and nonmissing", schema, ctx.rows(bad, schema)
            )
            missing = f"SELECT d.c0 FROM {self.active} a JOIN {self.vertices.table} d ON d.ordinal=a.ordinal WHERE NOT EXISTS(SELECT 1 FROM {table} s WHERE s.c0=d.c0)"
            ctx.invalid(
                "Scores must cover the search scope",
                {"vertex": self.vertices.typ},
                ctx.rows(missing, {"vertex": self.vertices.typ}),
            )
            ctx.execute(
                f"INSERT INTO {self.weights} SELECT a.ordinal,s.c1 FROM {self.active} a JOIN {self.vertices.table} d ON d.ordinal=a.ordinal JOIN {table} s ON s.c0=d.c0"
            )
        self.priority = ctx.raw_table("rank INTEGER PRIMARY KEY,v INTEGER UNIQUE")
        ctx.execute(
            f"INSERT INTO {self.priority}(v) SELECT w.v FROM {self.weights} w LEFT JOIN {self.edges} e ON e.u=w.v GROUP BY w.v ORDER BY w.w DESC,count(e.v) DESC,w.v"
        )
        ctx.paths.add("paged induced adjacency, search stack and exact vertex scores")

    def weight(self, v):
        return Fraction(
            self.ctx.execute(
                f"SELECT w FROM {self.weights} WHERE v=?", (v,)
            ).fetchone()[0]
        )

    def domain(self):
        ctx = self.ctx
        table = ctx.table({"vertex": self.vertices.typ})
        ctx.execute(
            f"INSERT INTO {table}(c0) SELECT d.c0 FROM {self.vertices.table} d JOIN {self.active} a ON a.ordinal=d.ordinal ORDER BY d.ordinal"
        )
        result = stored_frame(
            ctx, table, {"vertex": self.vertices.typ}, key=("vertex",)
        )
        result._known_count = self.size
        return result

    def verify_clique(self, members):
        ctx = self.ctx
        missing = ctx.execute(
            f"SELECT 1 FROM {members} a JOIN {members} b ON a.v<b.v WHERE NOT EXISTS(SELECT 1 FROM {self.edges} e WHERE e.u=a.v AND e.v=b.v) LIMIT 1"
        ).fetchone()
        extension = ctx.execute(
            f"SELECT 1 FROM {self.active} a WHERE NOT EXISTS(SELECT 1 FROM {members} c WHERE c.v=a.ordinal) AND NOT EXISTS(SELECT 1 FROM {members} c WHERE NOT EXISTS(SELECT 1 FROM {self.edges} e WHERE e.u=a.ordinal AND e.v=c.v)) LIMIT 1"
        ).fetchone()
        if missing or extension:
            raise RuntimeError("Internal clique verification failed")

    def verify_cycle(self, path, n, chordless):
        ctx = self.ctx
        missing = ctx.execute(
            f"SELECT 1 FROM {path} a JOIN {path} b ON b.p=(a.p+1)%? WHERE NOT EXISTS(SELECT 1 FROM {self.edges} e WHERE e.u=a.v AND e.v=b.v) LIMIT 1",
            (n,),
        ).fetchone()
        if missing:
            raise RuntimeError("Internal cycle verification failed")
        if chordless:
            return (
                ctx.execute(
                    f"SELECT 1 FROM {path} a JOIN {path} b ON a.p<b.p JOIN {self.edges} e ON e.u=a.v AND e.v=b.v WHERE b.p-a.p NOT IN (1,?) LIMIT 1",
                    (n - 1,),
                ).fetchone()
                is None
            )
        return True
