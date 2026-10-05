"""Finite, cooperative budgets shared by nested check operations."""
from contextvars import ContextVar
from math import isfinite
from time import perf_counter

_active = ContextVar('mipframe_check_budget', default=None)


class ProofBudgetExceeded(RuntimeError):
    """
    A check stopped without publishing a partial proof.

    MIPFRAME HELP BEGIN
    A finite check budget stopped unpublished work.

    Full contract: mf.help("api.ProofBudgetExceeded")
    MIPFRAME HELP END
    """
    def __init__(self, phase, resource, consumed, limit):
        self.phase, self.resource = phase, resource
        self.consumed, self.limit = consumed, limit
        super().__init__(f'Check budget exceeded in {phase}: {resource}={consumed} '
                         f'(limit {limit}); use check.batch() to share work, or '
                         'explicitly raise this finite budget.')


class CheckBudget:
    def __init__(self, *, time_limit=30, max_tasks=1_000_000,
                 max_nodes=4096, max_work=100_000_000, memory_mb=128, disk_mb=None):
        for name, value in dict(time_limit=time_limit, max_tasks=max_tasks,
                                max_nodes=max_nodes, max_work=max_work,
                                memory_mb=memory_mb).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        for name,value in dict(max_tasks=max_tasks,max_nodes=max_nodes,max_work=max_work).items():
            if type(value) is not int:raise ValueError(f'{name} must be a positive integer')
        self.limits = dict(time_limit=time_limit, tasks=max_tasks, nodes=max_nodes,
                           work=max_work, memory=memory_mb * 1024**2)
        self.started = perf_counter()
        self.counts = dict(tasks=0, nodes=0, work=0, memory=0)
        self.phase = 'registration'
        self.disk_mb = disk_mb
        self.tokens = []

    def charge(self, resource='work', count=1, *, phase=None):
        if phase is not None:
            self.phase = phase
        self.counts[resource] += count
        if self.counts[resource] > self.limits[resource]:
            raise ProofBudgetExceeded(self.phase, resource, self.counts[resource], self.limits[resource])
        self.poll()

    def poll(self):
        elapsed = perf_counter() - self.started
        if elapsed > self.limits['time_limit']:
            raise ProofBudgetExceeded(self.phase, 'time_limit', elapsed, self.limits['time_limit'])

    def __enter__(self):
        self.tokens.append(_active.set(self))
        return self

    def __exit__(self, *_):
        _active.reset(self.tokens.pop())


def current_budget():
    return _active.get()


from contextlib import contextmanager
from functools import wraps


@contextmanager
def activate(budget):
    token=_active.set(budget)
    try:yield budget
    finally:_active.reset(token)


def check_operation(fn):
    """One finite clock/work meter for an operation and all its nested checks."""
    @wraps(fn)
    def call(*args,**kwargs):
        budget=current_budget() or CheckBudget(memory_mb=kwargs.get('memory_mb',128),disk_mb=kwargs.get('disk_mb'))
        with activate(budget):
            budget.poll()
            result=fn(*args,**kwargs)
            budget.poll()
            return result
    return call


def batch_operation(fn):
    @wraps(fn)
    def call(self,*args,**kwargs):
        with activate(self.budget):return fn(self,*args,**kwargs)
    return call
