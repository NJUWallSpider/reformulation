"""Disk artifact: CSR and CSC-position arrays, exact dictionary and provenance."""
from pathlib import Path
from functools import lru_cache
from fractions import Fraction as F
import hashlib,json,sqlite3,struct,time,resource,sys
import numpy as np
from .numbers import encoding,from_encoding,LPIRError
from .parser import parse

SCHEMA_VERSION=1

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def dump(path,data):
    compact = Path(path).name in ('compressed.json', 'structures.json')
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=None if compact else 2,separators=(',', ':') if compact else None)+'\n')

class Writer:
    def __init__(self,path):
        self.path=Path(path)
        self.db=sqlite3.connect(self.path/'metadata.sqlite')
        self.db.executescript('''
        CREATE TABLE numbers(id INTEGER PRIMARY KEY,mantissa TEXT,exponent INTEGER,approx REAL);
        CREATE TABLE variables(id INTEGER PRIMARY KEY,name TEXT UNIQUE,kind TEXT,lo INTEGER,hi INTEGER,obj INTEGER,line INTEGER);
        CREATE TABLE rows(id INTEGER PRIMARY KEY,name TEXT UNIQUE,lo INTEGER,hi INTEGER,line INTEGER);
        ''')
        self.numids={};self.names={};self.variables=[];self.rows=0;self.nnz=0
        self.ptr=[0];self.raw=open(self.path/'entries.tmp','wb');self.sense='min';self.constant=self.number(0)
    def number(self,value):
        if value is None:return None
        key=encoding(value)
        if key not in self.numids:
            idx=len(self.numids);self.numids[key]=idx
            try:approx=float(value)
            except OverflowError:approx=None
            self.db.execute('INSERT INTO numbers VALUES(?,?,?,?)',(idx,*key,approx))
        return self.numids[key]
    def variable(self,name,line):
        if name not in self.names:
            idx=len(self.variables);self.names[name]=idx
            self.variables.append([idx,name,'C',self.number(0),None,self.number(0),line])
        return self.names[name]
    def set_kind(self,v,kind):
        if self.variables[v][2]=='B' and kind=='I':return
        self.variables[v][2]=kind
    def set_bound(self,v,lo,hi,setlo,sethi):
        if setlo:self.variables[v][3]=self.number(lo)
        if sethi:self.variables[v][4]=self.number(hi)
    def objective(self,a,c):
        for v,value in a.items():self.variables[v][5]=self.number(value)
        self.constant=self.number(c)
    def row(self,name,a,lo,hi,line):
        self.db.execute('INSERT INTO rows VALUES(?,?,?,?,?)',(self.rows,name,self.number(lo),self.number(hi),line))
        for v,c in sorted(a.items()):
            self.raw.write(struct.pack('<QQ',v,self.number(c)))
        self.nnz+=len(a);self.ptr.append(self.nnz);self.rows+=1
    def finish(self):
        values={idx:from_encoding(*key) for key,idx in self.numids.items()}
        for record in self.variables:
            if record[2]=='B':
                lo,hi=record[3:5]
                record[3]=self.number(max(F(0),values[lo])) if lo is not None else self.number(0)
                record[4]=self.number(min(F(1),values[hi])) if hi is not None else self.number(1)
        self.db.executemany('INSERT INTO variables VALUES(?,?,?,?,?,?,?)',self.variables)
        self.db.commit();self.raw.close();self.db.close()
    def arrays(self):
        n=len(self.variables);z=self.nnz
        np.save(self.path/'row_ptr.npy',np.asarray(self.ptr,dtype=np.int64))
        cols=np.lib.format.open_memmap(self.path/'col_idx.npy',mode='w+',dtype=np.int64,shape=(z,))
        coeff=np.lib.format.open_memmap(self.path/'coeff_id.npy',mode='w+',dtype=np.int64,shape=(z,))
        count=np.zeros(n,dtype=np.int64)
        if z:
            raw=np.memmap(self.path/'entries.tmp',dtype='<u8',mode='r',shape=(z,2))
            for start in range(0,z,131072):
                stop=min(z,start+131072)
                block=raw[start:stop]
                cols[start:stop]=block[:,0];coeff[start:stop]=block[:,1]
                np.add.at(count,block[:,0].astype(np.int64),1)
            del raw
        cols.flush();coeff.flush()
        colptr=np.empty(n+1,dtype=np.int64);colptr[0]=0;np.cumsum(count,out=colptr[1:])
        np.save(self.path/'col_ptr.npy',colptr)
        positions=np.lib.format.open_memmap(self.path/'col_positions.npy',mode='w+',dtype=np.int64,shape=(z,))
        cursor=colptr[:-1].copy()
        for start in range(0,z,131072):
            for p,v in enumerate(cols[start:start+131072],start):
                positions[cursor[v]]=p;cursor[v]+=1
        positions.flush()
        (self.path/'entries.tmp').unlink()

class Model:
    def __init__(self,path):
        self.path=Path(path)
        self.manifest=json.loads((self.path/'manifest.json').read_text())
        if self.manifest['schema_version']!=SCHEMA_VERSION:raise LPIRError('Unsupported IR schema version')
        db=sqlite3.connect(f'file:{(self.path/"metadata.sqlite").resolve()}?mode=ro',uri=True)
        self.numbers=db.execute('SELECT mantissa,exponent FROM numbers ORDER BY id').fetchall()
        self.variables=db.execute('SELECT * FROM variables ORDER BY id').fetchall()
        self.rows=db.execute('SELECT * FROM rows ORDER BY id').fetchall();db.close()
        for name in ('row_ptr','col_idx','coeff_id','col_ptr','col_positions'):
            setattr(self,name,np.load(self.path/(name+'.npy'),mmap_mode='r'))
        self.var_names={r[1]:r[0] for r in self.variables};self.row_names={r[1]:r[0] for r in self.rows}
    @lru_cache(maxsize=65536)
    def num(self,idx):
        return None if idx is None else from_encoding(*self.numbers[int(idx)])
    def row_ids(self,r):
        lo,hi=map(int,self.row_ptr[r:r+2]);return [(int(v),int(c)) for v,c in zip(self.col_idx[lo:hi],self.coeff_id[lo:hi])]
    def row(self,r):return {v:self.num(c) for v,c in self.row_ids(r)}
    def incident(self,v):
        lo,hi=map(int,self.col_ptr[v:v+2]);ps=self.col_positions[lo:hi]
        return list(map(int,np.searchsorted(self.row_ptr,ps,side='right')-1))
    @property
    def compressed(self):
        if not hasattr(self,'_compressed'):
            self._compressed=json.loads((self.path/'compressed.json').read_text())
        return self._compressed


def build(source,out):
    out=Path(out)
    if out.exists() and any(out.iterdir()):raise LPIRError(f'Output directory must be empty: {out}')
    out.mkdir(parents=True,exist_ok=True)
    begin=time.perf_counter();writer=Writer(out)
    try:parse(source,writer)
    except Exception:
        if not writer.raw.closed:writer.raw.close()
        try:writer.db.close()
        except sqlite3.Error:pass
        raise
    parsed=time.perf_counter();writer.arrays();indexed=time.perf_counter()
    manifest={'schema_version':SCHEMA_VERSION,'model_version':sha256(source),'source':str(Path(source).resolve()),'source_bytes':Path(source).stat().st_size,'sense':writer.sense,'objective_constant':writer.constant,'variables':len(writer.variables),'rows':writer.rows,'nonzeros':writer.nnz,'timings':{'parse':parsed-begin,'index':indexed-parsed}}
    manifest['base_sha256']={p.name:sha256(p) for p in sorted(out.iterdir()) if p.suffix in ('.sqlite','.npy')}
    dump(out/'manifest.json',manifest)
    from .structure import discover
    model=Model(out);start=time.perf_counter();discover(model)
    manifest['timings']['discover']=time.perf_counter()-start
    from .verify import verify
    start=time.perf_counter();verification=verify(Model(out));manifest['timings']['verify']=time.perf_counter()-start
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    manifest['peak_rss_bytes']=int(peak if sys.platform=='darwin' else peak*1024)
    manifest['peak_rss_scope']='process high-water mark (includes earlier work if API reused)'
    manifest['artifact_bytes']={p.name:p.stat().st_size for p in out.iterdir() if p.is_file()}
    manifest['timings']['total']=time.perf_counter()-begin
    dump(out/'manifest.json',manifest);dump(out/'verification.json',verification)
    return Model(out)
