"""Deferred check graphs and exact execution without internal proof objects.

Only exported conclusions are issued. Logical nodes and expression references are
also the certificate: replay never trusts saved statuses or dependency summaries.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from math import ceil, floor, inf
from time import perf_counter
from types import MappingProxyType
import weakref

_live_nodes = weakref.WeakKeyDictionary()

def _release_nodes(facts_ref, count):
    f=facts_ref()
    if f is not None:_live_nodes[f]=max(0,_live_nodes.get(f,0)-count)

from mipframe.check.budget import CheckBudget, current_budget, batch_operation
from mipframe.check.expressions import ExpressionPool, _active_pool
from mipframe.check.kernel import exact, facts
from mipframe.check.certificate import InvalidCertificateError
from mipframe.check.compact import BatchProof, ProofRef, _KEY, _issued


@dataclass(frozen=True, slots=True, eq=False)
class CheckHandle:
    """
    Unexecuted task reference; deliberately has no status or proof authority.

    MIPFRAME HELP BEGIN
    Pending task identity, never a checked proof.

    Full contract: mf.help("api.CheckHandle")
    MIPFRAME HELP END
    """
    _plan: object
    _index: int

    def for_target(self, key):
        if not isinstance(self._index,tuple) or self._index[0]!='many':
            raise TypeError('This handle has one target')
        return CheckHandle(self._plan,dict(self._index[1])[key])

    def __bool__(self):
        raise TypeError('CheckHandle is pending; call batch.run() and select a result')


class CheckResults(Mapping):
    """
    MIPFRAME HELP BEGIN
    Named completed results with distinct mathematical and diagnostic permissions.

    Full contract: mf.help("api.CheckResults")
    MIPFRAME HELP END
    """
    def __init__(self, values, statistics):
        self._values = MappingProxyType(dict(values))
        self.statistics = MappingProxyType(dict(statistics))

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def require(self):
        """
        MIPFRAME HELP BEGIN
        Require every named exported result to succeed.

        Full contract: mf.help("api.CheckResults.require")
        MIPFRAME HELP END
        """
        for result in self._values.values():
            result.require()
        return self


class GraphTargets:
    """Borrowed target view: only callers requesting terms expand a conclusion."""
    kind = 'affine'

    def __init__(self, state, roots):
        self.state, self.roots = state, tuple(roots)
        origins=[state.tasks.config[i][2] if state.tasks.rule_at(i)=='linear' and len(state.tasks.config[i])==3 else None for i in roots]
        if roots and all(origin is not None and origin[0]=='row' for origin in origins) and len({origin[1] for origin in origins})==len(roots):
            self.kind='row';self.ids=tuple(origin[1] for origin in origins)
        elif roots and all(origin is not None and origin[0]=='var' for origin in origins):
            self.kind='var';self.ids=tuple(origin[1] for origin in origins)
        elif roots and all(state.tasks.rule_at(i)=='bound' for i in roots):
            self.kind='var';self.ids=tuple(state.tasks.config[i][0] for i in roots)
        elif roots and all(state.tasks.rule_at(i)=='one_hot_exclusion' for i in roots):
            self.kind='var';self.ids=tuple(state.tasks.config[i][0] for i in roots)
        elif roots and all(state.tasks.rule_at(i)=='round' and len(state.tasks.config[i])==2 for i in roots):
            self.kind='var';self.ids=tuple(state.tasks.config[i][0] for i in roots)
        elif roots and all(state.tasks.rule_at(i)=='affine_integrality' for i in roots):
            self.kind='integrality';self.ids=tuple(state.tasks.config[i][0] for i in roots)
        elif roots and all(origin is not None and origin[0]=='affine' for origin in origins):
            self.ids=tuple(origin[1] for origin in origins)
        else:self.ids = tuple(range(len(roots)))
        self.sides = tuple(('ub','lb','eq')[state.tasks.relation[i]] for i in roots)
        self.rhs = tuple(state.tasks.numbers[state.tasks.rhs[i]] for i in roots)

    def claim(self, f, index):
        if self.kind=='integrality':raise ValueError('Not a linear target')
        if f is not self.state.f:
            raise InvalidCertificateError('Foreign graph target')
        expr, rhs, rel = self.state.tasks.claim_at(self.roots[index])
        terms = self.state.terms(expr)
        return terms, rhs, rel


class GraphProof(BatchProof):
    def __init__(self, owner, state, roots):
        state.budget.poll()
        if not getattr(state,'published',False):
            count=len(state.groups)
            total=_live_nodes.get(state.f,0)+count
            if total>state.budget.limits['nodes']:
                from mipframe.check.budget import ProofBudgetExceeded
                raise ProofBudgetExceeded('publication','active_nodes',total,state.budget.limits['nodes'])
            _live_nodes[state.f]=total
            state.published=True
            weakref.finalize(state,_release_nodes,weakref.ref(state.f),count)
        targets = GraphTargets(state, roots)
        super().__init__(_KEY, owner, targets, [state.status[i] for i in roots],
                         rule='check_graph', parents=state.parents,
                         domain='integer' if any(state.integer[i] for i in roots) else 'relaxation',
                         assumptions=tuple(dict.fromkeys(state.claim_data(a) for i in roots for a in state.conditions[i])))
        object.__setattr__(self, '_graph', state)
        object.__setattr__(self, '_roots', tuple(roots))

    def assumptions(self, index=None):
        if index is None:
            return self._assumptions
        return tuple(self._graph.claim_data(c) for c in self._graph.conditions[self._roots[index]])

    def domain_at(self, index):
        return 'integer' if self._graph.integer[self._roots[index]] else 'relaxation'

    def dependencies(self, index=None):
        roots = self._roots if index is None else (self._roots[index],)
        emitted, masks = set(), {}
        for root in roots:
            for dependency in self._graph.final_dependencies[root]:
                if dependency[0]=='mask':
                    _,universe,lo,hi=dependency
                    old=masks.get(universe,(0,0));masks[universe]=(old[0]|lo,old[1]|hi)
                elif dependency not in emitted:
                    emitted.add(dependency)
                    yield dependency
        for universe,(lo,hi) in masks.items():
            variables=self._graph.universes[universe]
            for bits,side in ((lo,'lb'),(hi,'ub')):
                while bits:
                    bit=bits & -bits;position=bit.bit_length()-1
                    item=('var',variables[position],side);bits-=bit
                    if item not in emitted:
                        emitted.add(item);yield item

    def graph_record(self):
        return dict(expressions=self._graph.pool.nodes, tasks=self._graph.tasks,
                    roots=self._roots)


def variable_id(owner, variable):
    from mipframe.frame.var import Var
    from mipframe.frame.schema import Ref
    if isinstance(variable,Var):
        identity=(variable._owner.uid,variable._owner.version,'var')
    elif isinstance(variable,Ref):identity=(variable.uid,variable.version,variable.kind)
    elif isinstance(variable,__import__('numbers').Integral) and not isinstance(variable,bool):
        variable=int(variable);facts(owner).validate('var',variable);return variable
    else:raise InvalidCertificateError('Expected a variable of this snapshot')
    if identity!=(owner.uid,owner.version,'var'):
        from mipframe.frame.schema import SchemaError
        raise SchemaError('Foreign variable')
    facts(owner).validate('var',variable.id)
    return variable.id


class CheckBatch:
    """
    A single snapshot's deferred check plan. Context exit never executes it.

    MIPFRAME HELP BEGIN
    Deferred checks owned by one model snapshot.

    Full contract: mf.help("api.CheckBatch")
    MIPFRAME HELP END
    """
    def __init__(self, checker, **options):
        self.owner = checker._owner
        self.f = facts(self.owner)
        self.budget = current_budget() or CheckBudget(**options)
        self.options = options
        self.pool = ExpressionPool(self.owner, self.budget)
        from mipframe.check.ir import TaskTable
        self.tasks, self.outputs, self.parents = TaskTable(self.pool.numbers), {}, []
        self.parent_ids = {}
        self.diagnostics = []
        self.closed = False
        self.token = None

    def __enter__(self):
        self._guard()
        if self.token is not None:
            raise ValueError('Batch is already entered')
        self.token = _active_pool.set(self.pool)
        return self

    def __exit__(self, *_):
        if self.token is not None:
            _active_pool.reset(self.token)
            self.token = None
        self.close()

    def close(self):
        """
        MIPFRAME HELP BEGIN
        Release unpublished plan resources.

        Full contract: mf.help("api.CheckBatch.close")
        MIPFRAME HELP END
        """
        self.closed = True
        if self.pool is not None:self.pool.finished = True
        self.pool = None
        self.diagnostics = []
        # Issued results own the graph; the builder retains no unpublished data.
        self.tasks = []
        self.outputs = {}
        self.parents = []
        self.parent_ids = {}

    def _guard(self):
        if self.closed:
            raise ValueError('Batch is sealed or closed')
        if facts(self.owner) is not self.f:
            raise InvalidCertificateError('Batch snapshot changed')
        self.owner.model
        self.budget.poll()

    def _claim(self, claim):
        from mipframe.linear.claim import Claim
        if not isinstance(claim, Claim):
            raise InvalidCertificateError('Expected a mathematical Claim')
        node = self.pool.import_expr(claim.expr)
        return node, exact(-claim.expr.constant), claim.relation

    def _reference(self, proof):
        if isinstance(proof, CheckHandle):
            if proof._plan is not self or type(proof._index) is not int or not 0 <= proof._index < len(self.tasks):
                raise InvalidCertificateError('Foreign or invalid pending handle')
            return ('task', proof._index)
        if not isinstance(proof, ProofRef) or proof.batch not in _issued or proof.batch._facts is not self.f or not proof.ok:
            raise InvalidCertificateError('Expected a checked proof or a handle of this batch')
        b = proof.batch
        self.pool.terms(b._targets.claim(self.f, proof.index)[0])
        for terms, _, _ in b.assumptions(proof.index):
            self.pool.terms(terms)
        key = id(b)
        if key not in self.parent_ids:
            self.parent_ids[key] = len(self.parents)
            self.parents.append(b)
        return 'external', self.parent_ids[key], proof.index

    def _sources(self, using, assume):
        from mipframe.check.compiler import source_records
        result = []
        for weight, source in source_records(self.owner, using):
            weight = exact(weight)
            if isinstance(source, (CheckHandle, ProofRef)):
                ref = self._reference(source)
            else:
                claim = self._claim(source)
                if source._source is None:
                    if claim not in assume:
                        raise InvalidCertificateError('Unproved claim is not an explicit assumption')
                    ref = 'assumption', claim
                else:
                    s = source._source
                    axis, ident, side = s['axis'], s['id'], s['side']
                    node = self.pool.row(ident) if axis=='row' else self.pool.terms(((ident,1),))
                    lo, hi = self.f.row_bounds(ident) if axis=='row' else self.f.var_bounds(ident)
                    if side not in ('lb','ub','eq') or side=='eq' and lo!=hi:
                        raise InvalidCertificateError('Invalid source side')
                    rhs = lo if side=='lb' else hi
                    rel = {'lb':'>=','ub':'<=','eq':'=='}[side]
                    if rhs in (-inf,inf) or claim[1:] != (rhs,rel) or claim[0]!=node and self.pool.sparse(((claim[0],1),(node,-1))):
                        raise InvalidCertificateError('Forged source provenance')
                    ref = 'model', axis, ident, side
            result.append((*ref, weight))
        return tuple(result)

    def _stage(self, rule, target, sources=(), assume=(), config=(), result_key=None):
        self._guard()
        if result_key is not None:
            if not isinstance(result_key, str) or result_key in self.outputs:
                raise ValueError('result_key must be a unique string')
        self.budget.charge('tasks')
        self.budget.charge('memory', 112 + len(sources) * 40 + len(assume) * 24)
        ident = len(self.tasks)
        self.tasks.append((rule, target, tuple(sources), tuple(assume), tuple(config)))
        if result_key is not None:
            self.outputs[result_key] = ident
        return CheckHandle(self, ident)

    def linear(self, target, *, using=(), residual='exact', assume=(), domain='auto', result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a linear task without executing it.

        Full contract: mf.help("api.CheckBatch.linear")
        MIPFRAME HELP END
        """
        self._guard()
        if residual not in ('exact', 'bounds') or domain not in ('auto', 'integer', 'relaxation'):
            raise ValueError('Invalid residual or proof domain')
        conditions = tuple(dict.fromkeys(self._claim(a) for a in assume))
        claim = self._claim(target)
        if isinstance(using, dict):
            if claim[2] != '==' or set(using) != {'le', 'ge'}:
                raise InvalidCertificateError('Equality needs le/ge certificates')
            from mipframe.linear.claim import Claim
            branches = [self.linear(Claim(target.expr, rel), using=using[key], residual=residual,
                                    assume=assume, domain=domain)
                        for key, rel in (('le', '<='), ('ge', '>='))]
            return self._stage('equality', claim, [self._reference(p) for p in branches], result_key=result_key)
        return self._stage('linear', claim, self._sources(using, conditions), conditions,
                           (residual, domain), result_key)

    def identity(self, left, right=0, *, using=(), assume=(), result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a identity task without executing it.

        Full contract: mf.help("api.CheckBatch.identity")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import coerce
        target=(coerce(self.owner,left)-right).eq(0)
        conditions=tuple(dict.fromkeys(self._claim(a) for a in assume))
        return self._stage('identity',self._claim(target),self._sources(using,conditions),conditions,('exact','auto'),result_key)

    def infeasible(self, *, using=(), assume=(), residual='exact', result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a infeasible task without executing it.

        Full contract: mf.help("api.CheckBatch.infeasible")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import LinExpr
        return self.linear(LinExpr(self.owner, 1) <= 0, using=using, assume=assume,
                           residual=residual, result_key=result_key)

    def cases(self, target, *, var, cases, domain='auto', result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a cases task without executing it.

        Full contract: mf.help("api.CheckBatch.cases")
        MIPFRAME HELP END
        """
        if domain not in ('auto', 'integer') or not isinstance(cases, dict):
            raise InvalidCertificateError('Cases require an integer-domain value-to-proof mapping')
        v = variable_id(self.owner,var)
        sources = tuple(self._reference(p) for p in cases.values())
        return self._stage('cases', self._claim(target), sources,
                           config=(v, tuple(exact(k) for k in cases)), result_key=result_key)

    def round(self, target, *, from_, domain='auto', result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a round task without executing it.

        Full contract: mf.help("api.CheckBatch.round")
        MIPFRAME HELP END
        """
        if domain not in ('auto', 'integer'):
            raise InvalidCertificateError('Rounding is integer-only')
        return self._stage('round', self._claim(target), (self._reference(from_),), result_key=result_key)

    def _many(self, handles, result_key):
        if result_key is not None and (not isinstance(result_key,str) or result_key in self.outputs):
            raise ValueError('result_key must be a unique string')
        if len(dict(handles))!=len(handles):raise ValueError('Duplicate target IDs')
        selected=('many',tuple(handles))
        if result_key is not None:self.outputs[result_key]=selected
        return CheckHandle(self,selected)

    def bound(self, *, vars, values, side='ub', result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a bound task without executing it.

        Full contract: mf.help("api.CheckBatch.bound")
        MIPFRAME HELP END
        """
        from mipframe.data.columns import ExactColumn
        variables=tuple(variable_id(self.owner,v) for v in vars)
        values=ExactColumn(values,len(variables))
        if side not in ('lb','ub','eq'):raise ValueError('Invalid bound side')
        handles=[]
        for v,value in zip(variables,values):
            self.f.validate('var',v)
            target=(self.pool.terms(((v,1),)),exact(value),{'lb':'>=','ub':'<=','eq':'=='}[side])
            h=self._stage('bound',target,config=(v,side))
            handles.append((v,h._index))
        return self._many(handles,result_key)

    def affine_integrality(self, *, vars, rows, result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a affine_integrality task without executing it.

        Full contract: mf.help("api.CheckBatch.affine_integrality")
        MIPFRAME HELP END
        """
        variables=tuple(variable_id(self.owner,v) for v in vars)
        rows=tuple(int(r.id if hasattr(r,'id') else r) for r in rows)
        if len(variables)!=len(rows):raise ValueError('Definition count mismatch')
        handles=[]
        for v,r in zip(variables,rows):
            self.f.validate('var',v);self.f.side('row',r,'eq')
            h=self._stage('affine_integrality',(self.pool.terms(((v,1),)),0,'=='),config=(v,r))
            handles.append((v,h._index))
        return self._many(handles,result_key)

    def enumerate(self, target, *, vars, using=(), assume=(), max_states=100000,
                  time_limit=30, domain='auto', result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a enumerate task without executing it.

        Full contract: mf.help("api.CheckBatch.enumerate")
        MIPFRAME HELP END
        """
        if domain not in ('auto','integer'):
            raise InvalidCertificateError('Enumeration is integer-only')
        if type(max_states) is not int or max_states<0:
            raise ValueError('max_states must be a finite nonnegative integer')
        from math import isfinite
        if time_limit is None or not isfinite(time_limit) or time_limit<0:
            raise ValueError('time_limit must be finite and nonnegative')
        selected=tuple(variable_id(self.owner,v) for v in vars)
        conditions=tuple(dict.fromkeys(self._claim(a) for a in assume))
        return self._stage('enumerate',self._claim(target),self._sources(using,conditions),conditions,
                           (selected,max_states,time_limit),result_key)

    def _diagnostic(self, kind, payload, result_key):
        self._guard()
        if result_key is not None and (not isinstance(result_key,str) or result_key in self.outputs):
            raise ValueError('result_key must be a unique string')
        self.budget.charge('tasks')
        ident=('diagnostic',len(self.diagnostics))
        self.diagnostics.append((kind,payload))
        if result_key is not None:self.outputs[result_key]=ident
        return CheckHandle(self,ident)

    def solution(self, point, *, result_key=None, **options):
        """
        MIPFRAME HELP BEGIN
        Register a solution task without executing it.

        Full contract: mf.help("api.CheckBatch.solution")
        MIPFRAME HELP END
        """
        return self._diagnostic('solution',(point,options),result_key)

    def frame(self, frame, kind, columns=None, *, result_key=None, **options):
        """
        MIPFRAME HELP BEGIN
        Register a frame task without executing it.

        Full contract: mf.help("api.CheckBatch.frame")
        MIPFRAME HELP END
        """
        return self._diagnostic('frame',(frame,kind,dict(options,columns=columns)),result_key)

    def model_verify(self, *, result_key=None):
        """
        MIPFRAME HELP BEGIN
        Register a model_verify task without executing it.

        Full contract: mf.help("api.CheckBatch.model_verify")
        MIPFRAME HELP END
        """
        return self._diagnostic('model_verify',None,result_key)

    @batch_operation
    def run(self):
        """
        MIPFRAME HELP BEGIN
        Execute exported dependency closures and seal the plan.

        Full contract: mf.help("api.CheckBatch.run")
        MIPFRAME HELP END
        """
        self._guard()
        self.closed = True
        start = perf_counter()
        state = GraphExecution(self.owner, self.pool, self.tasks, tuple(self.parents), self.budget)
        try:
            math_outputs = {key:i for key,i in self.outputs.items() if type(i) is int}
            many_outputs = {key:i[1] for key,i in self.outputs.items() if isinstance(i,tuple) and i[0]=='many'}
            roots = tuple(dict.fromkeys((*math_outputs.values(),*(j for pairs in many_outputs.values() for _,j in pairs))))
            state.execute(roots)
            self.budget.phase = 'publication'
            self.budget.poll()
            proof = GraphProof(self.owner, state, tuple(math_outputs.values()))
            values = {key: ProofRef(proof, i) for i, key in enumerate(math_outputs)}
            for key,pairs in many_outputs.items():
                values[key]=GraphProof(self.owner,state,tuple(j for _,j in pairs))
            resources={k:self.options[k] for k in ('memory_mb','disk_mb') if k in self.options}
            diagnostic_outputs={key:i[1] for key,i in self.outputs.items() if isinstance(i,tuple) and i[0]=='diagnostic'}
            grouped={}
            for i in dict.fromkeys(diagnostic_outputs.values()):
                kind,payload=self.diagnostics[i]
                grouped.setdefault(kind,[]).append((i,payload))
            checked={}
            try:
                for kind,requests in grouped.items():
                    if kind=='solution':
                        from mipframe.check.finite import solution_group
                        outputs=solution_group(self.owner,[p for _,p in requests],budget=self.budget,**resources)
                    elif kind=='frame':
                        from mipframe.check.frame import check_group
                        outputs=check_group(self.owner,[p for _,p in requests],budget=self.budget,**resources)
                    elif kind=='model_verify':
                        from mipframe.check.model import verify
                        self.budget.poll()
                        report=verify(self.owner)
                        self.budget.poll()
                        outputs=[report]*len(requests)
                    else:raise InvalidCertificateError('Unknown diagnostic rule')
                    checked.update((i,r) for (i,_),r in zip(requests,outputs))
                values.update((key,checked[i]) for key,i in diagnostic_outputs.items())
            except BaseException:
                for report in checked.values():
                    if hasattr(report,'close'):report.close()
                raise
            statistics = dict(self.budget.counts, expressions=len(self.pool.nodes),
                              execution_seconds=perf_counter()-start,
                              total_seconds=perf_counter()-self.budget.started)
            return CheckResults(values, statistics)
        finally:
            self.close()


class GraphExecution:
    """One workspace for all logical tasks; no scalar-checker dispatch."""
    def __init__(self, owner, pool, tasks, parents, budget):
        self.owner, self.f, self.pool, self.tasks = owner, facts(owner), pool, tasks
        self.parents, self.budget = parents, budget
        n = len(tasks)
        self.status = bytearray(n)
        self.integer = bytearray(n)
        self.conditions = [()] * n
        self.dependencies = [()] * n
        self.edges = [()] * n
        self.external = [()] * n
        from mipframe.check.sparse_tree import SparseTree
        self.tree = None
        self.final_dependencies = {}
        self.universes = []
        self.groups = set()
        self.levels = [0] * n
        self.source_cache = {}
        self.terms_cache = {}
        self.current_records = {}
        self.condition_intern = {(): ()}
        self.details = {}
        self.counterexamples = {}

    def task(self, index):
        record=self.current_records.get(index)
        return self.tasks[index] if record is None else record

    def condition_terms(self, expression):
        if expression not in self.terms_cache:
            self.terms_cache[expression]=self.pool.sparse(((expression,1),))
        return self.terms_cache[expression]

    def terms(self, node):
        # Publication views remain usable after the original operation's budget
        # expires. A fresh read has its own bounded budget.
        return tuple(sorted(self.pool.sparse(((node, 1),), budget=current_budget() or CheckBudget()).items()))

    def claim_data(self, claim):
        node, rhs, relation = claim
        return self.terms(node), rhs, relation

    def import_data(self, data):
        terms, rhs, rel = data
        return self.pool.terms(terms), exact(rhs), rel

    def source(self, ref):
        kind = ref[0]
        if kind == 'task':
            i = ref[1]
            if self.tasks.rule_at(i)=='affine_integrality':
                raise InvalidCertificateError('Integrality evidence is not a linear premise')
            if self.status[i] != 1:
                raise InvalidCertificateError('Dependency is not proved')
            return self.tasks.claim_at(i), self.conditions[i], bool(self.integer[i])
        if kind == 'external':
            if type(ref[1]) is not int or not 0<=ref[1]<len(self.parents):
                raise ValueError('Invalid external parent reference')
            p, i = self.parents[ref[1]], ref[2]
            if p not in _issued or p._facts is not self.f or not 0 <= i < len(p._status) or p._status[i] != 1:
                raise InvalidCertificateError('Invalid external proof')
            return self.import_data(p._targets.claim(self.f, i)), tuple(self.import_data(a) for a in p.assumptions(i)), p.domain_at(i) == 'integer'
        if kind == 'assumption':
            return tuple(ref[1]), (), False
        if kind == 'model':
            key = tuple(ref[1:4])
            if key not in self.source_cache:
                self.source_cache[key] = self.import_data(self.f.side(*key))
            return self.source_cache[key], (), False
        raise InvalidCertificateError('Unknown source kind')

    @batch_operation
    def execute(self, roots):
        required, stack = set(), list(roots)
        while stack:
            i = stack.pop()
            if type(i) is not int or not 0 <= i < len(self.tasks):
                raise InvalidCertificateError('Invalid graph root')
            if i in required:
                continue
            required.add(i)
            edges,external=self.tasks.references(i)
            if any(type(p) is not int or not 0 <= p < i for p in edges):
                raise InvalidCertificateError('Cyclic or invalid task reference')
            self.edges[i] = edges
            self.external[i] = external
            stack.extend(edges)
        self.active = required
        # Pack dependency components into bounded workspaces. Released numeric
        # scratch is not retained by proof artifacts or by exported dependencies.
        leaders = {i:i for i in required}
        def find(i):
            while leaders[i]!=i:
                leaders[i]=leaders[leaders[i]];i=leaders[i]
            return i
        for i in required:
            for parent in self.edges[i]:
                leaders[find(i)]=find(parent)
        components={}
        for i in sorted(required):components.setdefault(find(i),[]).append(i)
        packs=[];pack=[]
        for component in components.values():
            if pack and len(pack)+len(component)>256:
                packs.append(pack);pack=[]
            pack.extend(component)
        if pack:packs.append(pack)
        roots=set(roots)
        from mipframe.check.sparse_tree import SparseTree
        for pack in packs:
            self.current_records={i:self.tasks[i] for i in pack}
            expressions=[]
            for i in pack:
                _,target,sources,assumptions,_=self.current_records[i]
                expressions.append(target[0]);expressions.extend(a[0] for a in assumptions)
                for ref in sources:
                    if ref[0] in ('external','model','assumption'):
                        claim,conditions,_=self.source(ref)
                        expressions.append(claim[0]);expressions.extend(a[0] for a in conditions)
            before=self.budget.counts['memory']
            self.tree=SparseTree(self.f,self.pool,self.budget,expressions)
            try:
                self.pending_checks=[]
                self._execute_nodes(sorted(pack))
                self._finish_linear(sorted(pack))
                for root in roots.intersection(pack):
                    seen,tree_seen,deps,stack=set(),set(),set(),[root]
                    dense_lo=dense_hi=0
                    while stack:
                        i=stack.pop()
                        if i in seen:continue
                        seen.add(i);stack.extend(self.edges[i])
                        for dependency in self.dependencies[i]:
                            if dependency[0]=='dense':
                                dense_lo |= dependency[1];dense_hi |= dependency[2]
                            elif dependency[0]=='tree':
                                deps.update(self.tree.dependencies(dependency[1],dependency[2],tree_seen))
                            else:deps.add(dependency)
                        for parent,position in self.external[i]:
                            deps.update(self.parents[parent].dependencies(position))
                    if dense_lo or dense_hi:
                        deps.add(('mask',len(self.universes),dense_lo,dense_hi))
                    self.final_dependencies[root]=tuple(sorted(deps))
                self.universes.append(self.tree.variables)
            finally:
                self.tree=None
                self.current_records={}
                self.terms_cache={}
                for i in pack:self.dependencies[i]=()
                self.budget.counts['memory']=before
            self.budget.charge('memory',sum(len(self.final_dependencies[r])*80 for r in roots.intersection(pack)))

    def _execute_nodes(self, required):
        self.budget.phase = 'kernel'
        levels,groups=self.levels,self.groups
        for i in sorted(required):
            rule, target, sources, assumptions, config = self.current_records[i]
            levels[i] = 1 + max((levels[j] for j in self.edges[i]), default=-1)
            group=(levels[i], rule, config[:2] if rule == 'linear' else ())
            if group not in groups:
                groups.add(group);self.budget.charge('nodes')
            self.budget.charge()
            if any(self.status[parent]!=1 for parent in self.edges[i]):
                self.status[i]=0
                self.conditions[i]=tuple(dict.fromkeys((*assumptions,*(a for parent in self.edges[i] for a in self.conditions[parent]))))
                self.integer[i]=any(self.integer[parent] for parent in self.edges[i])
                continue
            parents = [self.source(ref) for ref in sources]
            conditions = tuple(dict.fromkeys((*assumptions, *(a for _, cs, _ in parents for a in cs))))
            integer = any(p[2] for p in parents)
            deps = []
            if rule in ('linear','identity'):
                residual, domain = config[:2]
                if residual not in ('exact','bounds') or domain not in ('auto','integer','relaxation'):
                    raise InvalidCertificateError('Invalid linear options')
                if len(config)==3:
                    kind,ident,side=config[2]
                    if side not in ('lb','ub','eq') or type(ident) is not int or ident<0:
                        raise InvalidCertificateError('Invalid target provenance')
                    expected=self.import_data(self.f.side(kind,ident,side)) if kind=='row' else (self.pool.terms(((ident,1),)),target[1],{'lb':'>=','ub':'<=','eq':'=='}[side]) if kind=='var' else target
                    if kind not in ('row','var','affine') or not self.equal_claim(target,expected):
                        raise InvalidCertificateError('Target source provenance differs')
                if integer and domain == 'relaxation':
                    raise InvalidCertificateError('Integer proof cannot become an LP proof')
                integer |= domain == 'integer'
                self.current_task=i
                ok, deps = self.linear(target, sources, parents, conditions, residual)
            elif rule == 'cases':
                var, values = config
                rec = self.f.numeric_var(var)
                lo, hi = rec[3:5]
                if rec[2] != 'I' or lo == -inf or hi == inf or not values or len(values) != floor(hi)-ceil(lo)+1 or len(set(values)) != len(values) or any(Fraction(v).denominator != 1 or not ceil(lo) <= v <= floor(hi) for v in values) or len(values) != len(parents):
                    raise InvalidCertificateError('Cases must cover the complete finite integer domain')
                remaining = []
                for value, (claim, cs, _) in zip(values, parents):
                    if not self.equal_claim(claim, target):
                        raise InvalidCertificateError('Branch conclusion differs')
                    matched = False
                    for assumption in cs:
                        expr, rhs, rel = assumption
                        terms = self.condition_terms(expr)
                        if rel == '==' and len(terms) == 1 and var in terms and Fraction(rhs, terms[var]) == value:
                            matched = True
                        else:
                            remaining.append(assumption)
                    if not matched:
                        raise InvalidCertificateError('Missing matching case assumption')
                conditions = tuple(dict.fromkeys(remaining))
                deps = [('integer', var, 'kind'), ('var', var, 'lb'), ('var', var, 'ub')]
                integer, ok = True, True
            elif rule == 'bound':
                v,side=config
                if target!=(self.pool.terms(((v,1),)),target[1],{'lb':'>=','ub':'<=','eq':'=='}[side]):
                    raise InvalidCertificateError('Invalid bound target')
                lo,hi=self.f.var_bounds(v);value=target[1]
                ok=hi<=value if side=='ub' else lo>=value if side=='lb' else lo==hi==value
                deps=[('var',v,s) for s in (('lb','ub') if side=='eq' else (side,))]
            elif rule == 'one_hot_exclusion':
                v,groups=config
                expected=(self.pool.terms(((v,1),)),0,'<=')
                if not self.equal_claim(target,expected):raise InvalidCertificateError('Invalid exclusion target')
                from mipframe.check.compact import OneHotAffine
                cache=getattr(self,'one_hot_contexts',None)
                if cache is None:self.one_hot_contexts=cache={}
                key=tuple((r,tuple(tuple(pair) for pair in ds)) for r,ds in groups)
                if key not in cache:cache[key]=OneHotAffine(self.owner,key)
                context=cache[key]
                ok=any(v in active and any(not lo<=co.get(v,0)+c<=hi for _,co,c,lo,hi in definitions)
                       for active,(_,definitions) in zip(context.active,context.data))
                deps=context.deps;integer=True
            elif rule == 'affine_integrality':
                v,row=config
                if target!=(self.pool.terms(((v,1),)),0,'=='):
                    raise InvalidCertificateError('Invalid integrality target')
                terms,rhs,_=self.f.side('row',row,'eq')
                coefficients=dict(terms);a=coefficients.pop(v,0)
                if not a:raise InvalidCertificateError('Definition omits its variable')
                ok=Fraction(rhs,a).denominator==1
                for variable,c in coefficients.items():
                    ok &= Fraction(c,a).denominator==1 and self.f.var_kind(variable)=='I'
                    deps.append(('integer',variable,'kind'))
                deps.append(('row',row,'eq'));integer=True
            elif rule == 'enumerate':
                from mipframe.check.finite import enumerate_kernel
                variables,max_states,time_limit=config
                from math import isfinite
                if type(max_states) is not int or max_states<0 or time_limit is None or not isfinite(time_limit) or time_limit<0:
                    raise InvalidCertificateError('Invalid finite enumeration budget')
                status,deps,details,counterexample=enumerate_kernel(self.owner,self.claim_data(target),
                    [self.claim_data(p[0]) for p in parents],tuple(self.claim_data(a) for a in conditions),
                    vars=variables,max_states=max_states,time_limit=time_limit,
                    memory_mb=self.budget.limits['memory']/1024**2,budget=self.budget)
                for ref in sources:
                    if ref[0]=='model':deps.append(tuple(ref[1:4]))
                self.details[i]=details;self.counterexamples[i]=counterexample
                ok,integer=status==1,True
            elif rule == 'round':
                if len(parents)!=1:raise InvalidCertificateError('Rounding requires one parent')
                if config:
                    if len(config)!=2 or config[1] not in ('lb','ub'):
                        raise InvalidCertificateError('Invalid rounding provenance')
                    expected=(self.pool.terms(((config[0],1),)),target[1],'<=' if config[1]=='ub' else '>=')
                    if not self.equal_claim(target,expected):raise InvalidCertificateError('Rounding target provenance differs')
                claim, _, _ = parents[0]
                expr, rhs, rel = target
                if rel not in ('<=', '>=') or rel != claim[2] or self.pool.sparse(((expr, 1), (claim[0], -1))):
                    raise InvalidCertificateError('Rounding requires identical coefficients and direction')
                for v, c in self.pool.sparse(((expr, 1),)).items():
                    if Fraction(c).denominator != 1 or self.f.numeric_var(v)[2] != 'I':
                        raise InvalidCertificateError('Expression is not integer-valued')
                    deps.append(('integer', v, 'kind'))
                value = (floor if rel == '<=' else ceil)(claim[1])
                ok, integer = (value <= rhs if rel == '<=' else value >= rhs), True
            elif rule == 'equality':
                if len(parents) != 2 or target[2] != '==' or any(not self.equal_claim(p[0], (target[0], target[1], rel)) for p, rel in zip(parents, ('<=', '>='))):
                    raise InvalidCertificateError('Invalid equality branches')
                ok = True
            else:
                raise InvalidCertificateError(f'Unknown check rule: {rule}')
            self.status[i] = status if rule=='enumerate' else 2 if rule=='identity' and not ok and not sources and not conditions else int(ok)
            self.integer[i] = integer
            self.conditions[i] = self.condition_intern.setdefault(conditions, conditions)
            self.dependencies[i] = tuple(deps)

    def _finish_linear(self, required):
        results=self.tree.block.residual_many([item[1] for item in self.pending_checks]) if self.pending_checks else ()
        for (i,_,constant,contradiction,rel,residual),(value,zero,deps) in zip(self.pending_checks,results):
            constant+=value
            ok=contradiction or ((zero or rel!='==' and residual=='bounds') and (constant==0 if rel=='==' else constant<=0))
            rule,_,sources,_,_=self.current_records[i]
            self.status[i]=1 if ok else 2 if rule=='identity' and not sources and not self.conditions[i] else 0
            self.dependencies[i]=(*self.dependencies[i],*deps)
        # Verdict propagation is separate from symbolic compilation. Preliminary
        # internal records never acquire proof authority.
        for i in required:
            if any(self.status[parent]!=1 for parent in self.edges[i]):self.status[i]=0
        self.pending_checks=[]

    def equal_claim(self, left, right):
        return left[1:] == right[1:] and (left[0] == right[0] or not self.pool.sparse(((left[0], 1), (right[0], -1))))

    def linear(self, target, sources, parents, conditions, residual):
        expr, rhs, rel = target
        sign = -1 if rel == '>=' else 1
        pairs, constant, deps = [(expr, sign)], -sign*rhs, []
        for ref, (claim, _, _) in zip(sources, parents):
            weight = ref[-1]
            src, bound, relation = claim
            if relation != '==' and (weight < 0 or rel == '=='):
                raise InvalidCertificateError('Negative inequality multiplier or inequality for equality')
            if ref[0] == 'assumption' and claim not in conditions:
                raise InvalidCertificateError('Unscoped assumption source')
            scale = weight * (-1 if relation == '>=' else 1)
            pairs.append((src, -scale))
            constant += scale*bound
            if weight and ref[0] == 'model':
                deps.append(tuple(ref[1:4]))
        intervals, contradiction = {}, False
        for node, bound, relation in conditions:
            ts = self.condition_terms(node)
            if not ts:
                contradiction |= not (0 <= bound if relation == '<=' else 0 >= bound if relation == '>=' else bound == 0)
            if len(ts) != 1:
                continue
            v, a = next(iter(ts.items()))
            value = Fraction(bound, a)
            if a < 0:
                relation = {'<=': '>=', '>=': '<=', '==': '=='}[relation]
            lo, hi = intervals.get(v, self.f.var_bounds(v))
            # Both original bounds contribute to the local interval.
            deps.extend((('var', v, 'lb'), ('var', v, 'ub')))
            if relation in ('>=', '=='):
                lo = max(lo, value)
            if relation in ('<=', '=='):
                hi = min(hi, value)
            intervals[v] = lo, hi
            contradiction |= lo > hi
        small=self.pool.small_sparse(pairs)
        if small is not None:
            nonzero=False
            for v,c in small.items():
                self.f.validate('var',v)
                if v in intervals and intervals[v][0]==intervals[v][1]:constant+=c*intervals[v][0]
                elif rel!='==' and residual=='bounds':
                    bound=(intervals.get(v) or self.f.var_bounds(v))[c>0]
                    deps.append(('var',v,'ub' if c>0 else 'lb'))
                    constant=inf if bound in (-inf,inf) else constant+c*bound
                else:nonzero=True
            return contradiction or (not nonzero and (constant==0 if rel=='==' else constant<=0)),deps
        prepared=self.tree.block.prepare(pairs,intervals,rel!='==' and residual=='bounds')
        if prepared is not None:
            self.pending_checks.append((self.current_task,prepared,constant,contradiction,rel,residual))
            return True,deps
        value, zero, bound_deps = self.tree.residual(pairs, intervals, rel != '==' and residual == 'bounds')
        deps.extend(bound_deps)
        constant += value
        return contradiction or ((zero or rel != '==' and residual == 'bounds') and (constant == 0 if rel == '==' else constant <= 0)), deps



def replay_graph(model, record, parents=(), **options):
    """Validate a saved logical graph, then recompile and execute it."""
    if set(record) != {'expressions', 'tasks', 'roots'}:
        raise InvalidCertificateError('Invalid graph fields')
    plan = CheckBatch(model.check, **options)
    pool = plan.pool
    expressions = record['expressions']
    from mipframe.check.ir import ExpressionTable,TaskTable,validated_copy
    if isinstance(expressions,ExpressionTable) and isinstance(record['tasks'],TaskTable):
        nodes,tasks,numbers=validated_copy(expressions,record['tasks'],plan.f,plan.budget)
        pool.nodes,pool.numbers=nodes,numbers
        pool.lookup={('terms',tuple((int(nodes.items[j]),int(nodes.weights[j])) for j in range(nodes.offsets[i],nodes.offsets[i+1]))):i
                     for i,kind in enumerate(nodes.kind) if kind==0}
        state=GraphExecution(model,pool,tasks,tuple(parents),plan.budget)
        roots=tuple(record['roots'])
        state.execute(roots)
        return GraphProof(model,state,roots)
    if not expressions or tuple(expressions[0]) != ('terms', ()) and expressions[0] != ['terms', []]:
        raise InvalidCertificateError('Invalid zero expression')
    # Validate the logical representation while borrowing immutable tuples.
    # No saved verdict, compiled residual, or dependency mask is read here.
    from mipframe.check.ir import ExpressionTable,TaskTable
    nodes=ExpressionTable(pool.numbers);nodes.append(('terms',()))
    for i,value in enumerate(expressions[1:],1):
        if len(value)!=2:raise InvalidCertificateError('Invalid expression record')
        kind,data=value
        if kind not in ('terms','sum'):raise InvalidCertificateError('Unknown expression kind')
        values=[];previous=-1
        for entry in data:
            if len(entry)!=2:raise InvalidCertificateError('Invalid expression entry')
            ident,coefficient=entry
            if type(ident) is not int or ident<=previous:
                raise InvalidCertificateError('Expression entries must be canonical')
            previous=ident
            if kind=='sum':
                if not 0<=ident<i:raise InvalidCertificateError('Cyclic expression graph')
            else:plan.f.validate('var',ident)
            coefficient=exact(coefficient)
            if not coefficient:raise InvalidCertificateError('Zero expression entry')
            values.append((ident,coefficient))
        node=(kind,tuple(values))
        plan.budget.charge('memory',128+(len(values)*80 if kind=='terms' else 0))
        plan.budget.charge(count=len(values))
        nodes.append(node)
    pool.nodes=nodes
    pool.lookup={nodes.key(node):i for i,node in enumerate(nodes)}
    def claim(value):
        node,rhs,rel=value
        if type(node) is not int or not 0<=node<len(nodes) or rel not in ('<=','>=','=='):
            raise InvalidCertificateError('Invalid graph claim')
        return node,exact(rhs),rel
    supported={'linear','identity','cases','round','equality','enumerate','bound','affine_integrality','one_hot_exclusion'}
    tasks=TaskTable(pool.numbers)
    for value in record['tasks']:
        if len(value)!=5:raise InvalidCertificateError('Invalid task record')
        rule,target,sources,assumptions,config=value
        if rule not in supported:raise InvalidCertificateError('Unknown check rule')
        plan.budget.charge('tasks')
        refs=[]
        linear=rule in ('linear','identity','enumerate')
        for source in sources:
            source=tuple(source)
            kind=source[0]
            arity={'task':2,'external':3,'model':4,'assumption':2}.get(kind)
            if arity is None or len(source)!=arity+int(linear):
                raise InvalidCertificateError('Invalid source reference')
            if kind=='assumption':source=('assumption',claim(source[1]),*source[2:])
            if linear:source=(*source[:-1],exact(source[-1]))
            refs.append(source)
        if rule in ('linear','identity') and len(config)==3:
            config=config[0],config[1],tuple(config[2])
        elif rule=='enumerate':config=tuple(config[0]),config[1],config[2]
        elif rule=='cases':config=config[0],tuple(exact(v) for v in config[1])
        conditions=tuple(claim(a) for a in assumptions)
        task=(rule,claim(target),tuple(refs),conditions,tuple(config))
        plan.budget.charge('memory',112+len(refs)*40+len(conditions)*24)
        tasks.append(task)
    state = GraphExecution(model, pool, tasks, tuple(parents), plan.budget)
    roots = tuple(record['roots'])
    state.execute(roots)
    return GraphProof(model, state, roots)


def linear_rows_graph(owner, *, targets, witnesses, side='ub', residual='exact', **options):
    """Ragged row inputs are an adapter, not a separate checker engine."""
    from mipframe.check.kernel import CertificateBatch
    plan=CheckBatch(owner.check,**options)
    f=plan.f
    targets=tuple(map(int,targets.ids if hasattr(targets,'ids') else targets))
    if len(set(targets))!=len(targets):raise ValueError('Duplicate target row')
    certificate=witnesses if isinstance(witnesses,CertificateBatch) else CertificateBatch.paths(witnesses,**{k:v for k,v in options.items() if k in ('memory_mb','disk_mb')})
    if len(certificate.offsets)!=len(targets)+1:raise ValueError('Certificate dimensions differ')
    for i,ident in enumerate(targets):
        terms,rhs,rel=f.side('row',ident,side)
        target=(plan.pool.terms(terms),rhs,rel)
        sources=[]
        for j in range(int(certificate.offsets[i]),int(certificate.offsets[i+1])):
            kind=certificate.kinds[j]
            if kind not in ('row','var'):raise InvalidCertificateError('Expected model-side sources')
            source=int(certificate.sources[j]);source_side=certificate.sides[j]
            if kind=='row':
                if source not in plan.pool.rows:
                    st,_,_=f.side(kind,source,source_side)
                    plan.pool.rows[source]=plan.pool.terms(st)
            else:
                st,_,_=f.side(kind,source,source_side);plan.pool.terms(st)
            sources.append(('model',kind,source,source_side,certificate.multipliers[j]))
        plan._stage('linear',target,sources,config=(residual,'auto',('row',ident,side)))
    state=GraphExecution(owner,plan.pool,plan.tasks,(),plan.budget)
    try:
        roots=tuple(range(len(plan.tasks)))
        state.execute(roots)
        return GraphProof(owner,state,roots)
    finally:plan.close()


def execute_graph(owner, targets, certificate, *, residual='exact', parents=(), positions=(),
                  assumptions=(), domain='auto', rule='linear', **options):
    """Typed legacy inputs compile into the same logical graph and kernels."""
    if residual not in ('exact','bounds') or domain not in ('auto','integer','relaxation'):
        raise ValueError('Invalid linear options')
    plan=CheckBatch(owner.check,**options)
    n=len(targets.ids)
    if len(certificate.offsets)!=n+1 or len(parents)!=len(positions):
        raise InvalidCertificateError('Invalid certificate dimensions')
    try:
        conditions=tuple((plan.pool.terms(ts),exact(rhs),rel) for ts,rhs,rel in assumptions)
        external=[plan._reference(ProofRef(p,int(i))) for p,i in zip(parents,positions)]
        roots=[]
        for i in range(n):
            terms,rhs,rel=targets.claim(plan.f,i)
            target=plan.pool.terms(terms),exact(rhs),rel
            sources=[]
            for j in range(int(certificate.offsets[i]),int(certificate.offsets[i+1])):
                kind,ident,side=certificate.kinds[j],int(certificate.sources[j]),certificate.sides[j]
                if kind=='proof':ref=external[ident]
                elif kind=='assumption':ref=('assumption',conditions[ident])
                elif kind in ('row','var'):ref=('model',kind,ident,side)
                else:raise InvalidCertificateError('Unknown source kind')
                sources.append((*ref,certificate.multipliers[j]))
            origin=(targets.kind,int(targets.ids[i]),targets.sides[i])
            roots.append(plan._stage(rule,target,sources,conditions,(residual,domain,origin))._index)
        state=GraphExecution(owner,plan.pool,plan.tasks,tuple(plan.parents),plan.budget)
        state.execute(roots)
        return GraphProof(owner,state,roots)
    finally:plan.close()


def immediate(checker, rule, *args, memory_mb=128, disk_mb=None, **kwargs):
    with checker.batch(memory_mb=memory_mb,disk_mb=disk_mb) as plan:
        getattr(plan,rule)(*args,result_key='result',**kwargs)
        return plan.run()['result']
