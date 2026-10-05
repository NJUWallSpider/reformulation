"""One immutable sparse matrix, exposing both adjacency orientations."""


class SparseDataPlane:
    def __init__(self,arrays):
        self.arrays=arrays

    @property
    def shape(self):
        return len(self.arrays['row_ptr'])-1,len(self.arrays['col_ptr'])-1

    @property
    def nnz(self):return len(self.arrays['col_idx'])

    def adjacency(self,axis,ident):
        a=self.arrays
        if axis not in ('row','var'):raise ValueError('Invalid sparse axis')
        ptr=a['row_ptr' if axis=='row' else 'col_ptr']
        if not 0<=ident<len(ptr)-1:raise ValueError('Invalid sparse entity ID')
        lo,hi=map(int,ptr[ident:ident+2])
        return ((a['col_idx'][lo:hi],a['coeff_id'][lo:hi]) if axis=='row' else
                (a['csc_row_ids'][lo:hi],a['csc_value_ids'][lo:hi]))


class ExactSlice:
    """A borrowed sparse row/column; Python scalars exist only while iterating."""
    def __init__(self, ids, values, numbers):
        self.ids,self.value_ids,self.numbers=ids,values,numbers
    def __len__(self):return len(self.ids)
    def __iter__(self):
        if self.numbers.integral:
            for start in range(0,len(self.ids),4096):
                yield from zip(self.ids[start:start+4096].tolist(),
                    self.numbers.numerator[self.value_ids[start:start+4096]].tolist())
        else:
            yield from ((int(v),self.numbers[int(c)]) for v,c in zip(self.ids,self.value_ids))
    def __getitem__(self,i):
        if isinstance(i,slice):return ExactSlice(self.ids[i],self.value_ids[i],self.numbers)
        return int(self.ids[i]),self.numbers[int(self.value_ids[i])]
    def __eq__(self,other):
        if not hasattr(other,'__len__') or len(self)!=len(other):return False
        return all(a==b for a,b in zip(self,other))
