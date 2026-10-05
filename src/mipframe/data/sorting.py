"""Stable lexicographic array sorting with bounded in-memory runs.

Returned indices belong to the caller's workspace. The external merge reads
only a bounded fan-in and never passes numerical rows through SQL.
"""
import heapq
import numpy as np


def lexorder(work,columns):
    columns=tuple(columns)
    required=(len(columns[0])*max(64,32*len(columns))) if columns else 0
    with work.reserve(min(required,work.memory//2,work.available//2)) as budget:
        return _lexorder(work,columns,budget)


def _lexorder(work,columns,budget):
    columns=tuple(columns)
    count=len(columns[0]) if columns else 0
    if any(len(c)!=count for c in columns):raise ValueError('Sort column lengths differ')
    result=work.array(count,'<u8')
    if not count:return result
    block=max(1,budget//max(64,32*len(columns)))
    if count<=block:
        result[:]=np.lexsort(tuple(reversed(columns)))
        return result
    for start in range(0,count,block):
        stop=min(count,start+block)
        result[start:stop]=np.lexsort(tuple(c[start:stop] for c in reversed(columns)))+start
    scratch=work.array(count,'<u8')
    width=block
    def key(i):
        return (*((v.item() if isinstance(v,np.generic) else v) for c in columns for v in (c[i],)),i)
    while width<count:
        for start in range(0,count,width*32):
            stop=min(count,start+width*32)
            heap=[]
            for run in range(start,stop,width):
                ident=int(result[run]);heap.append((key(ident),run,min(run+width,stop)))
            heapq.heapify(heap);out=start
            while heap:
                _,position,end=heapq.heappop(heap)
                scratch[out]=result[position];out+=1;position+=1
                if position<end:heapq.heappush(heap,(key(int(result[position])),position,end))
        result,scratch=scratch,result;width*=32
    work.release(scratch)
    return result


class SortedLookup:
    """Scalar identity lookup over a sorted unique column, without a hash map."""
    def __init__(self,values):self.values=values
    def __getitem__(self,value):
        p=int(np.searchsorted(self.values,value))
        if p==len(self.values) or self.values[p]!=value:raise KeyError(value)
        return p
    def __contains__(self,value):
        try:self[value];return True
        except (KeyError,TypeError):return False
    def keys(self):return self.values


def pair_counts(work,blocks,upper_bound):
    """Count exact int64 pairs using the same bounded sort for every size.

    The caller supplies a cardinality bound, not a materialized Python table.
    Both the gathered columns and sorting indices spend the shared ledger.
    """
    if upper_bound<=2:
        # Fixed-size state avoids a sort and four workspace allocations for
        # the common degree-one/two case without introducing an unbounded map.
        with work.reserve(256):
            first=second=None;count=0
            for block in blocks:
                for row in block:
                    count+=1
                    if count>upper_bound:raise ValueError('Pair cardinality exceeds its bound')
                    pair=(int(row[0]),int(row[1]))
                    if first is None:first=pair
                    else:second=pair
            if first is None:return
            if second is None:yield *first,1
            elif first==second:yield *first,2
            else:
                if first>second:first,second=second,first
                yield *first,1
                yield *second,1
        return
    if upper_bound<=8:
        # The state is capped at eight pairs, including boxed Python integers.
        # Larger degrees always use the typed external sorter below.
        with work.reserve(128+192*upper_bound):
            pairs=[]
            for block in blocks:
                for row in block:
                    if len(pairs)>=upper_bound:raise ValueError('Pair cardinality exceeds its bound')
                    pairs.append((int(row[0]),int(row[1])))
            pairs.sort();previous=None;count=0
            for pair in pairs:
                if pair!=previous:
                    if previous is not None:yield *previous,count
                    previous=pair;count=1
                else:count+=1
            if previous is not None:yield *previous,count
        return
    pairs=work.array((upper_bound,2),'<i8');count=0
    for block in blocks:
        end=count+len(block)
        if end>upper_bound:raise ValueError('Pair cardinality exceeds its bound')
        pairs[count:end]=block;count=end
    order=lexorder(work,(pairs[:count,0],pairs[:count,1]))
    start=0
    while start<count:
        a,b=map(int,pairs[int(order[start])]);end=start+1
        while end<count:
            row=pairs[int(order[end])]
            if row[0]!=a or row[1]!=b:break
            end+=1
        yield a,b,end-start
        start=end
