"""Identity-aware extension of a point, separate from solution verification."""
from collections.abc import Mapping
from mipframe.check.kernel import facts,exact
from mipframe.frame.schema import Ref,DType
from mipframe.frame.array_source import frame_from_columns
import numpy as np


def lift(owner,solution,*,ancestor=None):
    child=facts(owner)
    if child.parent is None:raise ValueError('Snapshot has no extension map')
    parent=facts(ancestor) if ancestor is not None else child.parent
    current=child;layers=[]
    while current is not parent:
        if current.parent is None:raise ValueError('Requested snapshot is not an ancestor')
        if current.delta.drop_vars:raise ValueError('Isolated-variable deletion has no fixed extension map')
        layers.append(current.delta.fixed);current=current.parent
    from mipframe.data.exact_sparse import ExactSparse,ExactPoint
    from mipframe.frame.base import Frame
    records=solution.items() if isinstance(solution,Mapping) else ((r['var'],r['value']) for r in solution)
    def values():
        for ref,value in records:
            if hasattr(ref,'_owner'):
                identity=(ref._owner.uid,ref._owner.version,'var');ident=ref.id
            elif isinstance(ref,Ref):identity=(ref.uid,ref.version,ref.kind);ident=ref.id
            else:raise ValueError('Lift requires snapshot-qualified variable references')
            if identity!=(child.uid,child.revision,'var'):raise ValueError('Foreign lift coordinate')
            child.validate('var',ident);yield ident,exact(value)
    storage=ExactSparse().build(values(),duplicates=True)
    if len(storage.ids)!=child.nvars:storage.close();raise ValueError('Lift requires every live coordinate exactly once')
    point=ExactPoint(storage,child)
    # A lazy Frame keeps only the exact point and extension layers alive.
    def scan(fields,ctx):
        for v in parent.var_ids():
            for layer in layers:
                if v in layer:value=layer[v];break
            else:value=point[v]
            yield {n:(v if n=='var' else value) for n in fields}
    if ancestor is None:
        from mipframe.model import Model
        ancestor=Model._from_facts(parent,getattr(parent,'semantic',{}))
    result=Frame({'var':DType('ref',uid=parent.uid,version=parent.revision,axis='var'),'value':'number'},
                 source=scan,known_count=parent.nvars,key=('var',),models=(ancestor,))
    result._query.resource=point
    return result


class ExtensionMap(Mapping):
    """Borrow disjoint fixed-coordinate layers along an ancestor chain."""
    def __init__(self,layers):self.layers=tuple(layer for layer in layers if layer)
    def __len__(self):return sum(len(layer) for layer in self.layers)
    def __iter__(self):
        for layer in self.layers:yield from layer
    def __getitem__(self,key):
        for layer in reversed(self.layers):
            try:return layer[key]
            except KeyError:pass
        raise KeyError(key)
