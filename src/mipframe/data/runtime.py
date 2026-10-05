"""Shared resource ledger and explicit ownership for typed workspaces.

A child workspace spends the same budget. Arrays can move to a result lease
without copying or renewing that budget. Input snapshot mappings are not scratch.
"""
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from contextvars import ContextVar
_active = ContextVar("mipframe_workspace", default=None)


@dataclass
class ResourceLedger:
    memory: int
    disk_limit: int | None
    resident: int = 0
    disk: int = 0
    peak_resident: int = 0
    peak_disk: int = 0

    def charge_disk(self, size):
        if size > 0 and self.disk_limit is not None and self.disk + size > self.disk_limit:
            from mipframe.core.errors import DiskBudgetExceeded
            raise DiskBudgetExceeded('Shared workspace disk budget exceeded')
        self.disk += size
        self.peak_disk = max(self.peak_disk, self.disk)


class _Reservation:
    __slots__ = ('work', 'size')

    def __init__(self, work, size):
        self.work, self.size = work, size

    def __enter__(self):
        from mipframe.core.errors import QueryMemoryError
        work, size = self.work, self.size
        if work.closed or size < 0 or size > work.available:
            raise QueryMemoryError('Shared transient memory budget exceeded')
        work.ledger.resident += size
        work.ledger.peak_resident = max(work.ledger.peak_resident, work.resident)
        return size

    def __exit__(self, *_):
        self.work.ledger.resident -= self.size


class WorkContext:
    def __init__(self, memory_mb=128, disk_mb=None, *, ledger=None):
        from mipframe.core.resources import resource_limits
        memory,disk_limit=resource_limits(memory_mb,disk_mb)
        active=_active.get()
        if ledger is None and active is not None and not active.closed:ledger=active.ledger
        if ledger is None:ledger=ResourceLedger(memory,disk_limit)
        self.ledger = ledger
        self.maps = []
        self.temp = None
        self.closed = False
        self._allocations = {}
        self._serial = 0

    @property
    def memory(self): return self.ledger.memory
    @property
    def resident(self): return self.ledger.resident
    @property
    def disk(self): return self.ledger.disk
    @property
    def disk_limit(self): return self.ledger.disk_limit
    @disk_limit.setter
    def disk_limit(self, value): self.ledger.disk_limit = value
    @property
    def available(self): return max(0, self.memory-self.resident)

    def fork(self):
        if self.closed: raise ValueError('Workspace is closed')
        return WorkContext(ledger=self.ledger)

    def hold(self,size):
        """Retain a charge for a bounded Python cache until workspace release."""
        from mipframe.core.errors import QueryMemoryError
        if self.closed or size<0 or size>self.available:raise QueryMemoryError('Insufficient cache workspace')
        token=object();self.ledger.resident+=size
        self.ledger.peak_resident=max(self.ledger.peak_resident,self.resident)
        self._allocations[id(token)]=(token,size,None)
        return token

    def reserve(self, size):
        """Charge bounded Python/NumPy transient buffers to this same ledger."""
        return _Reservation(self, size)

    def _path(self):
        if self.temp is None: self.temp = TemporaryDirectory(prefix='mipframe-arrays-')
        self._serial += 1
        return Path(self.temp.name)/f'{self._serial}.npy'

    def array(self, shape, dtype, *, zero=False, disk=False):
        if self.closed: raise ValueError('Workspace is closed')
        from math import prod
        from numbers import Integral
        import sys
        dimensions = shape if isinstance(shape, tuple) else (shape,)
        if any(isinstance(n, bool) or not isinstance(n, Integral) or n < 0 for n in dimensions):
            raise ValueError('Array dimensions must be nonnegative integers')
        dtype = np.dtype(dtype)
        if dtype.hasobject: raise TypeError('Workspace arrays must have a primitive dtype')
        size = prod(map(int, dimensions))*dtype.itemsize
        if size > sys.maxsize: raise OverflowError('Array workspace size exceeds addressable capacity')
        if not size or (not disk and size <= max(0,self.available-self.memory//4)):
            a = np.zeros(shape, dtype=dtype) if zero else np.empty(shape, dtype=dtype)
            self.ledger.resident += size
            self.ledger.peak_resident = max(self.ledger.peak_resident, self.resident)
            self._allocations[id(a)] = (a, size, None)
            return a
        path = self._path()
        self.ledger.charge_disk(size+256)
        try:
            a = np.lib.format.open_memmap(path, mode='w+', dtype=dtype, shape=tuple(map(int, dimensions)))
            actual = path.stat().st_size
            self.ledger.charge_disk(actual-size-256)
        except BaseException:
            self.ledger.charge_disk(-size-256)
            path.unlink(missing_ok=True)
            raise
        self.maps.append(a)
        self._allocations[id(a)] = (a, actual, path)
        if zero: a[:] = 0
        return a

    def _key(self, a):
        while id(a) not in self._allocations and isinstance(getattr(a, 'base', None), np.ndarray):
            a = a.base
        if id(a) not in self._allocations: raise ValueError('Array is not owned by this workspace')
        return id(a)

    def transfer(self, a, target):
        """Move the allocation (including views) to another owner of this ledger."""
        if self.closed or target.closed: raise ValueError('Workspace is closed')
        if self.ledger is not target.ledger: raise ValueError('Cannot transfer across resource ledgers')
        key = self._key(a)
        root, size, path = self._allocations.pop(key)
        if path is not None:
            destination = target._path()
            path.rename(destination)
            self.maps = [v for v in self.maps if v is not root]
            target.maps.append(root)
            path = destination
        target._allocations[key] = (root, size, path)
        return a

    def seal(self, a):
        """Return storage whose read-only flag cannot be reversed by a caller."""
        key=self._key(a);root,size,path=self._allocations[key]
        if a is not root:raise ValueError('Seal a complete allocation before taking views')
        if path is None:
            if size<=self.available:
                result=np.frombuffer(a.tobytes(),dtype=a.dtype).reshape(a.shape)
                self.ledger.resident+=size
                self.ledger.peak_resident=max(self.ledger.peak_resident,self.resident)
                self.release(a);self._allocations[id(result)]=(result,size,None)
                return result
            result=self.array(a.shape,a.dtype,disk=True);result[:]=a
            self.release(a)
            return self.seal(result)
        root.flush()
        result=np.load(path,mmap_mode='r',allow_pickle=False)
        root._mmap.close()
        self._allocations.pop(key)
        self.maps=[v for v in self.maps if v is not root];self.maps.append(result)
        self._allocations[id(result)]=(result,size,path)
        return result

    def release(self, a):
        root, size, path = self._allocations.pop(self._key(a))
        if path is None:
            self.ledger.resident -= size
        else:
            if not root._mmap.closed: root._mmap.close()
            path.unlink(missing_ok=True)
            self.ledger.charge_disk(-size)
            self.maps = [v for v in self.maps if v is not root]

    def close(self):
        if self.closed: return
        for root, _, _ in list(self._allocations.values()): self.release(root)
        self.closed = True
        if self.temp is not None:
            self.temp.cleanup()
            self.temp = None

    def __del__(self):
        if hasattr(self, '_allocations') and not self.closed: self.close()
    def activate(self):return _active.set(self)
    def deactivate(self,token):_active.reset(token)
    def __enter__(self):self._token=self.activate();return self
    def __exit__(self,*_):
        self.deactivate(self._token);self.close()


class LeasedArray(np.ndarray):
    """Immutable array view retaining the workspace which owns its storage."""
    def __array_finalize__(self,parent):self._work_owner=getattr(parent,'_work_owner',None)


def leased_array(array,work):
    result=array.view(LeasedArray);result._work_owner=work;return result


class DiskReservation:
    """Track a temporary byte artifact until it is published or discarded."""
    def __init__(self,work):self.work=work;self.size=0
    def add(self,n):self.work.ledger.charge_disk(n);self.size+=n
    def close(self):self.work.ledger.charge_disk(-self.size);self.size=0
    def __del__(self):self.close()


def resource_scope(fn):
    """Carry one caller budget across compilation, kernels and publication."""
    from functools import wraps
    @wraps(fn)
    def call(*args,**kwargs):
        with WorkContext(kwargs.get('memory_mb',128),kwargs.get('disk_mb')):
            return fn(*args,**kwargs)
    return call


def workspace_method(fn):
    """Run a builder method against the ledger selected at construction."""
    from functools import wraps
    @wraps(fn)
    def call(self,*args,**kwargs):
        token=self.work.activate()
        try:return fn(self,*args,**kwargs)
        finally:self.work.deactivate(token)
    return call


def iteration_block(array):
    work=_active.get() or getattr(array,'_work_owner',None)
    return max(1,min(4096,(work.memory if work is not None else 128*1024**2)//128))
