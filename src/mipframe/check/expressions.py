"""Hash-consed exact affine DAGs. Hash matches always use tuple equality."""
from contextvars import ContextVar
from heapq import heappush, heappop
from mipframe.check.kernel import exact

_active_pool = ContextVar('mipframe_check_expressions', default=None)


def active_pool(owner):
    pool = _active_pool.get()
    if pool is not None and (owner.uid, owner.version) == pool.snapshot:
        return pool
    return None


class ExpressionPool:
    def __init__(self, owner, budget):
        self.owner, self.budget = owner, budget
        self.snapshot = owner.uid, owner.version
        from mipframe.check.ir import Numbers,ExpressionTable
        self.numbers=Numbers()
        self.nodes, self.lookup = ExpressionTable(self.numbers), {}
        self.leaf_objects = {}
        self.rows = {}
        self.intern(('terms', ()))

    def intern(self, node):
        key=self.nodes.key(node)
        found = self.lookup.get(key)
        if found is not None:
            return found
        self.budget.charge('memory', 128 + (len(node[1]) * 80 if node[0] == 'terms' else 0))
        ident = len(self.nodes)
        self.nodes.append_key(key)
        self.lookup[key] = ident
        return ident

    def import_expr(self, expr):
        expr._guard()
        if any((m.uid, m.version) != self.snapshot for m in expr._models):
            raise ValueError('Expression belongs to another snapshot')
        if getattr(expr, '_check_pool', None) is self:
            return expr._check_node
        # Keep a strong reference: Python object IDs alone are not cache keys.
        key = id(expr)
        item = self.leaf_objects.get(key)
        if item is not None and item[0] is expr:
            return item[1]
        terms = tuple((int(v), exact(c)) for v, c in expr._items())
        self.budget.charge(count=len(terms))
        ident = self.terms(terms)
        self.leaf_objects[key] = expr, ident
        return ident

    def row(self, ident):
        if ident not in self.rows:
            terms = tuple(self.owner.model.iter_terms(ident))
            self.budget.charge(count=len(terms))
            self.rows[ident] = self.terms(terms)
        return self.rows[ident]

    def terms(self, terms):
        values = {}
        for v, c in terms:
            if type(v) is not int or v < 0:
                raise ValueError('Invalid expression variable')
            c = exact(c)
            values[v] = values.get(v, 0) + c
        return self.intern(('terms', tuple(sorted((v, c) for v, c in values.items() if c))))

    def combine(self, pairs):
        values = {}
        for node, scale in pairs:
            scale = exact(scale)
            while node:
                kind,data=self.nodes[node]
                if kind!='sum' or len(data)!=1:break
                node,weight=data[0];scale*=weight
            if scale and node:
                values[node] = values.get(node, 0) + scale
        pairs = tuple(sorted((n, a) for n, a in values.items() if a))
        if not pairs:
            return 0
        if len(pairs) == 1 and pairs[0][1] == 1:
            return pairs[0][0]
        return self.intern(('sum', pairs))

    def small_sparse(self,pairs,limit=8):
        """Bounded symbolic cancellation before choosing a numerical tile."""
        weights={};queue=[];result={};work=0
        def add(i,c):
            if not i or not c:return
            if i not in weights:heappush(queue,-i);weights[i]=c
            else:weights[i]+=c
        for i,c in pairs:add(i,c)
        while queue:
            i=-heappop(queue);scale=weights.pop(i)
            if not scale:continue
            kind,data=self.nodes[i];work+=len(data)
            if work>limit:return None
            if kind=='sum':
                for child,c in data:add(child,c*scale)
            else:
                for v,c in data:result[v]=result.get(v,0)+c*scale
        self.budget.charge(count=work)
        return {v:c for v,c in result.items() if c}

    def sparse(self, pairs, *, budget=None):
        """Cancel shared subexpressions before visiting any leaf terms."""
        budget = self.budget if budget is None else budget
        weights, queue, acc = {}, [], {}
        def add(node, scale):
            if not node or not scale:
                return
            if node not in weights:
                heappush(queue, -node)
                weights[node] = scale
            else:
                weights[node] += scale
        for node, scale in pairs:
            add(node, scale)
        work = 0
        while queue:
            node = -heappop(queue)
            scale = weights.pop(node)
            if not scale:
                continue
            kind, data = self.nodes[node]
            work += len(data)
            if kind == 'sum':
                for child, weight in data:
                    add(child, scale * weight)
            else:
                for var, value in data:
                    acc[var] = acc.get(var, 0) + scale * value
            if work >= 1024:
                budget.charge(count=work)
                work = 0
        budget.charge(count=work)
        return {v: c for v, c in acc.items() if c}

    def read(self, node):
        from mipframe.check.budget import current_budget, CheckBudget
        budget = self.budget if _active_pool.get() is self and not getattr(self,'finished',False) else current_budget() or CheckBudget()
        return self.sparse(((node,1),),budget=budget)

    def expression(self, node, constant=0, models=None):
        from mipframe.linear.expr import LinExpr
        expr = object.__new__(LinExpr)
        expr._owner = self.owner
        expr._models = (self.owner,) if not models or all(m is self.owner for m in models) else tuple(dict.fromkeys((self.owner,*models)))
        expr._constant = exact(constant)
        expr._source = lambda: iter(sorted(self.read(node).items()))
        expr._id_source = None
        expr._store = expr._query = None
        expr._check_pool, expr._check_node = self, node
        return expr

    def shift(self, expr, constant):
        node=self.import_expr(expr)
        if not constant:return expr
        return self.expression(node,expr.constant+constant,models=expr._models)

    def add(self, left, right):
        node = self.combine(((self.import_expr(left), 1), (self.import_expr(right), 1)))
        return self.expression(node, left.constant + right.constant,
                               models=left._models + right._models)

    def scale(self, expr, scalar):
        node = self.combine(((self.import_expr(expr), exact(scalar)),))
        return self.expression(node, expr.constant * scalar, models=expr._models)
