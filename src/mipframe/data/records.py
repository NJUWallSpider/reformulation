"""Copy-on-write metadata records encoded as typed trees, never SQL/JSON payloads.

Each record is bounded on materialization; an arbitrary number of records can
spill. Tree children are contiguous and shared parent generations stay immutable.
"""
from collections.abc import MutableMapping
from fractions import Fraction
import json
import numpy as np
from mipframe.data.buffers import Buffer
from mipframe.data.intern import BytePool
from mipframe.data.pools import NumberPool
from mipframe.core.resources import size
from mipframe.core.errors import QueryMemoryError


def _key(value):return json.dumps(value,separators=(',',':'),ensure_ascii=False).encode('utf8')
def _unkey(raw):
    value=json.loads(raw)
    return tuple(value) if isinstance(value,list) else value


class FrozenPool:
    def __init__(self,arrays):self.__dict__.update(arrays)
    def __len__(self):return len(self.offsets)
    def value(self,i):
        start=int(self.offsets[i]);return self.data[start:start+int(self.lengths[i])].tobytes()
    def lookup(self,raw):
        import hashlib
        h=int.from_bytes(hashlib.blake2b(raw,digest_size=8).digest(),'little');p=h%len(self.slots)
        while self.slots[p]>=0:
            i=int(self.slots[p])
            if int(self.hashes[i])==h and int(self.lengths[i])==len(raw) and self.value(i)==raw:return i
            p=(p+1)%len(self.slots)
        return None


class RecordMap(MutableMapping):
    def __init__(self,work,parent=None):
        self.work,self.parent=work,parent;self.keys_pool=BytePool(work);self.text=BytePool(work);self.numbers=NumberPool(work)
        self.roots=Buffer(work,'<u8');self.tags=Buffer(work,'u1');self.refs=Buffer(work,'<u8');self.ends=Buffer(work,'<u8')
        self._count=len(parent) if parent is not None else 0;self._encoded=None
    def __len__(self):return self._count
    def __iter__(self):
        if self.parent is not None:
            for key in self.parent:
                if self.keys_pool.lookup(_key(key)) is None:yield key
        for i in range(len(self.keys_pool)):yield _unkey(self.keys_pool.value(i))
    def __getitem__(self,key):
        i=self.keys_pool.lookup(_key(key))
        if i is None:
            if self.parent is not None:return self.parent[key]
            raise KeyError(key)
        return self._decode(int(self.roots[i]))
    def __setitem__(self,key,value):
        if self._encoded is not None:raise ValueError('Published records are immutable; fork a new generation')
        # Input is borrowed for traversal; charge a bounded recursion frame.
        cost=256+size(key)
        if cost>self.work.memory//8:raise QueryMemoryError('A record key exceeds the working-memory budget')
        with self.work.reserve(cost):
            old=self.keys_pool.lookup(_key(key));ident=self.keys_pool.intern((_key(key),))
            root=self._encode(value)
            if old is None:
                self.roots.append(root)
                if self.parent is None or key not in self.parent:self._count+=1
            else:self.roots[ident]=root
    def __delitem__(self,key):raise TypeError('Published record identities cannot be deleted')
    def _encode(self,v):
        if isinstance(v,np.generic):v=v.item()
        if v is None:tag,ref=0,0
        elif isinstance(v,bool):tag,ref=1,int(v)
        elif isinstance(v,(int,Fraction)):tag,ref=2,self.numbers.intern(v)
        elif isinstance(v,str):
            raw=v.encode('utf8')
            if len(raw)>self.work.memory//8:raise QueryMemoryError('A metadata scalar exceeds the working-memory budget')
            tag,ref=3,self.text.intern((raw,))
        elif isinstance(v,(list,tuple)):tag,ref=(4 if isinstance(v,list) else 5),len(v)
        elif isinstance(v,dict):tag,ref=6,len(v)
        else:raise TypeError(f'Unsupported metadata scalar: {type(v).__name__}')
        i=len(self.tags);self.tags.append(tag);self.refs.append(ref);self.ends.append(0)
        if tag in (4,5):
            for x in v:self._encode(x)
        elif tag==6:
            for k,x in v.items():self._encode(k);self._encode(x)
        self.ends[i]=len(self.tags);return i
    def _decode(self,i):
        tag,ref=int(self.tags[i]),int(self.refs[i])
        if tag==0:return None
        if tag==1:return bool(ref)
        if tag==2:
            value=self.numbers[ref];return int(value) if isinstance(value,np.integer) else value
        if tag==3:return self.text.value(ref).decode('utf8')
        p=i+1
        if tag==6:
            out={}
            for _ in range(ref):
                key=self._decode(p);p=int(self.ends[p]);out[key]=self._decode(p);p=int(self.ends[p])
            return out
        out=[]
        for _ in range(ref):out.append(self._decode(p));p=int(self.ends[p])
        return tuple(out) if tag==5 else out
    def export(self,encode):
        if self._encoded is not None:return self._encoded
        def array(buffer):
            if isinstance(buffer,np.ndarray):return encode(buffer)
            with self.work.fork() as scratch:
                a=scratch.array(len(buffer),buffer.dtype);block=max(1,scratch.memory//128)
                for i in range(0,len(a),block):a[i:i+block]=buffer[i:i+block]
                return encode(a)
        def pool(p):return {n:array(getattr(p,n)) for n in ('data','offsets','lengths','hashes','slots')}
        self._encoded=dict(parent=None if self.parent is None else self.parent.export(encode),count=len(self),
            keys=pool(self.keys_pool),text=pool(self.text),numbers={k:array(v) for k,v in self.numbers.column.parts.items()},
            roots=array(self.roots),tags=array(self.tags),refs=array(self.refs),ends=array(self.ends))
        return self._encoded
    @classmethod
    def restore(cls,record,decode,work):
        from mipframe.data.relations import PackedColumn
        obj=object.__new__(cls);obj.work=work;obj._encoded=record;obj._count=record['count']
        obj.parent=None if record['parent'] is None else cls.restore(record['parent'],decode,work)
        obj.keys_pool=FrozenPool({k:decode(v) for k,v in record['keys'].items()})
        obj.text=FrozenPool({k:decode(v) for k,v in record['text'].items()})
        arrays={k:decode(v) for k,v in record['numbers'].items()}
        obj.numbers=PackedColumn('number',arrays) if 'd' in arrays else arrays['n']
        for n in ('roots','tags','refs','ends'):setattr(obj,n,decode(record[n]))
        return obj
