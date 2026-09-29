"""Cooperative working-set, candidate and time budgets shared by analyses."""

import time
from .numbers import LPIRError


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, seconds=30, memory_mb=128, candidates=1000000, **kw):
        if kw:
            raise LPIRError(f"Unknown budget fields: {list(kw)}")
        if seconds <= 0 or memory_mb <= 0 or candidates < 1:
            raise LPIRError("Budgets must be positive")
        self.deadline = time.monotonic() + seconds
        self.memory = int(memory_mb * 1024**2)
        self.remaining = candidates

    def tick(self, n=1):
        if time.monotonic() > self.deadline or self.remaining < n:
            raise BudgetExceeded()
        self.remaining -= n

    def require(self, bytes_):
        if bytes_ > self.memory:
            raise BudgetExceeded()
