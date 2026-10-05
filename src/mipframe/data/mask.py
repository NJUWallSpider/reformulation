"""Persistent chunk copy-on-write live masks; untouched pages stay shared."""
import numpy as np


class LiveMask:
    PAGE=128
    def __init__(self,size,parent=None,removed=(),work=None):
        from mipframe.data.runtime import WorkContext
        from mipframe.data.buffers import Buffer
        self.work=work.fork() if work is not None else WorkContext()
        self.size,self.parent=size,parent
        ids=np.asarray(removed);ids=ids[:int(np.searchsorted(ids,size))]
        pages=Buffer(self.work,'<u8');previous=None;block=max(1,self.work.memory//128)
        for start in range(0,len(ids),block):
            for page in np.unique(ids[start:start+block]//self.PAGE):
                if page!=previous:pages.append(page);previous=page
        self.pages=pages.finish();data=self.work.array((len(self.pages),self.PAGE),'?');lost=0
        for i,raw in enumerate(self.pages):
            page=int(raw);lo=page*self.PAGE;hi=min(size,lo+self.PAGE);data[i]=True
            if parent is not None:data[i,:hi-lo]=parent[np.arange(lo,hi,dtype='<u8')]
            a,b=np.searchsorted(ids,[lo,hi]);positions=ids[a:b]-lo
            lost+=int(data[i,positions].sum());data[i,positions]=False
        self.data=self.work.seal(data);self.count=(size if parent is None else parent.count)-lost
        self.index=None
        if (size+self.PAGE-1)//self.PAGE<=4096:
            index=self.work.array((size+self.PAGE-1)//self.PAGE,'<i4');index[:]=-1
            index[self.pages]=np.arange(len(self.pages));self.index=self.work.seal(index)
    def __len__(self):return self.size
    def __getitem__(self,key):
        if isinstance(key,(int,np.integer)):
            ident=int(key)
            if not 0<=ident<self.size:raise IndexError(ident)
            layer=self;page=ident//self.PAGE
            while layer is not None:
                i=int(layer.index[page]) if layer.index is not None else int(np.searchsorted(layer.pages,page))
                if i>=0 and i<len(layer.pages) and layer.pages[i]==page:return bool(layer.data[i,ident%self.PAGE])
                layer=layer.parent
            return True
        ids=np.arange(*key.indices(self.size),dtype='<u8') if isinstance(key,slice) else np.asarray(key)
        if np.any(ids<0) or np.any(ids>=self.size):raise IndexError('Mask index out of range')
        result=np.ones(ids.shape,dtype=bool);remaining=np.arange(ids.size);flat=ids.ravel();dest=result.ravel();layer=self
        while layer is not None and len(remaining):
            pages=flat[remaining]//self.PAGE;pos=np.searchsorted(layer.pages,pages)
            found=pos<len(layer.pages)
            if len(layer.pages):found&=layer.pages[np.minimum(pos,len(layer.pages)-1)]==pages
            chosen=remaining[found]
            dest[chosen]=layer.data[pos[found],flat[chosen]%self.PAGE]
            remaining=remaining[~found];layer=layer.parent
        return result
    def ids(self):
        if self.count==self.size:yield from range(self.size);return
        block=max(1,self.work.memory//128)
        for start in range(0,self.size,block):
            ids=np.arange(start,min(self.size,start+block),dtype='<u8')
            yield from map(int,ids[self[ids]])
