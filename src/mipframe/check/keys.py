"""Typed external certificate identities, retaining first-appearance order."""
import json
import hashlib
from collections.abc import Sequence
from mipframe.data.intern import BytePool
from mipframe.frame.schema import encode,decode
from mipframe.core.errors import QueryMemoryError


class Keys(Sequence):
    def __init__(self,work):self.work=work.fork();self.pool=BytePool(self.work)
    def _bytes(self,key):
        raw=json.dumps(encode(key),sort_keys=True,separators=(',',':')).encode('utf8')
        if len(raw)>self.work.memory//8:raise QueryMemoryError('Certificate key exceeds working memory')
        return raw
    def append(self,key):
        before=len(self);index=self.pool.intern((self._bytes(key),))
        if index!=before:raise ValueError('Duplicate target key')
    def lookup(self,key):
        raw=self._bytes(key);h=int.from_bytes(hashlib.blake2b(raw,digest_size=8).digest(),'little');slot=h%len(self.pool.slots)
        while self.pool.slots[slot]>=0:
            i=int(self.pool.slots[slot])
            if int(self.pool.hashes[i])==h and self.pool.value(i)==raw:return i
            slot=(slot+1)%len(self.pool.slots)
        raise KeyError(key)
    def __len__(self):return len(self.pool)
    def __getitem__(self,i):
        if isinstance(i,slice):return tuple(self[j] for j in range(*i.indices(len(self))))
        if i<0:i+=len(self)
        if not 0<=i<len(self):raise IndexError(i)
        return decode(json.loads(self.pool.value(i)))
    def tolist(self):return list(self)


class EncodedKeys(Sequence):
    def __init__(self,arrays):self.arrays=arrays
    def __len__(self):return len(self.arrays['offsets'])
    def __getitem__(self,i):
        if isinstance(i,slice):return tuple(self[j] for j in range(*i.indices(len(self))))
        a=self.arrays;start=int(a['offsets'][i]);end=start+int(a['lengths'][i])
        return decode(json.loads(a['data'][start:end].tobytes()))
