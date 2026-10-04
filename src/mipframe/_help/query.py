"""Read-only lookup. Query text is never interpreted as code or an import path."""
import copy
import difflib
import inspect
import hashlib
import json
import importlib
import re
import types
from functools import lru_cache
from importlib.resources import files

from .search import search


@lru_cache(maxsize=1)
def corpus():
    data = json.loads(files('mipframe._help').joinpath('catalog.json').read_text(encoding='utf8'))
    payload = {k:v for k,v in data.items() if k!='checksum'}
    if hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest() != data.get('checksum'):
        raise RuntimeError('Help resource checksum mismatch; reinstall the matching package')
    if data.get('schema_version') != 1:
        raise RuntimeError('Unsupported help resource schema; reinstall the matching package')
    return data


def normalize(s):
    return s.strip().removeprefix('mipframe.').removeprefix('mf.').lower()


def object_topic(obj, data):
    # Never call repr, model properties, arbitrary __getattr__, or a live guard.
    kind = type(obj)
    lineage = type.__dict__['__mro__'].__get__(kind)
    def class_key(cls):
        module = type.__dict__['__module__'].__get__(cls)
        name = type.__dict__['__qualname__'].__get__(cls)
        return module + ':' + name
    # isinstance/inspect.is* on an arbitrary instance can consult a user-defined
    # __class__ property. Dispatch on its actual type before reading any fields.
    if kind is types.ModuleType:
        names = {'mipframe.ops':'capability.ops','mipframe.ops.sets':'capability.ops',
                 'mipframe.ops.graph':'capability.ops','mipframe.alg':'capability.alg',
                 'mipframe.alg.graph':'capability.alg','mipframe.alg.integer':'capability.alg',
                 'mipframe.audit':'capability.audit','mipframe.analysis':'api.analysis'}
        if obj.__name__ in names:
            return names[obj.__name__]
        raise TypeError('Module is not a registered public help namespace')
    if BaseException in lineage:
        topic = object.__getattribute__(obj, '__dict__').get('help_topic')
        if topic:
            return topic
        for cls in lineage:
            ident = data['exceptions'].get(class_key(cls))
            if ident:
                return ident
        return 'concept.errors'
    if kind is types.MethodType:
        receiver = type(obj.__self__)
        for cls in type.__dict__['__mro__'].__get__(receiver):
            if class_key(cls) in data['objects']:
                name = type.__dict__['__qualname__'].__get__(cls) + '.' + obj.__func__.__name__
                matches = data['exact'].get(name.lower())
                if matches:
                    return matches[0]
        obj = obj.__func__
        kind = types.FunctionType
    if kind is types.FunctionType or type in lineage:
        key = obj.__module__ + ':' + obj.__qualname__ if kind is types.FunctionType else class_key(obj)
        ident = data['objects'].get(key)
        if ident:
            return ident
    if kind is property:
        return object_topic(obj.fget, data)
    for cls in lineage:
        ident = data['objects'].get(class_key(cls))
        if ident:
            return ident
    raise TypeError('Expected a help query, registered public object, or exception')


def query(topic=None, *, limit=3, detail='brief', format='text'):
    if type(limit) is not int or limit <= 0:
        raise ValueError('limit must be a positive integer')
    if detail not in ('brief', 'full') or format not in ('text', 'json'):
        raise ValueError('Use detail=brief/full and format=text/json')
    data = corpus()
    from mipframe import __version__
    warnings = []
    if data['version'] != __version__:
        warnings.append(f"resources target {data['version']}; package is {__version__}")
    q = 'home' if topic is None else topic if type(topic) is str else object_topic(topic, data)
    q = q.strip()
    by_id = {t['id']: t for t in data['topics']}
    exact = data['exact'].get(normalize(q))
    suggestions = []
    if exact:
        ids, match = exact[:limit], 'exact'
    elif not q:
        ids, match = [], 'none'
    elif (q.startswith(('mf.', 'm.', 'mipframe.', 'api.')) or '(' in q
          or re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', q)):
        # An unknown API spelling must not look like a supported callable.
        ids, match = [], 'none'
    else:
        ids = search(q, data['topics'], data['index'])[:limit]
        match = 'search' if ids else 'none'
    if not exact:
        suggestions = difflib.get_close_matches(normalize(q).removeprefix('api.'), data['api_names'], n=3, cutoff=.72)
    result = dict(version=__version__, documentation_version=data['version'], schema_version=1,
                  query=q, match=match, detail=detail, warnings=warnings, suggestions=suggestions,
                  results=[], navigation=['concept.frame', 'concept.proofs', 'concept.resources', 'skill'])
    for ident in ids:
        t = copy.deepcopy(by_id[ident])
        for k in ('bindings', 'keywords', 'aliases', 'snippet'):
            t.pop(k, None)
        for symbol, binding in by_id[ident].get('bindings', {}).items():
            module, path = binding.split(':')
            try:
                obj = importlib.import_module(module)
                for part in path.split('.'):
                    obj = inspect.getattr_static(obj, part)
                    if isinstance(obj, (staticmethod, classmethod)):
                        obj = obj.__func__
            except (ImportError, AttributeError):
                result['warnings'].append(f'{symbol} is unavailable in this source tree; regenerate help resources')
                t['signatures'][symbol] = ' [unavailable; stale documentation]'
                t['available'] = False
                continue
            if isinstance(obj, property):
                current = ' [property]'
            elif inspect.isclass(obj):
                current = ' [type]'
            else:
                sig = inspect.signature(obj)
                current = str(sig.replace(parameters=[p for n,p in sig.parameters.items() if n not in ('self','cls','ctx')]))
                current = re.sub(r'<object object at 0x[0-9a-f]+>', '<unchanged>', current)
            if current != t['signatures'][symbol]:
                result['warnings'].append(f'{symbol} signature differs from compiled documentation; regenerate help resources')
                t['signatures'][symbol] = current
        t['complete'] = detail == 'full' or ident == 'home' or not bool(t.get('body'))
        if detail == 'brief' and ident != 'home':
            t.pop('body', None)
        result['results'].append(t)
    return result
