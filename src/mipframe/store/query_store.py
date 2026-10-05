"""Ordered entity selection adapters. Numeric state lives in typed arrays."""
from collections.abc import Sequence
from mipframe.core.resources import memory_bytes
from mipframe.data.runtime import WorkContext,leased_array
from mipframe.data.buffers import Buffer
from mipframe.data.sorting import lexorder


class OrderedIndex(Sequence):
    unique=True
    def __init__(self,owner,array):self.owner,self.array=owner,array;self._members=None
    def __len__(self):self.owner.model;return len(self.array)
    def __iter__(self):
        self.owner.model
        for i in self.array:self.owner.model;yield int(i)
    def __getitem__(self,key):
        self.owner.model
        return OrderedIndex(self.owner,self.array[key]) if isinstance(key,slice) else int(self.array[key])
    def __contains__(self,value):
        self.owner.model
        if self._members is None:
            from mipframe.data.ids import IDSet
            self._members=IDSet(self.array,work=self.array._work_owner)
        return value in self._members
    def __eq__(self,other):return isinstance(other,Sequence) and len(self)==len(other) and all(a==b for a,b in zip(self,other))
    __hash__=None


def stored_index(owner,values,memory_mb=128,*,unique=False,disk_mb=None):
    owner.model
    work=WorkContext(memory_mb,disk_mb)
    try:
        with work.fork() as scratch:
            b=Buffer(scratch,'<u8')
            for v in values:b.append(int(v))
            raw=b.finish()
            if unique:
                order=lexorder(scratch,(raw,));first=Buffer(scratch,'<u8');previous=None
                for pos in order:
                    v=int(raw[pos])
                    if v!=previous:first.append(int(pos));previous=v
                positions=first.finish();sequence=lexorder(scratch,(positions,))
                result=work.array(len(positions),'<u8')
                for i,p in enumerate(sequence):result[i]=raw[positions[p]]
            else:result=work.array(len(raw),'<u8');result[:]=raw
            result=leased_array(work.seal(result),work)
        return OrderedIndex(owner,result)
    except BaseException:work.close();raise
