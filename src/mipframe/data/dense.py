"""Mutable typed metadata during import; record objects are temporary views."""
from mipframe.data.buffers import Buffer
from collections.abc import Sequence
import numpy as np

MISSING=(1<<64)-1


class DenseRecords(Sequence):
    def __init__(self,axis,work,names):
        self.axis=axis;self.names=names;self.count=0
        self.width=5 if axis=='row' else 7
        self.columns={i:Buffer(work,'u1' if axis=='var' and i==2 else '<u8') for i in range(2,self.width)}
    def __len__(self):return self.count
    def append(self,record):
        if len(record)!=self.width or record[0]!=len(self):raise ValueError('Invalid dense entity record')
        self.count+=1
        for i,values in self.columns.items():
            value=record[i]
            values.append('CIB'.index(value) if self.axis=='var' and i==2 else MISSING if value is None else value)
    def __getitem__(self,i):
        if not 0<=i<len(self):raise IndexError(i)
        return DenseRecord(self,i)
    def numeric(self,column):
        values=self.columns[column]
        return values

    def records(self):
        """Stream catalog records from completed columns without record proxies."""
        c=self.columns
        if self.axis=='row':
            for i,name in enumerate(self.names):
                lo,hi=int(c[2][i]),int(c[3][i])
                yield i,name,None if lo==MISSING else lo,None if hi==MISSING else hi,int(c[4][i])
        else:
            for i,name in enumerate(self.names):
                lo,hi=int(c[3][i]),int(c[4][i])
                yield i,name,'CIB'[int(c[2][i])],None if lo==MISSING else lo,None if hi==MISSING else hi,int(c[5][i]),int(c[6][i])


class DenseRecord(Sequence):
    def __init__(self,table,ident):self.table,self.ident=table,ident
    def __len__(self):return self.table.width
    def __getitem__(self,i):
        if isinstance(i,slice):return [self[j] for j in range(*i.indices(len(self)))]
        if i<0:i+=len(self)
        if i==0:return self.ident
        if i==1:return self.table.names[self.ident]
        if i not in self.table.columns:raise IndexError(i)
        value=int(self.table.columns[i][self.ident])
        return 'CIB'[value] if self.table.axis=='var' and i==2 else None if value==MISSING else value
    def __setitem__(self,i,value):
        if isinstance(i,slice):
            indices=range(*i.indices(len(self)))
            if len(indices)!=len(value):raise ValueError('Cannot resize dense metadata')
            for j,x in zip(indices,value):self[j]=x
            return
        if i<0:i+=len(self)
        if i not in self.table.columns:raise ValueError('Entity identity/name is immutable')
        self.table.columns[i][self.ident]=('CIB'.index(value) if self.table.axis=='var' and i==2 else MISSING if value is None else value)
