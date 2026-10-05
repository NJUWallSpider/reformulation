"""Exact sparse neighborhood histograms over typed family colors."""
from collections import Counter
import numpy as np
from mipframe.frame.array_source import frame_from_columns
from mipframe.frame.schema import DType,ref_type


def build_patterns(source, depth, memory_mb):
    from mipframe.store.incidence import default_depth
    from mipframe.ir.signatures import refinement
    from mipframe.data.runtime import WorkContext
    owner=source._owner
    kind='var' if source._entity_axis=='row' else 'row'
    depth=default_depth(owner,kind) if depth is None else depth
    if type(depth) is not int or depth<0:
        raise ValueError('depth must be a nonnegative integer')
    result=refinement(owner,depth=depth,memory_mb=memory_mb)
    colors=result._store.array(result._config,kind,depth)
    partition=result.partition(kind,depth)
    ids=[];patterns=[];intern={};counts=Counter()
    with WorkContext(memory_mb) as work:
        block=max(1,work.memory//256)
        for ident in dict.fromkeys(source.ids):
            histogram=Counter()
            for neighbors,_ in owner.model.sparse.blocks(source._entity_axis,ident,block):
                values,amounts=np.unique(colors[neighbors],return_counts=True)
                for color,n in zip(values,amounts):
                    label=int(color) if kind=='var' else partition.find('color',int(color))[1]
                    histogram[label]+=int(n)
            key=tuple(sorted(histogram.items()))
            pattern=intern.setdefault(key,len(intern))
            counts[pattern]+=1;ids.append(ident);patterns.append(pattern)
    keys=list(intern)
    order=sorted(counts,key=lambda p:(-counts[p],keys[p]))
    models=(owner,);axis=source._entity_axis
    members=frame_from_columns({axis:ids,'pattern':patterns},
        {axis:ref_type(owner,axis),'pattern':DType('int')},models=models,key=(axis,))
    item_patterns=[];families=[];amounts=[]
    prefix='V' if kind=='var' else 'F'
    for pattern,key in enumerate(keys):
        for color,n in key:
            item_patterns.append(pattern);families.append(f'{prefix}{color}@{depth}');amounts.append(n)
    items=frame_from_columns(dict(pattern=item_patterns,family=families,count=amounts),
        dict(pattern=DType('int'),family=DType('str'),count=DType('int')),models=models,key=('pattern','family'))
    return frame_from_columns(dict(pattern=order,count=[counts[p] for p in order]),
        dict(pattern=DType('int'),count=DType('int')),models=models,key=('pattern',),relations=dict(members=members,items=items))
