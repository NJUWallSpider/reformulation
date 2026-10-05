"""Bounded exact sparse accumulation using sorted runs and immutable typed output."""
from array import array
from contextlib import ExitStack
from fractions import Fraction
import heapq
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from mipframe.data.runtime import WorkContext
from mipframe.core.resources import size
from mipframe.core.errors import QueryMemoryError
from mipframe.core.errors import DiskBudgetExceeded


class ExactSparse:
    def __init__(self, memory_mb=128, disk_mb=None, *, work=None):
        self.work=work.fork() if work is not None else WorkContext(memory_mb,disk_mb)
        self.temp=None;self.serial=0;self.disk=0;self.closed=False
        self.ids=None;self.values=None

    def guard(self):
        if self.closed:raise ValueError('Expression storage is closed')

    def close(self):
        if getattr(self,"closed",True):return
        self.closed=True;self.work.close();self.work.ledger.charge_disk(-self.disk);self.disk=0
        if self.temp is not None:self.temp.cleanup()

    def __del__(self):self.close()

    def _charge(self, size):
        self.work.ledger.charge_disk(size)
        self.disk += size

    def check(self, value):
        if size(value)>self.work.memory//2:
            raise QueryMemoryError('An exact value exceeds the working-memory budget')

    def _path(self):
        if self.temp is None:self.temp=TemporaryDirectory(prefix='mipframe-exact-')
        self.serial+=1
        return Path(self.temp.name)/str(self.serial)

    def _write(self, records):
        path=self._path()
        with path.open('w',encoding='ascii') as stream:
            for v,c in records:
                self.check(c)
                text=f'{v} {c.numerator} {c.denominator}\n'
                if self.work.disk_limit is not None and self.work.disk+len(text)>self.work.disk_limit:
                    raise DiskBudgetExceeded('Exact sparse workspace disk budget exceeded')
                self._charge(len(text));stream.write(text)
        return path

    @staticmethod
    def _read(stream):
        for line in stream:
            v,n,d=line.split();d=int(d);yield int(v),int(n) if d==1 else Fraction(int(n),d)

    def _merged(self, paths, duplicates):
        with ExitStack() as stack:
            inputs=[self._read(stack.enter_context(p.open(encoding='ascii'))) for p in paths]
            current=None;value=0
            for v,c in heapq.merge(*inputs,key=lambda item:item[0]):
                if v!=current:
                    if current is not None and (value or duplicates):yield current,value
                    current,value=v,c
                else:
                    if duplicates:raise ValueError('Duplicate vector variable')
                    value+=c;self.check(value)
            if current is not None and (value or duplicates):yield current,value

    def build(self, records, *, duplicates=False):
        with self.work.reserve(min(self.work.memory//8,self.work.available//2)) as budget:
            return self._build(records,duplicates,budget)

    def _build(self, records, duplicates, budget):
        values={};used=0;levels=[]
        def add_run(path,level=0):
            while len(levels)<=level:levels.append([])
            levels[level].append(path)
            if len(levels[level])==32:
                merged=self._merge_run(levels[level],duplicates);levels[level]=[]
                add_run(merged,level+1)
        for v,c in records:
            c=c if type(c) is int else Fraction(c);self.check(c)
            if v in values:
                if duplicates:raise ValueError('Duplicate vector variable')
                old=values.pop(v);used-=size(old)+104;c+=old;self.check(c)
            if c or duplicates:values[v]=c;used+=size(c)+104
            if used>max(1,budget//2):
                add_run(self._write(sorted(values.items())));values={};used=0
        paths=[p for level in levels for p in level]
        if not paths:
            ordered=sorted(values.items())
        else:
            if values:paths.append(self._write(sorted(values.items())))
            while len(paths)>32:
                paths=[self._merge_run(paths[i:i+32],duplicates) for i in range(0,len(paths),32)]
            ordered=self._merged(paths,duplicates)
        self._finish(ordered)
        for path in paths:self._charge(-path.stat().st_size);path.unlink()
        return self

    def _merge_run(self, paths, duplicates):
        result=self._write(self._merged(paths,duplicates))
        for path in paths:self._charge(-path.stat().st_size);path.unlink()
        return result

    def _finish(self, records):
        # Fixed-width records hold common exact rationals; rare big integers use
        # byte offsets in a separate immutable payload, decoded at access time.
        dtype=np.dtype([('id','<u8'),('n','<i8'),('d','<i8'),('offset','<u8'),('length','<u8')])
        path=self._path();bigpath=self._path();count=0
        buffer=bytearray();threshold=max(dtype.itemsize,self.work.memory//8);pack=struct.Struct('<QqqQQ').pack
        with path.open('wb') as out,bigpath.open('wb') as big:
            for v,c in records:
                if -(1<<63)<=c.numerator<(1<<63) and 0<c.denominator<(1<<63):
                    item=(v,c.numerator,c.denominator,0,0)
                else:
                    text=f'{c.numerator}/{c.denominator}'.encode('ascii')
                    item=(v,0,0,big.tell(),len(text));self._charge(len(text));big.write(text)
                buffer.extend(pack(*item));count+=1
                if self.work.disk_limit is not None and self.work.disk+len(buffer)>self.work.disk_limit:
                    raise DiskBudgetExceeded('Exact sparse workspace disk budget exceeded')
                if len(buffer)>=threshold:self._charge(len(buffer));out.write(buffer);buffer.clear()
            self._charge(len(buffer));out.write(buffer)
        target=self.work.array(count,dtype)
        with path.open('rb') as stream:
            block=max(1,self.work.memory//(4*dtype.itemsize));offset=0
            while offset<count:
                values=np.frombuffer(stream.read(block*dtype.itemsize),dtype=dtype)
                target[offset:offset+len(values)]=values;offset+=len(values)
        target=self.work.seal(target);self.values=target;self.ids=target['id'];self.bigpath=bigpath
        self._charge(-path.stat().st_size);path.unlink()

    def items(self):
        self.guard()
        with self.bigpath.open('rb') as big:
            for row in self.values:
                self.guard()
                if row['d']:value=Fraction(int(row['n']),int(row['d']))
                else:big.seek(int(row['offset']));value=Fraction(big.read(int(row['length'])).decode('ascii'))
                yield int(row['id']),value


class ExactPoint:
    """Exact sparse point with random array lookup and bounded bigint decoding."""
    def __init__(self,storage,facts):
        self.storage,self.facts=storage,facts
        self.big=storage.bigpath.open('rb')
    def guard(self):self.storage.guard()
    def __getitem__(self,ident):
        self.storage.guard()
        ids=self.storage.ids
        p=int(np.searchsorted(ids,np.uint64(ident)))
        if p==len(ids) or ids[p]!=ident:return 0
        row=self.storage.values[p];d=int(row['d'])
        if d==1:return int(row['n'])
        if d:return Fraction(int(row['n']),d)
        self.big.seek(int(row['offset']))
        return Fraction(self.big.read(int(row['length'])).decode('ascii'))
    def items(self):
        return ((v,self[v]) for v in self.facts.var_ids())
    def close(self):
        self.big.close();self.storage.close()
    def __del__(self):
        if hasattr(self,'big'):self.close()
