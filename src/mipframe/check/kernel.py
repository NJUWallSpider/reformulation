"""Small exact certificate kernel. No Frame, SQL, symbolic search or Reports.

Integers stay Python integers (including multiplication, so overflow cannot wrap).
CSR and certificate indices are immutable arrays; only genuine rationals use
Fraction. The adapter performs one sequential metadata read per source snapshot.
"""
from dataclasses import dataclass, field
from fractions import Fraction
from math import inf, floor, ceil
from numbers import Integral
from types import MappingProxyType
import uuid
import numpy as np


from mipframe.core.scalars import exact, bound

def frozen_ints(values,*,offsets=False,memory_mb=128,disk_mb=None,work=None):
    from mipframe.data.runtime import WorkContext,leased_array
    from mipframe.data.buffers import Buffer
    owner=work.fork() if work is not None else WorkContext(memory_mb,disk_mb)
    try:
        if isinstance(values,np.ndarray) and values.dtype.kind in 'iu':
            if values.ndim!=1:raise ValueError('IDs must be one-dimensional')
            maximum=0;block=max(1,owner.memory//128)
            for start in range(0,len(values),block):
                piece=values[start:start+block]
                if np.any(piece<0):raise ValueError('Negative ID')
                maximum=max(maximum,int(piece.max()))
            dtype=np.dtype('<u8' if offsets or maximum>=2**32 else '<u4')
            base=values
            while isinstance(base,np.ndarray) and base.base is not None:base=base.base
            if values.dtype==dtype and (isinstance(base,bytes) or getattr(values,'_work_owner',None) is not None):owner.close();return values
            result=owner.array(len(values),dtype)
            for start in range(0,len(values),block):result[start:start+block]=values[start:start+block]
            return leased_array(owner.seal(result),owner)
        buffer=Buffer(owner,'<u8')
        maximum=0
        for x in values:
            if isinstance(x,bool) or not isinstance(x,Integral) or not 0<=x<2**64:raise ValueError('IDs and offsets must be nonnegative integers below 2**64')
            maximum=max(maximum,int(x));buffer.append(x)
        result=buffer.finish()
        if not offsets and maximum<2**32:
            target=owner.array(len(result),'<u4');target[:]=result;owner.release(result);result=owner.seal(target)
        return leased_array(result,owner)
    except BaseException:owner.close();raise


class Facts:
    """Immutable numerical facts; overlays share their parent's CSR and metadata."""
    def __init__(self, owner):
        self._initialize(owner.model)

    @classmethod
    def from_store(cls,store):
        result=cls.__new__(cls)
        result._initialize(store)
        return result

    def _initialize(self,m):
        self.uid = m.manifest['uid']
        self.revision = m.manifest['model_version']
        self._canonical_fingerprint = m.verified_content
        self.parent = None
        from mipframe.data.snapshot import Metadata
        self.data = m.data.acquire()
        self.numbers = self.data.numbers
        self.rows = Metadata(self.data, 'row', exact=True)
        self.variables = Metadata(self.data, 'var', exact=True)
        self.row_ptr = m.row_ptr
        self.col_idx = m.col_idx
        self.coeff_id = m.coeff_id
        self.row_capacity, self.var_capacity = len(self.rows), len(self.variables)
        self.nrows, self.nvars = self.row_capacity, self.var_capacity
        self.sense = m.manifest['sense']
        self.constant = self.numbers[m.manifest['objective_constant']]
        self.provenance = dict(m.manifest)
        self._cache = {}
        self.removed_rows = frozenset()
        self.removed_vars = frozenset()

    def row_ids(self):
        return range(self.row_capacity)

    def var_ids(self):
        return range(self.var_capacity)

    def row(self, r):
        if not 0 <= r < self.row_capacity:
            raise ValueError('Invalid row ID')
        return self.rows[r]

    def var(self, v):
        if not 0 <= v < self.var_capacity:
            raise ValueError('Invalid variable ID')
        return self.variables[v]

    def name(self,axis,ident):
        self.validate(axis,ident)
        return self.data.names[axis][ident]

    def numeric_var(self,v):
        self.validate('var',v)
        a=self.data.arrays;n=self.numbers
        return (v,None,'I' if a['var_type'][v] else 'C',
                n[int(a['var_lb_id'][v])] if a['var_lb_finite'][v] else -inf,
                n[int(a['var_ub_id'][v])] if a['var_ub_finite'][v] else inf,
                n[int(a['var_obj_id'][v])])

    def var_bounds(self,v):
        self.validate('var',v)
        a=self.data.arrays;n=self.numbers
        return (n[int(a['var_lb_id'][v])] if a['var_lb_finite'][v] else -inf,
                n[int(a['var_ub_id'][v])] if a['var_ub_finite'][v] else inf)

    def var_kind(self,v):
        self.validate('var',v)
        return 'I' if self.data.arrays['var_type'][v] else 'C'

    def validate(self, axis, ident):
        capacity = self.row_capacity if axis == 'row' else self.var_capacity
        if not 0 <= ident < capacity:
            raise ValueError('Invalid entity ID')

    def row_bounds(self, r):
        self.validate('row', r)
        a = self.data.arrays
        return (self.numbers[int(a['row_lb_id'][r])] if a['row_lb_finite'][r] else -inf,
                self.numbers[int(a['row_ub_id'][r])] if a['row_ub_finite'][r] else inf)

    def __copy__(self):
        result = type(self).__new__(type(self))
        result.__dict__.update(self.__dict__)
        if getattr(self, 'data', None) is not None:
            result.data = self.data.acquire()
        return result

    def __del__(self):
        data = getattr(self, 'data', None)
        if data is not None:
            self.data = None
            data.release()

    def terms(self, r):
        if not 0 <= r < self.row_capacity:
            raise ValueError('Invalid row ID')
        lo, hi = int(self.row_ptr[r]), int(self.row_ptr[r+1])
        from mipframe.data.sparse import ExactSlice
        return ExactSlice(self.col_idx[lo:hi],self.coeff_id[lo:hi],self.numbers)

    def row_degree(self, r):
        self.validate('row',r)
        return int(self.row_ptr[r+1])-int(self.row_ptr[r])

    @property
    def nnz(self):
        return len(self.col_idx)

    def column(self, v):
        self.validate('var', v)
        a = self.data.arrays
        start, stop = map(int,a['col_ptr'][v:v+2])
        from mipframe.data.sparse import ExactSlice
        return iter(ExactSlice(a['csc_row_ids'][start:stop],a['csc_value_ids'][start:stop],self.numbers))

    def side(self, kind, ident, side):
        if kind == 'row':
            if type(self) is Facts:
                if not 0 <= ident < self.row_capacity:
                    raise ValueError('Invalid row ID')
                a=self.data.arrays
                lo=self.numbers[int(a['row_lb_id'][ident])] if a['row_lb_finite'][ident] else -inf
                hi=self.numbers[int(a['row_ub_id'][ident])] if a['row_ub_finite'][ident] else inf
            else:
                lo,hi=self.row_bounds(ident)
            terms = self.terms(ident)
        elif kind == 'var':
            _, _, _, lo, hi, _ = self.numeric_var(ident)
            terms = ((ident, 1),)
        else:
            raise ValueError('Invalid fact kind')
        if side not in ('lb', 'ub', 'eq'):
            raise ValueError('Invalid fact side')
        if side == 'eq' and lo != hi:
            raise ValueError('Equality source must have equal bounds')
        rhs = lo if side == 'lb' else hi
        if rhs in (-inf, inf):
            raise ValueError('Certificate requires a finite source side')
        return terms, rhs, {'lb':'>=', 'ub':'<=', 'eq':'=='}[side]


class OverlayFacts(Facts):
    def __init__(self, parent, delta):
        self.uid = str(uuid.uuid4())
        self.revision = self.uid
        self.parent, self.delta = parent, delta
        self.provenance = parent.provenance
        compiled = delta.substitution
        if compiled is None:
            from mipframe.edit.substitution import compile_fixed
            from mipframe.data.runtime import WorkContext
            with WorkContext() as work:
                compiled = compile_fixed(parent,delta.fixed,delta.drop_rows,delta.row_bounds,
                                         delta.objective,False,work)
            from dataclasses import replace
            delta = self.delta = replace(delta,substitution=compiled)
        from mipframe.data.ids import IDSet
        self.removed_rows = IDSet(delta.drop_rows)
        from itertools import chain
        self.removed_vars = IDSet(delta.fixed.ids if not delta.drop_vars else chain(delta.fixed.ids,delta.drop_vars),work=compiled.resource)
        self.nrows = parent.nrows - len(self.removed_rows) + len(delta.add_rows)
        self.nvars = parent.nvars - len(self.removed_vars) + len(delta.add_vars)
        self.row_capacity = parent.row_capacity + len(delta.add_rows)
        self.var_capacity = parent.var_capacity + len(delta.add_vars)
        self.sense = delta.sense or parent.sense
        self.constant = (parent.constant if delta.objective is None else delta.objective[1])
        self.constant += compiled.objective_constant_delta
        self._cache, self._row_cache = {}, {}
        self._base=getattr(parent,'_base',parent)
        from mipframe.data.mask import LiveMask
        def live(axis,removed):
            capacity=self._base.var_capacity if axis=='var' else self._base.row_capacity
            old=getattr(parent,'_'+axis+'_live',None)
            return old if not removed and old is not None else LiveMask(capacity,old,removed.array,work=compiled.resource)
        self._var_live=live('var',self.removed_vars)
        self._all_base_vars=self._var_live.count==len(self._var_live)
        self._row_live=live('row',self.removed_rows)
        self._added_rows=dict(getattr(parent,'_added_rows',{}))
        self._added_rows.update(delta.add_rows)
        for r in self.removed_rows:self._added_rows.pop(r,None)

        self._added_vars=dict(getattr(parent,'_added_vars',{}))
        self._added_vars.update(delta.add_vars)
        for v in self.removed_vars:self._added_vars.pop(v,None)
        from mipframe.data.overrides import OverlayOverrides,Overrides
        self._var_bounds=OverlayOverrides(getattr(parent,'_var_bounds',None),delta.var_bounds,self.removed_vars,work=compiled.resource)
        self._var_kinds=OverlayOverrides(getattr(parent,'_var_kinds',None),delta.kinds,self.removed_vars,work=compiled.resource)
        self._objective=(getattr(parent,'_objective',None) if delta.objective is None else delta.objective[0])
        self._row_bounds=OverlayOverrides(getattr(parent,'_row_bounds',None),delta.row_bounds,self.removed_rows,work=compiled.resource)
        if compiled.shifts:
            from mipframe.edit.substitution import shifted_bounds
            bounds=shifted_bounds(parent,delta.row_bounds,compiled.shifts)
            self._row_bounds=OverlayOverrides(self._row_bounds,bounds,IDSet(),work=compiled.resource)
        self._nnz=parent.nnz-compiled.removed_nnz
        if type(parent) is Facts:
            ids=delta.drop_rows.array
            for start in range(0,len(ids),65536):
                chunk=ids[start:start+65536]
                self._nnz-=int((parent.row_ptr[chunk+1]-parent.row_ptr[chunk]).sum())
            # Derived trivial rows are empty after removing the selected incidence.
            self._nnz+=sum(parent.row_degree(r) for r in compiled.trivial_rows)
        else:self._nnz-=sum(parent.row_degree(r) for r in delta.drop_rows if r not in compiled.trivial_rows)
        self._nnz+=sum(len(row[3]) for row in delta.add_rows.values())

    def row_degree(self,r):
        self.validate('row',r)
        if r in self.delta.add_rows:return len(self.delta.add_rows[r][3])
        if r in self.delta.substitution.degrees:return int(self.delta.substitution.degrees[r])
        return self.parent.row_degree(r)

    def added_column(self,v):
        if not self._added_rows:return
        if not hasattr(self,'_added_column_storage'):
            from mipframe.data.exact_sparse import ExactSparse
            from mipframe.data.columns import exact_column_from_storage
            storage=ExactSparse(work=self.delta.substitution.resource)
            storage.build((ident*self.row_capacity+r,c)
                          for r,(_,_,_,terms) in self._added_rows.items() for ident,c in terms)
            self._added_column_storage=storage
            self._added_column_values=exact_column_from_storage(storage)
        storage=self._added_column_storage
        lo,hi=np.searchsorted(storage.ids,[v*self.row_capacity,(v+1)*self.row_capacity])
        if hi>lo:
            yield storage.ids[lo:hi]%self.row_capacity,np.arange(lo,hi,dtype='<u8'),self._added_column_values

    def validate(self,axis,ident):
        base=self._base
        capacity=base.row_capacity if axis=='row' else base.var_capacity
        live=self._row_live if axis=='row' else self._var_live
        added=self._added_rows if axis=='row' else self._added_vars
        if not (0<=ident<capacity and live[ident] or ident in added):
            raise ValueError('Invalid or removed entity ID')

    def row_bounds(self,r):
        self.validate('row',r)
        if self._row_bounds and r in self._row_bounds:return self._row_bounds[r]
        if r in self._added_rows:return self._added_rows[r][1:3]
        return self._base.row_bounds(r)

    def _cost(self,v):
        return self.parent.numeric_var(v)[5] if self.delta.objective is None else self.delta.objective[0].get(v,0)

    def row_ids(self):
        yield from self._row_live.ids()
        yield from self._added_rows

    def var_ids(self):
        yield from self._var_live.ids()
        yield from self._added_vars

    def name(self,axis,ident):
        self.validate(axis,ident)
        added=self._added_rows if axis=='row' else self._added_vars
        if ident in added:return added[ident][0]
        return self._base.data.names[axis][ident]

    def row(self,r):
        lo,hi=self.row_bounds(r)
        return r,self.name('row',r),lo,hi

    def var(self,v):
        i,_,kind,lo,hi,obj=self.numeric_var(v)
        return i,self.name('var',v),kind,lo,hi,obj

    def numeric_var(self,v):
        self.validate('var',v)
        if v in self._added_vars:
            _,kind,lo,hi,obj=self._added_vars[v]
        else:
            _,_,kind,lo,hi,obj=self._base.numeric_var(v)
        if self._var_bounds:lo,hi=self._var_bounds.get(v,(lo,hi))
        if self._objective is not None:obj=self._objective.get(v,0)
        return v,None,(self._var_kinds.get(v,kind) if self._var_kinds else kind),lo,hi,obj

    def var_bounds(self,v):
        self.validate('var',v)
        if self._var_bounds and v in self._var_bounds:return self._var_bounds[v]
        if v in self._added_vars:return self._added_vars[v][2:4]
        return self._base.var_bounds(v)

    def var_kind(self,v):
        self.validate('var',v)
        if self._var_kinds and v in self._var_kinds:return self._var_kinds[v]
        if v in self._added_vars:return self._added_vars[v][1]
        return self._base.var_kind(v)

    def terms(self, r):
        self.validate('row',r)
        base=self._base
        if r<base.row_capacity:
            lo,hi=map(int,base.row_ptr[r:r+2])
            ids=base.col_idx[lo:hi]
            from mipframe.data.sparse import ExactSlice
            if self._all_base_vars:return ExactSlice(ids,base.coeff_id[lo:hi],base.numbers)
            mask=self._var_live[ids]
            return ExactSlice(ids[mask],base.coeff_id[lo:hi][mask],base.numbers)
        return tuple((v,c) for v,c in self._added_rows[r][3] if (v<base.var_capacity and self._var_live[v]) or v in self._added_vars)

    def column(self, v):
        self.validate('var',v)
        base=self._base
        if v<base.var_capacity:
            a=base.data.arrays
            lo,hi=map(int,a['col_ptr'][v:v+2])
            rows=a['csc_row_ids'][lo:hi];mask=self._row_live[rows]
            from mipframe.data.sparse import ExactSlice
            yield from ExactSlice(rows[mask],a['csc_value_ids'][lo:hi][mask],base.numbers)
        for rows,values,numbers in self.added_column(v):
            for r,i in zip(rows,values):yield int(r),numbers[int(i)]

    @property
    def nnz(self):return self._nnz


def facts(owner):
    owner.model
    value = getattr(owner, '_exact_facts', None)
    if value is None:
        value = owner._exact_facts = Facts(owner)
    return value


@dataclass(frozen=True)
class CertificateBatch:
    """
    Ragged explicit sources. Kinds are row/var; sides are lb/ub/eq.

    MIPFRAME HELP BEGIN
    Immutable ragged source arrays for exact certificate execution.

    Full contract: mf.help("api.CertificateBatch")
    MIPFRAME HELP END
    """
    offsets: object
    sources: object
    multipliers: object = 1
    sides: object = 'ub'
    kinds: object = 'row'

    memory_mb: float = field(default=128,kw_only=True,repr=False,compare=False,metadata={'resource':True})
    disk_mb: object = field(default=None,kw_only=True,repr=False,compare=False,metadata={'resource':True})

    def __post_init__(self):
        from mipframe.data.runtime import WorkContext
        work=WorkContext(self.memory_mb,self.disk_mb)
        object.__setattr__(self,'_work',work)
        off, ids = frozen_ints(self.offsets,offsets=True,work=work), frozen_ints(self.sources,work=work)
        if not len(off) or off[0] != 0 or off[-1] != len(ids) or any(np.any(off[start+1:start+4097]<off[start:min(len(off)-1,start+4096)]) for start in range(0,len(off)-1,4096)):
            raise ValueError('Invalid witness offsets')
        if np.any(ids < 0):
            raise ValueError('Negative source ID')
        from mipframe.data.columns import EnumColumn, ExactColumn
        object.__setattr__(self, 'offsets', off)
        object.__setattr__(self, 'sources', ids)
        object.__setattr__(self, 'multipliers', ExactColumn(self.multipliers,len(ids),work=work))
        object.__setattr__(self, 'sides', EnumColumn(self.sides,('lb','ub','eq'),len(ids),work=work))
        object.__setattr__(self, 'kinds', EnumColumn(self.kinds,('row','var','proof','assumption'),len(ids),work=work))

    @classmethod
    def paths(cls,paths,*,memory_mb=128,disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Compile source row-ID paths with unit multipliers and upper sides.

        Full contract: mf.help("api.CertificateBatch.paths")
        MIPFRAME HELP END
        """
        from mipframe.data.runtime import WorkContext
        from mipframe.data.buffers import Buffer
        with WorkContext(memory_mb,disk_mb) as work:
            offsets,ids=Buffer(work,'<u8'),Buffer(work,'<u8');offsets.append(0)
            for path in paths:
                for ident in path:ids.append(ident)
                offsets.append(len(ids))
            return cls(offsets.finish(),ids.finish(),memory_mb=memory_mb,disk_mb=disk_mb)


@dataclass(frozen=True)
class Targets:
    """
    Existing rows, single-variable bounds, or CSR affine claims.

    MIPFRAME HELP BEGIN
    Existing entity targets or custom CSR affine targets.

    Full contract: mf.help("api.Targets")
    MIPFRAME HELP END
    """
    ids: object
    kind: str = 'row'
    sides: object = 'ub'
    rhs: object = ()
    offsets: object = ()
    variables: object = ()
    coefficients: object = ()

    memory_mb: float = field(default=128,kw_only=True,repr=False,compare=False,metadata={'resource':True})
    disk_mb: object = field(default=None,kw_only=True,repr=False,compare=False,metadata={'resource':True})

    def __post_init__(self):
        from mipframe.data.runtime import WorkContext
        work=WorkContext(self.memory_mb,self.disk_mb)
        object.__setattr__(self,'_work',work)
        ids = frozen_ints(self.ids,work=work)
        from mipframe.data.ids import IDSet
        if len(IDSet(ids,work=work)) != len(ids):
            raise ValueError('Target IDs must be distinct and nonnegative')
        object.__setattr__(self, 'ids', ids)
        from mipframe.data.columns import EnumColumn,ExactColumn
        sides=EnumColumn(self.sides,('lb','ub','eq'),len(ids),work=work)
        object.__setattr__(self,'sides',sides)
        if self.kind not in ('row', 'var', 'affine', 'integrality'):
            raise ValueError('Invalid target kind')
        if self.kind in ('var', 'affine'):
            rhs = ExactColumn(self.rhs,len(ids),work=work)
            if len(rhs) != len(ids):
                raise ValueError('Target RHS length mismatch')
            object.__setattr__(self, 'rhs', rhs)
        if self.kind == 'affine':
            off, vs = frozen_ints(self.offsets,offsets=True,work=work), frozen_ints(self.variables,work=work)
            cs = ExactColumn(self.coefficients,work=work)
            if len(off) != len(ids)+1 or off[0] != 0 or off[-1] != len(vs) or any(np.any(off[start+1:start+4097]<off[start:min(len(off)-1,start+4096)]) for start in range(0,len(off)-1,4096)) or len(vs) != len(cs):
                raise ValueError('Invalid affine target arrays')
            object.__setattr__(self, 'offsets', off)
            object.__setattr__(self, 'variables', vs)
            object.__setattr__(self, 'coefficients', cs)

    @classmethod
    def _rows(cls,ids,side,work):
        from mipframe.data.sorting import lexorder
        from mipframe.data.columns import EnumColumn
        with work.fork() as scratch:
            order=lexorder(scratch,(ids,));previous=None;block=max(1,work.memory//128)
            for start in range(0,len(ids),block):
                values=ids[order[start:start+block]]
                if len(values) and (previous is not None and values[0]==previous or np.any(values[1:]==values[:-1])):raise ValueError('Target IDs must be distinct')
                if len(values):previous=values[-1]
        value=object.__new__(cls)
        for name,item in dict(ids=ids,kind='row',sides=EnumColumn(side,('lb','ub','eq'),len(ids)),rhs=(),offsets=(),variables=(),coefficients=()).items():object.__setattr__(value,name,item)
        return value

    def claim(self, f, index):
        ident, side = int(self.ids[index]), self.sides[index]
        if self.kind == 'row':
            return f.side('row', ident, side)
        if self.kind == 'var':
            f.var(ident)
            return ((ident,1),), self.rhs[index], {'ub':'<=','lb':'>=','eq':'=='}[side]
        if self.kind != 'affine':
            raise ValueError('Not a linear target')
        lo, hi = map(int, self.offsets[index:index+2])
        from mipframe.data.columns import PairColumn
        terms=PairColumn(self.variables[lo:hi],self.coefficients[lo:hi])
        for v, _ in terms:
            f.validate("var",v)
        return terms, self.rhs[index], {'ub':'<=','lb':'>=','eq':'=='}[side]


def linear(f, targets, cert, residual, *, premises=(), assumptions=(), intervals=None, memory_mb=128, disk_mb=None):
    """Exact implications with bounded accumulation and array-file spill."""
    from mipframe.data.runtime import WorkContext
    from mipframe.data.exact_sparse import ExactSparse
    from collections import OrderedDict
    from itertools import chain
    with WorkContext(memory_mb,disk_mb) as work:
        if residual not in ('exact','bounds') or len(cert.offsets)!=len(targets.ids)+1:
            raise ValueError('Invalid linear certificate dimensions/residual')
        fast=path_linear(f,targets,cert,residual,work=work)
        if fast is not None:return fast
        from mipframe.data.columns import DependencyBuilder,EnumColumn
        from mipframe.data.runtime import leased_array
        output=work.fork();status=output.array(len(targets.ids),'u1',zero=True)
        bounds=DependencyBuilder(work,bounds=True);cache=OrderedDict()
        cache_limit=max(1,min(1024,work.memory//4096))
        for i in range(len(status)):
            terms,rhs,relation=targets.claim(f,i)
            sign=-1 if relation=='>=' else 1;constant=-sign*rhs
            def contributions():
                nonlocal constant
                for v,c in terms:yield v,sign*c
                for p in range(int(cert.offsets[i]),int(cert.offsets[i+1])):
                    a=cert.multipliers[p];key=cert.kinds[p],int(cert.sources[p]),cert.sides[p]
                    kind,ident,side=key
                    if key not in cache:
                        cache[key]=premises[ident] if kind=='proof' else assumptions[ident] if kind=='assumption' else f.side(*key)
                    ts,b,rel=cache[key];cache.move_to_end(key)
                    if len(cache)>cache_limit:cache.popitem(last=False)
                    if rel!='==' and (a<0 or relation=='=='):
                        raise ValueError('Negative inequality multiplier or inequality source for equality')
                    scale=a*(-1 if rel=='>=' else 1);constant+=scale*b
                    if scale:
                        for v,c in ts:yield v,-scale*c
            stream=iter(contributions());acc={};storage=None
            try:
                limit=max(1,work.memory//512)
                for v,c in stream:
                    value=acc.get(v,0)+c
                    if value:acc[v]=value
                    else:acc.pop(v,None)
                    if len(acc)>limit:
                        storage=ExactSparse(work=work)
                        storage.build(chain(acc.items(),stream));acc.clear();break
                entries=storage.items() if storage is not None else iter(acc.items())
                nonzero=False
                for v,c in entries:
                    if intervals and v in intervals and intervals[v][0]==intervals[v][1]:
                        constant+=c*intervals[v][0];continue
                    if not c:continue
                    if relation!='==' and residual=='bounds':
                        b=((intervals or {}).get(v) or f.var_bounds(v))[1 if c>0 else 0]
                        bounds.append('var',v,'ub' if c>0 else 'lb')
                        if b in (-inf,inf):constant=inf
                        elif constant!=inf:constant+=c*b
                    else:nonzero=True
                status[i]=int(not nonzero and (constant==0 if relation=='==' else constant<=0))
                bounds.end()
            finally:
                if storage is not None:storage.close()
        result=object.__new__(EnumColumn);result.labels=(0,1,2);result.codes=leased_array(output.seal(status),output)
        return result,bounds.freeze()


def path_linear(f, targets, cert, residual, *, work=None):
    """Check real row coefficients and every path join in bounded array chunks."""
    if type(f) is not Facts or targets.kind!='row' or residual!='exact':return None
    if work is None:
        from mipframe.data.runtime import WorkContext
        with WorkContext() as work:return path_linear(f,targets,cert,residual,work=work)
    off,src,ids=cert.offsets,cert.sources,targets.ids
    if not len(ids):return None
    block=max(1,work.memory//512)
    def all_codes(values,code):
        return all(np.all(values[start:start+block]==code) for start in range(0,len(values),block))
    if not all_codes(targets.sides.codes,1) or not all_codes(cert.kinds.codes,0) or not all_codes(cert.sides.codes,1):return None
    if any(a!=1 for a in cert.multipliers.values):return None
    with work.fork() as scratch:
        valid=scratch.array(f.row_capacity,'?',zero=True)
        u=scratch.array(f.row_capacity,f.col_idx.dtype);v=scratch.array(f.row_capacity,f.col_idx.dtype)
        ptr=f.row_ptr;a=f.data.arrays
        for column in (ids,src):
            for start in range(0,len(column),block):
                rows=np.unique(column[start:start+block])
                if np.any(rows>=f.row_capacity):raise ValueError('Invalid row ID')
                rows=rows[~valid[rows]]
                if not len(rows):continue
                if np.any(ptr[rows+1]-ptr[rows]!=2) or not np.all(a['row_ub_finite'][rows]):return None
                if any(f.numbers[int(i)]!=0 for i in np.unique(a['row_ub_id'][rows])):return None
                pos=ptr[rows].astype(np.int64);c1=f.coeff_id[pos];c2=f.coeff_id[pos+1]
                codes=np.unique(np.concatenate((c1,c2)));signs={int(c):f.numbers[int(c)] for c in codes}
                if any(c not in (-1,1) for c in signs.values()):return None
                plus=next((c for c,x in signs.items() if x==1),None)
                minus=next((c for c,x in signs.items() if x==-1),None)
                if plus is None or minus is None:return None
                positive=c1==plus
                if not np.all(np.where(positive,c2==minus,(c1==minus)&(c2==plus))):return None
                u[rows]=np.where(positive,f.col_idx[pos],f.col_idx[pos+1])
                v[rows]=np.where(positive,f.col_idx[pos+1],f.col_idx[pos]);valid[rows]=True
        for start in range(0,len(ids),block):
            stop=min(len(ids),start+block);starts=off[start:stop];ends=off[start+1:stop+1]
            if np.any(starts==ends):return None
            if np.any(u[src[starts]]!=u[ids[start:stop]]) or np.any(v[src[ends-1]]!=v[ids[start:stop]]):return None
        for start in range(0,len(src)-1,block):
            stop=min(len(src)-1,start+block);positions=np.arange(start+1,stop+1,dtype='<u8')
            ends=np.searchsorted(off[1:],positions)
            boundary=off[1:][ends]==positions
            if np.any((v[src[start:stop]]!=u[src[start+1:stop+1]])&~boundary):return None
    from mipframe.data.columns import ConstantStatus,empty_dependencies
    return ConstantStatus(1,len(ids)),empty_dependencies(len(ids),bounds=True)
