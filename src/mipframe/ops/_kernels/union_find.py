"""Deterministic union-find with budgeted arrays."""

import numpy as np


class DSU:
    def __init__(self,n,ctx=None):
        from mipframe.data.runtime import WorkContext
        self.work=ctx.work if ctx is not None else WorkContext()
        self.p=self.work.array(n,'<i8');self.rank=self.work.array(n,'i1',zero=True)
        self.minimum=self.work.array(n,'<i8')
        block=max(1,self.work.memory//128)
        for start in range(0,n,block):self.p[start:start+block]=np.arange(start,min(n,start+block))
        self.minimum[:]=self.p
        if ctx is not None:ctx.paths.add('array union-find')
    def state(self,x):return int(self.p[x]),int(self.rank[x]),int(self.minimum[x])
    def update(self,x,p,r,m):self.p[x],self.rank[x],self.minimum[x]=p,r,m

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
