"""Analysis and sparse-kernel sources use the common Frame execution interface."""

from fractions import Fraction
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType
from mipframe.frame.schema import ref_type


def sparse_counts(owner, rows=None, vars=None, axis="row"):
    if axis not in ("row", "var"):
        raise ValueError("axis must be row or var")
    from mipframe.ops.matrix import _axis
    from mipframe.frame.entity import RowFrame
    from mipframe.frame.entity import VarFrame

    rows = _axis(owner, rows, RowFrame)
    variables = _axis(owner, vars, VarFrame)
    sources, target = (rows, variables) if axis == "row" else (variables, rows)
    schema = {axis: ref_type(owner, axis), "nnz": DType("int")}

    from mipframe.frame.array_source import frame_from_columns
    import numpy as np
    m=owner.model
    target_ids=set(target.ids)
    source_ids=list(sources.ids)
    all_target=target_ids==set(m.entity_ids('var' if axis=='row' else 'row'))
    if all_target:
        counts=np.fromiter((m.degree(axis,int(i)) for i in source_ids),dtype=np.int64)
    else:
        opposite='var' if axis=='row' else 'row'
        counts=np.zeros(len(source_ids),dtype=np.int64)
        if sum(m.degree(opposite,int(j)) for j in target_ids)<sum(m.degree(axis,int(i)) for i in source_ids):
            positions={int(i):p for p,i in enumerate(source_ids)}
            for j in target_ids:
                for neighbors,_ in m.sparse.blocks(opposite,int(j),65536):
                    for raw in neighbors:
                        p=positions.get(int(raw))
                        if p is not None:counts[p]+=1
        else:
            for p,i in enumerate(source_ids):
                counts[p]=sum(int(j) in target_ids for neighbors,_ in m.sparse.blocks(axis,int(i),65536) for j in neighbors)
    result=frame_from_columns({axis:np.asarray(source_ids,dtype=np.int64),'nnz':counts},
                             schema,key=(axis,),models=(owner,),domains={axis:sources})
    result._query.spec=dict(kind='sparse_counts',uid=owner.uid,version=owner.version,
                           rows=rows,vars=variables,axis=axis)
    return result


def pattern_counts(source, *, depth=None, memory_mb=128):
    from mipframe.ir.patterns import build_patterns
    return build_patterns(source,depth,memory_mb)


def membership(source, depth=None, signatures=None):
    from mipframe.ir.signatures import refinement
    from mipframe.analysis import _Families
    owner, axis = source._owner, source._entity_axis
    depth = 2 if depth is None else depth
    result = refinement(owner,depth=depth) if signatures is None else signatures
    if result._store.owner is not owner: raise ValueError('Refinement belongs to another model')
    method = 'template' if result.mode == 'template' else 'exact_structural'
    provider = _Families(source,method,depth,result)
    directory = provider.directory()
    members = directory.related('members')
    def scan(fields, ctx):
        for row in members._raw((axis,'family'),ctx):
            raw = {axis:row[axis],'family':f'{row["family"]}@{depth}'}
            yield {n:raw[n] for n in fields}
    frame = Frame({axis:ref_type(owner,axis),'family':DType('str')},key=(axis,),models=(owner,),source=scan)
    frame._array_scan=scan
    frame._query.spec = dict(kind='membership',uid=owner.uid,version=owner.version,
        source=source,config=result.config,depth=depth)
    return frame


def signature_frame(rows, signature):
    from mipframe.ir.algebraic import signature_method
    from types import MappingProxyType

    owner = rows._owner
    method = signature_method(signature)
    schema = dict(
        row=ref_type(owner, "row"),
        name=DType("str"),
        nnz=DType("int"),
        relation=DType("str"),
        lb=DType("number"),
        ub=DType("number"),
        scale=DType("number"),
        signature_id=DType("str"),
        method=DType("str"),
    )

    def scan(fields, ctx):
        for ident in rows.ids:
            raw = {"row": ident}
            if "name" in fields:
                raw["name"] = owner.model.rows[ident][1]
            if set(fields) - {"row", "name"}:
                sig = method(owner, ident)
                raw.update(
                    nnz=sig.nnz,
                    relation=sig.sense,
                    lb=-float("inf") if sig.lhs is None else sig.lhs,
                    ub=float("inf") if sig.rhs is None else sig.rhs,
                    scale=sig.scale,
                    signature_id=__import__("hashlib").sha256(sig.key().encode()).hexdigest(),
                    method="algebraic" if signature=="algebratic" else signature,
                )
            yield {n: raw[n] for n in fields}

    frame = Frame(schema, key=("row",), models=rows._models, source=scan)
    ps = dict(
        row=ref_type(owner, "row"),
        coef=DType("number"),
        kind=DType("str", True),
        bounds=DType("str", True),
        count=DType("int"),
    )

    def pattern(fields, ctx):
        for ident in rows.ids:
            sig = method(owner, ident)
            for token, n in sig.pattern:
                coef, kind, bounds = (
                    (token, None, None) if isinstance(token, Fraction) else token
                )
                raw = dict(row=ident, coef=coef, kind=kind, bounds=bounds, count=n)
                yield {k: raw[k] for k in fields}

    detail = Frame(
        ps, key=("row", "coef", "kind", "bounds"), models=rows._models, source=pattern
    )
    frame._array_scan=scan
    detail._array_scan=pattern
    frame._relations = MappingProxyType({"pattern": detail})
    for f, part in ((frame, "summary"), (detail, "pattern")):
        f._query.spec = {
            "kind": "relation",
            "relation": f"signature:{owner.uid}:{signature}:{id(rows)}:{part}",
        }
    return frame
