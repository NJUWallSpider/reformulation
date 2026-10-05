"""Compact immutable entity ID sets with deterministic iteration."""
from collections.abc import Set
from numbers import Integral
import numpy as np


class IDSet(Set):
    def __init__(self,values=(),*,work=None,memory_mb=128,disk_mb=None):
        if isinstance(values,IDSet):self.array,self.mask=values.array,values.mask;return
        from mipframe.data.runtime import WorkContext
        work=work if work is not None else WorkContext(memory_mb,disk_mb)
        self.array=bounded_ids(values._typed_ids() if hasattr(values,'_typed_ids') else values,work)
        self.mask=None
    def __len__(self):return len(self.array)
    def __iter__(self):return map(int,self.array)
    def __contains__(self, value):
        if type(value) is not int and not isinstance(value,Integral):return False
        if not 0<=value<2**64:return False
        if self.mask is not None:return value<len(self.mask) and bool(self.mask[value])
        p=int(np.searchsorted(self.array,value))
        return p<len(self.array) and int(self.array[p])==value


def set_ids(left,right,operation,work):
    """Ascending entity set operations with bounded vector membership probes."""
    from mipframe.data.buffers import Buffer
    a=bounded_ids(left,work);b=bounded_ids(right,work)
    out=Buffer(work,'<u8');block=max(1,min(8192,work.memory//256,work.available//128))
    if operation=='union':
        for start in range(0,len(a),block):out.extend(a[start:start+block])
        a,b=b,a
    with work.reserve(block*64):
        for start in range(0,len(a),block):
            values=a[start:start+block]
            if len(b):
                positions=np.searchsorted(b,values)
                present=(positions<len(b))&(b[np.minimum(positions,len(b)-1)]==values)
            else:present=np.zeros(len(values),dtype=bool)
            out.extend(values[present if operation=='intersection' else ~present])
    result=out.finish()
    return bounded_ids(result,work) if operation=='union' else result


class MutableSelection(Set):
    """An edit's lazy bitmap, allocated within its shared array workspace."""
    def __init__(self,capacity,work):
        self.capacity,self.work=capacity,work
        self.mask=None;self.count=0
    def __len__(self):return self.count
    def __contains__(self,value):
        return self.mask is not None and isinstance(value,Integral) and 0<=value<self.capacity and bool(self.mask[value])
    def __iter__(self):
        if self.mask is not None:
            block=max(1,self.work.memory//16)
            for start in range(0,self.capacity,block):
                yield from (start+int(i) for i in np.flatnonzero(self.mask[start:start+block]))
    def update(self,values):
        from itertools import islice
        if self.mask is None:self.mask=self.work.array(self.capacity,'?',zero=True)
        block=max(1,self.work.memory//32)
        if isinstance(values,np.ndarray):
            for start in range(0,len(values),block):
                ids=np.unique(values[start:start+block])
                if len(ids) and (ids[0]<0 or ids[-1]>=self.capacity):raise ValueError('Selection ID exceeds snapshot capacity')
                self.count+=len(ids)-int(np.count_nonzero(self.mask[ids]));self.mask[ids]=True
            return
        iterator=iter(values)
        while chunk:=tuple(islice(iterator,block)):
            ids=np.unique(np.asarray(chunk,dtype='<u8'))
            if len(ids) and ids[-1]>=self.capacity:raise ValueError('Selection ID exceeds snapshot capacity')
            self.count+=len(ids)-int(np.count_nonzero(self.mask[ids]));self.mask[ids]=True
    def _typed_ids(self):
        from mipframe.data.runtime import leased_array
        work=self.work.fork();result=work.array(self.count,'<u4' if self.capacity<=2**32 else '<u8');offset=0
        if self.mask is not None:
            block=max(1,self.work.memory//64)
            for start in range(0,self.capacity,block):
                ids=np.flatnonzero(self.mask[start:start+block])+start
                result[offset:offset+len(ids)]=ids;offset+=len(ids)
        return leased_array(work.seal(result),work)


def bounded_ids(values,work):
    from mipframe.data.runtime import leased_array
    from mipframe.data.buffers import Buffer
    from mipframe.data.sorting import lexorder
    output=work.fork()
    try:
        with work.fork() as scratch:
            if isinstance(values,np.ndarray) and values.dtype.kind in 'iu':
                if values.ndim!=1 or values.dtype.kind=='i' and np.any(values<0):raise ValueError('Invalid entity ID')
                raw=values
            else:
                buffer=Buffer(scratch,'<u8')
                for v in values:
                    if isinstance(v,bool) or not isinstance(v,Integral) or not 0<=v<2**64:raise ValueError('Invalid entity ID')
                    buffer.append(v)
                raw=buffer.finish()
            block=max(1,work.memory//128)
            sorted_unique=all(np.all(raw[start+1:start+block+1]>raw[start:min(len(raw)-1,start+block)]) for start in range(0,len(raw)-1,block))
            if sorted_unique and hasattr(raw,'_work_owner'):output.close();return raw
            order=None if sorted_unique else lexorder(scratch,(raw,))
            dtype='<u4' if not len(raw) or raw[-1 if order is None else int(order[-1])]<2**32 else '<u8'
            target=output.array(len(raw),dtype);count=0;previous=None;block=max(1,work.memory//128)
            for start in range(0,len(raw),block):
                chunk=raw[start:start+block] if order is None else raw[order[start:start+block]]
                if sorted_unique:target[start:start+len(chunk)]=chunk;count+=len(chunk);continue
                take=np.ones(len(chunk),dtype=bool)
                if len(chunk)>1:take[1:]=chunk[1:]!=chunk[:-1]
                if len(chunk) and previous is not None:take[0]=chunk[0]!=previous
                selected=chunk[take];target[count:count+len(selected)]=selected;count+=len(selected)
                if len(chunk):previous=chunk[-1]
            target=output.seal(target)
        return leased_array(target[:count],output)
    except BaseException:output.close();raise
