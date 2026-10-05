"""Immutable compact exact and categorical columns for certificate boundaries."""
from collections.abc import Sequence
from fractions import Fraction
from numbers import Integral
import numpy as np


class ImmutableColumn:
    __slots__ = ()
    def __delattr__(self,name):raise AttributeError("Exact certificate columns are immutable")
    def __setattr__(self, name, value):
        if hasattr(self, name): raise AttributeError('Exact certificate columns are immutable')
        object.__setattr__(self, name, value)


class ConstantStatus(ImmutableColumn,Sequence):
    __slots__=('value','size')
    def __init__(self,value,size):self.value,self.size=value,size
    def __len__(self):return self.size
    def __getitem__(self,i):
        if isinstance(i,slice):return ConstantStatus(self.value,len(range(*i.indices(self.size))))
        if not -self.size<=i<self.size:raise IndexError(i)
        return self.value
    def count(self,value):return self.size if value==self.value else 0
    def __contains__(self,value):return self.size>0 and self.value==value


class EnumColumn(ImmutableColumn, Sequence):
    __slots__ = ("labels", "codes")
    def __init__(self, values, labels, size, *, work=None, memory_mb=128, disk_mb=None):
        if isinstance(values,EnumColumn):
            if values.labels!=tuple(labels) or len(values)!=size:raise ValueError("Column domain mismatch")
            self.labels,self.codes=values.labels,values.codes;return
        self.labels = tuple(labels)
        lookup = {v:i for i,v in enumerate(labels)}
        if isinstance(values, str):
            try:
                codes = np.broadcast_to(np.frombuffer(bytes([lookup[values]]), dtype="u1"), (size,))
            except KeyError:
                raise ValueError('Invalid categorical value') from None
        else:
            from mipframe.data.runtime import WorkContext
            owner=work if work is not None else WorkContext(memory_mb,disk_mb)
            builder=EnumBuilder(owner,labels)
            try:
                builder.extend(values)
                if len(builder)!=size:raise ValueError('Column length mismatch')
                self.codes=builder.freeze().codes
            except BaseException:builder.work.close();raise
            return
        if len(codes)!=size:raise ValueError('Column length mismatch')
        self.codes=codes

    def __len__(self):return len(self.codes)
    def __iter__(self):
        from mipframe.data.runtime import iteration_block
        block=iteration_block(self.codes)
        for start in range(0,len(self.codes),block):
            yield from map(self.labels.__getitem__,self.codes[start:start+block].tolist())
    def __getitem__(self, i):
        if isinstance(i,slice):
            result=object.__new__(EnumColumn);result.labels=self.labels;result.codes=self.codes[i];return result
        return self.labels[int(self.codes[i])]
    def tolist(self):return list(self)


class ExactColumn(ImmutableColumn, Sequence):
    __slots__ = ("values", "ids")
    """Dictionary IDs plus exact distinct values, avoiding repeated objects."""
    def __init__(self,values,size=None,*,memory_mb=128,disk_mb=None,work=None):
        from mipframe.core.scalars import exact
        if isinstance(values,ExactColumn):
            if size is not None and len(values)!=size:raise ValueError('Column length mismatch')
            self.values,self.ids=values.values,values.ids;return
        if isinstance(values,(str,Integral,Fraction)):
            if size is None:raise ValueError('Scalar column requires a size')
            self.values=(exact(values),);self.ids=np.broadcast_to(np.frombuffer(bytes(4),dtype='<u4'),(size,));return
        from mipframe.data.runtime import WorkContext
        owner=work.fork() if work is not None else WorkContext(memory_mb,disk_mb)
        try:
            builder=ExactBuilder(owner)
            for v in values:builder.append(v)
            if size is not None and len(builder)!=size:raise ValueError('Column length mismatch')
            result=builder.freeze();self.values,self.ids=result.values,result.ids
        finally:owner.close()

    def __len__(self):return len(self.ids)
    def __iter__(self):
        from mipframe.data.runtime import iteration_block
        block=iteration_block(self.ids)
        for start in range(0,len(self.ids),block):
            yield from map(self.values.__getitem__,self.ids[start:start+block].tolist())
    def __getitem__(self,i):
        if isinstance(i,slice):
            result=object.__new__(ExactColumn);result.values=self.values;result.ids=self.ids[i];return result
        return self.values[int(self.ids[i])]
    def tolist(self):return list(self)


class DependencyColumn(ImmutableColumn, Sequence):
    __slots__ = ("ids", "kinds", "sides")
    """Typed premise triples; tuples are produced only when traversing a proof."""
    KINDS=('row','var','integer')
    SIDES=('lb','ub','eq','kind')
    def __init__(self,records=()):
        if isinstance(records,DependencyColumn):
            self.ids,self.kinds,self.sides=records.ids,records.kinds,records.sides;return
        from mipframe.data.runtime import WorkContext,leased_array
        from mipframe.data.buffers import Buffer
        owner=WorkContext()
        try:
            kinds=Buffer(owner,'u1');sides=Buffer(owner,'u1');ids=Buffer(owner,'<u8')
            for kind,ident,side in records:
                if not isinstance(ident,Integral) or not 0<=ident<2**64:raise ValueError('Invalid premise ID')
                kinds.append(self.KINDS.index(kind));ids.append(int(ident));sides.append(self.SIDES.index(side))
            self.ids=leased_array(ids.finish(),owner)
            self.kinds=leased_array(kinds.finish(),owner);self.sides=leased_array(sides.finish(),owner)
        except BaseException:owner.close();raise
    def __len__(self):return len(self.ids)
    def __getitem__(self,i):
        if isinstance(i,slice):
            value=object.__new__(DependencyColumn)
            value.ids,value.kinds,value.sides=self.ids[i],self.kinds[i],self.sides[i];return value
        return self.KINDS[int(self.kinds[i])],int(self.ids[i]),self.SIDES[int(self.sides[i])]


class RaggedDependencies(ImmutableColumn, Sequence):
    __slots__ = ("values", "offsets", "bounds")
    def __init__(self,groups=(),*,bounds=False):
        if isinstance(groups,RaggedDependencies):
            self.values,self.offsets,self.bounds=groups.values,groups.offsets,bounds;return
        from mipframe.data.runtime import WorkContext,leased_array
        from mipframe.data.buffers import Buffer
        owner=WorkContext();offsets=Buffer(owner,'<u8');offsets.append(0)
        def flattened():
            count=0
            for group in groups:
                for row in group:
                    yield ('var',*row) if bounds else row
                    count+=1
                offsets.append(count)
        self.values=DependencyColumn(flattened());self.offsets=leased_array(offsets.finish(),owner)
        self.bounds=bounds
    def __len__(self):return len(self.offsets)-1
    def __getitem__(self,i):
        if isinstance(i,slice):return (self[j] for j in range(*i.indices(len(self))))
        if i<0:i+=len(self)
        if not 0<=i<len(self):raise IndexError(i)
        values=self.values[int(self.offsets[i]):int(self.offsets[i+1])]
        return ((ident,side) for _,ident,side in values) if self.bounds else values


def empty_dependencies(n,*,bounds=False):
    result=object.__new__(RaggedDependencies);result.values=DependencyColumn()
    result.offsets=np.broadcast_to(np.frombuffer(bytes(8),dtype='<u8'),(n+1,));result.bounds=bounds
    return result


class ExactBuilder:
    """Collision-safe dictionary encoding with bounded pools and a small cache."""
    def __init__(self,work=None):
        from mipframe.data.runtime import WorkContext
        from mipframe.data.relations import ColumnBuilder
        from mipframe.data.buffers import Buffer
        from mipframe.data.intern import BytePool
        from collections import OrderedDict
        self.work=work.fork() if work is not None else WorkContext()
        self.builder=ColumnBuilder(self.work,'number');self.pool=BytePool(self.work)
        self.ids=Buffer(self.work,'<u8');self.cache=OrderedDict();self.cache_bytes=0;self._cache_charge=self.work.hold(self.work.memory//64)
    def append(self,value):
        from mipframe.core.scalars import exact
        from mipframe.core.resources import size
        value=exact(value)
        ident=self.cache.get(value)
        if ident is None:
            raw=str(value).encode('ascii')
            if len(raw)>self.work.memory//8:
                from mipframe.core.errors import QueryMemoryError
                raise QueryMemoryError('An exact value exceeds the working-memory budget')
            ident=self.pool.intern((raw,))
            if ident==self.builder.count:self.builder.append(value)
            cost=size(value)+96
            if cost<self.work.memory//64:
                while self.cache and self.cache_bytes+cost>self.work.memory//64:
                    old,_=self.cache.popitem(last=False);self.cache_bytes-=size(old)+96
                self.cache[value]=ident;self.cache_bytes+=cost
        self.ids.append(ident)
    def extend(self,values):
        for value in values:self.append(value)
    def __len__(self):return len(self.ids)
    def __getitem__(self,i):
        from mipframe.data.relations import PackedColumn
        i=int(self.ids[i]);p=self.builder.parts
        return int(p['n'][i]) if 'd' not in p else PackedColumn('number',p)[i]
    def freeze(self):
        from mipframe.data.runtime import leased_array
        result=object.__new__(ExactColumn)
        result.values=TypedExactValues(self.builder.finish(),self.work)
        ids=self.ids.finish()
        if len(result.values)<2**32:
            compact=self.work.array(len(ids),'<u4');compact[:]=ids;self.work.release(ids);ids=self.work.seal(compact)
        result.ids=leased_array(ids,self.work)
        # Interning structures are no longer part of the published result.
        for buffer in (self.pool.data,self.pool.offsets,self.pool.lengths,self.pool.hashes):
            for chunk in buffer.chunks:self.work.release(chunk)
            buffer.chunks=[]
        self.work.release(self.pool.slots);self.cache.clear();self.cache_bytes=0;self.work.release(self._cache_charge)
        return result


class TypedExactValues(Sequence):
    def __init__(self,column,owner):self.column,self.owner=column,owner
    def __len__(self):return len(self.column)
    def __getitem__(self,i):
        if isinstance(i,slice):return (self[j] for j in range(*i.indices(len(self))))
        value=self.column[i]
        return int(value) if isinstance(value,np.integer) else value
    def native_arrays(self):
        if hasattr(self.column,'arrays'):return self.column.arrays
        n=len(self.column)
        return dict(n=self.column,d=np.broadcast_to(np.array([1],dtype='<i8'),(n,)),
            offset=np.broadcast_to(np.array([0],dtype='<u8'),(n,)),length=np.broadcast_to(np.array([0],dtype='<u8'),(n,)),big=np.empty(0,dtype='u1'))


class ArrayExactValues(Sequence):
    """ExactSparse-backed values; bigint text is decoded only on access."""
    def __init__(self, storage):
        self.storage = storage
        self._big = None
    def __len__(self): return len(self.storage.values)
    def __getitem__(self, index):
        if isinstance(index, slice): return [self[i] for i in range(*index.indices(len(self)))]
        row = self.storage.values[index]
        n, d = int(row['n']), int(row['d'])
        if d == 1: return n
        if d: return Fraction(n, d)
        if self._big is None: self._big = self.storage.bigpath.open('rb')
        self._big.seek(int(row['offset']))
        return Fraction(self._big.read(int(row['length'])).decode('ascii'))
    def __del__(self):
        if self._big is not None: self._big.close()


def exact_column_from_storage(storage):
    column = object.__new__(ExactColumn)
    column.values = ArrayExactValues(storage)
    # No second exact-number interning table; identity indices are a range view.
    ids = storage.work.array(len(storage.ids), '<u4' if len(storage.ids)<2**32 else '<u8')
    block=max(1,storage.work.memory//128)
    for start in range(0,len(ids),block):ids[start:start+block]=np.arange(start,min(len(ids),start+block))
    column.ids = storage.work.seal(ids)
    return column


class MappedExactValues(Sequence):
    def __init__(self,arrays):self.arrays=arrays
    def __len__(self):return len(self.arrays['n'])
    def __getitem__(self,i):
        n,d=int(self.arrays['n'][i]),int(self.arrays['d'][i])
        if d==1:return n
        if d:return Fraction(n,d)
        lo=int(self.arrays['offset'][i]);hi=lo+int(self.arrays['length'][i])
        return Fraction(self.arrays['big'][lo:hi].tobytes().decode('ascii'))
    def native_arrays(self):return self.arrays


def _native_exact_arrays(self):
    storage=self.storage
    arrays={n:storage.values[n] for n in ('n','d','offset','length')}
    arrays['big']=np.memmap(storage.bigpath,mode='r',dtype='u1') if storage.bigpath.stat().st_size else np.empty(0,dtype='u1')
    return arrays

ArrayExactValues.native_arrays=_native_exact_arrays


class EnumBuilder:
    def __init__(self,work,labels):
        from mipframe.data.buffers import Buffer
        self.work=work.fork();self.labels=tuple(labels);self.codes=Buffer(self.work,'u1')
    def append(self,value):self.codes.append(self.labels.index(value))
    def extend(self,values):
        for value in values:self.append(value)
    def __len__(self):return len(self.codes)
    def __iter__(self):
        for code in self.codes:yield self.labels[int(code)]
    def freeze(self):
        from mipframe.data.runtime import leased_array
        result=object.__new__(EnumColumn);result.labels=self.labels;result.codes=leased_array(self.codes.finish(),self.work);return result


class DependencyBuilder:
    """Incremental ragged premises; no per-target Python tuple collection."""
    def __init__(self,work,*,bounds=False):
        from mipframe.data.buffers import Buffer
        self.work=work.fork();self.bounds=bounds
        self.ids=Buffer(self.work,'<u8');self.kinds=Buffer(self.work,'u1');self.sides=Buffer(self.work,'u1')
        self.offsets=Buffer(self.work,'<u8');self.offsets.append(0)
    def append(self,kind,ident,side):
        self.ids.append(ident);self.kinds.append(DependencyColumn.KINDS.index(kind));self.sides.append(DependencyColumn.SIDES.index(side))
    def end(self):self.offsets.append(len(self.ids))
    def freeze(self):
        from mipframe.data.runtime import leased_array
        values=object.__new__(DependencyColumn)
        for name in ('ids','kinds','sides'):setattr(values,name,leased_array(getattr(self,name).finish(),self.work))
        result=object.__new__(RaggedDependencies);result.values=values;result.bounds=self.bounds
        result.offsets=leased_array(self.offsets.finish(),self.work)
        return result


class PairColumn(Sequence):
    """Borrowed exact sparse terms; reading a claim never expands its row."""
    def __init__(self,ids,values):self.ids,self.values=ids,values
    def __len__(self):return len(self.ids)
    def __eq__(self,other):return isinstance(other,Sequence) and len(self)==len(other) and all(a==b for a,b in zip(self,other))
    def __getitem__(self,i):
        if isinstance(i,slice):return PairColumn(self.ids[i],self.values[i])
        return int(self.ids[i]),self.values[i]
