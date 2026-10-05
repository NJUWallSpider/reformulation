from math import isfinite
import sys
from fractions import Fraction

def memory_bytes(memory_mb):
    if isinstance(memory_mb, bool) or not isinstance(memory_mb, (int, float)) or not isfinite(memory_mb) or memory_mb * 1024**2 < 16384:
        raise ValueError('memory_mb must be finite and provide at least 16 KiB')
    return int(memory_mb * 1024**2)


def size(value):
    n = sys.getsizeof(value)
    if isinstance(value, dict): return n + sum(size(k)+size(v) for k,v in value.items())
    if isinstance(value, (tuple,list)): return n + sum(map(size,value))
    if isinstance(value, Fraction): return n + size(value.numerator)+size(value.denominator)
    return n


def resource_limits(memory_mb=128,disk_mb=None):
    memory=memory_bytes(memory_mb)
    if disk_mb is not None and (isinstance(disk_mb,bool) or not isinstance(disk_mb,(int,float)) or not isfinite(disk_mb) or disk_mb<=0):
        raise ValueError('disk_mb must be finite and positive')
    return memory,None if disk_mb is None else int(disk_mb*1024**2)
