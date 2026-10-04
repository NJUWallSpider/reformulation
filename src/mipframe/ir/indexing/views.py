"""Indexed analysis as ordinary Frames over the shared discovery store."""
import hashlib
import json
from fractions import Fraction
from copy import deepcopy
from mipframe.frame import Frame, DType, col, SchemaError
from mipframe.frame.schema import ref_type
from mipframe.frame.base import PlanIDs
from mipframe.frame.entity import RowFrame, VarFrame
from mipframe.check.diagnostics import diagnostic, report_check
from mipframe.ir.indexing.nodes import nodes, SCHEMA as NODE_SCHEMA


def report(s, errors, **summary):
    errors = iter(errors)
    first = next(errors, None)
    from itertools import chain
    details = () if first is None else chain((first,), errors)
    check = dict(status='PROVED' if first is None else 'REFUTED',
        diagnostics=(dict(reason=e.get('reason','validation failure'),
                          detail=json.dumps(e,default=str)) for e in details))
    return diagnostic(s.owner, (check,), kind='indexed', summary=summary)



def _frame(result, schema, scan, key=None):
    f=Frame({n:t if isinstance(t,DType) else DType(t) for n,t in schema.items()},
            key=key,models=(result._store.owner,),source=scan)
    f._query.resource=result._store
    return f


class _Directory:
    def __init__(self, result, kind):
        self.result,self.kind=result,kind
        self.store=result._store
        self.owner=self.store.owner

    def identity(self, ident):
        payload=[self.owner.uid,self.owner.version,self.result.id,self.kind,ident]
        return 'indexed:'+hashlib.sha256(json.dumps(payload,separators=(',',':')).encode()).hexdigest()

    def records(self):
        for (text,) in self.store.execute('SELECT record FROM cards WHERE run=? AND kind=? ORDER BY id',(self.result.id,self.kind)):
            yield json.loads(text)

    def selected(self, view, keys, ctx):
        selected=view.select(indexed_key=col(keys['indexed_key'])).distinct()
        for card in self.records():
            if ctx.contains(selected,self.identity(card['id'])): yield card

    def directory(self):
        schema=dict(id='str',indexed_key='str')
        schema.update({
            'tensors':dict(var_count='int',complete_product='bool',missing_cells='int',verification='str',meaning='str'),
            'operators':dict(tensor='str',kind='str',row_count='int',fixed_axis='int',axis_invariant='bool',complete_row_product='bool',formula='str',verification='str'),
            'partitions':dict(row_count='int',var_count='int',verification='str'),
            'evidence':dict(reason='str'),
        }[self.kind])
        def scan(fields,ctx):
            for card in self.records():
                row=dict(card,indexed_key=self.identity(card['id']))
                if self.kind in ('tensors','partitions'):row['var_count']=card['variables']
                if self.kind=='operators':row['row_count']=card['rows']
                if self.kind=='partitions':row['row_count']=card['sets']
                yield {n:row[n] for n in fields}
        f=_frame(self.result,schema,scan,key=('id',))
        f._query.navigation=self,{'indexed_key':'indexed_key'}
        f._query.display_columns=tuple(n for n in schema if n not in ('id','indexed_key','tensor'))
        f._query.spec=dict(kind='indexed_directory',uid=self.owner.uid,version=self.owner.version,run=self.result.id,collection=self.kind)
        return f

    def related(self, view, name, keys):
        allowed={'tensors':('vars',),'operators':('rows',),'partitions':('rows','vars'),'evidence':()}[self.kind]
        if name in ('rows','vars'):
            if name not in allowed:raise KeyError(f'{self.kind} has no {name} member axis')
            axis='row' if name=='rows' else 'var'
            return self.related(view,name+'_members',keys).refs(axis)
        schemas={
            'dimensions':dict(component='str',axis='int',size='int'),
            'ast':NODE_SCHEMA,'origins':NODE_SCHEMA,'details':NODE_SCHEMA,
            'statistics':dict(path='str',node_type='str',text=DType('str',True),number=DType('number',True),boolean=DType('bool',True)),
            'rows_members':dict(row=ref_type(self.owner,'row')),
            'vars_members':dict(var=ref_type(self.owner,'var')),
            'members':dict(row=ref_type(self.owner,'row'),var=ref_type(self.owner,'var')),
            'coordinates':dict(var=ref_type(self.owner,'var'),i=ref_type(self.owner,'row'),j=ref_type(self.owner,'row'),axis0='int',axis1='int'),
            'axes':dict(axis='int',size='int',partition='str'),
            'axis_members':dict(axis='int',position='int',row=ref_type(self.owner,'row')),
            'bindings':dict(row=ref_type(self.owner,'row'),p='int',k='int',anchor=ref_type(self.owner,'var'),scale='number',lower=DType('number',True),upper=DType('number',True)),
            'kernel':dict(p='int',k=DType('int',True),j='int',value='number'),
            'alignment':dict(p='int',j='int'),
        }
        available={
            'tensors':{'dimensions','vars_members','coordinates','axes','axis_members','partitions'},
            'operators':{'dimensions','rows_members','bindings','kernel','alignment','ast','statistics','tensor'},
            'partitions':{'rows_members','vars_members','members','origins'},
            'evidence':{'details'},
        }[self.kind]
        if name not in available:raise KeyError(name)
        if name in ('partitions','tensor'):
            target=self.result.partitions if name=='partitions' else self.result.tensors
            def scan(fields,ctx):
                for card in self.selected(view,keys,ctx):
                    for ident in card['partitions'] if name=='partitions' else (card['tensor'],):
                        yield {'id':ident}
            selected=_frame(self.result,dict(id='str'),scan)
            return target.where(col('id').isin(selected))
        schema=dict(indexed_key='str',id='str',**schemas[name])
        def scan(fields,ctx):
            for card in self.selected(view,keys,ctx):
                prefix=dict(indexed_key=self.identity(card['id']),id=card['id'])
                for record in self.relation_records(card,name):
                    row=dict(prefix,**record)
                    yield {n:row[n] for n in fields}
        keys_by_name={'dimensions':('component','axis'),'ast':('path',),'origins':('path',),'details':('path',),'statistics':('path',),
                      'rows_members':('row',),'vars_members':('var',),'members':('row','var'),'coordinates':('var',),
                      'axes':('axis',),'axis_members':('axis','position'),'bindings':('row',),'kernel':('p','k','j'),'alignment':('p',)}
        f=_frame(self.result,schema,scan,key=('indexed_key',*keys_by_name[name]))
        f._query.spec=dict(kind='indexed_relation',source=view,name=name)
        f._query.display_columns=tuple(n for n in schema if n not in ('indexed_key','id'))
        if name=='coordinates':
            from types import MappingProxyType
            f._domains=MappingProxyType({n:self.related(view,'axes',keys).where(axis=a).rows for a,n in enumerate(('i','j'))})
        if name=='axes':f._query.navigation=_Axes(self,view,keys),{'indexed_key':'indexed_key','axis':'axis'}
        return f

    def relation_records(self, card, name):
        s=self.store; ident=card['id']
        if name in ('ast','origins','details','statistics'):
            value=card if name=='details' else card['kernel_statistics' if name=='statistics' else name]
            if name=='statistics':
                def numbers(v):
                    if isinstance(v,dict):return {k:numbers(x) for k,x in v.items()}
                    if isinstance(v,list):return [numbers(x) for x in v]
                    if isinstance(v,str):
                        try:return Fraction(v)
                        except ValueError:pass
                    return v
                value=numbers(value)
            yield from nodes(value);return
        if name=='dimensions':
            for component in ('shape','kernel_shape'):
                for axis,size in enumerate(card.get(component,())):yield dict(component=component,axis=axis,size=size)
            return
        if name=='axes':
            for axis,(g,part) in enumerate(zip(card['rows_domains'],card['partitions'])):
                yield dict(axis=axis,size=s.size(g),partition=part)
            return
        if name=='axis_members':
            for axis,g in enumerate(card['rows_domains']):
                for position,(r,) in enumerate(s.execute('SELECT id FROM domain_members WHERE g=? ORDER BY id',(g,))):
                    yield dict(axis=axis,position=position,row=r)
            return
        if name=='rows_members' and self.kind=='operators':
            sql='SELECT r FROM bindings WHERE o=? ORDER BY r';args=(ident,);fields=('row',)
        elif name in ('rows_members','vars_members'):
            axis='row' if name=='rows_members' else 'var'
            sql='SELECT id FROM domain_members WHERE g=? ORDER BY id';args=(card[axis+'s_domain'],);fields=(axis,)
        elif name=='members':
            sql='SELECT r,v FROM members WHERE g=? ORDER BY r,v';args=(card['g'],);fields=('row','var')
        elif name=='coordinates':
            sql='SELECT v,i,j,a,b FROM coordinates WHERE t=? ORDER BY v';args=(ident,);fields=('var','i','j','axis0','axis1')
        elif name=='bindings':
            sql='SELECT r,p,k,anchor,scale,lo,hi FROM bindings WHERE o=? ORDER BY p,k';args=(ident,);fields=('row','p','k','anchor','scale','lower','upper')
        elif name=='kernel':
            sql='SELECT p,CASE WHEN k=-1 THEN NULL ELSE k END,j,value FROM kernel WHERE o=? ORDER BY p,k,j';args=(ident,);fields=('p','k','j','value')
        elif name=='alignment':
            sql='SELECT p,j FROM alignment WHERE o=? ORDER BY p';args=(ident,);fields=('p','j')
        else:raise KeyError(name)
        for values in s.execute(sql,args):
            row=dict(zip(fields,values))
            for n in ('scale','lower','upper','value'):
                if n in row and row[n] is not None:row[n]=Fraction(row[n])
            yield row

    inspect_options = {'max_items', 'verbose'}

    def inspect(self, view, keys, *, max_items=15, verbose=False):
        from mipframe.frame.executor import Context
        parts=[view._render(max_rows=max_items)]
        if self.kind=='operators':
            parts.extend(r.formula for r in view.head(max_items))
        if verbose:
            for name in {'operators':('dimensions','statistics','bindings'),'tensors':('axes','coordinates'),
                         'partitions':('origins','members'),'evidence':('details',)}[self.kind]:
                parts.append(name+'\n'+view.head(max_items).related(name)._render(max_rows=max_items))
        total=len(view)
        if total>max_items:parts.append(f'{total-max_items} items omitted')
        return '\n'.join(parts)


class _Axes:
    def __init__(self, directory, source, keys):self.directory,self.source,self.keys=directory,source,keys
    def related(self,view,name,keys):
        if name not in ('rows','members'):raise KeyError(name)
        d=self.directory
        def scan(fields,ctx):
            selected=view.select(indexed_key=col(keys['indexed_key']),axis=col(keys['axis'])).distinct()
            axis_selections=[selected.where(axis=a).select('indexed_key').distinct() for a in (0,1)]
            # Key semijoin includes axis, so filtering one axis cannot leak the other.
            for card in d.selected(self.source,self.keys,ctx):
                ident=d.identity(card['id'])
                for axis,g in enumerate(card['rows_domains']):
                    if not ctx.contains(axis_selections[axis],ident):continue
                    for position,(r,) in enumerate(d.store.execute('SELECT id FROM domain_members WHERE g=? ORDER BY id',(g,))):
                        row=dict(indexed_key=ident,axis=axis,position=position,row=r)
                        yield {n:row[n] for n in fields}
        f=_frame(d.result,dict(indexed_key='str',axis='int',position='int',row=ref_type(d.owner,'row')),scan,key=('indexed_key','axis','position'))
        f._query.spec=dict(kind='indexed_relation',source=view,name='members')
        return f.refs('row') if name=='rows' else f


class IndexedResult:
    """
    One immutable, snapshot-bound discovery run; all directories are Frames.

    MIPFRAME HELP BEGIN
    Completed coordinate and operator discovery, with exact verification.

    Full contract: mf.help("api.IndexedResult")
    MIPFRAME HELP END
    """
    def __setattr__(self,name,value):
        if not name.startswith('_'):raise AttributeError('Indexed results are read-only')
        object.__setattr__(self,name,value)
    def __init__(self,store,run):self._store,self._id=store,run
    @property
    def id(self):self._store.guard();return self._id
    @property
    def scope(self):
        raw=dict(self._record()['scope']);raw['row_count']=raw.pop('rows');raw['var_count']=raw.pop('variables');return raw
    def _record(self):
        return json.loads(self._store.execute('SELECT record FROM runs WHERE id=?',(self.id,)).fetchone()[0])
    def _card(self,kind,ident):return self._store.get(self.id,kind,ident)
    def _directory(self,kind):return _Directory(self,kind).directory()
    def _one(self,kind,ident):
        if not isinstance(ident,str):raise TypeError('Indexed IDs must be strings')
        self._card(kind,ident)
        return self._directory(kind).where(id=ident)
    def tensor(self,ident):
        """
        MIPFRAME HELP BEGIN
        Select one indexed item by its required ID.

        Full contract: mf.help("api.IndexedResult.tensor")
        MIPFRAME HELP END
        """
        return self._one('tensors',ident)
    def operator(self,ident):
        """
        MIPFRAME HELP BEGIN
        Select one indexed item by its required ID.

        Full contract: mf.help("api.IndexedResult.operator")
        MIPFRAME HELP END
        """
        return self._one('operators',ident)
    def partition(self,ident):
        """
        MIPFRAME HELP BEGIN
        Select one indexed item by its required ID.

        Full contract: mf.help("api.IndexedResult.partition")
        MIPFRAME HELP END
        """
        return self._one('partitions',ident)
    def evidence_item(self,ident):
        """
        MIPFRAME HELP BEGIN
        Select one indexed item by its required ID.

        Full contract: mf.help("api.IndexedResult.evidence_item")
        MIPFRAME HELP END
        """
        return self._one('evidence',ident)
    @property
    def tensors(self):
        """
        MIPFRAME HELP BEGIN
        Query the indexed tensors directory.

        Full contract: mf.help("api.IndexedResult.tensors")
        MIPFRAME HELP END
        """
        return self._directory('tensors')
    @property
    def operators(self):
        """
        MIPFRAME HELP BEGIN
        Query the indexed operators directory.

        Full contract: mf.help("api.IndexedResult.operators")
        MIPFRAME HELP END
        """
        return self._directory('operators')
    @property
    def partitions(self):
        """
        MIPFRAME HELP BEGIN
        Query the indexed partitions directory.

        Full contract: mf.help("api.IndexedResult.partitions")
        MIPFRAME HELP END
        """
        return self._directory('partitions')
    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        Query the indexed evidence directory.

        Full contract: mf.help("api.IndexedResult.evidence")
        MIPFRAME HELP END
        """
        return self._directory('evidence')
    def to_record(self):
        """
        MIPFRAME HELP BEGIN
        Export detached run metadata.

        Full contract: mf.help("api.IndexedResult.to_record")
        MIPFRAME HELP END
        """
        record=self._record();record['scope']=self.scope;return record
    def validate(self,spec):
        """
        MIPFRAME HELP BEGIN
        Validate an independently supplied indexed hypothesis.

        Full contract: mf.help("api.IndexedResult.validate")
        MIPFRAME HELP END
        """
        from mipframe.ir.indexing.validation import validate, recipe_spec
        from mipframe.ir.indexing.candidates import normalize_spec
        recipe=recipe_spec(spec)
        answer=validate(self,normalize_spec(self,spec))
        answer._recipe=dict(kind='indexed',run=self.id,spec=recipe)
        return answer
    def verify(self,selection=None):
        """
        MIPFRAME HELP BEGIN
        Replay prepared tensors/operators or an explicit directory selection.

        Full contract: mf.help("api.IndexedResult.verify")
        MIPFRAME HELP END
        """
        if selection is None:
            groups=[('tensors',self.tensors),('operators',self.operators)]
        else:
            if not isinstance(selection,Frame):raise TypeError('selection must be an indexed directory Frame')
            navigation=selection._query.navigation
            if navigation is None or not isinstance(navigation[0],_Directory):raise SchemaError('Retain the indexed directory identity key')
            provider,_=navigation
            if provider.owner is not self._store.owner or provider.result.id!=self.id:raise SchemaError('Indexed selection belongs to a different model or run')
            if provider.kind=='evidence':raise ValueError('Discovery evidence is not a proposition')
            groups=[(provider.kind,selection)]
        def checks():
            from mipframe.frame.executor import Context
            for kind,view in groups:
                provider,keys=view._query.navigation
                from contextlib import closing
                with closing(Context(view._models)) as ctx:
                    for card in provider.selected(view,keys,ctx):
                        spec=dict(kind={'tensors':'cartesian','operators':'indexed_operator','partitions':'indexed_partition'}[kind],id=card['id'])
                        yield report_check(kind, card['id'], self.validate(spec))
        recipe=dict(kind='indexed',run=self.id)
        if selection is not None:recipe['selection']=selection.to_spec()
        return diagnostic(self._store.owner,checks(),kind='indexed',recipe=recipe)
    def __repr__(self):return f'<IndexedResult complete: {len(self.tensors)} coordinate views, {len(self.operators)} operators; heuristic search>'
    def _repr_pretty_(self,p,cycle):p.text(repr(self))


def ir_summary(owner,limit=None):
    results=getattr(owner,'_indexed_results',{});runs=[]
    for key,result in sorted(results.items())[:limit]:
        record=result.to_record()
        record.update(total_tensors=len(result.tensors),total_operators=len(result.operators),tensors=[],operators=[])
        for kind in ('tensors','operators'):
            directory=getattr(result,kind)
            for row in directory if limit is None else directory.head(limit):
                raw=result._card(kind,row.id)
                record[kind].append(dict(id=row.id,shape=raw['shape'],var_count=row.var_count,complete_product=row.complete_product,missing_cells=row.missing_cells) if kind=='tensors' else dict(id=row.id,row_count=row.row_count,kind=row.kind,axis_invariant=row.axis_invariant))
        runs.append(record)
    return dict(total_runs=len(results),status='complete' if runs else 'not_run',search_scope='declared candidate rules; not exhaustive structural discovery',runs=runs)
