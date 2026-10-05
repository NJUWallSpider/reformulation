"""Canonical mathematical identity, streamed from typed arrays in bounded blocks."""
import hashlib
import json
import numpy as np


def fingerprint(data,sense,constant,block=65536):
    a=data.arrays;n=len(data.numbers)
    used=np.zeros(n,dtype=bool);used[int(constant)]=True
    for field in ('coeff_id','var_obj_id'):
        for start in range(0,len(a[field]),block):used[a[field][start:start+block]]=True
    for axis in ('row','var'):
        for side in ('lb','ub'):
            ids=a[f'{axis}_{side}_id'];finite=a[f'{axis}_{side}_finite']
            for start in range(0,len(ids),block):
                selected=ids[start:start+block][finite[start:start+block].astype(bool)]
                used[selected]=True
    parts={int(i):data.numbers.parts(int(i)) for i in np.flatnonzero(used)}
    dictionary=sorted(set(parts.values()))
    indices={value:i for i,value in enumerate(dictionary)}
    mapping=np.zeros(n,dtype='<u8')
    for i,value in parts.items():mapping[i]=indices[value]
    digest=hashlib.sha256()
    digest.update(b'mipframe-mathematics-v2\0')
    digest.update(json.dumps([sense,int(mapping[int(constant)]),dictionary],separators=(',',':')).encode())
    def emit(name,values):
        digest.update(name.encode()+b'\0')
        digest.update(len(values).to_bytes(8,'little'))
        for start in range(0,len(values),block):
            digest.update(np.asarray(values[start:start+block],dtype='<u8').tobytes())
    for axis in ('row','var'):
        emit(axis+'_names',a[axis+'_name_offsets'])
        for start in range(0,len(a[axis+'_name_utf8']),block):digest.update(a[axis+'_name_utf8'][start:start+block].tobytes())
        for side in ('lb','ub'):
            ids=a[f'{axis}_{side}_id'];finite=a[f'{axis}_{side}_finite']
            digest.update(f'{axis}_{side}'.encode()+b'\0');digest.update(len(ids).to_bytes(8,'little'))
            for start in range(0,len(ids),block):
                encoded=mapping[ids[start:start+block]].copy()
                encoded[finite[start:start+block]==0]=len(dictionary)
                digest.update(encoded.tobytes())
    emit('var_type',a['var_type']);emit('row_ptr',a['row_ptr']);emit('col_idx',a['col_idx'])
    for field in ('var_obj_id','coeff_id'):
        ids=a[field];digest.update(field.encode()+b'\0');digest.update(len(ids).to_bytes(8,'little'))
        for start in range(0,len(ids),block):digest.update(mapping[ids[start:start+block]].tobytes())
    return digest.hexdigest()
