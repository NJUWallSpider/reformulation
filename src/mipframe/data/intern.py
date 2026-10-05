"""Collision-safe streaming byte interning using budgeted primitive arrays."""
import hashlib
from collections import OrderedDict
from itertools import chain
from bisect import bisect_right
import numpy as np
from mipframe.data.buffers import Buffer


class BytePool:
    def __init__(self,work):
        self.work=work
        self.data=Buffer(work,'u1');self.offsets=Buffer(work,'<u8');self.lengths=Buffer(work,'<u8');self.hashes=Buffer(work,'<u8')
        self.slots=work.array(16,'<i8');self.slots[:]=-1
        self.cache=OrderedDict();self.cache_bytes=0;self.cache_limit=None
    def __len__(self):return len(self.offsets)
    def _grow(self):
        old=self.slots;self.slots=self.work.array(len(old)*2,'<i8');self.slots[:]=-1
        for i in range(len(self)):
            p=int(self.hashes[i])%len(self.slots)
            while self.slots[p]>=0:p=(p+1)%len(self.slots)
            self.slots[p]=i
        self.work.release(old)
    def chunks(self,i,block=4096):
        start=int(self.offsets[i]);end=start+int(self.lengths[i])
        while start<end:
            p=bisect_right(self.data.starts,start)-1
            chunk=self.data.chunks[p];pos=start-self.data.starts[p];n=min(block,end-start,len(chunk)-pos)
            yield chunk[pos:pos+n].tobytes();start+=n
    def value(self,i):
        start=int(self.offsets[i]);length=int(self.lengths[i])
        if not length:return b''
        p=bisect_right(self.data.starts,start)-1;pos=start-self.data.starts[p];chunk=self.data.chunks[p]
        if pos+length<=len(chunk):return chunk[pos:pos+length].tobytes()
        return b''.join(self.chunks(i))
    def lookup(self,raw):
        return self._find(raw)[0]
    def _find(self,raw):
        h=int.from_bytes(hashlib.blake2b(raw,digest_size=8).digest(),'little');p=h%len(self.slots)
        while self.slots[p]>=0:
            i=int(self.slots[p])
            if int(self.hashes[i])==h and int(self.lengths[i])==len(raw):
                offset=0
                for chunk in self.chunks(i):
                    if chunk!=raw[offset:offset+len(chunk)]:break
                    offset+=len(chunk)
                else:return i,p,h
            p=(p+1)%len(self.slots)
        return None,p,h
    def intern(self,chunks):
        # Small repeated signatures must not be appended and compared byte by
        # byte on every occurrence. Bound both this cache and the packet used
        # to probe it; long signatures still stream into the same exact pool.
        if self.cache_limit is None:
            self.cache_limit=min(65536,self.work.memory//128,self.work.available//16)
            self._cache_charge=self.work.hold(self.cache_limit)
        packet_limit=min(512,self.work.memory//128,self.work.available//4)
        if isinstance(chunks,(tuple,list)) and len(chunks)==1:
            return self._intern_value(chunks[0])
        with self.work.reserve(packet_limit*2):
            packet=bytearray();iterator=iter(chunks)
            for chunk in iterator:
                if len(packet)+len(chunk)>packet_limit:
                    return self._intern_stream(chain((packet,chunk),iterator))
                packet.extend(chunk)
            return self._intern_value(bytes(packet))

    def _intern_value(self,raw):
        if raw in self.cache:return self.cache[raw]
        result,p,h=self._find(raw)
        if result is None:
            if len(self)*2>=len(self.slots):
                self._grow();p=h%len(self.slots)
                while self.slots[p]>=0:p=(p+1)%len(self.slots)
            result=len(self);start=self.data.count
            self.data.extend(np.frombuffer(raw,dtype='u1'))
            self.offsets.append(start);self.lengths.append(len(raw));self.hashes.append(h);self.slots[p]=result
        cost=128+len(raw)
        if cost<=self.cache_limit:
            while self.cache_bytes+cost>self.cache_limit:
                old,_=self.cache.popitem(last=False);self.cache_bytes-=128+len(old)
            self.cache[raw]=result;self.cache_bytes+=cost
        return result

    def _intern_stream(self,chunks):
        if len(self)*2>=len(self.slots):self._grow()
        start=self.data.count;digest=hashlib.blake2b(digest_size=8)
        for chunk in chunks:
            digest.update(chunk);self.data.extend(np.frombuffer(chunk,dtype='u1'))
        length=self.data.count-start;h=int.from_bytes(digest.digest(),'little');p=h%len(self.slots)
        while self.slots[p]>=0:
            i=int(self.slots[p])
            if int(self.hashes[i])==h and int(self.lengths[i])==length:
                old=int(self.offsets[i])
                def equal():
                    at=0
                    while at<length:
                        a=old+at;b=start+at
                        ai=bisect_right(self.data.starts,a)-1;bi=bisect_right(self.data.starts,b)-1
                        aa=self.data.chunks[ai];bb=self.data.chunks[bi]
                        ap=a-self.data.starts[ai];bp=b-self.data.starts[bi]
                        n=min(length-at,len(aa)-ap,len(bb)-bp,max(1,self.work.memory//128))
                        if not np.array_equal(aa[ap:ap+n],bb[bp:bp+n]):return False
                        at+=n
                    return True
                if equal():
                    self.data.count=start;return i
            p=(p+1)%len(self.slots)
        i=len(self);self.offsets.append(start);self.lengths.append(length);self.hashes.append(h);self.slots[p]=i
        return i
