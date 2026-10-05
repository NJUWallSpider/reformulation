"""Independent, budgeted MPS numerical readback against a logical snapshot."""
from math import inf
from tempfile import TemporaryFile
from fractions import Fraction
import numpy as np
from mipframe.data.exact_sparse import ExactSparse
from mipframe.data.runtime import WorkContext


class NameLookup:
    def __init__(self,f,axis,work,names=None):
        self.f,self.axis,self.names=f,axis,names
        n=f.nvars if axis=='var' else f.nrows
        capacity=1<<max(1,(max(1,n)*2-1).bit_length())
        self.ids=work.array(capacity,'<i8');self.ids[:]=-1
        self.hashes=work.array(capacity,'<u8');self.mask=capacity-1
        for ident in f.var_ids() if axis=='var' else f.row_ids():
            h=hash(self.name(ident))&((1<<64)-1);p=h&self.mask
            while self.ids[p]>=0:p=(p+1)&self.mask
            self.ids[p]=ident;self.hashes[p]=h
    def name(self,ident):return self.names[ident] if self.names is not None else self.f.name(self.axis,ident)
    def __getitem__(self,name):
        h=hash(name)&((1<<64)-1);p=h&self.mask
        while self.ids[p]>=0:
            if self.hashes[p]==h:
                ident=int(self.ids[p])
                if self.name(ident)==name:return ident
            p=(p+1)&self.mask
        raise ValueError('Artifact contains unknown '+self.axis+' '+name)


class ExactSlots:
    """Mutable primitive rational slots; exceptional big values use a byte file."""
    dtype=np.dtype([('n','<i8'),('d','<i8'),('offset','<u8'),('length','<u8')])
    def __init__(self,work,n,default=0):
        self.work=work;self.data=work.array(n,self.dtype,zero=True)
        self.big=None;self.disk=0
        if default==inf:self.data['n'][:]=1
    def __getitem__(self,i):
        row=self.data[i];n,d=int(row['n']),int(row['d'])
        if d==1:return n
        if d:return Fraction(n,d)
        if row['length']:
            self.big.seek(int(row['offset']));return Fraction(self.big.read(int(row['length'])).decode('ascii'))
        return inf if n==1 else -inf if n==-1 else 0
    def __setitem__(self,i,value):
        if value in (-inf,inf):self.data[i]=(1 if value==inf else -1,0,0,0);return
        n,d=value.numerator,value.denominator
        if -(1<<63)<=n<(1<<63) and d<(1<<63):self.data[i]=(n,d,0,0);return
        text=f'{n}/{d}'.encode('ascii')
        if len(text)>self.work.memory//2:
            from mipframe.frame.executor import QueryMemoryError
            raise QueryMemoryError('An exact value exceeds the readback budget')
        if self.big is None:self.big=TemporaryFile()
        self.work.ledger.charge_disk(len(text));self.disk+=len(text)
        self.big.seek(0,2);offset=self.big.tell();self.big.write(text)
        self.data[i]=(0,0,offset,len(text))
    def close(self):
        if self.big is not None:self.big.close();self.big=None
        self.work.ledger.charge_disk(-self.disk);self.disk=0
    def __del__(self):self.close()


class Accumulator(ExactSparse):
    def __init__(self,work):
        super().__init__(work=work);self.acc={};self.levels=[]
        self.limit=max(1,self.work.memory//2048)
    def push(self,key,value):
        value=self.acc.get(key,0)+value
        if value:self.acc[key]=value
        else:self.acc.pop(key,None)
        self.check(value)
        if len(self.acc)>=self.limit:self.flush()
    def flush(self):
        if not self.acc:return
        path=self._write(sorted(self.acc.items()));self.acc.clear();level=0
        while True:
            while len(self.levels)<=level:self.levels.append([])
            self.levels[level].append(path)
            if len(self.levels[level])<32:break
            path=self._merge_run(self.levels[level],False);self.levels[level]=[];level+=1
    def empty(self):
        if not any(self.levels):return not self.acc
        self.flush();paths=[p for level in self.levels for p in level]
        while len(paths)>32:paths=[self._merge_run(paths[i:i+32],False) for i in range(0,len(paths),32)]
        return next(self._merged(paths,False),None) is None


class RepeatedColumn(Exception):pass
class CacheOverflow(Exception):pass


def compare_cached(f,path,options):
    """Bounded small-metadata cache; larger inputs use typed slots below.

    The caller reserves the entire worst-case cache and limits entity counts.
    Only one column residual exists, never a second complete sparse matrix.
    """
    from mipframe.store.mps_output import parse_numeric
    base=getattr(f,'_base',f)
    def name(axis,i):
        capacity=base.var_capacity if axis=='var' else base.row_capacity
        return base.data.names[axis][i] if i<capacity else f.name(axis,i)
    nv={name('var',v):v for v in f.var_ids()};nr={name('row',r):r for r in f.row_ids()}
    bounds=base.row_bounds if not getattr(f,'_row_bounds',None) and not getattr(f,'_added_rows',None) else f.row_bounds
    seen=set();current=None;residual={};mismatch=False;total=0
    def coefficient(row,name,value):
        nonlocal current,residual,mismatch,total
        if name not in nv or row not in nr:raise ValueError('Artifact coefficient identity mismatch')
        if current!=name:
            mismatch|=bool(residual)
            if name in seen:raise RepeatedColumn()
            current=name;seen.add(name);residual=dict(f.column(nv[name]));total+=len(residual)
        r=nr[row];difference=residual.get(r,0)-value
        if difference:residual[r]=difference
        else:residual.pop(r,None)
    actual=parse_numeric(path,coefficient_sink=coefficient,metadata_limits=(f.nvars,f.nrows),**options)
    if mismatch or residual:raise ValueError('Artifact coefficient mismatch')
    if actual['sense']!=f.sense or actual['constant']!=f.constant:raise ValueError('Artifact objective mismatch')
    if len(actual['variables'])!=f.nvars or len(actual['rows'])!=f.nrows:raise ValueError('Artifact dimensions mismatch')
    for name,v in nv.items():
        if actual['variables'].get(name)!=f.numeric_var(v)[2:]:raise ValueError('Artifact variable mismatch')
        if name not in seen and next(iter(f.column(v)),None) is not None:raise ValueError('Artifact missing coefficients')
    for name,r in nr.items():
        if actual['rows'].get(name)!=bounds(r):raise ValueError('Artifact row mismatch')
    return dict(status='PROVED',kind='artifact_readback',rows=f.nrows,variables=f.nvars,nonzeros=total)


def compare(f,path,*,mps_format='auto',rhs=None,bounds=None,ranges=None,memory_mb=128,disk_mb=None,work=None):
    from mipframe.store.mps import _fixed_fields
    from mipframe.store.mps_output import number
    root=work.fork() if work is not None else WorkContext(memory_mb,disk_mb)
    try:
        # Cache is explicitly bounded and shares the writer's remaining budget.
        cache_bytes=(f.nrows+f.nvars)*896+65536
        if cache_bytes<=min(80*1024**2,root.available*3//4):
            try:
                with root.reserve(cache_bytes):
                    return compare_cached(f,path,dict(mps_format=mps_format,rhs=rhs,bounds=bounds,ranges=ranges))
            except (RepeatedColumn,CacheOverflow):pass
        names_v=NameLookup(f,'var',root);names_r=NameLookup(f,'row',root)
        def run(global_matrix=False):
            with root.fork() as w:
                nvar=f.var_capacity;nrow=f.row_capacity
                vk=w.array(nvar,'u1',zero=True);vp=w.array(nvar,'?',zero=True)
                rk=w.array(nrow+1,'u1',zero=True)
                lower=ExactSlots(w,nvar);upper=ExactSlots(w,nvar,inf);cost=ExactSlots(w,nvar)
                rv=ExactSlots(w,nrow+1);rg=ExactSlots(w,nrow);has_range=w.array(nrow,'?',zero=True)
                seen=w.array(nvar,'?',zero=True);acc=Accumulator(w);current=None;total=0
                section=None;objective=None;intmode=False;lastcol=None;lastset={};ended=False;sense='min'
                choices={'RHS':rhs,'BOUNDS':bounds,'RANGES':ranges};found={s:False for s in choices};first={}
                def choose(sec,name):
                    choice=choices[sec]
                    if choice is None:
                        if sec in first and first[sec]!=name:raise ValueError('Multiple MPS vectors require selection')
                        first[sec]=name
                    yes=choice is None or choice==name
                    if yes:found[sec]=True
                    return yes
                def row(name):return nrow if name==objective else names_r[name]
                def variable(name):
                    v=names_v[name];vp[v]=True
                    return v
                def coefficient(v,r,value):
                    nonlocal current,acc,total
                    if global_matrix:
                        key=v*max(1,nrow)+r
                        if key>=2**64:raise OverflowError('Readback coordinate exceeds uint64')
                        acc.push(key,value);return
                    if current!=v:
                        if current is not None and not acc.empty():raise RepeatedColumn()
                        acc.close();acc=Accumulator(w)
                        if seen[v]:raise RepeatedColumn()
                        seen[v]=True;current=v
                        for rr,c in f.column(v):acc.push(rr,-c);total+=1
                    acc.push(r,value)
                try:
                    with open(path,encoding='utf8') as stream:
                        for line in stream:
                            if not line.strip() or line.startswith('*'):continue
                            if ended:raise ValueError('Data after ENDATA')
                            p=line.split()
                            if not line[0].isspace():
                                section=p[0].upper()
                                if section not in ('NAME','OBJSENSE','OBJNAME','ROWS','COLUMNS','RHS','RANGES','BOUNDS','ENDATA'):raise ValueError('Unsupported MPS section')
                                if section=='ENDATA':ended=True
                                elif section=='OBJSENSE' and len(p)>1:sense=p[1].lower()
                                elif section=='OBJNAME' and len(p)>1:objective=p[1]
                                continue
                            if section=='OBJSENSE':sense=p[0].lower();continue
                            if section=='OBJNAME':objective=p[0];continue
                            if section=='COLUMNS' and any(x.strip("'")=='MARKER' for x in p):
                                marker=p[-1].strip("'")
                                if marker not in ('INTORG','INTEND') or (marker=='INTORG')==intmode:raise ValueError('Invalid integer marker')
                                intmode=marker=='INTORG';continue
                            if mps_format!='free':
                                fixed=_fixed_fields(line,section)
                                if fixed is not None:p=fixed
                                elif mps_format=='fixed':raise ValueError('Invalid fixed MPS')
                            if section=='ROWS':
                                if len(p)!=2 or p[0] not in 'NELG':raise ValueError('Invalid row record')
                                kind,name=p
                                if objective is None and kind=='N':objective=name
                                r=row(name)
                                if rk[r]:raise ValueError('Duplicate row')
                                rk[r]=ord(kind)
                            elif section=='COLUMNS':
                                if not p[0]:p[0]=lastcol
                                if len(p) not in (3,5) or not p[0]:raise ValueError('Invalid column record')
                                lastcol=p[0];v=variable(lastcol)
                                if intmode and not vk[v]:vk[v]=1;upper[v]=1
                                for k in range(1,len(p),2):
                                    r=row(p[k])
                                    if not rk[r]:raise ValueError('Undeclared row')
                                    value=number(p[k+1])
                                    if r==nrow:cost[v]=cost[v]+value
                                    else:coefficient(v,r,value)
                            elif section in ('RHS','RANGES'):
                                if len(p) not in (3,5):raise ValueError('Invalid RHS/range record')
                                if not p[0]:p[0]=lastset.get(section,'')
                                lastset[section]=p[0]
                                if not choose(section,p[0]):continue
                                for k in range(1,len(p),2):
                                    r=row(p[k])
                                    if not rk[r]:raise ValueError('Undeclared row')
                                    if section=='RHS':rv[r]=number(p[k+1])
                                    else:
                                        if r==nrow:raise ValueError('Range on objective')
                                        rg[r]=number(p[k+1]);has_range[r]=True
                            elif section=='BOUNDS':
                                if len(p) not in (3,4):raise ValueError('Invalid bound record')
                                kind,bset,name=p[:3]
                                if not choose(section,bset):continue
                                v=variable(name);value=number(p[3]) if len(p)==4 else None
                                if kind in ('LO','UP','FX','LI','UI') and value is None:raise ValueError('Missing bound value')
                                if kind in ('LO','LI'):lower[v]=value
                                elif kind in ('UP','UI'):upper[v]=value
                                elif kind=='FX':lower[v]=value;upper[v]=value
                                elif kind=='FR':lower[v]=-inf;upper[v]=inf
                                elif kind=='MI':lower[v]=-inf
                                elif kind=='PL':upper[v]=inf
                                elif kind=='BV':vk[v]=1;lower[v]=0;upper[v]=1
                                else:raise ValueError('Unsupported bound kind')
                                if kind in ('LI','UI'):vk[v]=1
                            else:raise ValueError('Unexpected MPS data')
                    if global_matrix:
                        for v in f.var_ids():
                            for r,c in f.column(v):acc.push(v*max(1,nrow)+r,-c)
                        total=f.nnz
                    if not acc.empty():raise ValueError('Artifact coefficient mismatch')
                    if not ended or intmode or objective is None or not rk[nrow]:raise ValueError('Incomplete MPS')
                    if sense!=f.sense or -rv[nrow]!=f.constant:raise ValueError('Artifact objective mismatch')
                    if np.count_nonzero(vp)!=f.nvars or np.count_nonzero(rk)!=f.nrows+1:raise ValueError('Artifact dimensions mismatch')
                    for sec,choice in choices.items():
                        if choice is not None and not found[sec]:raise ValueError('Selected vector missing')
                    for v in f.var_ids():
                        expected=f.numeric_var(v)
                        if ('I' if vk[v] else 'C',lower[v],upper[v],cost[v])!=expected[2:]:raise ValueError('Artifact variable mismatch: '+f.name('var',v))
                        if not global_matrix and not seen[v] and next(iter(f.column(v)),None) is not None:raise ValueError('Artifact missing coefficients')
                    for r in f.row_ids():
                        kind=chr(int(rk[r]));b=rv[r]
                        lo=b if kind in ('E','G') else -inf;hi=b if kind in ('E','L') else inf
                        if has_range[r]:
                            value=rg[r]
                            if kind=='L':lo=b-abs(value)
                            elif kind=='G':hi=b+abs(value)
                            elif kind=='E':lo,hi=(b,b+value) if value>=0 else (b+value,b)
                            else:raise ValueError('Range on free row')
                        if (lo,hi)!=f.row_bounds(r):raise ValueError('Artifact row mismatch: '+f.name('row',r))
                    return dict(status='PROVED',kind='artifact_readback',rows=f.nrows,variables=f.nvars,nonzeros=total)
                finally:
                    acc.close()
                    for values in (lower,upper,cost,rv,rg):values.close()
        try:return run()
        except RepeatedColumn:return run(True)
    finally:root.close()


def compare_lp(f,path,*,memory_mb=128,disk_mb=None,rename_vars=None,rename_rows=None,work=None,**_):
    """Audit LP parser events a row at a time, retaining only typed metadata."""
    from mipframe.store.stream_lp import parse
    with (work.fork() if work is not None else WorkContext(memory_mb,disk_mb)) as work:
        nv=NameLookup(f,'var',work,rename_vars);nr=NameLookup(f,'row',work,rename_rows)
        lower=ExactSlots(work,f.var_capacity);upper=ExactSlots(work,f.var_capacity,inf)
        cost=ExactSlots(work,f.var_capacity);kinds=work.array(f.var_capacity,'u1',zero=True)
        vp=work.array(f.var_capacity,'?',zero=True);rp=work.array(f.row_capacity,'?',zero=True)
        class Consumer:
            rows=0;constant=0;sense=None;acc=None;negative=False
            def number(self,x):return x
            def variable(self,name,line=None):
                v=nv[name];vp[v]=True;return v
            def new_row(self,name,line):
                r=nr[name]
                if rp[r]:raise ValueError('Duplicate artifact row')
                rp[r]=True;self.rows+=1;self.acc=Accumulator(work);self.negative=False
                return r
            def term(self,r,v,c):
                if r==-1:cost[v]=cost[v]+c
                else:self.acc.push(v,c)
            def negate_row(self,r):self.negative=not self.negative
            def row_bounds(self,r,lo,hi):
                if (lo if lo is not None else -inf,hi if hi is not None else inf)!=f.row_bounds(r):raise ValueError('Artifact row bounds mismatch')
                for v,c in f.terms(r):self.acc.push(v,c if self.negative else -c)
                if not self.acc.empty():raise ValueError('Artifact coefficient mismatch')
                self.acc.close();self.acc=None
            def set_bound(self,v,lo,hi,setlo,sethi):
                if setlo:lower[v]=-inf if lo is None else lo
                if sethi:upper[v]=inf if hi is None else hi
            def set_kind(self,v,kind):
                kinds[v]=1
                if kind=='B':lower[v]=max(lower[v],0);upper[v]=min(upper[v],1)
        consumer=Consumer()
        try:
            parse(path,consumer)
            if consumer.sense!=f.sense or consumer.constant!=f.constant:raise ValueError('Artifact objective mismatch')
            if int(vp.sum())!=f.nvars or int(rp.sum())!=f.nrows:raise ValueError('Artifact dimensions mismatch')
            for v in f.var_ids():
                if ('I' if kinds[v] else 'C',lower[v],upper[v],cost[v])!=f.numeric_var(v)[2:]:raise ValueError('Artifact variable mismatch')
            return dict(status='PROVED',kind='artifact_readback',rows=f.nrows,variables=f.nvars,nonzeros=f.nnz)
        finally:
            if consumer.acc is not None:consumer.acc.close()
            for x in (lower,upper,cost):x.close()
