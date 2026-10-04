"""Built-in Frame source codecs; query operators are serialized separately."""
from fractions import Fraction
from mipframe.frame.schema import DType, SchemaError, decode


_READERS = {}


def source(*kinds):
    def register(reader):
        for kind in kinds:
            _READERS[kind] = reader
        return reader
    return register


def owner(spec, models):
    model = models.get(spec.get('uid'))
    if model is None:
        raise SchemaError('Missing model binding')
    if (model.uid, model.version) != (spec['uid'], spec['version']):
        raise SchemaError('Model binding identity/version mismatch')
    return model


def encode_source(frame, node):
    from mipframe.frame.base import Frame, PlanIDs
    spec = dict(frame._query.spec)
    if not spec:
        raise SchemaError('This analysis relation must be supplied as an explicit relation binding')
    kind = spec['kind']
    if kind not in _READERS:
        raise SchemaError('Unknown scan source')
    if kind in ('row', 'var'):
        ids = frame._index
        if isinstance(ids, PlanIDs):
            return node(ids.frame)
        spec['ids'] = {'range':[ids.start,ids.stop,ids.step]} if isinstance(ids,range) else list(map(int,ids))
    elif kind == 'terms':
        spec.update(rows=frame._rows, vars=frame._vars)
    elif kind == 'relation':
        spec['schema'] = {n:t.to_spec() for n,t in frame.schema.items()}
    arguments = spec.pop('arguments') if kind == 'algorithm' else None
    def value(item):
        if isinstance(item,Frame):return node(item)
        if isinstance(item,dict):return {k:value(v) for k,v in item.items()}
        if isinstance(item,(list,tuple)):return [value(v) for v in item]
        return item
    result = {'op':'scan', **value(spec)}
    if kind == 'algorithm':
        from mipframe.ops._runtime import recipe_arguments
        # Keep the node dictionaries: a later shared use may assign their $id.
        result['arguments'] = recipe_arguments(arguments, node)
    return result


def decode_source(spec, build, models, relations):
    reader = _READERS.get(spec['kind'])
    if reader is None:
        raise SchemaError('Unknown scan source')
    return reader(spec, build, models, relations)


@source('row', 'var')
def entities(n, build, models, relations):
    model = owner(n,models)
    ids = n['ids']
    ids = range(*ids['range']) if isinstance(ids,dict) else ids
    total = model.shape[0 if n['kind']=='row' else 1]
    if any(type(i) is not int or not 0<=i<total for i in ids):
        raise SchemaError('Invalid model IDs')
    return (model.rows if n['kind']=='row' else model.vars)._select(ids)


@source('entity_refs')
def refs(n, build, models, relations):
    return build(n['source']).refs(n['column'])


@source('entity_set')
def entity_set(n, build, models, relations):
    from mipframe.ops.support import set_operation
    return set_operation(build(n['left']),build(n['right']),n['operation'])


@source('relation')
def relation(n, build, models, relations):
    if n['relation'] not in relations:
        raise SchemaError('Missing explicit relation binding')
    result = relations[n['relation']]
    if dict(result.schema) != {k:DType(**v) for k,v in n['schema'].items()}:
        raise SchemaError('Relation schema or snapshot binding mismatch')
    return result


@source('algorithm')
def algorithm(n, build, models, relations):
    from mipframe.ops._runtime import restore
    return restore(n,build)


@source('algorithm_relation')
def algorithm_relation(n, build, models, relations):
    return build(n['source']).relations[n['name']]


@source('indexed_directory')
def indexed_directory(n, build, models, relations):
    model = owner(n,models)
    result = getattr(model,'_indexed_results',{}).get(n['run'])
    if result is None:raise SchemaError('Saved indexed run is not available')
    if n['collection'] not in ('tensors','operators','partitions','evidence'):
        raise SchemaError('Invalid indexed collection')
    return result._directory(n['collection'])


@source('indexed_relation', 'family_relation')
def related(n, build, models, relations):
    result = build(n['source'])
    if n['kind']=='family_relation' and ('bases' in n or 'numeric_filters' in n):
        bases = n.get('bases')
        if bases is not None and (not isinstance(bases,list) or any(v not in ('canonical','raw','scale_normalized') for v in bases)):
            raise SchemaError('Invalid numeric basis')
        encoded = n.get('numeric_filters',{})
        if not isinstance(encoded,dict) or not set(encoded)<={'basis','quantity','role','orientation','coefficient'}:
            raise SchemaError('Invalid numeric filters')
        if any(not isinstance(values,list) for values in encoded.values()):
            raise SchemaError('Invalid numeric filter values')
        filters = {k:tuple(decode(v) for v in values) for k,values in encoded.items()}
        provider,keys = result._query.navigation
        return provider.related(result,n['name'],keys,bases=None if bases is None else tuple(bases),filters=filters)
    return result.related(n['name'])


@source('families', 'signatures', 'refinement')
def analysis(n, build, models, relations):
    from mipframe import analysis
    model = owner(n,models)
    options = analysis._options(n['config'])
    selected = build(n['source'])
    if getattr(selected,'_owner',None) is not model:
        raise SchemaError('Family source belongs to another model')
    if n['kind']=='refinement':
        return analysis._transitions(model,n['start'],n['stop'],kind=n['axis'],entities=selected,method=n['method'],**options)
    result = getattr(analysis,n['kind'])(selected,method=n['method'],depth=n['depth'],**options)
    if n['kind']=='families' and n.get('label') is not None:
        if not isinstance(n['label'],str):raise SchemaError('Invalid family label filter')
        result = result._query.navigation[0].directory(label=n['label'])
    if result._query.spec['config'] != n['config']:
        raise SchemaError('Incompatible family configuration')
    return result


@source('membership')
def membership(n, build, models, relations):
    from mipframe.frame.adapters import membership
    from mipframe.ir.signatures import refinement
    model = owner(n,models)
    config = n['config']
    args = {k:config[k] for k in ('mode','coefficient','rhs','objective')}
    if config.get('multiplicity_ratio') is not None:
        args['multiplicity_ratio'] = Fraction(config['multiplicity_ratio'])
    result = refinement(model,depth=n['depth'],**args)
    if result.config != config:raise SchemaError('Incompatible signature configuration')
    return membership(build(n['source']),n['depth'],result)


@source('terms')
def terms(n, build, models, relations):
    return owner(n,models).terms(rows=None if n['rows'] is None else build(n['rows']),
                                vars=None if n['vars'] is None else build(n['vars']))


@source('sparse_counts')
def sparse_counts(n, build, models, relations):
    from mipframe.frame.adapters import sparse_counts
    return sparse_counts(owner(n,models),build(n['rows']),build(n['vars']),n['axis'])


@source('project')
def project(n, build, models, relations):
    return build(n['source']).project(**{k:build(v) for k,v in n['bindings'].items()})
