"""Deterministic union-find with resident arrays or a paged SQLite state."""

import numpy as np


class DSU:
    def __init__(self, n, ctx=None):
        self.ctx = ctx
        self.memory = ctx is None or ctx.reserve(n * 17)
        if self.memory:
            self.p = np.arange(n, dtype=np.int64)
            self.rank = np.zeros(n, dtype=np.int8)
            self.minimum = np.arange(n, dtype=np.int64)
        else:
            self.table = ctx.raw_table(
                "id INTEGER PRIMARY KEY,p INTEGER,r INTEGER,minimum INTEGER"
            )
            for i in range(n):
                ctx.execute(f"INSERT INTO {self.table} VALUES(?,?,0,?)", (i, i, i))
        if ctx:
            ctx.paths.add("array union-find" if self.memory else "paged union-find")

    def state(self, x):
        if self.memory:
            return int(self.p[x]), int(self.rank[x]), int(self.minimum[x])
        return self.ctx.execute(
            f"SELECT p,r,minimum FROM {self.table} WHERE id=?", (x,)
        ).fetchone()

    def update(self, x, p, r, m):
        if self.memory:
            self.p[x], self.rank[x], self.minimum[x] = p, r, m
        else:
            self.ctx.execute(
                f"UPDATE {self.table} SET p=?,r=?,minimum=? WHERE id=?", (p, r, m, x)
            )

    def find(self, x):
        while True:
            p, r, m = self.state(x)
            if p == x:
                return x
            gp = self.state(p)[0]
            self.update(x, gp, r, m)
            x = p

    def union(self, x, y):
        x, y = self.find(x), self.find(y)
        if x == y:
            return
        _, rx, mx = self.state(x)
        _, ry, my = self.state(y)
        if rx < ry:
            x, y, rx, ry, mx, my = y, x, ry, rx, my, mx
        self.update(y, x, ry, my)
        self.update(x, x, rx + (rx == ry), min(mx, my))

    def representative(self, x):
        return self.state(self.find(x))[2]
