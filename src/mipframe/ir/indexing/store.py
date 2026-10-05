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
from mipframe.data.runtime import WorkContext
from mipframe.store.numbers import MIPFrameError

VERSION='7.0'
FILENAME='indexing.sqlite'


def js(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=lambda x:str(x) if isinstance(x,Fraction) else x)


def ident(prefix,value):return prefix+hashlib.sha256(js(value).encode()).hexdigest()[:24]


class Table:
    """Bounded primitive or packed exact columns, without Python dictionaries."""
    def __init__(self,fields,records=(),numeric=(),work=None):
        from mipframe.data.relations import build
        self.fields=tuple(fields);self.numeric=set(numeric);self.work=work or WorkContext()
        relation=build(self.work,{n:'number' if n in self.numeric else 'int' for n in self.fields},
                       (dict(zip(self.fields,row)) for row in records))
        self.columns=relation.columns
    def __len__(self):return len(self.columns[self.fields[0]])
    def records(self):
        from mipframe.data.relations import scalar
        for i in range(len(self)):yield tuple(None if self.columns[n][i] is None else Fraction(self.columns[n][i]) if n in self.numeric else int(self.columns[n][i]) for n in self.fields)
    def copy(self):
        result=object.__new__(Table);result.fields=self.fields;result.numeric=self.numeric.copy()
        result.columns=self.columns.copy();result.work=self.work;return result


class Store:
    def __init__(self,owner,memory_mb=128,disk_mb=None):
        self.owner=owner;self.work=WorkContext(memory_mb,disk_mb);self.memory=self.work.memory
        self.closed=False;self.maps=[];self._parents=[];self._borrowers=0
        from mipframe.data.records import RecordMap
        self.runs=RecordMap(self.work);self.cards=RecordMap(self.work);self.candidates=RecordMap(self.work)
        self.domains={};self.domain_axes={}
        self.members={};self.coordinates={};self.bindings={};self.kernels={};self.alignments={}
        self._domain_index={};self.artifact_refs={}
        try:
            previous=getattr(owner,'_indexing_store',None)
            if previous is not None and not previous.closed:
                previous._borrowers+=1;self._parents.append(previous)
                self.runs=RecordMap(self.work,previous.runs);self.cards=RecordMap(self.work,previous.cards)
                self.candidates=RecordMap(self.work,previous.candidates)
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
        self.maps.clear()
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
            if json.loads(meta['identity'])!=expected:raise MIPFrameError('Incompatible indexed result artifact')
            name=meta['manifest']
            from mipframe.store.artifacts import sha256
            if Path(name).name!=name:raise MIPFrameError('Invalid indexed manifest path')
            manifest=cache_directory(self.owner)/'derived'/name
            if sha256(manifest)!=meta['content_hash']:raise MIPFrameError('Corrupt indexed result artifact')
            state=json.loads(manifest.read_text())
            from mipframe.store.artifacts import sha256
            def array(record):
                name=record['path'];p=cache_directory(self.owner)/'derived'/name
                if Path(name).name!=name or sha256(p)!=record['checksum']:raise MIPFrameError('Corrupt indexed array artifact')
                a=np.load(p,mmap_mode='r',allow_pickle=False)
                if a.dtype.str!=record['dtype'] or list(a.shape)!=record['shape']:raise MIPFrameError('Invalid indexed array shape')
                self.maps.append(a);values=np.asarray(a);self.artifact_refs[id(values)]=record;return values
            from mipframe.data.records import RecordMap
            for name in ('runs','cards','candidates'):
                setattr(self,name,RecordMap(self.work,RecordMap.restore(state[name],array,self.work)))
            self.domains={int(g):array(record['array']) for g,record in state['domains'].items()}
            self.domain_axes={int(g):record['axis'] for g,record in state['domains'].items()}
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
        except (sqlite3.Error,ValueError,KeyError,OSError) as error:raise MIPFrameError(f'Invalid indexed result artifact: {error}') from error

    def publish(self):
        self.guard();directory=cache_directory(self.owner)/'derived';directory.mkdir(exist_ok=True)
        from mipframe.store.artifacts import sha256
        from mipframe.data.runtime import DiskReservation
        reservation=DiskReservation(self.work)
        generation=uuid.uuid4().hex;created=[];published_refs={}
        previous_records=[(records,records._encoded) for records in (self.runs,self.cards,self.candidates)]
        def array(a,persistent=True):
            if id(a) in self.artifact_refs:return self.artifact_refs[id(a)]
            name=f'index-{generation}-{len(created)}.npy';path=directory/name
            reservation.add(a.nbytes+256)
            created.append(path);np.save(path,a,allow_pickle=False)
            record=dict(path=name,dtype=a.dtype.str,shape=list(a.shape),checksum=sha256(path,work=self.work))
            if persistent:published_refs[id(a)]=record
            return record
        temporary_catalog=None
        try:
            state=dict(runs=self.runs.export(lambda a:array(a,False)),cards=self.cards.export(lambda a:array(a,False)),candidates=self.candidates.export(lambda a:array(a,False)),
                       domains={g:dict(axis=self.domain_axes[g],array=array(values)) for g,values in self.domains.items()})
            def column(v):
                return dict(packed=v.kind,arrays={k:array(a) for k,a in v.arrays.items()}) if hasattr(v,'arrays') else array(v)
            for name in ('members','coordinates','bindings','kernels','alignments'):
                state[name]={key:dict(fields=t.fields,numeric=sorted(t.numeric),columns={n:column(v) for n,v in t.columns.items()})
                             for key,t in getattr(self,name).items()}
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
