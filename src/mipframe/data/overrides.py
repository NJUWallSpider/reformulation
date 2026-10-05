"""Immutable sorted ID overrides, sharing exact/categorical dictionary columns."""
from collections.abc import Mapping
from math import inf
import numpy as np
from mipframe.data.columns import ExactColumn,EnumColumn


class BoundColumn:
    def __init__(self,values):
        flags=[]
        def finite():
            for value in values:
                flag=-1 if value==-inf else 1 if value==inf else 0
                flags.append(flag);yield 0 if flag else value
        self.exact=ExactColumn(finite());self.flags=np.frombuffer(np.asarray(flags,dtype='i1').tobytes(),dtype='i1')
    def __len__(self):return len(self.flags)
    def __getitem__(self,i):
        flag=int(self.flags[i]);return (-inf if flag<0 else inf) if flag else self.exact[i]


class Overrides(Mapping):
    def __init__(self,values=(),*,kind='exact'):
        if isinstance(values,StagedOverrides):values=values.freeze()
        if isinstance(values,Overrides):
            self.ids,self.columns,self.kind=values.ids,values.columns,values.kind;return
        records=sorted(dict(values).items());self.kind=kind
        dtype='<u4' if not records or records[-1][0]<2**32 else '<u8'
        if any(not isinstance(k,(int,np.integer)) or isinstance(k,bool) or not 0<=k<2**64 for k,v in records):raise ValueError('Invalid override ID')
        self.ids=np.frombuffer(np.asarray([k for k,v in records],dtype=dtype).tobytes(),dtype=dtype)
        if kind=='bounds':self.columns=(BoundColumn(v[0] for k,v in records),BoundColumn(v[1] for k,v in records))
        elif kind=='kind':self.columns=(EnumColumn((v for k,v in records),('C','I'),len(records)),)
        else:self.columns=(ExactColumn(v for k,v in records),)
    def __len__(self):return len(self.ids)
    def __iter__(self):return map(int,self.ids)
    def __getitem__(self,key):
        if not isinstance(key,(int,np.integer)) or key<0:raise KeyError(key)
        if key>=2**(8*self.ids.dtype.itemsize):raise KeyError(key)
        i=int(np.searchsorted(self.ids,self.ids.dtype.type(key)))
        if i==len(self.ids) or int(self.ids[i])!=key:raise KeyError(key)
        return tuple(c[i] for c in self.columns) if self.kind=='bounds' else self.columns[0][i]
    def items(self):
        for i,key in enumerate(self.ids):
            yield int(key),tuple(c[i] for c in self.columns) if self.kind=='bounds' else self.columns[0][i]


class OverlayOverrides(Mapping):
    """Share sorted typed override layers; decode only requested values.

    IDs index the visible domain. Layers are searched newest first, so updating
    a bound never expands all previous exact dictionary entries into objects.
    """
    def __init__(self,parent,update,removed,*,work=None):
        from itertools import chain
        from mipframe.data.ids import IDSet
        from mipframe.data.runtime import WorkContext,leased_array
        self.resource=(work or WorkContext()).fork()
        layers=parent.layers if isinstance(parent,OverlayOverrides) else ((parent,) if parent else ())
        self.layers=(*layers,update) if update else layers
        old=parent.ids if parent else np.empty(0,dtype='<u4')
        if not len(update.ids):ids=old
        elif not len(old):ids=update.ids
        else:ids=IDSet(chain(old,update.ids),work=self.resource).array
        block=max(1,self.resource.memory//128)
        if removed and len(ids):
            def surviving():
                for start in range(0,len(ids),block):
                    chunk=ids[start:start+block];p=np.searchsorted(removed.array,chunk)
                    keep=(p==len(removed))|(removed.array[np.minimum(p,len(removed)-1)]!=chunk)
                    yield from chunk[keep]
            ids=IDSet(surviving(),work=self.resource).array
        self.ids=ids;self.index=None;self.source=self.position=None
        if not len(ids):return
        # The common single-layer case borrows the original exact columns.
        self.direct=len(self.layers)==1 and ids is self.layers[0].ids
        if not self.direct:
            source=self.resource.array(len(ids),'<u4');position=self.resource.array(len(ids),'<u8')
            for layer_id,layer in enumerate(self.layers):
                for start in range(0,len(layer.ids),block):
                    chunk=layer.ids[start:start+block];p=np.searchsorted(ids,chunk)
                    keep=(p<len(ids))&(ids[np.minimum(p,len(ids)-1)]==chunk)
                    source[p[keep]]=layer_id;position[p[keep]]=np.flatnonzero(keep)+start
            self.source=leased_array(self.resource.seal(source),self.resource)
            self.position=leased_array(self.resource.seal(position),self.resource)
        if ids[-1]<max(65536,len(ids)*16) and (int(ids[-1])+1)*8<=self.resource.available//4:
            index=self.resource.array(int(ids[-1])+1,'<i8');index[:]=-1
            for start in range(0,len(ids),block):index[ids[start:start+block]]=np.arange(start,min(start+block,len(ids)))
            self.index=leased_array(self.resource.seal(index),self.resource)
    def __len__(self):return len(self.ids)
    def __iter__(self):return map(int,self.ids)
    def _position(self,key):
        if not len(self.ids):return -1
        if not isinstance(key,(int,np.integer)) or key<0:return -1
        if self.index is not None:return int(self.index[key]) if key<len(self.index) else -1
        if key>=2**(8*self.ids.dtype.itemsize):return -1
        p=int(np.searchsorted(self.ids,self.ids.dtype.type(key)))
        return p if p<len(self.ids) and self.ids[p]==key else -1
    def __contains__(self,key):return self._position(key)>=0
    def __getitem__(self,key):
        p=self._position(key)
        if p<0:raise KeyError(key)
        layer=self.layers[0 if self.direct else int(self.source[p])];i=p if self.direct else int(self.position[p])
        return tuple(c[i] for c in layer.columns) if layer.kind=='bounds' else layer.columns[0][i]


def overrides_from_storage(storage):
    from mipframe.data.columns import exact_column_from_storage
    result = object.__new__(Overrides)
    result.ids, result.kind = storage.ids, 'exact'
    result.columns = (exact_column_from_storage(storage),)
    return result


def overrides_from_columns(ids, columns, *, kind='exact'):
    result = object.__new__(Overrides)
    result.ids, result.kind, result.columns = ids, kind, tuple(columns)
    if any(len(c)!=len(ids) for c in columns): raise ValueError('Override dimensions differ')
    return result


def streamed_bound_column(values,count,work):
    from mipframe.data.exact_sparse import ExactSparse
    from mipframe.data.columns import exact_column_from_storage
    from mipframe.data.runtime import leased_array
    resource=work.fork();flags=resource.array(count,'i1');storage=ExactSparse(work=resource)
    try:
        def finite():
            used=0
            for i,value in enumerate(values):
                if i>=count:raise ValueError('Bound column length mismatch')
                flag=-1 if value==-inf else 1 if value==inf else 0
                flags[i]=flag;used+=1;yield i,0 if flag else value
            if used!=count:raise ValueError('Bound column length mismatch')
        storage.build(finite(),duplicates=True)
        column=object.__new__(BoundColumn)
        column.flags=leased_array(resource.seal(flags),resource)
        column.exact=exact_column_from_storage(storage)
        return column
    except BaseException:storage.close();resource.close();raise


def streamed_bounds(ids,records,work):
    # A repeatable validated generator keeps both columns exact without row objects.
    lo=streamed_bound_column((a for a,b in records()),len(ids),work)
    hi=streamed_bound_column((b for a,b in records()),len(ids),work)
    return overrides_from_columns(ids,(lo,hi),kind='bounds')


class StagedOverrides(Mapping):
    """Small operation list over immutable typed layers; freeze once per revision."""
    def __init__(self,kind,work):self.kind,self.work=kind,work;self.layers=[];self.cache=None
    def update(self,layer):
        if layer:self.layers.append(layer);self.cache=None
    def __getitem__(self,key):
        for layer in reversed(self.layers):
            try:return layer[key]
            except KeyError:pass
        raise KeyError(key)
    def __iter__(self):return iter(self.freeze())
    def __len__(self):return len(self.freeze())
    def __bool__(self):return bool(self.layers)
    def freeze(self):
        if self.cache is not None:return self.cache
        if not self.layers:self.cache=Overrides(kind=self.kind)
        elif len(self.layers)==1:self.cache=self.layers[0]
        else:
            from itertools import chain
            from mipframe.data.ids import IDSet
            ids=IDSet(chain.from_iterable(layer.ids for layer in self.layers),work=self.work).array
            if self.kind=='bounds':self.cache=streamed_bounds(ids,lambda:(self[v] for v in ids),self.work)
            else:
                from mipframe.data.runtime import leased_array
                resource=self.work.fork();codes=resource.array(len(ids),'u1')
                for i,v in enumerate(ids):codes[i]=int(self[v]=='I')
                column=object.__new__(EnumColumn);column.labels=('C','I');column.codes=leased_array(resource.seal(codes),resource)
                self.cache=overrides_from_columns(ids,(column,),kind='kind')
        return self.cache


class BoundSide:
    """Internal replay view over a typed or legacy saved bounds mapping."""
    def __init__(self,values,side):self.values,self.side=values,side
    def __getitem__(self,i):
        from mipframe.core.scalars import bound
        return bound((self.values[i] if isinstance(self.values,Mapping) and not isinstance(self.values,dict) else self.values[str(i)])[self.side])


class BoundVector(Mapping):
    """Exact sparse vector with finite/infinite flag records in one sorted artifact."""
    def __init__(self,records,work):
        from mipframe.data.exact_sparse import ExactSparse
        from mipframe.data.columns import ArrayExactValues
        from mipframe.data.runtime import leased_array
        self.storage=ExactSparse(work=work)
        def encoded():
            for ident,value in records:
                flag=-1 if value==-inf else 1 if value==inf else 0
                if not 0<=ident<2**63:raise ValueError('Bound vector ID exceeds storage range')
                yield 2*ident,0 if flag else value
                yield 2*ident+1,flag
        self.storage.build(encoded(),duplicates=True);self.values=ArrayExactValues(self.storage)
        resource=work.fork();ids=resource.array(len(self.storage.ids)//2,'<u8');block=max(1,work.memory//128)
        for start in range(0,len(ids),block):ids[start:start+block]=self.storage.ids[2*start:2*(start+block):2]//2
        self.ids=leased_array(resource.seal(ids),resource)
    def __len__(self):return len(self.ids)
    def __iter__(self):return map(int,self.ids)
    def __getitem__(self,key):
        i=int(np.searchsorted(self.ids,key))
        if i==len(self.ids) or self.ids[i]!=key:raise KeyError(key)
        flag=self.values[2*i+1]
        return (-inf if flag<0 else inf) if flag else self.values[2*i]
