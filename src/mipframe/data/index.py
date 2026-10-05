"""Stable positional lookup without an entity-sized Python dictionary."""
from mipframe.data.sorting import lexorder
from mipframe.data.runtime import WorkContext


class PositionIndex:
    def __init__(self,values,*,work=None,memory_mb=128,disk_mb=None):
        self.values=values;self.work=work.fork() if work is not None else WorkContext(memory_mb,disk_mb)
        self.order=lexorder(self.work,(values,))
    def __getitem__(self,key):
        lo,hi=0,len(self.order)
        while lo<hi:
            mid=(lo+hi)//2
            if int(self.values[int(self.order[mid])])<key:lo=mid+1
            else:hi=mid
        if lo==len(self.order) or int(self.values[int(self.order[lo])])!=key:raise KeyError(key)
        return int(self.order[lo])
