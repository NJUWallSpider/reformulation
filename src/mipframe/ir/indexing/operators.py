"""Exact sparse reduction recovery and bounded numeric verification."""

from fractions import Fraction as F
import json
from mipframe.ir.indexing.store import ident
from mipframe.ir.indexing.discovery import evidence
from mipframe.store.numbers import MIPFrameError


def operator_ast(t, axis, invariant):
    return {
        "op": "range",
        "lower": {"parameter": "lower", "indices": ["p", "k"]},
        "upper": {"parameter": "upper", "indices": ["p", "k"]},
        "expression": {
            "op": "sub",
            "left": {"tensor": "y", "indices": ["p"]},
            "right": {
                "op": "sum",
                "index": "j",
                "domain": {"tensor": t, "fixed_axis": axis, "fixed_index": "k"},
                "term": {
                    "op": "mul",
                    "left": {
                        "parameter": "A",
                        "indices": ["p", "j"] if invariant else ["p", "k", "j"],
                    },
                    "right": {
                        "tensor": t,
                        "indices": ["k", "j"] if axis == 0 else ["j", "k"],
                    },
                },
            },
        },
    }


def _ref_id(s, value, axis):
    from mipframe.frame.schema import Ref
    from mipframe.frame.schema import SchemaError
    if isinstance(value, Ref):
        if (value.uid,value.version,value.kind)!=(s.owner.uid,s.owner.version,axis):
            raise SchemaError('Indexed record references another snapshot or axis')
        return value.id
    if type(value) is not int:
        raise SchemaError('Indexed identity requires an original integer ID or Ref')
    return value


def recover(s, run, edges):
    from collections import Counter, defaultdict
    from mipframe.ir.indexing.store import Table
    m = s.owner.model
    selected_rows = sorted(set(map(int,edges.columns['r'])))
    for tensor in s.records(run,'tensors'):
        t = tensor['id']
        coords = {v:(a,b) for v,_,_,a,b in s.coordinates[t].records()}
        bundles = [[],[]];slices = {}
        for r in selected_rows:
            support = m.support(r)
            positions = [coords[int(v)] for v in support if int(v) in coords]
            if not positions or len(support)!=len(positions)+1:continue
            axes = [axis for axis in (0,1) if len({p[axis] for p in positions})==1]
            if not axes:continue
            terms = list(m.iter_terms(r))
            inside = [(v,c) for v,c in terms if v in coords]
            anchor,scale = next((v,c) for v,c in terms if v not in coords)
            lo,hi = m.row_bounds(r)
            if scale<0:lo,hi = hi,lo
            lo,hi = (None if lo is None else (lo if scale==1 else F(lo)/scale)),(None if hi is None else (hi if scale==1 else F(hi)/scale))
            for axis in axes:bundles[axis].append((r,anchor,positions[0][axis],scale,lo,hi))
            slices[r] = [(coords[v][0],coords[v][1],(-c if scale==1 else -F(c)/scale)) for v,c in inside]
        for axis,bundle in enumerate(bundles):
            if not bundle:continue
            duplicates = sorted((anchor,k,n) for (anchor,k),n in Counter((x[1],x[2]) for x in bundle).items() if n>1)
            if duplicates:
                evidence(s,run,'duplicate row coordinates',tensor=t,axis=axis,counterexample=list(duplicates[0]));continue
            anchors = {v:p for p,v in enumerate(sorted({x[1] for x in bundle}))}
            np_,nk,nj = len(anchors),tensor['shape'][axis],tensor['shape'][1-axis]
            invariant = len(bundle)==np_*nk
            vectors = {};base = {}
            for r,anchor,k,scale,lo,hi in bundle:
                vector = tuple(sorted((point[1-axis],point[2]) for point in slices[r]))
                vectors[r] = vector
                if anchor in base and base[anchor]!=vector:invariant = False
                base[anchor] = vector
            o = ident('operator_',[t,axis])
            bindings = sorted(((r,anchors[anchor],k,anchor,scale,lo,hi) for r,anchor,k,scale,lo,hi in bundle),key=lambda x:(x[1],x[2]))
            s.bindings[o] = s.make_table(('r','p','k','anchor','scale','lo','hi'),bindings,numeric=('scale','lo','hi'))
            kernel = {}
            for r,p,k,*_ in bindings:
                for j,value in vectors[r]:kernel[p,-1 if invariant else k,j] = value
            s.kernels[o] = s.make_table(('p','k','j','value'),((*key,value) for key,value in sorted(kernel.items())),numeric=('value',))
            card = dict(id=o,tensor=t,fixed_axis=axis,rows=len(bundle),shape=[np_,nk],
                kernel_shape=[np_,nj] if invariant else [np_,nk,nj],axis_invariant=invariant,
                complete_row_product=len(bundle)==np_*nk,kind='broadcast_reduction' if invariant else 'indexed_reduction',
                ast=operator_ast(t,axis,invariant),verification='verified',
                formula=f"lower[p,k] <= y[p] - Sum_j A[{'p,j' if invariant else 'p,k,j'}] * X[{'k,j' if axis == 0 else 'j,k'}] <= upper[p,k]")
            errors = check_operator(s,card)
            if errors:raise MIPFrameError('Internal indexed operator expansion failure: '+str(errors))
            card['kernel_statistics'] = kernel_stats(s,card)
            s.put(run,'operators',o,card)


def check_operator(s, card, *, kernel=None, rows=None, coordinates=None, ast=None):
    """Independently expand exact sparse kernels and compare complete original rows."""
    from collections import defaultdict
    expected_ast = operator_ast(card['tensor'],card['fixed_axis'],card['axis_invariant'])
    if ast is not None and ast!=expected_ast:return [dict(reason='unsupported or changed indexed AST')]
    if card['ast']!=expected_ast:return [dict(reason='AST disagrees with expression')]
    m = s.owner.model;o = card['id'];axis = card['fixed_axis'];t = card['tensor']
    # Dense snapshot IDs can be checked without decoding names/objective/bounds
    # for every coordinate. Sparse overlay IDs retain the metadata lookup path.
    row_ids,var_ids=m.entity_ids('row'),m.entity_ids('var')
    def contains(ids,table,value):
        if isinstance(ids,range):return value in ids
        try:table[value];return True
        except (KeyError,IndexError):return False
    try:
        entries = s.kernels[o].records(exact=False) if kernel is None else (
            (x['p'],-1 if x.get('k') is None else x['k'],x['j'],F(x['value'])) for x in kernel)
        kernels = defaultdict(list);seen = set()
        for p,k,j,value in entries:
            if (any(type(i) is not int for i in (p,k,j)) or not 0<=p<card['shape'][0]
                or not 0<=j<card['kernel_shape'][-1] or (k!=-1 if card['axis_invariant'] else not 0<=k<card['shape'][1])):
                return [dict(reason='out-of-domain kernel entry')]
            if (p,k,j) in seen:raise ValueError('duplicate kernel entry')
            seen.add((p,k,j));kernels[p,k].append((j,value))
        coords = ((v,a,b) for v,_,_,a,b in s.coordinates[t].records()) if coordinates is None else (
            (_ref_id(s,x['variable'],'var'),x['axis0'],x['axis1']) for x in coordinates)
        mapping = {};variables = set()
        for v,a,b in coords:
            if v in variables or (a,b) in mapping:raise ValueError('duplicate coordinate')
            if any(type(i) is not int or i<0 for i in (v,a,b)):raise ValueError('invalid coordinate')
            if not contains(var_ids,m.variables,v):raise ValueError('variable outside snapshot')
            variables.add(v);mapping[a,b] = v
        bindings = s.bindings[o].records(exact=False) if rows is None else (
            (_ref_id(s,x.get('row',x.get('row_id')),'row'),x['p'],x['k'],_ref_id(s,x['anchor'],'var'),
             F(x['scale']),None if x['lower'] is None else F(x['lower']),None if x['upper'] is None else F(x['upper'])) for x in rows)
        row_seen,pk_seen = set(),set();pa,ap = {},{};errors = []
        for r,p,k,anchor,scale,lo,hi in bindings:
            if r in row_seen or (p,k) in pk_seen:raise ValueError('duplicate row or binding coordinate')
            row_seen.add(r);pk_seen.add((p,k))
            if pa.setdefault(p,anchor)!=anchor or ap.setdefault(anchor,p)!=p:return [dict(reason='anchor mapping mismatch')]
            if any(type(i) is not int for i in (r,p,k,anchor)) or r<0 or anchor<0 or not 0<=p<card['shape'][0] or not 0<=k<card['shape'][1]:
                errors.append(dict(reason='row or index outside domain',row_id=r));continue
            if not contains(row_ids,m.rows,r) or not contains(var_ids,m.variables,anchor):
                errors.append(dict(reason='row or index outside domain',row_id=r));continue
            if not scale:errors.append(dict(reason='zero scale',row_id=r));continue
            expected = {anchor:scale};missing = False
            for j,value in kernels[p,-1 if card['axis_invariant'] else k]:
                v = mapping.get((k,j) if axis==0 else (j,k))
                if v is None:
                    if value:errors.append(dict(reason='kernel on missing coordinate',row_id=r));missing = True
                    continue
                expected[v] = expected.get(v,0)-value*scale
            if missing:continue
            expected = {v:c for v,c in expected.items() if c}
            if dict(m.iter_terms(r))!=expected:errors.append(dict(reason='coefficient mismatch',row_id=r))
            low,high = (None if lo is None else lo*scale),(None if hi is None else hi*scale)
            if scale<0:low,high = high,low
            if (low,high)!=m.row_bounds(r):errors.append(dict(reason='bound mismatch',row_id=r))
        if len(row_seen)!=card['rows']:errors.append(dict(reason='operator row coverage mismatch'))
        return errors
    except (ValueError,KeyError,TypeError,IndexError,ZeroDivisionError) as error:
        return [dict(reason='invalid candidate',detail=str(error))]


def kernel_stats(s, card):
    from collections import defaultdict
    from mipframe.ir.indexing.store import Table
    o = card['id'];np_ = card['shape'][0];nj = card['kernel_shape'][-1]
    total = np_*nj*(1 if card['axis_invariant'] else card['shape'][1])
    entries = list(s.kernels[o].records(exact=False));values = [v for _,_,_,v in entries]
    if len(values)<total:values.append(F(0))
    stats = dict(integer=all(v.denominator==1 for v in values),min=str(min(values,default=0)),
        max=str(max(values,default=0)),zeros=total-len(entries),stored_nonzeros=len(entries),implicit_zero=True)
    s.alignments.pop(o,None)
    if not card['axis_invariant'] or np_!=nj:return stats
    vectors = defaultdict(dict)
    for p,k,j,value in entries:vectors[p][j] = value
    alignment = {};used = set()
    for p in range(np_):
        vector = vectors[p];mx = max(vector.values(),default=None)
        missing = nj-len(vector)
        if missing:mx = max(mx,F(0)) if mx is not None else F(0)
        maxima = [j for j,v in vector.items() if v==mx]
        ties = len(maxima)+(missing if mx==0 else 0)
        if ties!=1:return stats
        which = maxima[0] if maxima else next(j for j in range(nj) if j not in vector)
        if which in used:return stats
        alignment[p] = which;used.add(which)
    reverse = {j:p for p,j in alignment.items()}
    symmetric = all(value==vectors[reverse[j]].get(alignment[p],0) for p,_,j,value in entries)
    diagonal = [vectors[p].get(j,F(0)) for p,j in alignment.items()]
    stats['alignment'] = dict(basis='unique row maximum bijection',symmetric_after_mapping=symmetric,
        diagonal_constant=str(diagonal[0]) if diagonal and len(set(diagonal))==1 else None)
    s.alignments[o] = s.make_table(('p','j'),sorted(alignment.items()))
    return stats
