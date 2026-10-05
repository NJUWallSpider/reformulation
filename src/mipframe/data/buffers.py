import numpy as np
from bisect import bisect_right

class Buffer:
    """Geometric typed chunks: O(log output) mappings even at minimum budgets."""
    def __init__(self,work,dtype):
        self.work,self.dtype=work,np.dtype(dtype)
        self.block=max(1,min(256,work.memory//(32*self.dtype.itemsize)))
        self.chunks=[];self.starts=[];self.count=0;self.capacity=0
    def reserve(self,n):
        start=self.count;self.count+=n
        while self.capacity<self.count:
            size=max(self.block,self.capacity)
            self.starts.append(self.capacity);self.chunks.append(self.work.array(size,self.dtype));self.capacity+=size
        return start
    def append(self,v):
        i=self.count
        if i==self.capacity:self.reserve(1)
        else:self.count+=1
        # Appends normally land in the last chunk; a pool may rewind count
        # after finding a duplicate, so retain the general indexed fallback.
        if i>=self.starts[-1]:self.chunks[-1][i-self.starts[-1]]=v
        else:self[i]=v
    def extend(self,values):
        start=self.reserve(len(values));offset=0
        while offset<len(values):
            i=start+offset;p=bisect_right(self.starts,i)-1
            chunk=self.chunks[p];pos=i-self.starts[p];n=min(len(values)-offset,len(chunk)-pos)
            chunk[pos:pos+n]=values[offset:offset+n];offset+=n
    def __len__(self):return self.count
    def blocks(self,limit=None):
        """Borrow used chunk spans without scalar decoding or concatenation."""
        for start,chunk in zip(self.starts,self.chunks):
            count=min(len(chunk),self.count-start)
            if count<=0:break
            width=count if limit is None else limit
            for pos in range(0,count,width):yield chunk[pos:min(count,pos+width)]
    def __getitem__(self,i):
        if isinstance(i,slice):return np.fromiter((self[j] for j in range(*i.indices(self.count))),dtype=self.dtype)
        if i<0:i+=self.count
        if not 0<=i<self.count:raise IndexError(i)
        if i<len(self.chunks[0]):return self.chunks[0][i]
        if i>=self.starts[-1]:return self.chunks[-1][i-self.starts[-1]]
        p=bisect_right(self.starts,i)-1;return self.chunks[p][i-self.starts[p]]
    def __setitem__(self,i,v):
        if i>=self.starts[-1]:self.chunks[-1][i-self.starts[-1]]=v;return
        p=bisect_right(self.starts,i)-1;self.chunks[p][i-self.starts[p]]=v
    def finish(self):
        result=self.work.array(self.count,self.dtype)
        for lo,chunk in zip(self.starts,self.chunks):
            n=max(0,min(len(chunk),self.count-lo));result[lo:lo+n]=chunk[:n];self.work.release(chunk)
        self.chunks=[];self.starts=[]
        return self.work.seal(result)
