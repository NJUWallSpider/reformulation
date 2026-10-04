"""Private-kernel probes used only by independent mathematical oracle tests."""
from types import SimpleNamespace
from mipframe.ir.signatures import refinement, _Signature
from mipframe.ir.algebraic import signature_method
from mipframe.frame.entity import RowFrame
import mipframe as mf


def refinement_probe(owner, **options):
    result=refinement(owner,**options)
    depth=options.get('depth',2)
    return SimpleNamespace(_store=result._store,_config=result._config,_key=result._key,
        config=result.config,depth=depth,mode=result.mode,
        row_color={d:result._store.array(result._config,'row',d) for d in range(depth+1)},
        var_color={d:result._store.array(result._config,'var',d) for d in range(depth+1)})


def signature_key(row, method='algebraic', *, depth=None, **options):
    if isinstance(row,RowFrame):
        return mf.analysis.signatures(row,method='template' if method=='structural' else method,depth=depth,**options)
    if method in ('structural','exact_structural','exact_strctural'):
        result=refinement(row._owner,depth=2 if depth is None else depth,
            mode='template' if method=='structural' else 'exact',**options)
        return _Signature(result,'row',row.id,depth)
    return signature_method(method)(row._owner,row.id)


def indexed_shape(frame, component='shape'):
    return list(frame.related('dimensions').where(component=component).column('size'))

def indexed_axes(frame):
    return [frame.related('axes').where(axis=axis).rows for axis in range(2)]

def indexed_metadata(frame, relation):
    from mipframe.ir.indexing.nodes import restore
    return restore(frame.related(relation))
