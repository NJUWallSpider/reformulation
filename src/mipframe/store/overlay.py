"""Logical sparse overlay: shared base arrays plus immutable delta selections."""
from pathlib import Path
from tempfile import TemporaryDirectory
from math import inf
from fractions import Fraction
import sqlite3
import numpy as np
from mipframe.store.numbers import MIPFrameError


class IDs(tuple):
    unique=True


class Metadata:
    def __init__(self,store,axis):self.store,self.axis=store,axis
    def __len__(self):return self.store.f.nrows if self.axis=='row' else self.store.f.nvars
    def __iter__(self):
        for i in self.store.entity_ids(self.axis):yield self[i]
    def __getitem__(self,i):
        self.store.guard();n=self.store.number
        if self.axis=='row':
            i,name,lo,hi=self.store.f.row(i)
            return i,name,n(lo),n(hi),0
        i,name,kind,lo,hi,obj=self.store.f.var(i)
        return i,name,kind,n(lo),n(hi),n(obj),0


class Names:
    def __init__(self,table):self.table=table;self._mapping=None
    def _get(self):
        if self._mapping is None:self._mapping={r[1]:r[0] for r in self.table}
        return self._mapping
    def __getitem__(self,name):return self._get()[name]
    def __iter__(self):return iter(self._get())
    def __len__(self):return len(self.table)


class OverlayStore:
    def __init__(self,f):
        self.f=f;self._closed=False;self._db=None;self._temporary=None
        self._arrays=None;self._ids={};self._numbers=[];self._number_ids={}
        self.rows,self.variables=Metadata(self,'row'),Metadata(self,'var')
        self.row_names,self.var_names=Names(self.rows),Names(self.variables)
        self.manifest=dict(f.provenance,uid=f.uid,model_version=f.revision,schema_version=7,
                           stage='overlay',parent=None if f.parent is None else dict(uid=f.parent.uid,version=f.parent.revision),
                           sense=f.sense,objective_constant=self.number(f.constant),
                           rows=f.nrows,variables=f.nvars)
        self.manifest.pop("nonzeros",None)
        self._scan_data=None

    def guard(self):
        if self._closed:raise MIPFrameError('Model is closed')

    def entity_ids(self,axis):
        self.guard()
        if axis not in self._ids:self._ids[axis]=IDs(self.f.row_ids() if axis=='row' else self.f.var_ids())
        return self._ids[axis]

    def capacity(self,axis):return self.f.row_capacity if axis=='row' else self.f.var_capacity

    def number(self,value):
        if value in (-inf,inf):return None
        if value not in self._number_ids:
            self._number_ids[value]=len(self._numbers);self._numbers.append(value)
        return self._number_ids[value]

    def num(self,i):
        self.guard()
        return None if i is None else Fraction(self._numbers[int(i)])

    def gather(self,axis,fields,ids):
        names=('id','name','lo','hi','line') if axis=='row' else ('id','name','kind','lo','hi','obj','line')
        table=self.rows if axis=='row' else self.variables
        return {int(i):{name:table[int(i)][names.index(name)] for name in fields} for i in ids}

    def name(self,axis,ident):self.guard();return self.f.name(axis,ident)
    def support(self,r):
        self.guard()
        return np.fromiter((v for v,_ in self.f.terms(r)),dtype=np.uint64)
    def row_bounds(self,r):
        lo,hi=self.f.row_bounds(r)
        return (None if lo==-inf else lo),(None if hi==inf else hi)
    def variable_kind(self,v):return self.f.numeric_var(v)[2]
    def uniform_coefficient(self,r):
        first=None
        for _,c in self.f.terms(r):
            if first is None:first=c
            elif first!=c:return None
        return first
    def iter_terms(self,r):self.guard();return iter(self.f.terms(r))
    def row(self,r):return dict(self.iter_terms(r))
    def row_ids(self,r):return [(v,self.number(c)) for v,c in self.iter_terms(r)]
    def variable_type(self,v):
        from mipframe.frame.sources import binary
        v=self.variables[v] if isinstance(v,int) else v
        return 'B' if binary(v[2],-inf if v[3] is None else self.num(v[3]),inf if v[4] is None else self.num(v[4])) else v[2]

    @property
    def nnz(self):
        self.guard()
        if 'nonzeros' not in self.manifest:
            self.manifest['nonzeros'] = self.f.nnz
        return self.manifest['nonzeros']

    def prepare_scan(self,memory_mb):
        from mipframe.store.scan import Scan
        # Intern each distinct runtime value, retaining only the dictionary.
        for v in self.variables:pass
        for row in self.rows:
            for _,c in self.iter_terms(row[0]):self.number(c)
        scan=Scan(memory_mb)
        try:
            scan.read_number=lambda i:(str(self._numbers[i].numerator),str(self._numbers[i].denominator))
            scan.number_rows=lambda:((i,*scan.read_number(i)) for i in range(len(self._numbers)))
            scan.prepare_numbers(len(self._numbers))
            for v in self.variables:scan.variable(v)
            rp=[0];counts={v:0 for v in self.entity_ids('var')}
            for row in self.rows:
                terms=list(self.iter_terms(row[0]))
                scan.row(row,np.asarray([self.number(c) for _,c in terms],dtype=np.int64))
                rp.append(rp[-1]+len(terms))
                for v,c in terms:counts[v]+=1
            cp=np.concatenate(([0],np.cumsum(list(counts.values()),dtype=np.int64)))
            self._scan_data=scan.export(np.asarray(rp,dtype=np.int64),cp)
        finally:scan.close()

    @property
    def sparse(self):
        from mipframe.store.sparse_store import SparseStore
        return SparseStore(self)

    def iter_column(self,v):
        self.guard()
        return self.f.column(v)

    def incident(self,v):return (r for r,c in self.iter_column(v))

    def degree(self,axis,ident):
        return sum(1 for _ in (self.iter_terms(ident) if axis=='row' else self.iter_column(ident)))

    def coefficient(self,row,var):
        for v,c in self.iter_terms(row):
            if v==var:return c
        raise KeyError((row,var))

    @property
    def path(self):
        self.guard()
        if self._temporary is None:self._temporary=TemporaryDirectory(prefix='mipframe-overlay-query-')
        return Path(self._temporary.name)

    def close(self):
        self._closed=True
        if self._temporary is not None:self._temporary.cleanup();self._temporary=None
    _close_connection=close
