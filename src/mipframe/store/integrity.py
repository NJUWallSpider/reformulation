"""Storage facts shared by native opening, verification and export."""
from mipframe.store.artifacts import sha256
from mipframe.store.numbers import MIPFrameError


def dimensions_valid(store):
    """Validate both orientations and exact dictionary references in bounded blocks."""
    import numpy as np
    try:
        a=store.data.arrays
        nr,nv=len(store.rows),len(store.variables)
        nnz=len(a['col_idx']);nnum=len(a['number_flags'])
        expected={'row_ptr':nr+1,'col_ptr':nv+1,'col_idx':nnz,'coeff_id':nnz,
                  'col_positions':nnz,'csc_row_ids':nnz,'csc_value_ids':nnz,
                  'numerator_i64':nnum,'denominator_i64':nnum,'number_approx':nnum,'number_big_offsets':nnum+1,
                  'var_type':nv,'var_obj_id':nv}
        for axis,n in (('row',nr),('var',nv)):
            expected.update({axis+'_'+field:n for field in ('lb_id','ub_id','lb_finite','ub_finite','line')})
            expected[axis+'_name_offsets']=n+1
        for name,n in expected.items():
            value=a[name]
            if value.ndim!=1 or len(value)!=n:return False
            if name!='number_approx' and value.dtype.kind not in 'iu':return False
        for key in ('row_ptr','col_ptr'):
            ptr=a[key]
            if ptr[0]!=0 or ptr[-1]!=nnz or np.any(ptr[1:]<ptr[:-1]):return False
        for key,maximum in (('col_idx',nv),('coeff_id',nnum),('csc_row_ids',nr),('csc_value_ids',nnum),('col_positions',nnz),('var_obj_id',nnum)):
            for start in range(0,len(a[key]),65536):
                values=a[key][start:start+65536]
                if np.any(values<0) or np.any(values>=maximum):return False
        if np.any((a['var_type']!=0)&(a['var_type']!=1)):return False
        for axis,n in (('row',nr),('var',nv)):
            for side in ('lb','ub'):
                ids=a[f'{axis}_{side}_id'];flags=a[f'{axis}_{side}_finite']
                if np.any((flags!=0)&(flags!=1)) or np.any(ids<0) or np.any(ids>=nnum):return False
            offsets=a[axis+'_name_offsets'];data=a[axis+'_name_utf8']
            if data.dtype!=np.dtype('uint8') or offsets[0]!=0 or offsets[-1]!=len(data) or np.any(offsets[1:]<offsets[:-1]):return False
        flags=a['number_flags'];small=flags!=3
        if np.any((flags<1)|(flags>3)) or np.any(a['denominator_i64'][small]<=0):return False
        if np.any(a['denominator_i64'][flags==1]!=1):return False
        offsets=a['number_big_offsets'];payload=a['number_big_utf8']
        if payload.dtype!=np.dtype('uint8') or offsets[0]!=0 or offsets[-1]!=len(payload) or np.any(offsets[1:]<offsets[:-1]):return False
        from math import gcd
        for ident in np.flatnonzero(flags==3):
            n,d=store.data.numbers.parts(int(ident));n,d=int(n),int(d)
            if d<=0 or gcd(n,d)!=1:return False
        for ident in np.flatnonzero(flags==2):
            n,d=int(a['numerator_i64'][ident]),int(a['denominator_i64'][ident])
            if d<=1 or gcd(n,d)!=1:return False
        nonzero=a['numerator_i64']!=0
        for ident in np.flatnonzero(flags==3):nonzero[ident]=bool(store.data.numbers[int(ident)])
        if np.any(~nonzero[a['coeff_id']]):return False
        for start in range(0,nnz,65536):
            stop=min(start+65536,nnz)
            offsets=np.arange(start,stop,dtype=np.uint64)
            positions=a['col_positions'][start:stop]
            rows=np.searchsorted(a['row_ptr'],positions,side='right')-1
            cols=np.searchsorted(a['col_ptr'],offsets,side='right')-1
            if not (np.array_equal(rows,a['csc_row_ids'][start:stop]) and
                    np.array_equal(cols,a['col_idx'][positions]) and
                    np.array_equal(a['coeff_id'][positions],a['csc_value_ids'][start:stop])):return False
            # Strictly increasing neighbors prohibit duplicates and ensure the CSC
            # position mapping is a bijection, including across block boundaries.
            begin=max(1,start)
            for ptr,neighbors in ((a['row_ptr'],a['col_idx']),(a['col_ptr'],a['csc_row_ids'])):
                p=np.arange(begin,stop,dtype=np.uint64)
                same=np.searchsorted(ptr,p,side='right')==np.searchsorted(ptr,p-1,side='right')
                if np.any(same & (neighbors[begin:stop]<=neighbors[begin-1:stop-1])):return False
        return True
    except (KeyError,ValueError,IndexError,TypeError,OverflowError):
        return False


def checks(store, *, hash_file=sha256):
    if hasattr(store,'f'):
        store.guard();f=store.f
        valid=all(f.row(r)[2]<=f.row(r)[3] and all(0<=v<f.var_capacity and c for v,c in f.terms(r)) for r in f.row_ids())
        yield dict(component='storage',item='overlay',status='PROVED' if valid else 'REFUTED',reason='' if valid else 'Invalid overlay')
        return
    valid = True
    for name, digest in store.manifest['base_sha256'].items():
        path = store.path / name
        ok = path.exists() and hash_file(path) == digest
        valid &= ok
        yield dict(component='storage', item=name,
                   status='PROVED' if ok else 'REFUTED',
                   reason='' if ok else 'Base artifact checksum mismatch')
    if valid:
        ok = dimensions_valid(store)
        yield dict(component='storage', item='dimensions',
                   status='PROVED' if ok else 'REFUTED',
                   reason='' if ok else 'Invalid sparse model dimensions')


def require_integrity(store):
    for check in checks(store):
        if check['status'] != 'PROVED':
            raise MIPFrameError(f"{check['reason']}: {check['item']}")
