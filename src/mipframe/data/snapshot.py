"""Shared immutable mmap ownership and array-backed metadata projections."""
from pathlib import Path
from collections.abc import Sequence
from math import inf
import numpy as np
from .numbers import ExactNumbers, BIGINT_FALLBACK


class StringArray(Sequence):
    def __init__(self, offsets, data):
        self.offsets, self.data = offsets, data

    def __len__(self):
        return len(self.offsets)-1

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(len(self))) ]
        if i < 0:
            i += len(self)
        if not 0 <= i < len(self):
            raise IndexError(i)
        return self.data[int(self.offsets[i]):int(self.offsets[i+1])].tobytes().decode('utf-8')


def write_strings(path,name,values,*,work=None):
    """Stream string bytes and offsets without a full Python bytearray."""
    from mipframe.data.runtime import WorkContext
    from mipframe.data.buffers import Buffer
    path=Path(path)
    with (work.fork() if work is not None else WorkContext()) as work:
        offsets=Buffer(work,'<u8');offsets.append(0);data=Buffer(work,'u1');block=max(1,work.memory//128)
        for value in values:
            raw=value.encode('utf-8')
            if len(raw)>work.memory//8:
                from mipframe.core.errors import QueryMemoryError
                raise QueryMemoryError('String exceeds working memory')
            data.extend(np.frombuffer(raw,dtype='u1'));offsets.append(len(data))
        np.save(path/(name+'_offsets.npy'),offsets.finish(),allow_pickle=False)
        np.save(path/(name+'_utf8.npy'),data.finish(),allow_pickle=False)


def write_pool_strings(path,name,pool,*,work):
    """Publish an already packed dictionary without decoding/re-encoding it."""
    path=Path(path)
    with work.fork() as scratch:
        offsets=scratch.array(len(pool)+1,'<u8');position=0
        for block in pool.offsets.blocks():
            offsets[position:position+len(block)]=block;position+=len(block)
        offsets[-1]=len(pool.data)
        np.save(path/(name+'_offsets.npy'),offsets,allow_pickle=False)
        data=scratch.array(len(pool.data),'u1');position=0
        for block in pool.data.blocks():
            data[position:position+len(block)]=block;position+=len(block)
        np.save(path/(name+'_utf8.npy'),data,allow_pickle=False)


class Metadata(Sequence):
    def __init__(self, data, axis, *, exact=False):
        self.data, self.axis, self.exact = data, axis, exact

    def __len__(self):
        return len(self.data.arrays[self.axis+'_lb_id'])

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[j] for j in range(*i.indices(len(self)))]
        i = int(i)
        if i < 0:
            i += len(self)
        if not 0 <= i < len(self):
            raise IndexError(i)
        a, axis = self.data.arrays, self.axis
        lo = int(a[axis+'_lb_id'][i]) if a[axis+'_lb_finite'][i] else None
        hi = int(a[axis+'_ub_id'][i]) if a[axis+'_ub_finite'][i] else None
        name = self.data.names[axis][i]
        if self.exact:
            lo = -inf if lo is None else self.data.numbers[lo]
            hi = inf if hi is None else self.data.numbers[hi]
        if axis == 'row':
            result = (i, name, lo, hi)
        else:
            obj = int(a['var_obj_id'][i])
            result = (i, name, 'I' if a['var_type'][i] else 'C', lo, hi,
                      self.data.numbers[obj] if self.exact else obj)
        return result if self.exact else (*result, int(a[axis+'_line'][i]))


class SnapshotData:
    def __init__(self, path, db):
        self.path = Path(path)
        self.maps = {p.stem: np.load(p, mmap_mode='r', allow_pickle=False)
                     for p in self.path.glob('*.npy')}
        # Plain ndarray views avoid constructing a new memmap wrapper per slice.
        self.arrays = {k: np.asarray(v) for k, v in self.maps.items()}
        from .numbers import BigParts
        big=BigParts(StringArray(self.arrays['number_big_offsets'],self.arrays['number_big_utf8']))
        self.numbers = ExactNumbers(self.arrays, big)
        from .sparse import SparseDataPlane
        self.sparse = SparseDataPlane(self.arrays)
        self.names = {axis: StringArray(self.arrays[axis+'_name_offsets'], self.arrays[axis+'_name_utf8'])
                      for axis in ('row','var')}
        self.rows, self.variables = Metadata(self,'row'), Metadata(self,'var')
        self.references = 1
        self.temporary = None
        self.closed = False

    def acquire(self):
        if self.closed:
            raise ValueError('Snapshot data is closed')
        self.references += 1
        return self

    def release(self):
        if self.closed:
            return
        self.references -= 1
        if self.references:
            return
        self.closed = True
        for a in (*self.maps.values(),*getattr(self,"artifact_maps",())):
            if getattr(a, '_mmap', None) is not None and not a._mmap.closed:
                a._mmap.close()
        if getattr(self,'cache_temporary',None) is not None:
            self.cache_temporary.cleanup();self.cache_temporary=None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def __del__(self):
        if getattr(self, 'references', 0):
            self.references = 1
            self.release()
