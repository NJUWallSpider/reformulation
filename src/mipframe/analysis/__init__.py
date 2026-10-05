"""Exact analysis tables. Template structural families are the Model default."""
from fractions import Fraction
from functools import partial
from collections import OrderedDict
from hashlib import sha256
from math import inf
import json

from mipframe.frame.base import Frame, PlanIDs
from mipframe.frame.entity import RowFrame, VarFrame
from mipframe.frame.schema import DType, ref_type
from mipframe.frame.expr import col
from mipframe.ir.signatures import refinement as _refinement, _Signature
from mipframe.ir.families import _FamilyStore, _MemberIndex

_METHODS = {'algebratic': 'algebraic', 'exact_strctural': 'exact_structural'}


def _entities(entities):
    from mipframe.frame.row import Row
    if isinstance(entities, Row):
        return entities._owner.rows._select((entities.id,))
    if not isinstance(entities, (RowFrame, VarFrame)):
        raise TypeError('Expected a Row, RowFrame or VarFrame')
    entities._guard()
    return entities


def _method(method):
    method = _METHODS.get(method, method)
    if method not in ('template', 'algebraic', 'typed', 'exact_structural'):
        raise ValueError('method must be template, algebraic, typed or exact_structural')
    return method


def families(entities, *, method, depth=None, **options):
    """
    Group selected entities using full-model identities; return a family Frame.

    MIPFRAME HELP BEGIN
    Group entities with an explicit advanced feature rule.

    Full contract: mf.help("api.analysis.families")
    MIPFRAME HELP END
    """
    entities = _entities(entities)
    method = _method(method)
    structural = method in ('template', 'exact_structural')
    if not structural and (entities._entity_axis != 'row' or depth is not None or options):
        raise ValueError('algebraic/typed require rows and accept no refinement options')
    depth = (2 if depth is None else depth) if structural else None
    result = (_refinement(entities._owner, depth=depth,
        mode='template' if method == 'template' else 'exact', **options) if structural else None)
    return _Families(entities, method, depth, result).directory()


class _Families:
    """Query source and relation definitions, with no per-view database ownership."""
    def __init__(self, entities, method, depth, result):
        self.entities, self.owner = entities, entities._owner
        self.kind, self.method, self.depth, self.result = entities._entity_axis, method, depth, result
        if result is not None:
            self.store = result.partition(self.kind, depth)
            self.config = result.config
        else:
            stores = getattr(self.owner, '_analysis_groups', None)
            if stores is None:
                self.owner._analysis_groups = stores = {}
            if method not in stores:
                stores[method] = _FamilyStore(self.owner.rows, method)
            self.store = stores[method]
            self.config = {'method': method}
        self.config_key = json.dumps(self.config, sort_keys=True, separators=(',', ':'))
        self.full = isinstance(entities.ids, range) and entities.ids == range(self.owner.shape[0 if self.kind == 'row' else 1])

    def identity(self, ident):
        payload = [self.owner.uid, self.owner.version, self.config_key, self.kind, self.depth, ident]
        return 'family:' + sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()

    def entries(self, ctx, label=None):
        records = self.store.entries()
        if label is not None:
            from mipframe.store.incidence import _FAMILY
            match = _FAMILY.fullmatch(label)
            if match is None or match[3] is not None or match[1] != ('F' if self.kind == 'row' else 'V'):
                return
            try:
                records = (self.store.find('position' if self.kind=='row' else 'color',int(match[2])),)
            except (KeyError, IndexError):
                return
        for fid, position, total, first, identity in records:
            ids = _MemberIndex(self.store, fid, range(total))
            count = total if self.full else sum(ctx.contains(self.entities, i) for i in ids)
            if count:
                label = f'V{identity}' if self.kind == 'var' else f'F{position}'
                yield dict(fid=fid, id=label, family_key=self.identity(identity), identity=identity,
                           count=count, total_count=total, first=first, ids=ids)

    def members(self, entry):
        cls = RowFrame if self.kind == 'row' else VarFrame
        frame = cls(self.owner, entry['ids'])
        return frame if self.full else frame.where(col(self.kind).isin(self.entities))

    def filter_directory(self, source, predicates, equalities):
        label = equalities.get('id')
        previous = source._query.selection
        if isinstance(label,str) and (previous is None or previous==label):
            return self.directory(label=label)
        return None

    def filter_numeric(self, source, predicates, equalities, *, view,name,keys,bases,filters):
        allowed = {'basis','quantity','orientation','role','coefficient'} & set(source.schema)
        restrictions = dict(filters or {})
        def restrict(column, values):
            if column in allowed:
                restrictions[column] = tuple(v for v in values if column not in restrictions or v in restrictions[column])
        from mipframe.frame.expr import QueryExpr
        for column,value in equalities.items():
            if value is None or isinstance(value,(str,int,Fraction)):
                restrict(column,(value,))
        def visit(predicate):
            if not isinstance(predicate,QueryExpr): return
            if predicate.op=='and':
                for child in predicate.args: visit(child)
            elif predicate.op in ('eq','isin') and predicate.args[0].op=='col':
                values = predicate.args[1] if predicate.op=='isin' else (predicate.args[1],)
                if all(v.op=='literal' for v in values):
                    restrict(predicate.args[0].args[0],tuple(v.args[0] for v in values))
        for predicate in predicates: visit(predicate)
        if restrictions != (filters or {}):
            return self.related(view,name,keys,bases=bases,filters=restrictions)
        return None

    def signature(self, entry):
        if self.result is not None:
            return _Signature(self.result, self.kind, entry['first'], self.depth)
        return self.store._decode(entry['identity'])

    def record(self, e, fields):
        sig = self.signature(e)
        raw = {n: e[n] for n in ('id', 'family_key', 'count', 'total_count')}
        raw.update(kind=self.kind, method=self.method, depth=self.depth,
            signature_id=sha256(sig.key().encode()).hexdigest(), parent=None, parent_key=None, parent_depth=None)
        if {'nnz_min', 'nnz_max'} & set(fields):
            degrees = (self.owner.model.degree(self.kind,int(i)) for i in self.members(e).ids)
            lo = hi = next(degrees)
            for n in degrees: lo, hi = min(lo, n), max(hi, n)
            raw.update(nnz_min=lo, nnz_max=hi)
        if self.result is not None:
            if self.depth:
                previous = self.result.partition(self.kind, self.depth-1)
                row = (previous.find('color',sig.parent_color)[1],)
                raw.update(parent=f'F{row[0]}' if self.kind == 'row' else f'V{sig.parent_color}', parent_depth=self.depth-1)
                payload = [self.owner.uid, self.owner.version, self.config_key, self.kind, self.depth-1, sig.parent_color]
                raw['parent_key'] = 'family:' + sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
            from mipframe.ir.display import structure_text
            if 'structure' in fields: raw['structure'] = structure_text(sig)
            if self.kind == 'row':
                raw.update(relation=sig.base[0], rhs_class=sig.base[1], sign_symmetric=sig.base == ('=', 'zero'))
            else:
                raw.update(type=sig.base[0], bounds_class=sig.base[1], objective_class=sig.base[2])
        else:
            raw.update(relation=sig.sense, rhs_class=None, sign_symmetric=sig.sense == '=' and sig.rhs == 0,
                       lb=-inf if sig.lhs is None else sig.lhs, ub=inf if sig.rhs is None else sig.rhs)
            if 'structure' in fields: raw['structure'] = repr(sig.pattern)
        return {n: raw[n] for n in fields}

    def directory(self, label=None):
        schema = dict(id='str', family_key='str', signature_id='str', kind='str', method='str',
                      depth=DType('int', True), parent=DType('str', True), parent_key=DType('str', True), parent_depth=DType('int', True),
                      count='int', total_count='int', nnz_min='int', nnz_max='int', structure='str')
        if self.kind == 'row':
            schema.update(relation='str', rhs_class=DType('str', True), sign_symmetric='bool')
            if self.result is None: schema.update(lb='number', ub='number')
        else: schema.update(type='str', bounds_class='str', objective_class='str')
        def scan(fields, ctx):
            for e in self.entries(ctx,label): yield self.record(e, fields)
        f = _frame(self.owner, schema, scan, key=('family_key',))
        f._query.navigation = self, {'family_key': 'family_key'}
        f._query.pushdown = self.filter_directory
        if label is not None: f._query.selection = label
        elif self.full: f._known_count = self.store.total
        f._query.spec = dict(kind='families', uid=self.owner.uid, version=self.owner.version,
            source=self.entities, method=self.method, depth=self.depth, config=self.config, label=label)
        return f

    def selected(self, view, keys, ctx):
        selection = view.select(family_key=col(keys['family_key'])).distinct()
        for e in self.entries(ctx,view._query.selection):
            if ctx.contains(selection, e['family_key']): yield e

    def related(self, view, name, keys, bases=None, filters=None):
        if name in ('rows', 'vars'):
            if name != self.kind+'s': raise KeyError(f'{self.kind} family members use .{self.kind}s')
            direct = getattr(view, '_family_label', None)
            if direct is not None and self.full:
                field = 'position' if self.kind == 'row' else 'color'
                fid, _, total, _, _ = self.store.find(field,int(direct[1:]))
                cls = RowFrame if self.kind == 'row' else VarFrame
                return cls(self.owner,_MemberIndex(self.store,fid,range(total)))
            member = self.related(view, 'members', keys).select(self.kind).distinct()
            cls = RowFrame if self.kind == 'row' else VarFrame
            return cls(self.owner, PlanIDs(member, self.kind))
        schemas = {
            'members': dict(family_key='str', family='str', **{self.kind: ref_type(self.owner, self.kind)}),
            'config': dict(family_key='str', method='str', depth=DType('int', True), config='str'),
            'levels': dict(depth='int', row_families='int', var_families='int'),
            'pattern': dict(family_key='str', orientation='int', position='int', coefficient='number',
                role_kind=DType('str', True), role=DType('str', True), role_depth=DType('int', True),
                multiplicity='int', multiplicity_min='int', multiplicity_max='int',
                type=DType('str', True), bounds_class=DType('str', True), objective_class=DType('str', True),
                relation=DType('str', True), rhs_class=DType('str', True), role_key=DType('str', True)),
            'parameters': dict(family_key='str', basis='str', orientation='int', role='str', quantity='str',
                coefficient=DType('number', True), minimum='number', maximum='number', distinct='int', count='int', varying='bool'),
            'histogram': dict(family_key='str', basis='str', orientation='int', role='str', quantity='str',
                coefficient=DType('number', True), value='number', count='int'),
            'sources': dict(family_key='str', basis='str', orientation='int', member=ref_type(self.owner,self.kind),
                row=ref_type(self.owner,'row'), factor='number', scale='number'),
        }
        if name not in schemas: raise KeyError(name)
        if self.result is None and name in ('levels', 'parameters', 'histogram', 'sources'):
            raise ValueError(f'{name} requires structural families; algebraic/typed values are in signatures()')
        def scan(fields, ctx):
            if name == 'levels':
                if next(self.selected(view, keys, ctx), None) is None:
                    return
                for d in range(self.depth+1):
                    raw = dict(depth=d, row_families=self.result.partition('row', d).total, var_families=self.result.partition('var', d).total)
                    yield {k:raw[k] for k in fields}
                return
            for e in self.selected(view, keys, ctx):
                if name == 'members':
                    for ident in self.members(e).ids:
                        raw = dict(family_key=e['family_key'], family=e['id'], **{self.kind: ident})
                        yield {n:raw[n] for n in fields}
                elif name == 'config':
                    raw = dict(family_key=e['family_key'],method=self.method,depth=self.depth,config=self.config_key)
                    yield {n:raw[n] for n in fields}
                elif name == 'pattern':
                    for raw in self.pattern(e, fields): yield {n:raw[n] for n in fields}
                else:
                    for raw in self.numeric(e, name, bases=bases, filters=filters): yield {n:raw[n] for n in fields}
        relation_keys = {'members':(self.kind,), 'config':('family_key',), 'levels':('depth',),
            'pattern':('family_key','orientation','position'),
            'parameters':('family_key','basis','orientation','role','quantity','coefficient'),
            'histogram':('family_key','basis','orientation','role','quantity','coefficient','value'),
            'sources':('family_key','basis','orientation','member','row')}
        f = _frame(self.owner, schemas[name], scan, key=relation_keys[name])
        f._query.spec = dict(kind='family_relation', source=view, name=name)
        if bases is not None: f._query.spec['bases'] = list(bases)
        if filters:
            from mipframe.frame.schema import encode
            f._query.spec['numeric_filters'] = {n:[encode(v) for v in values] for n,values in filters.items()}
        if name in ('parameters','histogram','sources'):
            f._query.pushdown = partial(self.filter_numeric, view=view,name=name,keys=keys,bases=bases,filters=filters)
        return f

    def pattern(self, e, fields=None):
        sig = self.signature(e)
        if self.result is None:
            for position, (token,n) in enumerate(sig.pattern):
                coef, typ, bounds = (token,None,None) if isinstance(token,Fraction) else token
                yield dict(family_key=e['family_key'],orientation=0,position=position,coefficient=coef,
                    role_kind=None,role=None,role_depth=None,multiplicity=n,multiplicity_min=n,multiplicity_max=n,type=typ,bounds_class=bounds,objective_class=None,relation=None,rhs_class=None,role_key=None)
            return
        for orientation, pattern in enumerate(sig.patterns):
            for position,(coef,role,n) in enumerate(pattern):
                base = dict(type=None,bounds_class=None,objective_class=None,relation=None,rhs_class=None)
                if fields is None or set(fields) & set(base):
                    base.update(_neighbor_base(self.result,self.kind,self.depth-1,role))
                yield dict(family_key=e['family_key'],orientation=orientation,position=position,coefficient=coef,
                    role_kind='var' if self.kind=='row' else 'row_state',role=f'V{role}' if self.kind=='row' else f'Rstate{role}',
                    role_depth=self.depth-1,multiplicity=getattr(n,'mode',n),multiplicity_min=getattr(n,'min',n),
                    multiplicity_max=getattr(n,'max',n),
                    role_key=f'{"+" if coef>0 else "-"}{"V" if self.kind=="row" else "Rstate"}{role}',**base)

    def numeric(self, e, name, bases=None, filters=None):
        from mipframe.ir.family_attributes import observations
        filters = filters or {}
        for basis in (('canonical','raw','scale_normalized') if bases is None else bases):
            if 'basis' in filters and basis not in filters['basis']: continue
            with observations(self.result,self.kind,self.depth,self.members(e),basis) as data:
                base = dict(family_key=e['family_key'],basis=basis)
                if name == 'sources':
                    for (o,member,row),(factor,scale) in data.source_records():
                        if 'orientation' in filters and o not in filters['orientation']: continue
                        yield dict(base,orientation=o,member=member,row=row,factor=Fraction(factor),scale=Fraction(scale))
                    continue
                for o in sorted(data.populations):
                    if 'orientation' in filters and o not in filters['orientation']: continue
                    for role,quantity,coefficient,histogram in data.distributions(o,self.kind,filters):
                        prefix = dict(base,orientation=o,role=role,quantity=quantity,coefficient=coefficient)
                        if name == 'histogram':
                            for value,n in histogram:
                                yield dict(prefix,value=_number(value),count=n)
                        else:
                            count=distinct=0; lo=hi=None
                            for value,n in histogram:
                                v=_number(value);lo=v if lo is None else min(lo,v);hi=v if hi is None else max(hi,v)
                                count+=n;distinct+=1
                            if count: yield dict(prefix,minimum=lo,maximum=hi,distinct=distinct,count=count,varying=distinct>1)

    inspect_options = {'verbose','samples','sample_formula','max_terms','max_items','max_splits'}

    def inspect(self, view, keys, **opts):
        from mipframe.ir.display import inspect_families
        return inspect_families(self,view,keys,**opts)


def _number(value):
    return float(value) if str(value) in ('inf','-inf') else Fraction(value)


def _frame(owner,schema,scan,key=None):
    result=Frame({n:DType(t) if isinstance(t,str) else t for n,t in schema.items()},key=key,models=(owner,),source=scan)
    result._array_scan=scan
    return result


def signatures(entities, *, method, depth=None, **options):
    """
    Exact entity features as a Frame, with normalized pattern and level relations.

    MIPFRAME HELP BEGIN
    Read exact entity features, patterns and structural levels.

    Full contract: mf.help("api.analysis.signatures")
    MIPFRAME HELP END
    """
    entities=_entities(entities);method=_method(method)
    owner,axis=entities._owner,entities._entity_axis
    if method in ('algebraic','typed'):
        if axis != 'row' or depth is not None or options:
            raise ValueError('algebraic/typed require rows and accept no refinement options')
        from mipframe.frame.adapters import signature_frame
        result=signature_frame(entities,method)
        result._query.navigation=_FeatureRelations(result.relations,owner),{'row':'row'}
        result._query.spec=dict(kind='signatures',uid=owner.uid,version=owner.version,source=entities,method=method,depth=None,config={'method':method})
        return result
    catalog=families(entities,method=method,depth=depth,**options)
    provider=catalog._query.navigation[0]
    def scan(fields,ctx):
        for ident in entities.ids:
            sig=_Signature(provider.result,axis,ident,provider.depth)
            raw={axis:ident,'depth':provider.depth,'color':sig.color,'signature_id':sha256(sig.key().encode()).hexdigest(),'family_key':provider.identity(sig.color)}
            yield {n:raw[n] for n in fields}
    result=_frame(owner,{axis:ref_type(owner,axis),'depth':'int','color':'int','signature_id':'str','family_key':'str'},scan,key=(axis,))
    result._query.navigation=_SignatureRelations(provider),{axis:axis}
    result._query.spec=dict(kind='signatures',uid=owner.uid,version=owner.version,source=entities,method=method,depth=provider.depth,config=provider.config)
    return result


class _SignatureRelations:
    def __init__(self, provider): self.provider=provider
    def related(self,view,name,keys):
        p=self.provider;axis=p.kind
        if name not in ('levels','pattern'): raise KeyError(name)
        selected=(p.owner.rows if axis=='row' else p.owner.vars).where(col(axis).isin(view.select(**{axis:col(keys[axis])})))
        if name=='pattern': return families(selected,method=p.method,depth=p.depth,**_options(p.config)).related('pattern')
        def scan(fields,ctx):
            for ident in selected.ids:
                for d in range(p.depth+1):
                    raw={axis:ident,'depth':d,'color':int(p.result._store.array(p.result._config,axis,d)[ident])}
                    yield {n:raw[n] for n in fields}
        result = _frame(p.owner,{axis:ref_type(p.owner,axis),'depth':'int','color':'int'},scan,key=(axis,'depth'))
        result._query.spec = dict(kind='family_relation',source=view,name=name)
        return result


def _options(config):
    return {k:(Fraction(v) if k=='multiplicity_ratio' else v) for k,v in config.items()
            if k in ('coefficient','rhs','objective','multiplicity_ratio') and v is not None}


def _transitions(owner, start=1, stop=2, *, kind='row', entities=None, method='template', **options):
    """Parent/child transitions, including unchanged families and selected counts."""
    from mipframe.ir.display import validate_transition
    validate_transition(start,stop,kind)
    if method not in ('template','exact_structural'): raise ValueError('Refinement requires a structural method')
    entities=(owner.rows if kind=='row' else owner.vars) if entities is None else _entities(entities)
    if entities._owner is not owner or entities._entity_axis!=kind: raise ValueError('entities must have the same model and kind')
    before=families(entities,method=method,depth=start,**options)
    after=families(entities,method=method,depth=stop,**options)
    old=before._query.navigation[0];new=after._query.navigation[0]
    def scan(fields,ctx):
        colors=new.result._store.array(new.result._config,kind,start)
        from collections import Counter
        counts=Counter(int(colors[int(i)]) for i in new.store.first)
        for e in new.entries(ctx):
            parent_color=int(colors[e['first']]);record=old.store.find('color',parent_color)
            row=(record[1],record[2]);children=counts[parent_color]
            raw=dict(parent=f'F{row[0]}' if kind=='row' else f'V{parent_color}',parent_key=old.identity(parent_color),
                child=e['id'],child_key=e['family_key'],from_depth=start,to_depth=stop,count=e['count'],total_count=e['total_count'],unchanged=children==1)
            yield {n:raw[n] for n in fields}
    f=_frame(owner,dict(parent='str',parent_key='str',child='str',child_key='str',from_depth='int',to_depth='int',count='int',total_count='int',unchanged='bool'),scan,key=('parent_key','child_key'))
    f._query.navigation=_TransitionRelations(before,after),{'parent_key':'parent_key','child_key':'child_key'}
    f._query.spec=dict(kind='refinement',uid=owner.uid,version=owner.version,source=entities,start=start,stop=stop,axis=kind,method=method,config=new.config)
    return f


class _TransitionRelations:
    def __init__(self,before,after): self.before,self.after=before,after
    def related(self,view,name,keys):
        if name not in ('before','after'): raise KeyError(name)
        key='parent_key' if name=='before' else 'child_key'
        return getattr(self,name).where(col('family_key').isin(view.select(**{key:col(keys[key])}).distinct()))


class _FeatureRelations:
    def __init__(self, relations, owner): self.relations,self.owner=relations,owner
    def related(self, view, name, keys):
        if name not in self.relations: raise KeyError(name)
        selection = self.relations[name].where(col('row').isin(view.select(row=col(keys['row'])).distinct()))
        result = Frame(selection.schema,key=selection.key,models=selection._models,source=selection._raw)
        result._query.spec = dict(kind='family_relation',source=view,name=name)
        return result


__all__ = ["families", "signatures"]


def _neighbor_base(result, source_kind, depth, color):
    """Bounded metadata lookup; a row-state role is not a public row-family ID."""
    import numpy as np
    store=result._store
    key=(result._config,source_kind,depth,color)
    cache=getattr(store,'_neighbor_bases',None)
    if cache is None: store._neighbor_bases=cache=OrderedDict()
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    for kind in (('var',) if source_kind=='row' else ('pos','neg')):
        colors=store.array(result._config,kind,depth)
        for start in range(0,len(colors),store.block):
            matches=np.flatnonzero(colors[start:start+store.block]==color)
            if len(matches):
                ident=start+int(matches[0]);table='var_data' if source_kind=='row' else 'row_data'
                base=store.base(result._config,'var' if source_kind=='row' else 'row',ident)
                names=('type','bounds_class','objective_class') if source_kind=='row' else ('relation','rhs_class')
                value=dict(zip(names,base))
                cache[key]=value
                while len(cache)>min(512,store.cache_limit): cache.popitem(last=False)
                return value
    raise ValueError('Unknown neighbor role in completed refinement')
