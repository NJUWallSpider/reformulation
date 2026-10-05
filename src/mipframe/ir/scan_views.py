"""Exact scan directories over typed partitions and sparse array traversals."""
from mipframe.store.scan import name_tokens,sense
from mipframe.frame.base import PlanIDs
from mipframe.frame.entity import RowFrame,VarFrame
from mipframe.frame.schema import DType,ref_type
from mipframe.frame.expr import col
from mipframe.ir.families import Partition


class Directory:
    def __init__(self,owner,kind,memory_mb):
        from mipframe.data.runtime import WorkContext
        self.work=WorkContext(memory_mb)
        self.owner,self.kind=owner,kind
        self.axis='var' if kind=='names_var' else 'row'
        self.partition=Partition();self.partition.owner=owner;self.partition.work=self.work
        try:
            self._build()
        except BaseException:
            self.partition.close()
            raise

    def key(self,gid):return f'scan:{self.owner.uid}:{self.owner.version}:{self.kind}:{gid}'

    def _build(self):
        with self.work.fork() as scratch:self._build_columns(scratch)

    def _build_columns(self,workspace):
        from mipframe.data.buffers import Buffer
        from mipframe.data.intern import BytePool
        from mipframe.data.relations import RelationBuilder,build
        import struct
        m=self.owner.model
        entities=m.variables if self.axis=='var' else m.rows
        keys=BytePool(workspace);ids=Buffer(workspace,'<u8');colors=Buffer(workspace,'<u8')
        meta_schema=(dict(sense='str',nnz='int',lb='number',ub='number') if self.kind=='row_shapes'
                     else dict(template='str',slot_count='int'))
        metadata=RelationBuilder(self.work,meta_schema)
        coefficients=RelationBuilder(self.work,dict(color='int',coefficient='number',count='int'))
        slots=RelationBuilder(workspace,dict(color='int',slot='int',value='number'))
        for row in entities:
            ident,name=row[:2]
            if self.kind=='row_shapes':
                lo,hi=m.num(row[2]),m.num(row[3]);rel=sense(lo,hi)
                with self.work.fork() as scratch:
                    terms=build(scratch,{'coefficient':'number'},({'coefficient':c} for _,c in m.iter_terms(ident)))
                    def chunks():
                        header=str((rel,len(terms),lo,hi)).encode('utf8')
                        yield struct.pack('<Q',len(header));yield header
                        for (value,),positions in terms.groups(('coefficient',)):
                            raw=str(value).encode('ascii')
                            yield struct.pack('<QQ',len(raw),len(positions));yield raw
                    color=keys.intern(chunks())
                    if color==metadata.count:
                        metadata.append(dict(sense=rel,nnz=len(terms),lb=lo,ub=hi))
                        for (value,),positions in terms.groups(('coefficient',)):
                            coefficients.append(dict(color=color,coefficient=value,count=len(positions)))
            else:
                template,values=name_tokens(name);color=keys.intern((template.encode('utf8'),))
                if color==metadata.count:metadata.append(dict(template=template,slot_count=len(values)))
                for i,value in enumerate(values):slots.append(dict(color=color,slot=i,value=value))
            ids.append(ident);colors.append(color)
        self.metadata=metadata.finish();self.coefficients=coefficients.finish()
        self.coefficient_order=self.coefficients.order(('color',))
        slot_table=slots.finish()
        with self.work.fork() as scratch:
            unique=build(scratch,dict(color='int',slot='int',value='number'),
                (dict(zip(('color','slot','value'),key)) for key,_ in slot_table.groups(('color','slot','value'))))
            def summaries():
                for (color,slot),positions in unique.groups(('color','slot')):
                    values=unique.columns['value']
                    yield dict(color=color,slot=slot,minimum=min(values[i] for i in positions),
                               maximum=max(values[i] for i in positions),distinct=len(positions))
            self.slots=build(self.work,dict(color='int',slot='int',minimum='number',maximum='number',distinct='int'),summaries())
        self._slot_order=range(len(self.slots))
        self.partition._build(ids,colors,range(len(keys)))

    def directory(self):
        from mipframe.analysis import _frame
        schema=dict(id='str',group_key='str',count='int')
        if self.kind=='row_shapes':schema.update(sense='str',nnz='int',lb=DType('number',True),ub=DType('number',True))
        else:schema.update(template='str',slot_count='int')
        def scan(fields,ctx):
            for fid,_,n,_,color in self.partition.entries():
                raw=dict(self.metadata.row(color),id=f'{"S" if self.kind=="row_shapes" else "N"}{color}',group_key=self.key(fid),count=n)
                yield {k:raw[k] for k in fields}
        frame=_frame(self.owner,schema,scan,key=('group_key',))
        frame._query.navigation=self,{'group_key':'group_key'}
        frame._query.spec=dict(kind='scan_directory',uid=self.owner.uid,version=self.owner.version,directory=self.kind)
        return frame

    def related(self,view,name,keys):
        from mipframe.analysis import _frame
        if name in ('rows','vars'):
            if name!=self.axis+'s':raise KeyError(name)
            member=self.related(view,'members',keys).select(self.axis).distinct()
            return (RowFrame if self.axis=='row' else VarFrame)(self.owner,PlanIDs(member,self.axis))
        schemas={'members':dict(group_key='str',**{self.axis:ref_type(self.owner,self.axis)}),
                 'coefficients':dict(group_key='str',coefficient='number',count='int'),
                 'slots':dict(group_key='str',slot='int',minimum='number',maximum='number',distinct='int')}
        if name not in schemas or (name=='coefficients')!=(self.kind=='row_shapes') and name!='members':raise KeyError(name)
        selected=view.select(group_key=col(keys['group_key'])).distinct()
        def scan(fields,ctx):
            for color in range(len(self.metadata)):
                metadata=self.metadata.row(color)
                group_key=self.key(color+1)
                if not ctx.contains(selected,group_key):continue
                if name=='members':
                    records=({self.axis:int(i)} for i in self.partition.member_ids(color+1))
                elif name=='coefficients':
                    from mipframe.data.relations import search_range
                    lo,hi=search_range(self.coefficients,self.coefficient_order,('color',),(color,))
                    records=self.coefficients.records(('coefficient','count'),self.coefficient_order[lo:hi])
                else:
                    from mipframe.data.relations import search_range
                    lo,hi=search_range(self.slots,self._slot_order,('color',),(color,))
                    records=self.slots.records(('slot','minimum','maximum','distinct'),range(lo,hi))
                for raw in records:
                    raw['group_key']=group_key
                    yield {k:raw[k] for k in fields}
        frame=_frame(self.owner,schemas[name],scan)
        frame._query.navigation=self,{'group_key':'group_key'}
        frame._query.spec=dict(kind='scan_relation',source=view,name=name)
        return frame
