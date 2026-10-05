"""Persistent exact sparse vectors and shared interval residual evaluation.

The fixed binary coordinate tree shares untouched subtrees after point updates.
A residual is a weighted collection of trees, never an expanded prefix vector.
Coefficient envelopes permit whole subtrees to use one bound direction; ambiguous
subtrees are split until exact leaf coefficients are reached.
"""
from array import array
from math import inf, isnan


class SparseTree:
    def __init__(self, facts, pool, budget, expressions=None):
        self.f, self.pool, self.budget = facts, pool, budget
        reachable, pending = set(), list(range(len(pool.nodes)) if expressions is None else expressions)
        while pending:
            i=pending.pop()
            if i in reachable:continue
            reachable.add(i)
            kind,data=pool.nodes[i]
            if kind=='sum':pending.extend(c for c,_ in data)
        used = dict.fromkeys(v for i in sorted(reachable) if pool.nodes[i][0]=='terms' for v,_ in pool.nodes[i][1])
        self.variables = tuple(used)
        self.coordinates = {v:i for i,v in enumerate(self.variables)}
        self.capacity = 1 << max(0, (len(self.variables)-1).bit_length())
        self.left, self.right, self.var = array('q', [0]), array('q', [0]), array('q', [-1])
        self.minimum, self.maximum, self.lb, self.ub = (array('q', [0]) for _ in range(4))
        self.values, self.value_ids = [0], {0: 0}
        self.cache = {0: 0}
        self.bounds_cache = {}
        self.subsets = {}
        self.nonzero_min = array('q', [0])
        self.nonzero_max = array('q', [0])
        self.reachable=reachable
        self._block=None

    @property
    def block(self):
        if self._block is None:
            from mipframe.check.integer_block import IntegerBlock
            self._block=IntegerBlock(self,self.reachable)
        return self._block

    def number(self, value):
        if value != value:
            value = None
        if value not in self.value_ids:
            self.value_ids[value] = len(self.values)
            self.values.append(value)
        return self.value_ids[value]

    def add_number(self, left, right):
        a, b = self.values[left], self.values[right]
        return self.number(None if a is None or b is None else a+b)

    def leaf(self, var, coefficient):
        if not coefficient:
            return 0
        if var not in self.bounds_cache:
            self.f.validate('var', var)
            self.bounds_cache[var] = self.f.var_bounds(var)
        lo, hi = self.bounds_cache[var]
        ident = self.append(0, 0, var, coefficient, coefficient,
                            self.number(coefficient*lo), self.number(coefficient*hi))
        return ident

    def append(self, left, right, var, minimum, maximum, lb, ub):
        self.budget.charge('memory', 72)
        i = len(self.left)
        self.left.append(left);self.right.append(right);self.var.append(var)
        self.minimum.append(self.number(minimum));self.maximum.append(self.number(maximum))
        self.lb.append(lb);self.ub.append(ub)
        children = [c for c in (left,right) if c]
        self.nonzero_min.append(self.number(min(self.values[self.nonzero_min[c]] for c in children) if children else minimum))
        self.nonzero_max.append(self.number(max(self.values[self.nonzero_max[c]] for c in children) if children else maximum))
        return i

    def branch(self, left, right):
        if not left and not right:
            return 0
        return self.append(left, right, -1,
                           min(self.values[self.minimum[left]], self.values[self.minimum[right]]),
                           max(self.values[self.maximum[left]], self.values[self.maximum[right]]),
                           self.add_number(self.lb[left], self.lb[right]),
                           self.add_number(self.ub[left], self.ub[right]))

    def build(self, terms, lo=0, hi=None):
        if hi is None:
            hi = self.capacity
        if not terms:
            return 0
        if hi-lo == 1:
            return self.leaf(self.variables[lo], terms[0][1])
        mid = (lo+hi)//2
        from bisect import bisect_left
        at = bisect_left(terms, (mid,))
        return self.branch(self.build(terms[:at],lo,mid), self.build(terms[at:],mid,hi))

    def add(self, a, b, weight=1):
        if not b or not weight:
            return a
        if not a and weight == 1:
            return b
        if self.var[b] >= 0:
            value = (self.values[self.minimum[a]] if a else 0) + weight*self.values[self.minimum[b]]
            return self.leaf(int(self.var[b]), value)
        return self.branch(self.add(self.left[a],self.left[b],weight),
                           self.add(self.right[a],self.right[b],weight))

    def expression(self, expr):
        if expr in self.cache:
            return self.cache[expr]
        stack = [(expr, False)]
        while stack:
            i, ready = stack.pop()
            if i in self.cache:
                continue
            kind, data = self.pool.nodes[i]
            if kind == 'terms':
                self.cache[i] = self.build(sorted((self.coordinates[v],c) for v,c in data))
            elif not ready:
                stack.append((i, True))
                stack.extend((child, False) for child, _ in data if child not in self.cache)
            else:
                value = 0
                # Build the largest/shared summand first. Expression IDs increase
                # with persistent prefixes, so this also avoids copying prefixes.
                for child, weight in sorted(data, reverse=True):
                    value = self.add(value, self.cache[child], weight)
                self.cache[i] = value
        return self.cache[expr]

    def subset(self, a, b):
        if not a or a==b:
            return True
        if not b:
            return False
        key=(a,b)
        if key not in self.subsets:
            if self.var[a]>=0:
                value=self.var[a]==self.var[b]
            else:
                value=self.subset(self.left[a],self.left[b]) and self.subset(self.right[a],self.right[b])
            self.budget.charge('memory',96)
            self.subsets[key]=value
        return self.subsets[key]

    def residual(self, pairs, intervals, bounds):
        """Return (upper bound, identically zero, shared bound dependencies)."""
        fast=self.block.residual(pairs,intervals,bounds)
        if fast is not None:return fast
        deps, total, zero = [], 0, True
        intervals = {self.coordinates[v]:bounds for v,bounds in intervals.items()}
        trees = [(self.expression(expr), scale) for expr, scale in pairs if scale and expr]
        stack = [(trees, 0, self.capacity)]
        work = 0
        while stack:
            entries, lo, hi = stack.pop()
            weights = {}
            for node, weight in entries:
                if node and weight:
                    weights[node] = weights.get(node, 0) + weight
            entries = [(n, w) for n, w in weights.items() if w]
            if not entries:
                continue
            work += len(entries)
            if work >= 256:
                self.budget.charge(count=work);work=0
            if hi-lo == 1:
                coefficient = sum(w*self.values[self.minimum[n]] for n,w in entries)
                if not coefficient:
                    continue
                if lo in intervals and intervals[lo][0] == intervals[lo][1]:
                    total += coefficient*intervals[lo][0]
                    continue
                zero = False
                if not bounds:
                    return total, False, deps
                side = 'ub' if coefficient>0 else 'lb'
                variable = self.variables[lo]
                bound = (intervals.get(lo) or self.bounds_cache[variable])[coefficient>0]
                total += coefficient*bound
                deps.append(('var',variable,side))
                continue
            low = sum(w*self.values[(self.minimum if w>0 else self.maximum)[n]] for n,w in entries)
            high = sum(w*self.values[(self.maximum if w>0 else self.minimum)[n]] for n,w in entries)
            if low == high == 0:
                continue
            if low<0<high:
                for anchor, weight in entries:
                    if all(self.subset(n,anchor) for n,_ in entries):
                        low=max(low, sum(w*self.values[(self.nonzero_min if w>0 else self.nonzero_max)[n]] if n==anchor else w*self.values[(self.minimum if w>0 else self.maximum)[n]] for n,w in entries))
                        high=min(high, sum(w*self.values[(self.nonzero_max if w>0 else self.nonzero_min)[n]] if n==anchor else w*self.values[(self.maximum if w>0 else self.minimum)[n]] for n,w in entries))
                        if high<=0 or low>=0:
                            break
            # Local assumptions may replace bounds only inside their coordinates.
            if bounds and not any(lo <= v < hi for v in intervals) and (high<=0 or low>=0):
                side = 'lb' if high<=0 else 'ub'
                values = self.lb if side=='lb' else self.ub
                pieces = [self.values[values[n]] for n,_ in entries]
                if all(v is not None and v not in (-inf,inf) for v in pieces):
                    total += sum(w*v for (_,w),v in zip(entries,pieces))
                    deps.extend(('tree',n,side) for n,_ in entries)
                    zero = False
                    continue
            mid=(lo+hi)//2
            stack.append(([(self.right[n],w) for n,w in entries],mid,hi))
            stack.append(([(self.left[n],w) for n,w in entries],lo,mid))
        self.budget.charge(count=work)
        return total, zero, deps

    def dependencies(self, node, side, seen):
        stack=[node]
        while stack:
            n=stack.pop()
            if not n or (n,side) in seen:
                continue
            seen.add((n,side))
            if self.var[n]>=0:
                yield 'var',int(self.var[n]),side
            else:
                stack.extend((self.left[n],self.right[n]))
