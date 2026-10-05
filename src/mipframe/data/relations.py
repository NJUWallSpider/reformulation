"""Bounded typed relation storage and stable external ordering.

No SQL, schema classes, Frame objects or diagnostic/report dependencies.
Values use primitive arrays and packed UTF-8; exact values never use floats.
"""
from collections.abc import Sequence
from fractions import Fraction
from functools import cmp_to_key
import math
from sys import getsizeof
import numpy as np
from mipframe.core.resources import size
from mipframe.core.errors import QueryMemoryError
from mipframe.data.buffers import Buffer


class PackedColumn(Sequence):
    dtype = np.dtype(object)
    def __init__(self, kind, arrays): self.kind,self.arrays=kind,arrays
    def __len__(self): return len(self.arrays['d'])
    def __getitem__(self, i):
        if isinstance(i,slice): return ColumnView(self,range(len(self))[i])
        if not isinstance(i,(int,np.integer)): return ColumnView(self,i)
        a=self.arrays;d=int(a['d'][i])
        if d==-1:return None
        if d==-2:return math.inf
        if d==-3:return -math.inf
        if self.kind in ('str','enum','null') or d==0:
            lo=int(a['offset'][i]);hi=lo+int(a['length'][i])
            text=a['big'][lo:hi].tobytes().decode('utf-8')
            return text if self.kind in ('str','enum') else Fraction(text)
        n=int(a['n'][i])
        return bool(n) if self.kind=='bool' else n if d==1 else Fraction(n,d)
    def tolist(self):return list(self)


class ColumnView(Sequence):
    def __init__(self,column,ids):self.column,self.ids=column,ids;self.dtype=column.dtype
    def __len__(self):return len(self.ids)
    def __getitem__(self,i):
        if isinstance(i,slice):return ColumnView(self.column,self.ids[i])
        return self.column[int(self.ids[i])]


class NullableColumnView(ColumnView):
    def __getitem__(self,i):
        if isinstance(i,slice):return type(self)(self.column,self.ids[i])
        ident=int(self.ids[i])
        return None if ident<0 else self.column[ident]


class DictionaryColumn(Sequence):
    """Lazy exact dictionary values for selected immutable metadata IDs."""
    dtype=np.dtype(object)
    def __init__(self,numbers,ids,selection,finite=None,infinite=None):
        self.numbers,self.ids,self.selection=numbers,ids,selection
        self.finite,self.infinite=finite,infinite
    def __len__(self):return len(self.selection)
    def __getitem__(self,i):
        if isinstance(i,slice):return ColumnView(self,range(len(self))[i])
        ident=int(self.selection[i])
        return self.infinite if self.finite is not None and not self.finite[ident] else self.numbers[int(self.ids[ident])]


class ColumnBuilder:
    def __init__(self,work,kind):
        self.work,self.kind=work,kind
        self.parts={'n':Buffer(work,'<i8')};self.count=0
        self.simple=kind in ('int','number','ref','draft_ref','bool')
    def _expand(self):
        if 'd' in self.parts:return
        for name,t in (('d','<i8'),('offset','<u8'),('length','<u8'),('big','u1')):self.parts[name]=Buffer(self.work,t)
        block=max(1,self.work.memory//256)
        for start in range(0,self.count,block):
            count=min(block,self.count-start)
            self.parts['d'].extend(np.ones(count,dtype='<i8'))
            for name in ('offset','length'):self.parts[name].extend(np.zeros(count,dtype='<u8'))
    def append(self,value):
        n=0;d=1;raw=b''
        if value is None:d=-1
        elif self.kind in ('str','enum'):d=0;raw=value.encode('utf-8')
        elif isinstance(value,float) and math.isinf(value):d=-2 if value>0 else -3
        else:
            n=value.numerator if isinstance(value,Fraction) else int(value)
            d=value.denominator if isinstance(value,Fraction) else 1
            if not -(2**63)<=n<2**63 or d>=2**63:raw=str(value).encode('ascii');n=d=0
        if len(raw)>self.work.memory//8:raise QueryMemoryError('A scalar exceeds the working-memory budget')
        if d!=1:self.simple=False;self._expand()
        p=self.parts;p['n'].append(n)
        if 'd' in p:
            p['d'].append(d);p['offset'].append(p['big'].count);p['length'].append(len(raw));p['big'].extend(np.frombuffer(raw,dtype='u1'))
        self.count+=1
    def finish(self):
        arrays={n:b.finish() for n,b in self.parts.items()}
        if self.simple or self.count==0:
            result=arrays['n']
            if self.kind=='bool':
                value=self.work.array(len(result),'?');value[:]=result;self.work.release(result);result=self.work.seal(value)
            for n,a in arrays.items():
                if n!='n':self.work.release(a)
            return result
        return PackedColumn(self.kind,arrays)

    def extend(self,column):
        if self.simple and 'd' not in self.parts and isinstance(column,np.ndarray) and column.dtype.kind in 'ib' and self.kind!='bool':
            self.parts['n'].extend(column);self.count+=len(column)
        elif self.simple and 'd' not in self.parts and isinstance(column,np.ndarray) and column.dtype.kind=='u' and column.dtype.itemsize<8:
            self.parts['n'].extend(column);self.count+=len(column)
        else:
            for value in column:self.append(value)


class Relation:
    def __init__(self,columns,count,work,max_record=128):
        self.columns,self.count,self.work,self.max_record=columns,count,work,max_record
    def __len__(self):return self.count
    def row(self,i,fields=None):
        return {n:scalar(self.columns[n][i]) for n in (self.columns if fields is None else fields)}
    def records(self,fields=None,ids=None):
        for i in range(self.count) if ids is None else ids:yield self.row(int(i),fields)
    def key(self,i,names):return tuple(scalar(self.columns[n][i]) for n in names)
    def order(self,names,descending=None):
        descending=tuple(descending or (False,)*len(names))
        if names and not any(descending) and all(isinstance(self.columns[n],np.ndarray) and self.columns[n].dtype.kind in 'iub' for n in names):
            from mipframe.data.sorting import lexorder
            return lexorder(self.work,tuple(self.columns[n] for n in names))
        def compare(i,j):
            for n,desc in zip(names,descending):
                x,y=scalar(self.columns[n][i]),scalar(self.columns[n][j])
                d=(x is None)-(y is None) if x is None or y is None else ((x>y)-(x<y))*(-1 if desc else 1)
                if d:return d
            return (i>j)-(i<j)
        return stable_order(self.work,self.count,compare,self.max_record)
    def groups(self,names,*,order=None):
        ids=self.order(names) if order is None else order;start=0
        try:
            while start<len(ids):
                key=self.key(int(ids[start]),names);end=start+1
                while end<len(ids) and self.key(int(ids[end]),names)==key:end+=1
                yield key,ids[start:end]
                start=end
        finally:
            if order is None and not self.work.closed:self.work.release(ids)

    def duplicate_groups(self,names,order):
        if names and all(isinstance(self.columns[n],np.ndarray) and not self.columns[n].dtype.hasobject for n in names):
            block=max(1,min(self.work.memory//256,self.work.available//128))
            duplicate=False;previous=None
            with self.work.reserve(block*64):
                for start in range(0,len(order),block):
                    ids=order[start:start+block];same=np.ones(max(0,len(ids)-1),dtype=bool)
                    boundary=previous is not None
                    last=[]
                    for j,name in enumerate(names):
                        values=self.columns[name][ids]
                        same &= values[1:]==values[:-1]
                        boundary=boundary and values[0]==previous[j]
                        last.append(scalar(values[-1]))
                    if boundary or np.any(same):duplicate=True;break
                    previous=last
            if not duplicate:return
        for key,ids in self.groups(names,order=order):
            if len(ids)>1:yield key,ids


def scalar(v):return v.item() if isinstance(v,np.generic) else v


class RelationBuilder:
    def __init__(self,work,schema):
        self.work=work;self.builders={n:ColumnBuilder(work,getattr(t,'kind',t)) for n,t in schema.items()}
        self.count=0;self.key_cost=sum(size(n) for n in schema)
        self.maximum=max(128,getsizeof(self.builders)+self.key_cost+36*len(schema))
    def append(self,row):
        cost=getsizeof(row)+self.key_cost+sum(size(row[n]) for n in self.builders);self.maximum=max(self.maximum,cost)
        if cost>self.work.memory//4:raise QueryMemoryError('A record exceeds the working-memory budget')
        with self.work.reserve(cost):
            for n,b in self.builders.items():b.append(row[n])
        self.count+=1
    def finish(self):return Relation({n:b.finish() for n,b in self.builders.items()},self.count,self.work,self.maximum)

    def extend(self,batch):
        if not all(isinstance(c,np.ndarray) and c.dtype.kind in 'iub' for c in batch.columns.values()):
            for row in batch.records(tuple(self.builders)):self.append(row)
            return
        for name,builder in self.builders.items():builder.extend(batch.columns[name])
        self.count+=batch.size


def build(work,schema,records):
    builder=RelationBuilder(work,schema)
    for row in records:builder.append(row)
    return builder.finish()


def unique_values(work,values,kind):
    builder=ColumnBuilder(work,kind)
    with work.fork() as scratch:
        table=Relation({'v':values},len(values),scratch)
        order=table.order(('v',))
        if isinstance(values,np.ndarray) and values.dtype.kind in 'iub':
            block=max(1,min(work.memory//128,work.available//64));previous=None
            with work.reserve(block*32):
                for start in range(0,len(order),block):
                    data=values[order[start:start+block]];keep=np.ones(len(data),dtype=bool)
                    keep[1:]=data[1:]!=data[:-1]
                    if previous is not None:keep[0]=data[0]!=previous
                    builder.extend(data[keep]);previous=data[-1]
        else:
            for key,_ in table.groups(('v',),order=order):builder.append(key[0])
    return builder.finish()


def stable_order(work,count,compare,item_size=128):
    """Stable pointer runs with two-way merges and bounded comparator state."""
    result=work.array(count,'<u8')
    budget=min(work.memory//8,work.available//2)
    if budget<128 and count:raise QueryMemoryError('Insufficient sorting workspace')
    block=max(1,budget//max(128,item_size*2));key=cmp_to_key(compare)
    with work.reserve(budget):
        for start in range(0,count,block):
            stop=min(count,start+block)
            result[start:stop]=sorted(range(start,stop),key=key)
        if count<=block:return result
        scratch=work.array(count,'<u8');width=block
        while width<count:
            for start in range(0,count,width*2):
                mid=min(count,start+width);end=min(count,mid+width);a,b=start,mid
                for out in range(start,end):
                    if a<mid and (b==end or compare(int(result[a]),int(result[b]))<=0):scratch[out]=result[a];a+=1
                    else:scratch[out]=result[b];b+=1
            result,scratch=scratch,result;width*=2
        work.release(scratch)
    return result


def search_range(table,ids,names,key):
    """Binary search exact sorted keys; null ordering matches Relation.order."""
    def ordered(k):return tuple((v is None,v) for v in k)
    wanted=ordered(key);lo,hi=0,len(ids)
    while lo<hi:
        mid=(lo+hi)//2
        if ordered(table.key(int(ids[mid]),names))<wanted:lo=mid+1
        else:hi=mid
    first=lo;hi=len(ids)
    while lo<hi:
        mid=(lo+hi)//2
        if ordered(table.key(int(ids[mid]),names))<=wanted:lo=mid+1
        else:hi=mid
    return first,lo


def match_ranges(left,right,left_names,right_names,left_order,right_order):
    """Stable exact join ranges, indexed by original left input position.

    Primitive scalar keys use bounded vector searches. Composite, nullable and
    packed exact keys share a linear merge; nulls never match. All output and
    scratch arrays spend the caller's workspace budget.
    """
    work=left.work
    lower=work.array(len(left),'<u8',zero=True)
    upper=work.array(len(left),'<u8',zero=True)
    matched=work.array(len(right),'?',zero=True)
    lc=[left.columns[n] for n in left_names]
    rc=[right.columns[n] for n in right_names]
    if len(lc)==1 and all(isinstance(c,np.ndarray) and c.dtype.kind in 'ib' for c in (*lc,*rc)):
        # Equal signed integer dtypes avoid NumPy's lossy int/uint promotion.
        budget=min(work.memory//8,work.available//2)
        if budget>=128:
            with work.reserve(budget):
                block=max(1,budget//64)
                for start in range(0,len(left),block):
                    stop=min(len(left),start+block);values=lc[0][start:stop]
                    lower[start:stop]=np.searchsorted(rc[0],values,side='left',sorter=right_order.view(np.int64))
                    upper[start:stop]=np.searchsorted(rc[0],values,side='right',sorter=right_order.view(np.int64))
                for start in range(0,len(right),block):
                    stop=min(len(right),start+block);values=rc[0][start:stop]
                    lo=np.searchsorted(lc[0],values,side='left',sorter=left_order.view(np.int64))
                    hi=np.searchsorted(lc[0],values,side='right',sorter=left_order.view(np.int64))
                    matched[start:stop]=lo!=hi
            return lower,upper,matched
    def groups(table,names,order):
        for key,ids in table.groups(names,order=order):
            yield tuple((v is None,v) for v in key),key,ids
    left_groups=iter(groups(left,left_names,left_order))
    right_groups=iter(groups(right,right_names,right_order))
    a=next(left_groups,None);b=next(right_groups,None);offset=0
    while a is not None and b is not None:
        if a[0]<b[0]:a=next(left_groups,None)
        elif a[0]>b[0]:
            offset+=len(b[2]);b=next(right_groups,None)
        else:
            if None not in a[1]:
                lower[a[2]]=offset;upper[a[2]]=offset+len(b[2]);matched[b[2]]=True
            a=next(left_groups,None);offset+=len(b[2]);b=next(right_groups,None)
    return lower,upper,matched


def unique_join_batches(work,left,right,lower,upper,order,*,outer=False):
    """Gather a validated join with at most one right match per left row."""
    from mipframe.data.batch import Columns
    width=max(128,32*(len(left)+len(right)+4))
    block=max(1,min(8192,work.memory//width//8,work.available//width//2))
    for start in range(0,len(lower),block):
        stop=min(len(lower),start+block)
        with work.reserve((stop-start)*width):
            present=lower[start:stop]!=upper[start:stop]
            li=np.arange(start,stop,dtype='<i8')
            if not outer:li=li[present]
            if not len(li):continue
            ri=np.full(len(li),-1,dtype='<i8')
            if outer:ri[present]=order[lower[start:stop][present]]
            else:ri[:]=order[lower[li]]
            columns={n:(c[li] if isinstance(c,np.ndarray) else ColumnView(c,li)) for n,c in left.items()}
            for n,c in right.items():
                columns[n]=NullableColumnView(c,ri) if outer and not np.all(present) else c[ri] if isinstance(c,np.ndarray) else ColumnView(c,ri)
            yield Columns(columns,len(li))
