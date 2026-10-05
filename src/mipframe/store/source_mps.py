"""MPS source bytes and compact physical-record index captured during import."""
from pathlib import Path
import hashlib
import json
import struct
import numpy as np

SECTIONS=('','NAME','OBJSENSE','OBJNAME','ROWS','COLUMNS','RHS','RANGES','BOUNDS','ENDATA')
RECORD=np.dtype([('offset','<u8'),('length','<u4'),('section','u1'),('flags','u1'),
                 ('row1','<i8'),('row2','<i8')])
PACK=struct.Struct('<QIBBqq')
CONTEXT=np.dtype([('start','<u8'),('var','<i8')])
CONTEXT_PACK=struct.Struct('<Qq')
HEADER,FIXED,SELECTED,MARKER,CONTINUATION=1,2,4,8,16


class SourceCapture:
    def __init__(self, source, directory, *, preserve=True):
        self.source,self.directory=Path(source),Path(directory)
        self.preserve=preserve;self.digest=hashlib.sha256();self.offset=0
        self.record=None;self.count=0;self.section=0;self.last_var=None;self.context_count=0
    def __enter__(self):
        self.input=self.source.open('rb')
        self.output=(self.directory/'source.mps').open('wb') if self.preserve else None
        self.index=(self.directory/'source_records.bin').open('wb')
        self.context=(self.directory/'source_contexts.bin').open('wb')
        return self
    def _flush(self):
        if self.record is not None:
            if self.record[4]!=self.last_var:
                self.context.write(CONTEXT_PACK.pack(self.count,self.record[4]));self.context_count+=1;self.last_var=self.record[4]
            r=self.record
            self.index.write(PACK.pack(r[0],r[1],r[2],r[3],r[5],r[6]));self.count+=1;self.record=None
    def readline(self, limit):
        self._flush()
        raw=self.input.readline(limit)
        if not raw:return ''
        self.digest.update(raw)
        if self.output is not None:self.output.write(raw)
        self.record=[self.offset,len(raw),self.section,SELECTED,-2,-2,-2]
        self.offset+=len(raw)
        return raw.decode('utf-8')
    def mark(self, *, section=None, flags=0, var=None, rows=None, selected=None):
        if section is not None:
            self.section=SECTIONS.index(section);self.record[2]=self.section
        self.record[3]|=flags
        if selected is False:self.record[3]&=~SELECTED
        if var is not None:self.record[4]=var
        if rows is not None:
            for i,r in enumerate(rows):self.record[5+i]=r
    def __exit__(self, typ, value, traceback):
        self._flush();self.input.close();self.index.close();self.context.close()
        if self.output is not None:self.output.close()
        for name,dtype,count in [('source_records',RECORD,self.count),('source_contexts',CONTEXT,self.context_count)]:
            raw=self.directory/(name+'.bin')
            if typ is None:
                target=np.lib.format.open_memmap(self.directory/(name+'.npy'),mode='w+',dtype=dtype,shape=(count,))
                with raw.open('rb') as stream:
                    start=0
                    while block:=stream.read(dtype.itemsize*8192):
                        values=np.frombuffer(block,dtype=dtype);target[start:start+len(values)]=values;start+=len(values)
                target.flush();target._mmap.close()
            raw.unlink(missing_ok=True)


class SourceRecords:
    """Borrow physical records; decode repeated column contexts only per chunk."""
    def __init__(self,records,contexts,start=0):self.records,self.contexts,self.start=records,contexts,start
    def __len__(self):return len(self.records)
    def __getitem__(self,key):
        if isinstance(key,slice):
            start,stop,step=key.indices(len(self))
            if step!=1:raise ValueError('Source index requires contiguous record ranges')
            return SourceRecords(self.records[key],self.contexts,self.start+start)
        if isinstance(key,str):
            if key!='var':return self.records[key]
            if 'var' in self.records.dtype.names:return self.records['var']
            positions=np.arange(self.start,self.start+len(self.records),dtype='<u8')
            runs=np.searchsorted(self.contexts['start'],positions,side='right')-1
            return self.contexts['var'][runs]
        return SourceRecord(self.records[key],self.contexts,self.start+int(key))


class SourceRecord:
    def __init__(self,record,contexts,index):self.record,self.contexts,self.index=record,contexts,index
    def __getitem__(self,key):
        if key!='var' or key in self.record.dtype.names:return self.record[key]
        run=int(np.searchsorted(self.contexts['start'],self.index,side='right'))-1
        return self.contexts['var'][run]
