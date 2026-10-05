"""Columnar logical check IR; numbers are interned once, never floats."""
from array import array
from collections.abc import Sequence
from mipframe.check.kernel import exact


class Numbers:
    def __init__(self):
        self.values=[];self.ids={}

    def intern(self,value):
        value=exact(value)
        found=self.ids.get(value)
        if found is None:
            found=len(self.values);self.ids[value]=found;self.values.append(value)
        return found

    def __getitem__(self,index):return self.values[index]


class ExpressionTable(Sequence):
    def __init__(self,numbers):
        self.numbers=numbers
        self.kind=array('B');self.offsets=array('Q',[0]);self.items=array('Q');self.weights=array('Q')

    def key(self,node):
        kind,data=node
        return kind,tuple((i,self.numbers.intern(c)) for i,c in data)

    def append_key(self,key):
        kind,data=key
        self.kind.append(kind=='sum')
        for i,c in data:self.items.append(i);self.weights.append(c)
        self.offsets.append(len(self.items))

    def append(self,node):self.append_key(self.key(node))
    def __len__(self):return len(self.kind)
    def __getitem__(self,index):
        if isinstance(index,slice):return tuple(self[i] for i in range(*index.indices(len(self))))
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        lo,hi=self.offsets[index:index+2]
        return ('sum' if self.kind[index] else 'terms',tuple((int(self.items[j]),self.numbers[self.weights[j]]) for j in range(lo,hi)))


_RULES=('linear','identity','cases','round','equality','enumerate','bound','affine_integrality','one_hot_exclusion')
_RELATIONS=('<=','>=','==')
_SOURCES=('task','external','model','assumption')
_SIDES=('lb','ub','eq')


class TaskTable(Sequence):
    def __init__(self,numbers):
        self.numbers=numbers
        self.rule=array('B');self.target=array('Q');self.rhs=array('Q');self.relation=array('B')
        self.source_offsets=array('Q',[0]);self.source_kind=array('B')
        self.source0=array('Q');self.source1=array('Q');self.source2=array('Q');self.weight=array('Q')
        self.condition_offsets=array('Q',[0]);self.condition_expr=array('Q');self.condition_rhs=array('Q');self.condition_rel=array('B')
        self.config=[]

    def append(self,record):
        rule,target,sources,assumptions,config=record
        self.rule.append(_RULES.index(rule));self.target.append(target[0])
        self.rhs.append(self.numbers.intern(target[1]));self.relation.append(_RELATIONS.index(target[2]))
        weighted=rule in ('linear','identity','enumerate')
        for source in sources:
            kind=source[0];self.source_kind.append(_SOURCES.index(kind))
            if kind=='task':a,b,c=source[1],0,0
            elif kind=='external':a,b,c=source[1],source[2],0
            elif kind=='model':a,b,c=('row','var').index(source[1]),source[2],_SIDES.index(source[3])
            else:
                expr,rhs,relation=source[1]
                a,b,c=expr,self.numbers.intern(rhs),_RELATIONS.index(relation)
            self.source0.append(a);self.source1.append(b);self.source2.append(c)
            self.weight.append(self.numbers.intern(source[-1] if weighted else 1))
        self.source_offsets.append(len(self.source_kind))
        for expr,rhs,relation in assumptions:
            self.condition_expr.append(expr);self.condition_rhs.append(self.numbers.intern(rhs));self.condition_rel.append(_RELATIONS.index(relation))
        self.condition_offsets.append(len(self.condition_expr))
        if rule=='cases':config=config[0],tuple(self.numbers.intern(v) for v in config[1])
        self.config.append(config)

    def claim_at(self,index):
        return int(self.target[index]),self.numbers[self.rhs[index]],_RELATIONS[self.relation[index]]

    def rule_at(self,index):return _RULES[self.rule[index]]

    def references(self,index):
        lo,hi=self.source_offsets[index:index+2]
        edges=[];external=[]
        for j in range(lo,hi):
            kind=self.source_kind[j]
            if kind==0:edges.append(int(self.source0[j]))
            elif kind==1:external.append((int(self.source0[j]),int(self.source1[j])))
        return tuple(edges),tuple(external)

    def __len__(self):return len(self.rule)
    def __getitem__(self,index):
        if isinstance(index,slice):return tuple(self[i] for i in range(*index.indices(len(self))))
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        rule=_RULES[self.rule[index]]
        target=int(self.target[index]),self.numbers[self.rhs[index]],_RELATIONS[self.relation[index]]
        weighted=rule in ('linear','identity','enumerate');sources=[]
        lo,hi=self.source_offsets[index:index+2]
        for j in range(lo,hi):
            kind=_SOURCES[self.source_kind[j]];a,b,c=int(self.source0[j]),int(self.source1[j]),int(self.source2[j])
            if kind=='task':source=(kind,a)
            elif kind=='external':source=(kind,a,b)
            elif kind=='model':source=(kind,('row','var')[a],b,_SIDES[c])
            else:source=(kind,(a,self.numbers[b],_RELATIONS[c]))
            if weighted:source=(*source,self.numbers[self.weight[j]])
            sources.append(source)
        lo,hi=self.condition_offsets[index:index+2]
        assumptions=tuple((int(self.condition_expr[j]),self.numbers[self.condition_rhs[j]],_RELATIONS[self.condition_rel[j]]) for j in range(lo,hi))
        config=self.config[index]
        if rule=='cases':config=config[0],tuple(self.numbers[v] for v in config[1])
        return rule,target,tuple(sources),assumptions,config


def native_table(table, encode):
    """Artifact codec: a few typed columns instead of per-task JSON objects."""
    import numpy as np
    import json
    from mipframe.data.columns import ExactColumn
    arrays={name:encode(np.frombuffer(value,dtype=np.dtype(value.typecode)))
            for name,value in vars(table).items() if isinstance(value,array)}
    result=dict(version=1,columns=arrays,numbers=encode(ExactColumn(table.numbers.values)))
    if isinstance(table,TaskTable):
        result['config']=encode(tuple(json.dumps(c,separators=(',',':')) for c in table.config))
    return result


def restore_table(kind, record, decode):
    import numpy as np
    import json
    expected={'version','columns','numbers'} | ({'config'} if kind=='tasks' else set())
    if set(record)!=expected or record['version']!=1:raise ValueError('Invalid check IR artifact')
    numbers=Numbers()
    for index,value in enumerate(decode(record['numbers'])):
        if numbers.intern(value)!=index:raise ValueError('Duplicate check IR numeric pool entry')
    table=TaskTable(numbers) if kind=='tasks' else ExpressionTable(numbers)
    expected_columns={name for name,value in vars(table).items() if isinstance(value,array)}
    if set(record['columns'])!=expected_columns:raise ValueError('Invalid check IR columns')
    for name,value in record['columns'].items():
        value=decode(value)
        if not isinstance(value,np.ndarray) or value.ndim!=1 or value.dtype.kind not in 'iu':
            raise ValueError('Check IR requires integer vectors')
        if np.any(value<0):raise ValueError('Negative check IR index')
        setattr(table,name,value)
    def offsets(values,n,size):
        if len(values)!=n+1 or values[0]!=0 or values[-1]!=size or np.any(values[1:]<values[:-1]):
            raise ValueError('Invalid check IR offsets')
    def indices(values,size):
        if np.any(values>=size):raise ValueError('Invalid check IR index')
    if kind=='tasks':
        n=len(table.rule)
        if any(len(getattr(table,k))!=n for k in ('target','rhs','relation')):raise ValueError('Invalid task columns')
        indices(table.rule,len(_RULES));indices(table.relation,len(_RELATIONS));indices(table.rhs,len(numbers.values))
        size=len(table.source_kind)
        if any(len(getattr(table,k))!=size for k in ('source0','source1','source2','weight')):raise ValueError('Invalid source columns')
        offsets(table.source_offsets,n,size);indices(table.source_kind,len(_SOURCES));indices(table.weight,len(numbers.values))
        model=table.source_kind==2;assumption=table.source_kind==3
        indices(table.source0[model],2);indices(table.source2[model],len(_SIDES))
        indices(table.source1[assumption],len(numbers.values));indices(table.source2[assumption],len(_RELATIONS))
        size=len(table.condition_expr)
        if len(table.condition_rhs)!=size or len(table.condition_rel)!=size:raise ValueError('Invalid condition columns')
        offsets(table.condition_offsets,n,size);indices(table.condition_rhs,len(numbers.values));indices(table.condition_rel,len(_RELATIONS))
        def immutable(value):return tuple(immutable(v) for v in value) if isinstance(value,list) else value
        table.config=[immutable(json.loads(value)) for value in decode(record['config'])]
        if len(table.config)!=n:raise ValueError('Invalid task configuration count')
    else:
        if len(table.items)!=len(table.weights):raise ValueError('Invalid expression columns')
        indices(table.kind,2);indices(table.weights,len(numbers.values))
        offsets(table.offsets,len(table),len(table.items))
    return table


def validated_copy(expressions, tasks, facts, budget):
    """Copy and validate typed logical columns without expanding task records.

    Saved verdicts and compiled instructions are absent from this representation.
    Rule-specific mathematical and scope validation still runs in the executor.
    """
    import numpy as np
    numbers=Numbers()
    for i,value in enumerate(expressions.numbers.values):
        if numbers.intern(value)!=i:raise ValueError('Duplicate numeric pool entry')
    if tasks.numbers.values!=expressions.numbers.values:
        raise ValueError('Expression and task number pools differ')
    def clone(source, cls):
        target=cls(numbers)
        for name,value in vars(target).items():
            if isinstance(value,array):
                raw=getattr(source,name)
                values=np.asarray(raw)
                if values.ndim!=1 or values.dtype.kind not in 'iu' or np.any(values<0):
                    raise ValueError('Invalid logical integer column')
                setattr(target,name,array(value.typecode,raw))
        return target
    e,t=clone(expressions,ExpressionTable),clone(tasks,TaskTable)
    t.config=list(tasks.config)
    def ix(values,size):
        if np.any(np.asarray(values)>=size):raise ValueError('Logical index out of range')
    def offsets(values,n,size):
        a=np.asarray(values)
        if len(a)!=n+1 or a[0]!=0 or a[-1]!=size or np.any(a[1:]<a[:-1]):raise ValueError('Invalid logical offsets')
    if len(e.items)!=len(e.weights):raise ValueError('Invalid expression columns')
    offsets(e.offsets,len(e),len(e.items));ix(e.kind,2);ix(e.weights,len(numbers.values))
    owners=np.repeat(np.arange(len(e),dtype=np.uint64),np.diff(np.asarray(e.offsets)).astype(np.int64))
    items=np.asarray(e.items);kinds=np.asarray(e.kind)[owners]
    if np.any(items[kinds==1]>=owners[kinds==1]):raise ValueError('Cyclic expression graph')
    if len(items)>1 and np.any((owners[1:]==owners[:-1]) & (items[1:]<=items[:-1])):
        raise ValueError('Expression entries must be canonical')
    if any(not numbers[i] for i in set(e.weights)):raise ValueError('Zero expression entry')
    for v in np.unique(items[kinds==0]):facts.validate('var',int(v))
    if not len(e) or e[0]!=('terms',()):raise ValueError('Invalid zero expression')
    n=len(t)
    if any(len(getattr(t,k))!=n for k in ('target','rhs','relation','config')):raise ValueError('Invalid task columns')
    ix(t.rule,len(_RULES));ix(t.target,len(e));ix(t.rhs,len(numbers.values));ix(t.relation,len(_RELATIONS))
    count=len(t.source_kind)
    if any(len(getattr(t,k))!=count for k in ('source0','source1','source2','weight')):raise ValueError('Invalid source columns')
    offsets(t.source_offsets,n,count);ix(t.source_kind,len(_SOURCES));ix(t.weight,len(numbers.values))
    k=np.asarray(t.source_kind);a=np.asarray(t.source0);b=np.asarray(t.source1);c=np.asarray(t.source2)
    source_owners=np.repeat(np.arange(n,dtype=np.uint64),np.diff(np.asarray(t.source_offsets)).astype(np.int64))
    if np.any(a[k==0]>=source_owners[k==0]):raise ValueError('Cyclic task graph')
    ix(a[k==2],2);ix(c[k==2],len(_SIDES))
    ix(a[k==3],len(e));ix(b[k==3],len(numbers.values));ix(c[k==3],len(_RELATIONS))
    count=len(t.condition_expr)
    if len(t.condition_rhs)!=count or len(t.condition_rel)!=count:raise ValueError('Invalid condition columns')
    offsets(t.condition_offsets,n,count);ix(t.condition_expr,len(e));ix(t.condition_rhs,len(numbers.values));ix(t.condition_rel,len(_RELATIONS))
    # The owned arrays are compact; logical indexing overhead is charged as well.
    budget.charge('tasks',n)
    budget.charge('memory',128*len(e)+112*n+40*len(t.source_kind)+24*count+80*int(np.count_nonzero(kinds==0)))
    budget.charge(count=len(e.items)+len(t.source_kind)+count+n)
    return e,t,numbers
