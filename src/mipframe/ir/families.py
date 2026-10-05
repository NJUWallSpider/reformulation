"""Exact signature partitions with shared typed membership arrays."""
from collections.abc import Sequence
import numpy as np
from mipframe.ir.algebraic import signature_method, signature_decoder


class Partition:
    def _build(self, ids, colors, identities):
        from mipframe.data.runtime import WorkContext
        from mipframe.data.relations import Relation
        from mipframe.data.buffers import Buffer
        self.work=getattr(self,'work',None) or WorkContext()
        owned=[]
        def column(values):
            if isinstance(values,np.ndarray):return values
            buffer=values if isinstance(values,Buffer) else Buffer(self.work,'<i8')
            if buffer is not values:
                for value in values:buffer.append(value)
            result=buffer.finish();owned.append((buffer.work,result));return result
        id_column,color_column=column(ids),column(colors)
        table=Relation({'id':id_column,'color':color_column},min(len(id_column),len(color_column)),self.work)
        parts={n:Buffer(self.work,'<u8') for n in ('colors','counts','first','offsets','members','minima')}
        parts['offsets'].append(0)
        for (color,),group in table.groups(('color',)):
            parts['colors'].append(color);parts['counts'].append(len(group));parts['first'].append(table.columns['id'][int(group[0])])
            minimum=None
            block=max(1,min(self.work.memory//128,self.work.available//64))
            with self.work.reserve(block*32):
                for start in range(0,len(group),block):
                    values=id_column[group[start:start+block]]
                    parts['members'].extend(values)
                    value=int(np.min(values));minimum=value if minimum is None else min(minimum,value)
            parts['minima'].append(minimum);parts['offsets'].append(len(parts['members']))
        for n,b in parts.items():setattr(self,n,b.finish())
        for owner,value in owned:owner.release(value)
        order=Relation({'count':self.counts,'minimum':self.minima},len(self.counts),self.work)
        self.order=self.work.seal(order.order(('count','minimum'),(True,False)))
        self.positions=self.work.array(len(self.colors),'<u8')
        for position,i in enumerate(self.order):self.positions[int(i)]=position
        self.positions=self.work.seal(self.positions)
        self.identities=identities;self.total=len(self.colors);self.closed=False

    def _color_index(self,color):
        i=int(np.searchsorted(self.colors,color))
        if i==len(self.colors) or self.colors[i]!=color:raise KeyError(color)
        return i

    def guard(self):
        self.owner.model
        if self.closed:
            from mipframe.store.numbers import MIPFrameError
            raise MIPFrameError('Partition is closed')

    def entries(self):
        self.guard()
        for position,i in enumerate(self.order):
            self.guard()
            color = int(self.colors[i])
            yield color+1, position, int(self.counts[i]), int(self.first[i]), self.identities[color]

    def find(self, field, value):
        self.guard()
        i = int(self.order[value]) if field=='position' else self._color_index(int(value))
        color = int(self.colors[i])
        return color+1, int(self.positions[i]), int(self.counts[i]), int(self.first[i]), self.identities[color]

    def member_ids(self,fid):
        self.guard()
        i=self._color_index(int(fid)-1)
        return self.members[int(self.offsets[i]):int(self.offsets[i+1])]

    def close(self):
        self.closed=True
        if hasattr(self,'work'):self.work.close()

    _close_connection=close


class _FamilyStore(Partition):
    def __init__(self, rows, signature):
        method=signature_method(signature)
        self._decode=signature_decoder(signature)
        self.owner=rows._owner
        from mipframe.data.runtime import WorkContext
        from mipframe.data.intern import BytePool
        from mipframe.data.buffers import Buffer
        from mipframe.data.pools import TextValues
        self.work=WorkContext();keys=BytePool(self.work);ids=Buffer(self.work,'<u8');colors=Buffer(self.work,'<u8')
        for ident in rows.ids:
            key=method(self.owner,ident).key()
            color=keys.intern((key.encode('utf8'),))
            ids.append(ident);colors.append(color)
        self._build(ids,colors,TextValues(keys))
        from mipframe.store.lifecycle import register
        register(self.owner,self)

    def record(self,position):
        fid,_,count,_,key=self.find('position',position)
        return fid,self._decode(key),count


class _MemberIndex(Sequence):
    unique=True

    def __init__(self,store,fid,ordinals):
        self.store,self.fid,self.ordinals=store,fid,ordinals

    def __len__(self):
        self.store.guard()
        return len(self.ordinals)

    def __getitem__(self,position):
        self.store.guard()
        if isinstance(position,slice):
            return type(self)(self.store,self.fid,self.ordinals[position])
        return int(self.store.member_ids(self.fid)[self.ordinals[position]])

    def __iter__(self):
        ids=self.store.member_ids(self.fid)
        for ordinal in self.ordinals:
            self.store.guard()
            yield int(ids[ordinal])

    def __contains__(self,ident):
        return any(i==ident for i in self)
