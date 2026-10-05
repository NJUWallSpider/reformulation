from mipframe.control.cache import directory as cache_directory
"""Template and exact disk-backed refinement on the orientation-expanded graph.

Numerical values, roles and signatures live in typed artifacts. SQLite holds
configuration identities and array manifests; hashes never replace full equality.
"""

from collections import OrderedDict
from fractions import Fraction
from itertools import chain, islice, zip_longest
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import math
import sqlite3
import struct
from weakref import WeakSet

import numpy as np

from mipframe.frame.entity import _sense
from mipframe.store.numbers import MIPFrameError
from mipframe.ir.typed import bound_class

VERSION = "7.0"
TOKEN = struct.Struct(">qqq")
KEY_CHUNK = 256


def _new_hash():
    return hashlib.blake2b(digest_size=32)


def _json(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def validate(depth, coefficient, rhs, objective, memory_mb, mode="template", multiplicity_ratio=2):
    if type(depth) is not int or depth < 0:
        raise ValueError("depth must be a nonnegative integer")
    if mode not in ("template", "exact"):
        raise ValueError("mode must be 'template' or 'exact'")
    expected = "sign" if mode == "template" else "scale_normalized"
    if coefficient is not None and coefficient != expected:
        raise ValueError(f"mode={mode!r} requires coefficient={expected!r}; use mode='exact' for scale_normalized labels")
    if isinstance(multiplicity_ratio, bool) or not isinstance(multiplicity_ratio, (int, float, Fraction)):
        raise ValueError("multiplicity_ratio must be finite and greater than 1")
    try:
        ratio = Fraction(str(multiplicity_ratio))
    except (ValueError, OverflowError):
        raise ValueError("multiplicity_ratio must be finite and greater than 1") from None
    if ratio <= 1:
        raise ValueError("multiplicity_ratio must be finite and greater than 1")
    if mode == "exact" and ratio != 2:
        raise ValueError("multiplicity_ratio only applies to mode='template'")
    if rhs not in ("sign", "nonzero"):
        raise ValueError("rhs must be 'sign' or 'nonzero'")
    if objective != "sign":
        raise ValueError("objective must be 'sign'")
    if isinstance(memory_mb, bool) or not isinstance(memory_mb, (int, float)) or not math.isfinite(memory_mb) or memory_mb <= 0:
        raise ValueError("memory_mb must be finite and positive")
    # Fixed interning chunks and SQLite require a small minimum workspace.
    if memory_mb * 1024**2 < 16384:
        raise ValueError("memory_mb must provide at least 16 KiB")
    return _json({"mode": mode, "coefficient": expected, "rhs": rhs, "objective": objective,
                  "multiplicity_ratio": str(ratio) if mode == "template" else None,
                  "equality": "dual_zero_rhs", "algorithm": "7.0"})


def number_class(value, policy="sign"):
    if value is None:
        return "infinite"
    if not value:
        return "zero"
    if policy == "nonzero":
        return "nonzero"
    return "positive" if value > 0 else "negative"


class RefinementStore:
    def __init__(self, owner, memory_mb,disk_mb=None):
        self.owner = owner
        self.temp = TemporaryDirectory(prefix="mipframe-refinement-")
        self.arrays = {}
        self.maps = []
        self.serial = 0
        self.role_cache = OrderedDict()
        self.value_cache = OrderedDict()
        self.intern_cache = OrderedDict()
        self.counts = {}
        self.family_stores = WeakSet()
        self.db = None
        self._interned = {}
        self._pending_artifacts = []
        self._pending_definitions = []
        from mipframe.data.runtime import WorkContext
        self.work=WorkContext(memory_mb,disk_mb)
        self._token_data = {}
        self._roles = {}
        self._role_values = {}
        try:
            self.db = sqlite3.connect(cache_directory(owner) / "refinement.sqlite")
            self.db.execute("PRAGMA temp_store=FILE")
            self.set_memory(memory_mb)
            expected = _json([owner.uid, owner.model.manifest["model_version"], VERSION])
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                old = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
                if old and old[0] != expected:
                    raise MIPFrameError("Refinement sidecar version/model mismatch; rebuild in a new artifact directory")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS configs(id INTEGER PRIMARY KEY,key TEXT UNIQUE,depth INTEGER);
                CREATE TABLE IF NOT EXISTS array_artifacts(config INTEGER,kind TEXT,depth INTEGER,path TEXT,dtype TEXT,n INTEGER,checksum TEXT,
                    PRIMARY KEY(config,kind,depth));
            """)
            old = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
            if not old:
                self.db.execute("INSERT INTO meta VALUES('source',?)", (expected,))
                self.db.commit()
            from mipframe.store.lifecycle import register
            register(owner, self)
            self.dtype = np.dtype("<i4" if max(2 * owner.model.capacity("row"), owner.model.capacity("var")) <= np.iinfo(np.int32).max else "<i8")
        except BaseException:
            self.close()
            raise

    def set_memory(self, memory_mb):
        self.memory = int(memory_mb * 1024**2)
        self.block = max(1, min(65536, self.memory // 128))
        if self.db is not None:
            kb = max(1, min(2048, self.memory // 4096))
            self.db.execute(f"PRAGMA cache_size=-{kb}")
            self.db.execute(f"PRAGMA temp.cache_size=-{kb}")
        # Numeric caches stay bounded even under small caller budgets.
        self.cache_limit = max(1, min(1024, self.memory // 4096))
        self.role_cache.clear()
        self.value_cache.clear()
        self.intern_cache.clear()

    def guard(self):
        self.owner.model
        if self.db is None:
            raise MIPFrameError("Refinement store is closed")

    def _close_connection(self):
        from mipframe.store.lifecycle import release_all, close_connection
        actions = [store.close for store in self.family_stores]
        actions.append(lambda: close_connection(self))
        actions.extend(array._mmap.close for array in self.maps if hasattr(array,'_mmap') and not array._mmap.closed and id(array) not in self.work._allocations)
        release_all(actions)
        self.maps.clear()
        if hasattr(self,'work'):self.work.close()
        self.arrays.clear()
        self.intern_cache.clear()

    def close(self):
        from mipframe.store.lifecycle import release_all, cleanup_directory
        temp, self.temp = self.temp, None
        release_all((self._close_connection, lambda: cleanup_directory(temp)))

    def _work(self, size, dtype=None):
        result=self.work.array(size,dtype or self.dtype)
        self.maps.append(result)
        return result

    def _write_array(self,config,kind,depth,array):
        from mipframe.store.artifacts import sha256
        from mipframe.data.runtime import DiskReservation
        directory=cache_directory(self.owner)/'derived'
        directory.mkdir(exist_ok=True)
        import uuid
        name=f'{config}-{kind}-{depth}-{uuid.uuid4().hex}.npy'
        path=directory/name
        staged=directory/(name+'.tmp')
        reservation=DiskReservation(self.work)
        try:
            reservation.add(array.nbytes+256)
            with staged.open('wb') as out:np.save(out,array,allow_pickle=False)
            staged.replace(path)
            self.db.execute('INSERT OR REPLACE INTO array_artifacts VALUES(?,?,?,?,?,?,?)',
                            (config,kind,depth,name,array.dtype.str,len(array),sha256(path,work=self.work)))
            self._pending_artifacts.append((path,reservation))
        except BaseException:
            staged.unlink(missing_ok=True);path.unlink(missing_ok=True);reservation.close()
            raise

    def _finish_artifacts(self,published):
        pending,self._pending_artifacts=self._pending_artifacts,[]
        for path,reservation in pending:
            if not published:path.unlink(missing_ok=True)
            reservation.close()

    def array(self,config,kind,depth):
        self.guard();key=config,kind,depth
        if key not in self.arrays:
            record=self.db.execute('SELECT path,dtype,n,checksum FROM array_artifacts WHERE config=? AND kind=? AND depth=?',key).fetchone()
            if record is None:raise MIPFrameError('Missing derived array artifact')
            name,dtype,n,checksum=record
            from mipframe.store.artifacts import sha256
            if Path(name).name!=name or sha256(cache_directory(self.owner)/'derived'/name,work=self.work)!=checksum:
                raise MIPFrameError('Invalid derived array checksum')
            result=np.load(cache_directory(self.owner)/'derived'/name,mmap_mode='r',allow_pickle=False)
            if result.dtype.str!=dtype or len(result)!=n:raise MIPFrameError('Invalid derived array dimensions')
            self.maps.append(result);self.arrays[key]=result
        return self.arrays[key]

    def _load_roles(self):
        if getattr(self,'_roles_loaded',False):return
        from mipframe.data.intern import BytePool
        self._role_pool=BytePool(self.work.fork());self._roles_loaded=True
        if self.db.execute("SELECT 1 FROM array_artifacts WHERE config=0 AND kind='roles_offsets' AND depth=0").fetchone():
            offsets=self.array(0,'roles_offsets',0);lengths=self.array(0,'roles_lengths',0);data=self.array(0,'roles_bytes',0)
            for start,length in zip(offsets,lengths):
                raw=data[int(start):int(start+length)].tobytes();self._role_pool.intern((raw,))

    def role(self,value):
        self._load_roles();ident=self._role_pool.intern((str(abs(value)).encode('ascii'),))+1
        return ident if value>0 else -ident

    def role_value(self,ident):
        self.guard();self._load_roles();key=abs(int(ident))
        if key not in self.role_cache:
            self.role_cache[key]=Fraction(self._role_pool.value(key-1).decode('ascii'))
            if len(self.role_cache)>self.cache_limit:self.role_cache.popitem(last=False)
        value=self.role_cache[key]
        return value if ident>0 else -value

    def _intern(self,config,kind,depth,header,tokens=()):
        from mipframe.data.intern import BytePool
        namespace=config,kind,depth
        if namespace not in self._interned:self._interned[namespace]=BytePool(self.work.fork())
        raw=(str(header) if type(header) is int else _json(header)).encode('utf-8')
        def chunks():
            yield struct.pack('>Q',len(raw));yield raw
            for a,b,n in tokens:yield TOKEN.pack(int(a),int(b),int(n))
        return self._interned[namespace].intern(chunks())

    def _flush_definitions(self):
        # One manifest per typed vector, never one SQL row per color.
        self._load_roles()
        from mipframe.data.buffers import Buffer
        for name,buffer in (('bytes',self._role_pool.data),('offsets',self._role_pool.offsets),('lengths',self._role_pool.lengths)):
            # Retain the mutable pool for subsequent rounds; publish a bounded copy.
            with self.work.fork() as scratch:
                out=Buffer(scratch,buffer.dtype)
                for start in range(0,len(buffer),self.block):out.extend(buffer[start:start+self.block])
                self._write_array(0,'roles_'+name,0,out.finish())
        for (config,kind,depth),pool in self._interned.items():
            for name,buffer in (('signature_bytes',pool.data),('signature_offsets',pool.offsets),('signature_lengths',pool.lengths)):
                array=buffer.finish();self._write_array(config,kind+'_'+name,depth,array)
            pool.work.close()
        self._interned.clear()

    def _signature_parts(self,config,kind,depth,color):
        key=config,kind,depth
        if key in self._interned:
            pool=self._interned[key]
            return pool.data,int(pool.offsets[color]),int(pool.lengths[color])
        offsets=self.array(config,kind+'_signature_offsets',depth)
        lengths=self.array(config,kind+'_signature_lengths',depth)
        return self.array(config,kind+'_signature_bytes',depth),int(offsets[color]),int(lengths[color])

    def _signature_header(self,config,kind,depth,color):
        data,start,_=self._signature_parts(config,kind,depth,color)
        length=struct.unpack('>Q',bytes(int(data[i]) for i in range(start,start+8)))[0]
        return json.loads(bytes(int(data[i]) for i in range(start+8,start+8+length)))

    def tokens(self,config,kind,depth,color):
        self.guard();data,start,length=self._signature_parts(config,kind,depth,int(color))
        header=struct.unpack('>Q',bytes(int(data[i]) for i in range(start,start+8)))[0]
        for pos in range(start+8+header,start+length,TOKEN.size):
            yield TOKEN.unpack(bytes(int(data[i]) for i in range(pos,pos+TOKEN.size)))

    def token_count(self,config,kind,depth,color):
        data,start,length=self._signature_parts(config,kind,depth,int(color))
        header=struct.unpack('>Q',bytes(int(data[i]) for i in range(start,start+8)))[0]
        return (length-8-header)//TOKEN.size

    def _aggregate(self,blocks,upper_bound):
        from mipframe.data.sorting import pair_counts
        if upper_bound<=8:
            yield from pair_counts(self.work,blocks,upper_bound)
            return
        with self.work.fork() as scratch:
            yield from pair_counts(scratch,blocks,upper_bound)

    def base(self,config,kind,ident):
        return self._signature_header(config,kind,0,int(self.array(config,kind,0)[ident]))

    def row_info(self,config,row):
        m=self.owner.model
        scale=self.role_value(self.array(config,'row_scale_role',-1)[row])
        _,_,lo_id,hi_id,_=m.rows[row]
        lo,hi=m.num(lo_id),m.num(hi_id)
        if scale<0:lo,hi=None if hi is None else -hi,None if lo is None else -lo
        lo=None if lo is None else lo/abs(scale)
        hi=None if hi is None else hi/abs(scale)
        return scale,lo,hi,lo==hi==0

    def _row_tokens(self, row, edge, old_vars, reverse=False):
        m = self.owner.model
        if hasattr(m,'f'):
            scale=self.role_value(self.array(self._active_config,'row_scale_role',-1)[row])
            template=self._active_template
            def logical():
                buffer=[]
                for v,c in m.iter_terms(row):
                    coefficient=c/scale
                    role=self.role(Fraction(1 if coefficient>0 else -1) if template else coefficient)
                    buffer.append((-role if reverse else role,int(old_vars[v])))
                    if len(buffer)>=self.block:
                        yield np.asarray(buffer,dtype=np.int64);buffer=[]
                if buffer:yield np.asarray(buffer,dtype=np.int64)
            return self._aggregate(logical(),m.degree('row',row))
        start, stop = int(m.row_ptr[row]), int(m.row_ptr[row+1])
        def blocks():
            for begin in range(start, stop, self.block):
                end = min(begin + self.block, stop)
                data = np.empty((end-begin, 2), dtype=np.int64)
                data[:, 0] = -edge[begin:end] if reverse else edge[begin:end]
                data[:, 1] = old_vars[m.col_idx[begin:end]]
                yield data
        return self._aggregate(blocks(), stop-start)

    def _var_tokens(self, var, edge, old_pos, old_neg):
        m = self.owner.model
        if hasattr(m,'f'):
            scales=self.array(self._active_config,'row_scale_role',-1)
            def logical():
                buffer=[]
                for row,c in m.iter_column(var):
                    coefficient=c/self.role_value(scales[row])
                    role=self.role(Fraction(1 if coefficient>0 else -1) if self._active_template else coefficient)
                    buffer.append((role,int(old_pos[row])))
                    if old_neg[row]>=0:buffer.append((-role,int(old_neg[row])))
                    if len(buffer)>=self.block:
                        yield np.asarray(buffer,dtype=np.int64);buffer=[]
                if buffer:yield np.asarray(buffer,dtype=np.int64)
            return self._aggregate(logical(),2*m.degree('var',var))
        start, stop = int(m.col_ptr[var]), int(m.col_ptr[var+1])
        def blocks():
            for begin in range(start, stop, self.block):
                positions = m.col_positions[begin:min(begin+self.block, stop)]
                rows = m.data.arrays["csc_row_ids"][begin:min(begin+self.block, stop)]
                data = np.empty((len(rows), 2), dtype=np.int64)
                data[:, 0], data[:, 1] = edge[positions], old_pos[rows]
                yield data
                dual = old_neg[rows] >= 0
                if np.any(dual):
                    data = np.empty((int(np.count_nonzero(dual)), 2), dtype=np.int64)
                    data[:, 0], data[:, 1] = -edge[positions[dual]], old_neg[rows[dual]]
                    yield data
        return self._aggregate(blocks(), 2*(stop-start))

    def _prepare(self, config, policy):
        m = self.owner.model
        template = json.loads(self.db.execute("SELECT key FROM configs WHERE id=?", (config,)).fetchone()[0]).get("mode") == "template"
        nr, nv = m.capacity("row"), m.capacity("var")
        arrays = {kind: self._work(nv if kind == "var" else nr) for kind in ("row", "var", "pos", "neg")}
        edge = self._work(0 if hasattr(m,"f") else m.nnz, np.dtype("<i8"))
        scales = self._work(nr,np.dtype("<i8"))
        scales[:] = 0
        for array in arrays.values():array[:]=-1
        arrays["neg"][:] = -1

        for row, _, lo_id, hi_id, _ in m.rows:
            lo, hi = m.num(lo_id), m.num(hi_id)
            relation = _sense(lo, hi)
            scale = 0
            if hasattr(m,'f'):
                for _,value in m.iter_terms(row):scale=max(scale,abs(value))
            else:
                start, stop = int(m.row_ptr[row]), int(m.row_ptr[row+1])
                if stop-start<=4:
                    for ident in m.coeff_id[start:stop]:scale=max(scale,abs(m.num(int(ident))))
                else:
                    for begin in range(start, stop, self.block):
                        for ident in np.unique(m.coeff_id[begin:min(begin+self.block, stop)]):
                            scale = max(scale, abs(m.num(int(ident))))
            scale = scale or 1
            if relation == ">=":
                scale = -scale
                lo, hi, relation = None, -lo, "<="
            elif relation == "=" and hi < 0:
                scale = -scale
                lo, hi = -hi, -lo
            # Bounds above already have their direction transformed; divide by |scale|.
            # Preserve exact quotients, but avoid constructing Fraction(1)
            # and Fraction(0) for each incidence of unit-scaled rows.
            if abs(scale)!=1:
                lo = None if lo is None else Fraction(lo) / abs(scale)
                hi = None if hi is None else Fraction(hi) / abs(scale)
            rhs_class = [number_class(lo, policy), number_class(hi, policy)] if relation in ("range", "free") else number_class(hi, policy)
            base = [relation, rhs_class]
            dual = relation == "=" and hi == 0

            arrays["row"][row] = self._intern(config, "row", 0, base)
            state = self._intern(config, "state", 0, base)
            arrays["pos"][row] = state
            if dual:
                arrays["neg"][row] = state
            scales[row]=self.role(scale)
            def label_value(value):
                if template:return self.role(1 if (value>0)==(scale>0) else -1)
                value=value if scale==1 else -value if scale==-1 else Fraction(value)/scale
                return self.role(value)
            if hasattr(m,'f'):
                for _,value in m.iter_terms(row):label_value(value)
            else:
                if stop-start<=4:
                    for pos in range(start,stop):edge[pos]=label_value(m.num(int(m.coeff_id[pos])))
                else:
                    for begin in range(start, stop, self.block):
                        end = min(begin+self.block, stop)
                        ids, inverse = np.unique(m.coeff_id[begin:end], return_inverse=True)
                        labels = np.array([label_value(m.num(int(ident))) for ident in ids], dtype=np.int64)
                        edge[begin:end] = labels[inverse]
        for var, _, kind, lo, hi, obj, _ in m.variables:
            base = [kind, bound_class(m.num(lo), m.num(hi)), number_class(m.num(obj))]

            arrays["var"][var] = self._intern(config, "var", 0, base)
        self._write_array(config, "edge", -1, edge)
        self._write_array(config,"row_scale_role",-1,scales)
        for kind, array in arrays.items():
            self._write_array(config, kind, 0, array)
        self.db.execute("UPDATE configs SET depth=0 WHERE id=?", (config,))
        return {("edge", -1): edge, ("row_scale_role",-1):scales, **{(kind, 0): array for kind, array in arrays.items()}}

    def _round(self, config, depth):
        settings = json.loads(self.db.execute("SELECT key FROM configs WHERE id=?", (config,)).fetchone()[0])
        self._active_config=config
        self._active_template=settings.get("mode")=="template"
        if settings.get("mode") == "template":
            return self._template_round(config, depth, Fraction(settings["multiplicity_ratio"]))
        m = self.owner.model
        edge = self.array(config, "edge", -1)
        old_row, old_var, old_pos, old_neg = (self.array(config, kind, depth-1) for kind in ("row", "var", "pos", "neg"))
        arrays = {kind: self._work(m.capacity("var") if kind == "var" else m.capacity("row")) for kind in ("row", "var", "pos", "neg")}
        for array in arrays.values():array[:]=-1
        for row in m.entity_ids("row"):
            pos = self._intern(config, "state", depth, int(old_pos[row]), self._row_tokens(row, edge, old_var))
            arrays["pos"][row] = pos
            states = [pos]
            if old_neg[row] >= 0:
                neg = self._intern(config, "state", depth, int(old_neg[row]), self._row_tokens(row, edge, old_var, True))
                arrays["neg"][row] = neg
                states.append(neg)
            arrays["row"][row] = self._intern(config, "row", depth, [int(old_row[row]), sorted(states)])
        for var in m.entity_ids("var"):
            arrays["var"][var] = self._intern(config, "var", depth, int(old_var[var]), self._var_tokens(var, edge, old_pos, old_neg))
        for kind, array in arrays.items():
            self._write_array(config, kind, depth, array)
        self.db.execute("UPDATE configs SET depth=? WHERE id=?", (depth, config))
        return {(kind, depth): array for kind, array in arrays.items()}

    def _template_partition(self,config,kind,depth,nodes,size,ratio):
        from mipframe.data.buffers import Buffer
        from mipframe.data.sorting import lexorder
        with self.work.fork() as scratch:
            nodes_buf=Buffer(scratch,'<i8');coarse_buf=Buffer(scratch,'<i8');offsets=Buffer(scratch,'<u8');offsets.append(0)
            aa=Buffer(scratch,'<i8');bb=Buffer(scratch,'<i8');nn=Buffer(scratch,'<i8')
            for ident,old,tokens in nodes:
                start=len(aa)
                for a,b,n in tokens:aa.append(a);bb.append(b);nn.append(n)
                coarse=self._intern(config,kind+'_coarse',depth,int(old),((aa[j],bb[j],1) for j in range(start,len(aa))))
                nodes_buf.append(ident);coarse_buf.append(coarse);offsets.append(len(aa))
            node=nodes_buf.finish();coarse=coarse_buf.finish();off=offsets.finish();a=aa.finish();b=bb.finish();n=nn.finish()
            token_color=scratch.array(len(a),'<i8')
            for i in range(len(node)):token_color[int(off[i]):int(off[i+1])]=coarse[i]
            order=lexorder(scratch,(token_color,a,b,n));modes=scratch.array(len(a),'<i8')
            role=None;previous=None;mode=0
            for j in order:
                key=(int(token_color[j]),int(a[j]),int(b[j]));value=int(n[j])
                if key!=role:role=key;previous=None;mode=0
                if previous is None or value*ratio.denominator>previous*ratio.numerator:mode+=1
                previous=value;modes[j]=mode
            scratch.release(order)
            nodeorder=lexorder(scratch,(node,));result=self._work(size);result[:]=-1
            for i in nodeorder:
                tokens=((int(a[j]),int(b[j]),int(modes[j])) for j in range(int(off[i]),int(off[i+1])))
                color=self._intern(config,kind,depth,int(coarse[i]),tokens)
                result[node[i]]=color;token_color[int(off[i]):int(off[i+1])]=color
            order=lexorder(scratch,(token_color,a,b))
            dtype=np.dtype([(name,'<i8') for name in ('color','a','b','lo','hi')]);out=Buffer(scratch,dtype)
            key=None;lo=hi=0
            for j in order:
                current=(int(token_color[j]),int(a[j]),int(b[j]));value=int(n[j])
                if current!=key:
                    if key is not None:out.append((*key,lo,hi))
                    key=current;lo=hi=value
                else:lo=min(lo,value);hi=max(hi,value)
            if key is not None:out.append((*key,lo,hi))
            self._write_array(config,kind+'_ranges',depth,out.finish())
        return result

    def multiplicity_range(self, config, kind, depth, color, a, b):
        self.guard()
        values=self.array(config,kind+'_ranges',depth)
        key=(int(color),int(a),int(b));lo,hi=0,len(values)
        while lo<hi:
            mid=(lo+hi)//2;record=values[mid]
            if (int(record['color']),int(record['a']),int(record['b']))<key:lo=mid+1
            else:hi=mid
        if lo==len(values):return None
        record=values[lo]
        if (int(record['color']),int(record['a']),int(record['b']))!=key:return None
        return int(record['lo']),int(record['hi'])

    def _template_round(self, config, depth, ratio):
        m = self.owner.model
        edge = self.array(config, "edge", -1)
        old_row, old_var, old_pos, old_neg = (self.array(config, kind, depth-1)
                                             for kind in ("row", "var", "pos", "neg"))
        def states():
            for row in m.entity_ids("row"):
                yield 2*row, old_pos[row], self._row_tokens(row, edge, old_var)
                if old_neg[row] >= 0:
                    yield 2*row+1, old_neg[row], self._row_tokens(row, edge, old_var, True)
        start = len(self.maps)
        state = self._template_partition(config, "state", depth, states(), 2*m.capacity("row"), ratio)
        arrays = {kind: self._work(m.capacity("row")) for kind in ("row", "pos", "neg")}
        arrays["pos"][:] = state[::2]
        arrays["neg"][:] = state[1::2]
        # The intermediate state buffer is not a published color array.
        if len(self.maps) > start and self.maps[start] is state:
            self.work.release(state)
            self.maps.pop(start)
        for row in m.entity_ids("row"):
            colors = [int(arrays["pos"][row])]
            if arrays["neg"][row] >= 0:
                colors.append(int(arrays["neg"][row]))
            arrays["row"][row] = self._intern(config, "row", depth, [int(old_row[row]), sorted(colors)])
        nodes = ((var, old_var[var], self._var_tokens(var, edge, old_pos, old_neg))
                 for var in m.entity_ids("var"))
        arrays["var"] = self._template_partition(config, "var", depth, nodes, m.capacity("var"), ratio)
        for kind, array in arrays.items():
            self._write_array(config, kind, depth, array)
        self.db.execute("UPDATE configs SET depth=? WHERE id=?", (depth, config))
        return {(kind, depth): array for kind, array in arrays.items()}

    def _publish(self, config, arrays):
        for (kind, depth), array in arrays.items():
            array.flags.writeable = False
            self.arrays[config, kind, depth] = array

    def _discard_work(self, start):
        """Release failed-round files while retaining newly loaded prior checkpoints."""
        retained = {id(array) for array in self.arrays.values()}
        keep = self.maps[:start]
        for array in self.maps[start:]:
            if id(array) in retained:
                keep.append(array)
            else:
                if id(array) in self.work._allocations:self.work.release(array)
                elif hasattr(array,'_mmap'):array._mmap.close()
        self.maps = keep

    def compute(self, key, depth):
        self.guard()
        row = self.db.execute("SELECT id,depth FROM configs WHERE key=?", (key,)).fetchone()
        if row is None:
            start = len(self.maps)
            try:
                self.db.execute("BEGIN")
                config = self.db.execute("INSERT INTO configs(key,depth) VALUES(?,-1)", (key,)).lastrowid
                arrays = self._prepare(config, json.loads(key)["rhs"])
                self._flush_definitions()
                self.db.commit()
                self._finish_artifacts(True)
            except BaseException:
                self.db.rollback()
                self._finish_artifacts(False)
                self._interned.clear();self._pending_definitions.clear();self._token_data.clear()
                self.counts.clear()
                self.role_cache.clear()
                self.intern_cache.clear()
                self._discard_work(start)
                raise
            self._publish(config, arrays)
            current = 0
        else:
            config, current = row
        for layer in range(current+1, depth+1):
            start = len(self.maps)
            try:
                self.db.execute("BEGIN")
                arrays = self._round(config, layer)
                self._flush_definitions()
                self.db.commit()
                self._finish_artifacts(True)
            except BaseException:
                self.db.rollback()
                self._finish_artifacts(False)
                self._interned.clear();self._pending_definitions.clear();self._token_data.clear()
                self.counts.clear()
                self.intern_cache.clear()
                self._discard_work(start)
                raise
            self._publish(config, arrays)
        return config
