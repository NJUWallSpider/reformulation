"""Array builder shared by MPS and LP, with bounded sorted term runs."""
from array import array
from fractions import Fraction
from pathlib import Path
import heapq
import numpy as np
from mipframe.core.errors import MIPFrameError
from .numbers import write_numbers
from .snapshot import write_strings

TERM = np.dtype([('v','<u8'), ('r','<i8'), ('c','<u8')])


class Writer:
    def __init__(self, path, memory_mb=128, disk_mb=None):
        from mipframe.core.resources import memory_bytes
        self.memory = memory_bytes(memory_mb)
        self.path = Path(path)
        self.rows = self.nvars = self.nnz = 0
        self.sense = 'min'
        from .runtime import WorkContext
        from .pools import NumberPool,NameIndex
        from .buffers import Buffer
        self.work=WorkContext(memory_mb,disk_mb)
        self.values=NumberPool(self.work)
        # Column-oriented MPS repeatedly revisits row names. Give that shared
        # index the cache formerly duplicated by parser-specific row objects.
        self.row_names,self.var_names=NameIndex(self.work,cache_fraction=4),NameIndex(self.work,cache_fraction=32)
        from .dense import DenseRecords
        self.row_data,self.var_data=DenseRecords('row',self.work,self.row_names.names),DenseRecords('var',self.work,self.var_names.names)
        self.terms = array('q')
        self.runs = []
        self.negated = Buffer(self.work,'?')
        self.constant = self.number(0)
        self.closed = False

    def number(self, value):
        if value is None:
            return None
        if value.denominator == 1:
            value = int(value)
        return self.values.intern(value)

    @property
    def nnums(self):
        return len(self.values)

    def num(self, ident):
        return None if ident is None else self.values[ident]

    def variable(self, name, line):
        ident = self.var_names.intern(name)
        if ident == self.nvars:
            self.nvars += 1
            self.var_data.append([ident,name,'C',0,None,0,line])
        return ident

    def kind(self, ident):
        return 'CIB'[int(self.var_data.columns[2][ident])]

    def set_kind(self, ident, kind):
        if not (self.kind(ident) == 'B' and kind == 'I'):
            self.var_data[ident][2] = kind

    def set_bound(self, ident, lo, hi, setlo, sethi):
        if setlo:
            self.var_data[ident][3] = self.number(lo)
        if sethi:
            self.var_data[ident][4] = self.number(hi)

    def new_row(self, name, line):
        ident = self.rows
        if self.row_names.intern(name)!=ident:
            raise MIPFrameError(f'line {line}: duplicate row {name}')
        self.rows += 1
        self.negated.append(False)
        self.row_data.append([ident,name,None,None,line])
        return ident

    def row_bounds(self, ident, lo, hi):
        from .dense import MISSING
        self.row_data.columns[2][ident]=MISSING if lo is None else self.number(lo)
        self.row_data.columns[3][ident]=MISSING if hi is None else self.number(hi)

    def row(self, name, a, lo, hi, line):
        r = self.new_row(name,line)
        for v,c in a.items():
            self.term(r,v,c)
        self.row_bounds(r,lo,hi)

    def objective(self, a, constant):
        for v,c in a.items():
            self.term(-1,v,c)
        self.constant = self.number(constant)

    def term(self, r, v, value):
        if value:
            self.terms.extend((v,r,self.number(value)))
            if len(self.terms)*8 >= self.memory//4:
                self._flush()

    def negate_row(self, r):
        self.negated[r]=not self.negated[r]

    def _flush(self):
        if not self.terms:
            return
        a = np.frombuffer(self.terms, dtype=TERM)
        # Sort each bounded run. A column-oriented file is already ordered by v;
        # sorting row IDs within a column also makes duplicate reduction adjacent.
        a.sort(order=('v','r'), kind='stable')
        p = self.path/f'terms-{len(self.runs)}.npy'
        np.save(p,a,allow_pickle=False)
        self.runs.append(p)
        del a
        self.terms = array('q')

    def _merge_runs(self):
        """Bound the merge fan-in, including descriptors and heap entries."""
        generation=0
        while len(self.runs)>32:
            merged=[]
            try:
                for start in range(0,len(self.runs),32):
                    paths=self.runs[start:start+32]
                    maps=[np.load(p,mmap_mode='r',allow_pickle=False) for p in paths]
                    out=None
                    path=self.path/f'terms-merged-{generation}-{start}.npy'
                    try:
                        out=np.lib.format.open_memmap(path,mode='w+',dtype=TERM,shape=(sum(map(len,maps)),))
                        def records(a):
                            for x in a:yield int(x['v']),int(x['r']),int(x['c'])
                        stream=heapq.merge(*(records(a) for a in maps),key=lambda x:x[:2])
                        from itertools import islice
                        position=0
                        while chunk:=list(islice(stream,max(1,self.memory//256))):
                            out[position:position+len(chunk)]=np.asarray(chunk,dtype=TERM);position+=len(chunk)
                        out.flush()
                    finally:
                        if out is not None:out._mmap.close()
                        for a in maps:a._mmap.close()
                    merged.append(path)
                for path in self.runs:path.unlink()
                self.runs=merged;generation+=1
            except BaseException:
                for path in merged:path.unlink(missing_ok=True)
                raise

    def _entries(self):
        self._flush()
        self._merge_runs()
        maps = [np.load(p,mmap_mode='r') for p in self.runs]
        def records(a):
            for x in a:
                yield int(x['v']),int(x['r']),int(x['c'])
        try:
            streams = [records(a) for a in maps]
            stream = streams[0] if len(streams)==1 else heapq.merge(*streams,key=lambda x:x[:2])
            key, value = None, 0
            for v,r,c in stream:
                if key != (v,r):
                    if key is not None and value:
                        yield *key, value
                    key, value = (v,r), 0
                value += self.values[c]
            if key is not None and value:
                yield *key, value
        finally:
            for a in maps:
                a._mmap.close()
            for p in self.runs:
                p.unlink()
            self.runs.clear()

    def _matrix(self):
        """Build final orientations on disk; only bounded input/output buffers live in RAM."""
        from .runtime import WorkContext
        chunk=max(1,self.memory//128)
        raw_rows=self.path/'csc-rows.raw';raw_values=self.path/'csc-values.raw'
        maps=[]
        def output(name,n,dtype):
            result=np.lib.format.open_memmap(self.path/(name+'.npy'),mode='w+',dtype=dtype,shape=(n,))
            maps.append(result)
            return result
        arrays={}
        try:
            with self.work.fork() as work:
                col_ptr=output('col_ptr',self.nvars+1,'<u8');col_ptr[:]=0
                row_counts=work.array(self.rows,np.uint64,zero=True)
                rows,values=array('Q'),array('Q')
                with raw_rows.open('wb') as row_file,raw_values.open('wb') as value_file:
                    for v,r,value in self._entries():
                        if r==-1:self.var_data[v][5]=self.number(value);continue
                        if self.negated[r]:value=-value
                        rows.append(r);values.append(self.number(value))
                        col_ptr[v+1]+=1;row_counts[r]+=1;self.nnz+=1
                        if len(rows)>=chunk:
                            rows.tofile(row_file);values.tofile(value_file)
                            rows,values=array('Q'),array('Q')
                    rows.tofile(row_file);values.tofile(value_file)
                np.cumsum(col_ptr,out=col_ptr)
                ids='<u4' if max(self.rows,self.nvars,self.nnums)<2**32 else '<u8'
                csc_rows=output('csc_row_ids',self.nnz,ids)
                csc_values=output('csc_value_ids',self.nnz,ids)
                with raw_rows.open('rb') as row_file,raw_values.open('rb') as value_file:
                    for start in range(0,self.nnz,chunk):
                        count=min(chunk,self.nnz-start)
                        csc_rows[start:start+count]=np.fromfile(row_file,dtype=np.uint64,count=count)
                        csc_values[start:start+count]=np.fromfile(value_file,dtype=np.uint64,count=count)
                row_ptr=output('row_ptr',self.rows+1,'<u8');row_ptr[0]=0
                np.cumsum(row_counts,out=row_ptr[1:])
                col_idx=output('col_idx',self.nnz,ids)
                coeff_id=output('coeff_id',self.nnz,ids)
                col_positions=output('col_positions',self.nnz,'<u8')
                # NumPy's stable integer sort uses radix sorting. Use it only when
                # the permutation and companion vectors fit the workspace budget.
                if self.nnz*40+self.rows*8<=self.memory:
                    order=np.argsort(csc_rows,kind='stable')
                    columns=np.repeat(np.arange(self.nvars,dtype=ids),np.diff(col_ptr).astype(np.int64))
                    col_idx[:]=columns[order];coeff_id[:]=csc_values[order]
                    col_positions[order]=np.arange(self.nnz,dtype=np.uint64)
                else:
                    cursors=work.array(self.rows,np.uint64)
                    cursors[:]=row_ptr[:-1]
                    # Counting scatter is linear and has no nnz-sized permutation.
                    for v in range(self.nvars):
                        for pos in range(int(col_ptr[v]),int(col_ptr[v+1])):
                            r=int(csc_rows[pos]);dest=int(cursors[r]);cursors[r]+=1
                            col_idx[dest]=v;coeff_id[dest]=csc_values[pos];col_positions[pos]=dest
                arrays=dict(row_ptr=row_ptr,col_idx=col_idx,coeff_id=coeff_id,col_ptr=col_ptr,
                            col_positions=col_positions,csc_row_ids=csc_rows,csc_value_ids=csc_values)
                for value in arrays.values():value.flush()
            return arrays,ids
        except BaseException:
            for value in maps:
                if not value._mmap.closed:value._mmap.close()
            raise
        finally:
            raw_rows.unlink(missing_ok=True);raw_values.unlink(missing_ok=True)

    def finish(self):
        arrays,ids=self._matrix()
        for v in self.var_data:
            if v[2]=='B':
                lo,hi=self.num(v[3]),self.num(v[4])
                v[3]=self.number(max(0,lo) if lo is not None else 0)
                v[4]=self.number(min(1,hi) if hi is not None else 1)
                v[2]='I'
        from .dense import MISSING
        def output(name,values,dtype,count,transform=None):
            with self.work.fork() as work:
                a=work.array(count,dtype)
                block=max(1,min(work.memory//128,work.available//64))
                with work.reserve(block*32):
                    for start in range(0,count,block):
                        v=values[start:start+block]
                        a[start:start+len(v)]=v if transform is None else transform(v)
                np.save(self.path/(name+'.npy'),a,allow_pickle=False)
        for axis,records,offset in (('row',self.row_data,2),('var',self.var_data,3)):
            records.columns={j:b.finish() for j,b in records.columns.items()}
            for side,j in (('lb',offset),('ub',offset+1)):
                values=records.numeric(j)
                output(axis+'_'+side+'_id',values,ids,len(records),lambda v:np.where(v!=MISSING,v,0))
                output(axis+'_'+side+'_finite',values,'u1',len(records),lambda v:v!=MISSING)
            output(axis+'_line',records.numeric(records.width-1),'<u8',len(records))
            from .snapshot import write_pool_strings
            write_pool_strings(self.path,axis+'_name',records.names.pool,work=self.work)
        output('var_type',self.var_data.numeric(2),'u1',self.nvars,lambda v:v==1)
        output('var_obj_id',self.var_data.numeric(5),ids,self.nvars)
        for a in arrays.values():a.flush();a._mmap.close()
        write_numbers(self.path,self.values,work=self.work)
    def close(self):
        self.closed=True
        self.work.close()
        for p in self.runs:
            p.unlink(missing_ok=True)
        self.runs.clear()
