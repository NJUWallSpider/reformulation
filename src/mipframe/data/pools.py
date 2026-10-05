"""Mutable, budgeted import dictionaries with collision-safe typed indices."""
from collections.abc import Mapping, Sequence
from fractions import Fraction
from collections import OrderedDict
from mipframe.data.intern import BytePool
from mipframe.core.errors import QueryMemoryError


class TextValues(Sequence):
    def __init__(self,pool):self.pool=pool
    def __len__(self):return len(self.pool)
    def __getitem__(self,i):
        if not 0<=i<len(self):raise IndexError(i)
        return self.pool.value(i).decode('utf8')


class NameIndex(Mapping):
    def __init__(self,work,*,cache_fraction=16):
        self.pool=BytePool(work);self.names=TextValues(self.pool);self.cache=OrderedDict();self.cache_size=0
        self.cache_limit=work.memory//cache_fraction;self._cache_charge=work.hold(self.cache_limit)
    def __len__(self):return len(self.pool)
    def __iter__(self):return iter(self.names)
    def __getitem__(self,key):
        if key in self.cache:return self.cache[key]
        found=self.pool.lookup(key.encode('utf8'))
        if found is None:raise KeyError(key)
        self._cache(key,found)
        return found
    def _cache(self,key,value):
        cost=128+len(key)*4
        if cost>self.cache_limit:return
        while self.cache and self.cache_size+cost>self.cache_limit:
            old,_=self.cache.popitem(last=False);self.cache_size-=128+len(old)*4
        if key not in self.cache:self.cache[key]=value;self.cache_size+=cost
    def __setitem__(self,key,value):
        if self.intern(key)!=value:raise ValueError('Entity identity collision')
    def intern(self,key):
        if key in self.cache:return self.cache[key]
        raw=key.encode('utf8')
        if len(raw)>self.pool.work.memory//8:raise QueryMemoryError('An entity name exceeds the working-memory budget')
        value=self.pool.intern((raw,))
        self._cache(key,value)
        return value


class NumberPool(Sequence):
    def __init__(self,work):
        from mipframe.data.relations import ColumnBuilder
        self.pool=BytePool(work);self.column=ColumnBuilder(work,'number');self.cache=OrderedDict();self.cache_size=0;self._cache_charge=work.hold(work.memory//128)
    def __len__(self):return self.column.count
    def intern(self,value):
        if value in self.cache:return self.cache[value]
        raw=str(value).encode('ascii')
        if len(raw)>self.pool.work.memory//8:raise QueryMemoryError('An exact value exceeds the working-memory budget')
        ident=self.pool.intern((raw,))
        if ident==len(self):self.column.append(value)
        from mipframe.core.resources import size
        cost=size(value)+96
        if cost<self.pool.work.memory//128:
            while self.cache and self.cache_size+cost>self.pool.work.memory//128:
                old,_=self.cache.popitem(last=False);self.cache_size-=size(old)+96
            self.cache[value]=ident;self.cache_size+=cost
        return ident
    def __getitem__(self,i):
        if not 0<=i<len(self):raise IndexError(i)
        from mipframe.data.relations import PackedColumn
        parts=self.column.parts
        return int(parts['n'][i]) if 'd' not in parts else PackedColumn('number',parts)[i]
