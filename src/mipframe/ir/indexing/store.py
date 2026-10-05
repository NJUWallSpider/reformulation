from mipframe.control.cache import directory as cache_directory
"""Typed indexed artifacts with an atomically published control catalog."""
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from fractions import Fraction
import numpy as np
from mipframe.data.ids import IDSet
from mipframe.ir.indexing.records import IndexRecords
from mipframe.data.runtime import WorkContext
from mipframe.store.numbers import MIPFrameError

VERSION='7.1'
FILENAME='indexing.sqlite'


def js(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=lambda x:str(x) if isinstance(x,Fraction) else x)


def ident(prefix,value):return prefix+hashlib.sha256(js(value).encode()).hexdigest()[:24]


class Table:
    """Bounded primitive or packed exact columns, without Python dictionaries."""
    def __init__(self,fields,records=(),numeric=(),work=None):
        from mipframe.data.relations import ColumnBuilder
        self.fields=tuple(fields);self.numeric=set(numeric);self.work=work or WorkContext()
        builders=[ColumnBuilder(self.work,'number' if n in self.numeric else 'int') for n in self.fields]
        # This internal relation has fixed tuple structure. ColumnBuilder checks
        # oversized exact scalars; reserve one tuple instead of measuring and
        # allocating a dictionary for every row.
        from itertools import islice
        row_cost=128+96*len(self.fields)
        block=max(1,min(4096,self.work.memory//8//row_cost,self.work.available//4//row_cost))
        iterator=iter(records)
        with self.work.reserve(block*row_cost):
            while rows:=tuple(islice(iterator,block)):
                for builder,values in zip(builders,zip(*rows)):
                    # Homogeneous primitive columns use Buffer.extend. Mixed
                    # nullable/rational columns retain the exact scalar path.
                    column=np.asarray(values)
                    # Mixed signed/large unsigned integers may infer float64;
                    # never accept that lossy coercion for exact columns.
                    builder.extend(column if column.dtype.kind in 'iu' else values)
        self.columns={n:b.finish() for n,b in zip(self.fields,builders)}
    def __len__(self):return len(self.columns[self.fields[0]])
    def records(self,*,exact=True):
        columns=[(self.columns[n],n in self.numeric) for n in self.fields]
        for i in range(len(self)):
            row=[]
            for column,numeric in columns:
                value=column[i]
                if value is not None:
                    if numeric and exact:
                        if not isinstance(value,Fraction):value=Fraction(int(value))
                    elif not numeric or isinstance(value,np.integer):value=int(value)
                row.append(value)
            yield tuple(row)
    @classmethod
    def from_columns(cls,columns,work,numeric=()):
        """Borrow immutable columns whose allocation is retained by the store."""
        result=object.__new__(cls);result.fields=tuple(columns);result.numeric=set(numeric)
        result.columns=columns;result.work=work
        return result
    def copy(self):
        result=object.__new__(Table);result.fields=self.fields;result.numeric=self.numeric.copy()
        result.columns=self.columns.copy();result.work=self.work;return result


class Store:
    def __init__(self,owner,memory_mb=128,disk_mb=None):
        self.owner=owner;self.work=WorkContext(memory_mb,disk_mb);self.memory=self.work.memory
        self.closed=False;self.maps=[];self._parents=[];self._borrowers=0
        from mipframe.data.records import RecordMap
        self.runs=RecordMap(self.work);self.cards=IndexRecords(self.work);self.candidates=IndexRecords(self.work)
        self.domains={};self.domain_axes={}
        self.members={};self.coordinates={};self.bindings={};self.kernels={};self.alignments={}
        self._domain_index={};self.artifact_refs={};self._loaded_arrays={}
        try:
            previous=getattr(owner,'_indexing_store',None)
            if previous is not None and not previous.closed:
                previous._borrowers+=1;self._parents.append(previous)
                self.runs=RecordMap(self.work,previous.runs);self.cards=IndexRecords(self.work,previous.cards)
                self.candidates=IndexRecords(self.work,previous.candidates)
                self.domains=dict(previous.domains);self.domain_axes=dict(previous.domain_axes)
                self.artifact_refs=dict(previous.artifact_refs)
                for name in ('members','coordinates','bindings','kernels','alignments'):
                    setattr(self,name,{k:v.copy() for k,v in getattr(previous,name).items()})
                # Reopen mmap ownership when borrowing a persisted store, so closing
                # an older result cannot invalidate a newly published run.

            else:self._load()
            for g,ids in self.domains.items():self._domain_index.setdefault((self.domain_axes[g],hashlib.sha256(ids).digest()),[]).append(g)
            from mipframe.store.lifecycle import register
            register(owner,self)
        except BaseException:self.close();raise

    def guard(self):
        self.owner.model
        if self.closed:raise MIPFrameError('Indexed store is closed')

    def close(self):
        if self.closed:return
        self.closed=True
        self._release()

    def _release(self):
        if not self.closed or self._borrowers:return
        self.work.close()
        for a in self.maps:
            if not a._mmap.closed:a._mmap.close()
        self.maps.clear();self._loaded_arrays.clear()
        for parent in self._parents:
            parent._borrowers-=1;parent._release()
        self._parents.clear()
    _close_connection=close

    def make_table(self,fields,records=(),numeric=()):
        return Table(fields,records,numeric,work=self.work)

    def domain(self,axis,ids):
        values=IDSet(ids,work=self.work).array
        key=axis,hashlib.sha256(values).digest()
        for g in self._domain_index.get(key,()):
            if np.array_equal(values,self.domains[g]):return g
        g=len(self.domains)+1;self._domain_index.setdefault(key,[]).append(g)
        values.flags.writeable=False;self.domains[g]=values;self.domain_axes[g]=axis
        return g
    def domain_sorted(self,axis,values):
        """Intern a canonical immutable ID view owned by this store."""
        key=axis,hashlib.sha256(values).digest()
        for g in self._domain_index.get(key,()):
            if np.array_equal(values,self.domains[g]):return g
        g=len(self.domains)+1;self._domain_index.setdefault(key,[]).append(g)
        self.domains[g]=values;self.domain_axes[g]=axis
        return g
    def size(self,g):return len(self.domains[g])
    def records(self,run,kind):
        self.guard()
        for r,k,key in sorted(self.cards):
            if (r,k)==(run,kind):yield deepcopy(self.cards[r,k,key])
    def get(self,run,kind,key):self.guard();return deepcopy(self.cards[run,kind,key])
    def put(self,run,kind,key,record):
        old=self.cards.get((run,kind,key))
        if old is not None:
            fields={'partitions':('rows_domain','vars_domain'),'tensors':('rows_domains','vars_domain'),'operators':('tensor','fixed_axis')}.get(kind)
            if not (all(old[f]==record[f] for f in fields) if fields else old==record):
                raise MIPFrameError('Indexed identifier collision; no result was published')
        self.cards[run,kind,key]=deepcopy(record)

    def _load(self):
        path=cache_directory(self.owner)/FILENAME
        if not path.exists():return
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                meta=dict(db.execute('SELECT key,value FROM meta'))
            expected=[VERSION,self.owner.uid,self.owner.model.manifest['model_version']]
            identity=json.loads(meta['identity'])
            if identity not in (expected,['7.0',*expected[1:]]):raise MIPFrameError('Incompatible indexed result artifact')
            name=meta['manifest']
            from mipframe.store.artifacts import sha256
            if Path(name).name!=name:raise MIPFrameError('Invalid indexed manifest path')
            manifest=cache_directory(self.owner)/'derived'/name
            if sha256(manifest)!=meta['content_hash']:raise MIPFrameError('Corrupt indexed result artifact')
            state=json.loads(manifest.read_text())
            from mipframe.store.artifacts import sha256
            loaded=self._loaded_arrays
            def array(record):
                if 'view' in record:
                    base=array(record['view']);start=record['start'];stop=record['stop']
                    if (base.ndim!=1 or type(start) is not int or type(stop) is not int
                        or not 0<=start<=stop<=len(base)):
                        raise MIPFrameError('Invalid indexed array view')
                    values=base[start:stop];self.artifact_refs[id(values)]=record
                    return values
                name=record['path'];p=cache_directory(self.owner)/'derived'/name
                if name in loaded:
                    descriptor,values=loaded[name]
                    if descriptor!=record:raise MIPFrameError('Inconsistent indexed array descriptor')
                    return values
                if Path(name).name!=name or sha256(p)!=record['checksum']:raise MIPFrameError('Corrupt indexed array artifact')
                a=np.load(p,mmap_mode='r',allow_pickle=False)
                if a.dtype.str!=record['dtype'] or list(a.shape)!=record['shape']:raise MIPFrameError('Invalid indexed array shape')
                self.maps.append(a);values=np.asarray(a);self.artifact_refs[id(values)]=record
                loaded[name]=(record,values);return values
            from mipframe.data.records import RecordMap
            for name in ('runs','cards','candidates'):
                cls=RecordMap if name=='runs' else IndexRecords
                setattr(self,name,cls(self.work,RecordMap.restore(state[name],array,self.work)))
            self.domains={int(g):array(record['array']) for g,record in state['domains'].items()}
            self.domain_axes={int(g):record['axis'] for g,record in state['domains'].items()}
            for group in state.get('singleton_domains',[]):
                ids=array(group['ids']);values=array(group['values'])
                if ids.ndim!=1 or values.ndim!=1 or len(ids)!=len(values):
                    raise MIPFrameError('Invalid singleton domains')
                for i,g in enumerate(ids):
                    g=int(g)
                    if g in self.domains:raise MIPFrameError('Duplicate indexed domain')
                    self.domains[g]=values[i:i+1];self.domain_axes[g]=group['axis']
            for name in ('members','coordinates','bindings','kernels','alignments'):
                tables={}
                for key,record in state[name].items():
                    t=object.__new__(Table);t.fields=tuple(record['fields']);t.numeric=set(record['numeric'])
                    from mipframe.data.relations import PackedColumn
                    t.columns={n:PackedColumn(v['packed'],{part:array(a) for part,a in v['arrays'].items()})
                               if 'packed' in v else array(v) for n,v in record['columns'].items()}
                    t.work=self.work
                    if set(t.fields)!=set(t.columns) or not t.numeric<=set(t.fields):raise MIPFrameError('Invalid indexed columns')
                    if len({len(v) for v in t.columns.values()})>1:raise MIPFrameError('Invalid indexed table dimensions')
                    tables[int(key) if name=='members' else key]=t
                setattr(self,name,tables)
            for group in state.get('singleton_members',[]):
                ids=array(group['ids']);columns={n:array(a) for n,a in group['columns'].items()}
                if ids.ndim!=1 or any(v.ndim!=1 or len(v)!=len(ids) for v in columns.values()):
                    raise MIPFrameError('Invalid singleton members')
                for i,g in enumerate(ids):
                    g=int(g)
                    if g in self.members:raise MIPFrameError('Duplicate indexed members')
                    self.members[g]=Table.from_columns({n:v[i:i+1] for n,v in columns.items()},self.work,group['numeric'])
        except (sqlite3.Error,ValueError,KeyError,OSError) as error:raise MIPFrameError(f'Invalid indexed result artifact: {error}') from error

    def publish(self):
        self.guard();directory=cache_directory(self.owner)/'derived';directory.mkdir(exist_ok=True)
        from mipframe.store.artifacts import sha256
        from mipframe.data.runtime import DiskReservation
        reservation=DiskReservation(self.work)
        generation=uuid.uuid4().hex;created=[];published_refs={};shared_refs={}
        previous_records=[(records,records._encoded) for records in (self.runs,self.cards,self.candidates)]
        def array(a,persistent=True):
            if id(a) in self.artifact_refs:return self.artifact_refs[id(a)]
            if persistent and id(a) in published_refs:return published_refs[id(a)]
            # Persist shared contiguous backing columns once, with checked offsets.
            # Temporary RecordMap export arrays cannot be cached by object ID:
            # their workspace is released between columns and IDs can be reused.
            root=a
            if persistent:
                while isinstance(root.base,np.ndarray):root=root.base
                if root is not a and root.ndim==a.ndim==1 and root.dtype==a.dtype and a.flags.c_contiguous:
                    entry=shared_refs.get(id(root))
                    if entry is None:
                        entry=(root,array(root));shared_refs[id(root)]=entry
                    start=(a.ctypes.data-root.ctypes.data)//a.dtype.itemsize
                    record=dict(view=entry[1],start=start,stop=start+len(a))
                    published_refs[id(a)]=record
                    return record
            name=f'index-{generation}-{len(created)}.npy';path=directory/name
            reservation.add(a.nbytes+256)
            created.append(path);np.save(path,a,allow_pickle=False)
            record=dict(path=name,dtype=a.dtype.str,shape=list(a.shape),checksum=sha256(path,work=self.work))
            if persistent:published_refs[id(a)]=record
            return record
        temporary_catalog=None
        try:
            state=dict(runs=self.runs.export(lambda a:array(a,False)),cards=self.cards.export(lambda a:array(a,False)),candidates=self.candidates.export(lambda a:array(a,False)),
                       domains={g:dict(axis=self.domain_axes[g],array=array(values)) for g,values in self.domains.items() if len(values)!=1})
            def column(v):
                return dict(packed=v.kind,arrays={k:array(a) for k,a in v.arrays.items()}) if hasattr(v,'arrays') else array(v)
            # Dense single-row groups keep the catalog small as well as avoiding
            # one file per member/domain. Readback creates cheap immutable views.
            from collections import defaultdict
            groups=defaultdict(list)
            for g,values in self.domains.items():
                if len(values)==1:groups[self.domain_axes[g],values.dtype.str].append(g)
            def packed(ids,dtype,values):
                with self.work.fork() as scratch:
                    a=scratch.array(len(ids),dtype)
                    for i,value in enumerate(values):a[i]=value
                    return array(a,False)
            state['singleton_domains']=[dict(axis=axis,ids=packed(ids,'<u8',ids),
                values=packed(ids,dtype,(self.domains[g][0] for g in ids)))
                for (axis,dtype),ids in groups.items()]
            groups=defaultdict(list)
            for g,t in self.members.items():
                if len(t)==1 and all(isinstance(v,np.ndarray) for v in t.columns.values()):
                    groups[t.fields,tuple(sorted(t.numeric)),tuple(v.dtype.str for v in t.columns.values())].append(g)
            singleton_keys=set(g for ids in groups.values() for g in ids)
            state['singleton_members']=[dict(ids=packed(ids,'<u8',ids),numeric=numeric,
                columns={n:packed(ids,dtype,(self.members[g].columns[n][0] for g in ids)) for n,dtype in zip(fields,dtypes)})
                for (fields,numeric,dtypes),ids in groups.items()]
            for name in ('members','coordinates','bindings','kernels','alignments'):
                state[name]={key:dict(fields=t.fields,numeric=sorted(t.numeric),columns={n:column(v) for n,v in t.columns.items()})
                             for key,t in getattr(self,name).items() if name!='members' or key not in singleton_keys}
            manifest=directory/f'index-{generation}.json';created.append(manifest)
            with manifest.open('w') as stream:json.dump(state,stream,separators=(',',':'));stream.flush();os.fsync(stream.fileno())
            reservation.add(manifest.stat().st_size)
            fd,temporary_catalog=tempfile.mkstemp(prefix='.indexing-',suffix='.sqlite',dir=cache_directory(self.owner));os.close(fd)
            with closing(sqlite3.connect(temporary_catalog)) as db:
                db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
                db.executemany('INSERT INTO meta VALUES(?,?)',[
                    ('identity',js([VERSION,self.owner.uid,self.owner.model.manifest['model_version']])),
                    ('manifest',manifest.name),('content_hash',sha256(manifest,work=self.work))]);db.commit()
            os.replace(temporary_catalog,cache_directory(self.owner)/FILENAME)
            self.artifact_refs.update(published_refs)
        except BaseException:
            for records,encoded in previous_records:records._encoded=encoded
            for p in created:p.unlink(missing_ok=True)
            raise
        finally:
            reservation.close()
            if temporary_catalog is not None:Path(temporary_catalog).unlink(missing_ok=True)


def restore(owner):
    if (cache_directory(owner)/FILENAME).exists():
        store=Store(owner);owner._indexing_store=store
        from mipframe.ir.indexing.views import IndexedResult
        owner._indexed_results={r:IndexedResult(store,r) for r in sorted(store.runs)}
