"""Complete sparse projection counts in source selection order."""
import numpy as np
from mipframe.data.sorting import lexorder


def sparse_projection(work,ptr,neighbors,sources,targets):
    """Return match counts and one witness ID; targets are sorted and unique.

    Only selected CSR/CSC spans are read. All matches are counted even after a
    second match, so callers can publish complete cardinality diagnostics.
    """
    counts=work.array(len(sources),'<i8',zero=True)
    values=work.array(len(sources),'<i8');values[:]=-1
    if not len(sources) or not len(targets):return counts,values
    with work.fork() as scratch:
        order=lexorder(scratch,(sources,))
        block=max(1,min(8192,work.memory//256,work.available//128))
        start=0
        with work.reserve(block*64):
            while start<len(order):
                end=start+1;first=last=int(sources[order[start]])
                while end<len(order) and int(sources[order[end]])==last+1:
                    last+=1;end+=1
                lo,hi=int(ptr[first]),int(ptr[last+1])
                for begin in range(lo,hi,block):
                    stop=min(hi,begin+block);ids=neighbors[begin:stop]
                    positions=np.searchsorted(targets,ids)
                    matched=(positions<len(targets))&(targets[np.minimum(positions,len(targets)-1)]==ids)
                    offsets=np.flatnonzero(matched)
                    rows=np.searchsorted(ptr[first+1:last+2],begin+offsets,side='right')
                    output=order[start+rows]
                    np.add.at(counts,output,1)
                    values[output]=ids[offsets]
                start=end
    return counts,values
