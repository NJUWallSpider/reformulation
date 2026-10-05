"""CSR construction from scalar columns; no public interface dependencies."""
import numpy as np
from mipframe.data.relations import build, scalar
from mipframe.data.sorting import lexorder


class ScalarLookup:
    def __init__(self,values):self.values=values
    def __getitem__(self,value):
        lo,hi=0,len(self.values)
        while lo<hi:
            mid=(lo+hi)//2
            if self.values[mid]<value:lo=mid+1
            else:hi=mid
        if lo==len(self.values) or self.values[lo]!=value:raise KeyError(value)
        return lo
    def __contains__(self,value):
        try:self[value];return True
        except (KeyError,TypeError):return False
    def keys(self):return self.values

    def locate(self,column,work):
        """Resolve an exact column in bounded batches; -1 marks absent values."""
        out=work.array(len(column),'<i8')
        if isinstance(column,np.ndarray) and isinstance(self.values,np.ndarray) and column.dtype.kind in 'ib' and self.values.dtype.kind in 'ib':
            block=max(1,min(work.memory//128,work.available//128))
            with work.reserve(block*64):
                for start in range(0,len(column),block):
                    data=column[start:start+block]
                    if not len(self.values):out[start:start+len(data)]=-1;continue
                    pos=np.searchsorted(self.values,data)
                    valid=pos<len(self.values)
                    valid &= self.values[np.minimum(pos,len(self.values)-1)]==data
                    pos[~valid]=-1;out[start:start+len(data)]=pos
        else:
            for i,value in enumerate(column):
                try:out[i]=self[value]
                except KeyError:out[i]=-1
        return out


class CSRGraph:
    def __init__(self,work,aa,bb,domains,kind):
        with work.fork() as scratch:
            domain=build(scratch,{'v':kind},({'v':scalar(v)} for c in domains for v in c))
            def unique():
                for key,_ in domain.groups(('v',)):
                    if key[0] is None:raise ValueError('Vertex domain cannot contain null')
                    yield {'v':key[0]}
            self.values=build(work,{'v':kind},unique()).columns['v']
        self.n=len(self.values);self.lookup=ScalarLookup(self.values)
        self.original_u=work.array(len(aa),'<i8');self.original_v=work.array(len(bb),'<i8')
        block=max(1,work.memory//128)
        for target,column in ((self.original_u,aa),(self.original_v,bb)):
            if isinstance(column,np.ndarray) and isinstance(self.values,np.ndarray):
                for start in range(0,len(column),block):
                    data=column[start:start+block];pos=np.searchsorted(self.values,data)
                    if np.any(pos>=self.n) or np.any(self.values[pos]!=data):raise KeyError('Endpoint outside declared domain')
                    target[start:start+len(data)]=pos
            else:
                for i,v in enumerate(column):target[i]=self.lookup[v]
        temp=work.fork()
        try:
            order=lexorder(temp,(self.original_u,self.original_v))
            starts=temp.array(len(order),'?',zero=True);pairs=0;previous=None
            for start in range(0,len(order),block):
                idx=order[start:start+block];u=self.original_u[idx];v=self.original_v[idx]
                mark=np.ones(len(idx),dtype=bool)
                if len(idx)>1:mark[1:]=(u[1:]!=u[:-1])|(v[1:]!=v[:-1])
                if previous is not None and len(idx):mark[0]=(u[0],v[0])!=previous
                starts[start:start+len(idx)]=mark;pairs+=int(mark.sum())
                if len(idx):previous=(u[-1],v[-1])
            self.u=work.array(pairs,'<i8');self.v=work.array(pairs,'<i8')
            self.representative=work.array(pairs,'<u8')
            self.pair_for_original=work.array(len(order),'<u8');offset=-1
            for start in range(0,len(order),block):
                idx=order[start:start+block];mark=starts[start:start+len(idx)]
                pair=offset+np.cumsum(mark,dtype=np.int64)
                self.pair_for_original[idx]=pair
                pick=idx[mark];p=pair[mark]
                self.u[p]=self.original_u[pick];self.v[p]=self.original_v[pick];self.representative[p]=pick
                if len(pair):offset=int(pair[-1])
        finally:temp.close()
        self.ptr=work.array(self.n+1,'<i8',zero=True)
        for start in range(0,len(self.u),block):np.add.at(self.ptr,self.u[start:start+block]+1,1)
        np.cumsum(self.ptr,out=self.ptr)
        order=lexorder(work,(self.v,self.u))
        self.reverse=work.array(len(order),'<i8')
        for start in range(0,len(order),block):self.reverse[start:start+block]=self.u[order[start:start+block]]
        self.rptr=work.array(self.n+1,'<i8',zero=True)
        for start in range(0,len(self.v),block):np.add.at(self.rptr,self.v[start:start+block]+1,1)
        np.cumsum(self.rptr,out=self.rptr);work.release(order)

    def neighbors(self,v,reverse=False):
        ptr,ids=(self.rptr,self.reverse) if reverse else (self.ptr,self.v)
        return ids[int(ptr[v]):int(ptr[v+1])]
