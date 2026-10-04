"""Lossless nested metadata in scalar, ordered node tables."""
from fractions import Fraction
from mipframe.frame import Frame, DType, SchemaError

SCHEMA = dict(path=DType('str'), node_type=DType('str'), text=DType('str',True),
              number=DType('number',True), boolean=DType('bool',True))


def nodes(value, path=''):
    row = dict(path=path, text=None, number=None, boolean=None)
    if isinstance(value, dict): kind='object'
    elif isinstance(value, (list,tuple)): kind='array'
    elif value is None: kind='null'
    elif type(value) is bool: kind='bool'; row['boolean']=value
    elif isinstance(value,(int,Fraction)): kind='number'; row['number']=value
    elif isinstance(value,str): kind='str'; row['text']=value
    else: raise TypeError(f'Unsupported metadata value: {type(value).__name__}')
    yield dict(row,node_type=kind)
    if kind in ('object','array'):
        for key, child in (value.items() if kind=='object' else enumerate(value)):
            token=str(key).replace('~','~0').replace('/','~1')
            yield from nodes(child,path+'/'+token)


def restore(records):
    """Validate a complete node tree, preserving arrays and empty containers."""
    values={}
    for r in records:
        path=r['path']; kind=r['node_type']
        if path in values: raise SchemaError('Duplicate metadata path')
        if kind=='object': value={}
        elif kind=='array': value=[]
        elif kind=='null': value=None
        elif kind=='str': value=r['text']
        elif kind=='number': value=r['number']; value=int(value) if value.denominator==1 else value
        elif kind=='bool': value=r['boolean']
        else: raise SchemaError('Invalid metadata node type')
        values[path]=value
        if path:
            parent,sep,token=path.rpartition('/')
            if not sep or parent not in values: raise SchemaError('Metadata parents must precede children')
            token=token.replace('~1','/').replace('~0','~')
            target=values[parent]
            if isinstance(target,list):
                if token!=str(len(target)): raise SchemaError('Invalid metadata array order')
                target.append(value)
            elif isinstance(target,dict): target[token]=value
            else: raise SchemaError('Scalar metadata parent')
    if '' not in values: raise SchemaError('Missing metadata root')
    return values['']
