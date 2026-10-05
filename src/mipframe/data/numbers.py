"""Exact numeric dictionary backed by immutable primitive arrays."""
from collections import OrderedDict
from fractions import Fraction
from pathlib import Path
import numpy as np

INT64_EXACT, RATIONAL_INT64_EXACT, BIGINT_FALLBACK = 1, 2, 3


class ExactNumbers:
    def __init__(self, arrays, big):
        self.numerator = arrays['numerator_i64']
        self.denominator = arrays['denominator_i64']
        self.flags = arrays['number_flags']
        self.approx = arrays['number_approx']
        self.integral = bool(np.all(self.flags == INT64_EXACT))
        self.big = big
        self.cache = OrderedDict()

    def __len__(self):
        return len(self.flags)

    def __getitem__(self, ident):
        if ident is None:
            return None
        ident = int(ident)
        if not 0 <= ident < len(self):
            raise ValueError('Invalid number ID')
        cached = self.cache.get(ident)
        if cached is not None:return cached
        if self.flags[ident] == INT64_EXACT:
            value = int(self.numerator[ident])
        elif self.flags[ident] == RATIONAL_INT64_EXACT:
            value = Fraction(int(self.numerator[ident]), int(self.denominator[ident]))
        else:
            n, d = self.big[ident]
            value = int(n) if int(d) == 1 else Fraction(int(n), int(d))
        self.cache[ident] = value
        if len(self.cache) > 8192:
            self.cache.popitem(last=False)
        return value

    def parts(self, ident):
        ident = int(ident)
        if self.flags[ident] == BIGINT_FALLBACK:
            return self.big[ident]
        return str(int(self.numerator[ident])), str(int(self.denominator[ident]))

    def rows(self):
        for i in range(len(self)):
            yield i, *self.parts(i)


def write_numbers(path,values,*,work=None):
    # These mappings are final artifacts, not a second resident exact dictionary.
    arrays={}
    specs={'numerator_i64':'<i8','denominator_i64':'<i8','number_flags':'u1','number_approx':'<f8'}
    try:
        for name,dtype in specs.items():
            arrays[name]=np.lib.format.open_memmap(Path(path)/(name+'.npy'),mode='w+',dtype=dtype,shape=(len(values),))
        limit=np.iinfo(np.int64)
        for i,value in enumerate(values):
            p,q=value.numerator,value.denominator
            fits=limit.min<=p<=limit.max and q<=limit.max
            arrays['numerator_i64'][i]=p if fits else 0
            arrays['denominator_i64'][i]=q if fits else 1
            arrays['number_flags'][i]=(INT64_EXACT if q==1 else RATIONAL_INT64_EXACT) if fits else BIGINT_FALLBACK
            try:arrays['number_approx'][i]=float(value)
            except OverflowError:arrays['number_approx'][i]=np.inf if value>0 else -np.inf
        from .snapshot import write_strings
        write_strings(path,'number_big',
            (str(v.numerator)+'/'+str(v.denominator) if arrays['number_flags'][i]==BIGINT_FALLBACK else ''
             for i,v in enumerate(values)),work=work)
    finally:
        for a in arrays.values():a.flush();a._mmap.close()


class BigParts:
    """Lazy mmap text fallback: decoding a rare big rational performs no SQL."""
    def __init__(self,strings):self.strings=strings
    def __getitem__(self,ident):
        numerator,denominator=self.strings[ident].split('/')
        return numerator,denominator
