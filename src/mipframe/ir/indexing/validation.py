"""Independent exact replay of indexed hypotheses on sparse snapshot arrays."""
from mipframe.ir.indexing.views import report
from mipframe.ir.indexing.operators import check_operator, _ref_id, kernel_stats


def support(s, r):
    m = s.owner.model
    if type(r) is not int or r<0:raise ValueError('invalid row')
    try:m.rows[r]
    except (KeyError,IndexError):raise ValueError('row outside model') from None
    return set(map(int,m.support(r)))


def axis_members(s, records):
    members = {};size = 0
    for i,record in enumerate(records):
        r = record['row_id'];actual = support(s,r);count = 0
        for v in record['variables']:
            if type(v) is not int or v not in actual:raise ValueError('member absent from original row')
            if v in members:raise ValueError('overlapping membership')
            members[v] = (r,i);count += 1
        if not count:raise ValueError('empty declared axis member')
        size += 1
    if not size:raise ValueError('empty partition')
    return members,size


def coordinates(records):
    values = {};seen = set()
    for x in records:
        v,a,b = x['variable'],x['axis0'],x['axis1']
        if any(type(i) is not int or i<0 for i in (v,a,b)):raise ValueError('invalid coordinate')
        if v in values or (a,b) in seen:raise ValueError('duplicate coordinate')
        values[v] = (a,b);seen.add((a,b))
    return values


def validate(result, spec):
    s = result._store;s.guard();kind = spec.get('kind');errors = []
    if 'analysis' in spec:raise ValueError('Legacy analysis handles are not accepted')
    if kind=='indexed_operator':
        candidate = spec.get('candidate')
        card = candidate.operator if candidate else result._card('operators',spec['id'])
        errors = check_operator(s,card,kernel=candidate.kernel if candidate else spec.get('kernel'),
            rows=candidate.rows if candidate else spec.get('rows'),
            coordinates=candidate.coordinates if candidate else None,ast=spec.get('ast'))
        return report(s,errors,operation=kind,checked_rows=card['rows'])
    if kind=='cartesian' and 'partitions' in spec:return explicit_cartesian(result,spec)
    if kind=='cartesian':
        t = result._card('tensors',spec['id']);table = s.coordinates[t['id']]
        expected = {v:(a,b) for v,i,j,a,b in table.records()}
        try:
            if len(set(expected.values()))!=len(expected):raise ValueError('duplicate coordinate')
            if 'coordinates' in spec and coordinates(spec['coordinates'])!=expected:
                raise ValueError('coordinate universe or mapping mismatch')
            # Membership uses sorted CSR support without rebuilding a row set
            # for every coordinate of a high-degree row.
            import numpy as np
            for v,i,j,a,b in table.records():
                for r in (i,j):
                    if r<0:raise ValueError('invalid row')
                    values=s.owner.model.support(r)
                    pos=int(np.searchsorted(values,v))
                    if pos==len(values) or int(values[pos])!=v:
                        errors.append(dict(reason='coordinate absent from raw row',row_id=r,variable=v))
        except (ValueError,KeyError,TypeError) as error:errors.append(dict(reason=str(error)))
        return report(s,errors,operation=kind,complete_product=t['complete_product'],checked_variables=len(table),missing_cells=t['missing_cells'])
    if kind=='indexed_partition':
        records = spec.get('sets')
        if records is None:
            from itertools import groupby
            card = result._card('partitions',spec['id'])
            records = (dict(row_id=r,variables=[v for _,v in entries])
                for r,entries in groupby(s.members[card['g']].records(),key=lambda x:x[0]))
            universe = set(map(int,s.domains[card['vars_domain']]))
        else:universe = set(spec['variables']) if 'variables' in spec else None
        n = 0
        try:
            members,n = axis_members(s,records)
            if universe is not None and set(members)!=universe:raise ValueError('universe mismatch')
        except (ValueError,KeyError,TypeError) as error:errors.append(dict(reason=str(error)))
        return report(s,errors,operation=kind,checked_sets=n)
    if kind=='row_bundle':
        card = result._card('operators',spec['id'])
        return report(s,check_operator(s,card,rows=spec.get('rows')),operation=kind,checked_rows=card['rows'])
    if kind=='axis_alignment':
        op = result._card('operators',spec['id'])
        # Derive the bijection again from exact values; labels are not evidence.
        stats = kernel_stats(s,op)
        if 'alignment' not in stats:return report(s,[dict(reason='no unique maximum bijection')],operation=kind)
        if 'mapping' in spec:
            try:
                mapping = {};seen = set()
                for x in spec['mapping']:
                    p,j = x['p'],x['j']
                    if type(p) is not int or type(j) is not int or p in mapping or j in seen:raise ValueError('alignment is not bijective')
                    mapping[p] = j;seen.add(j)
                if mapping!=dict(s.alignments[op['id']].records()):raise ValueError('alignment mismatch')
            except (ValueError,KeyError,TypeError) as error:errors.append(dict(reason=str(error)))
        return report(s,errors,operation=kind)
    raise ValueError('Unknown indexed validation kind')


def explicit_cartesian(result, spec):
    s = result._store;parts = spec['partitions']
    if len(parts)!=2:raise ValueError('Cartesian validation requires exactly two partitions')
    sizes = [0,0];errors = [];mapping = {}
    try:
        axes = []
        for side,part in enumerate(parts):
            members,sizes[side] = axis_members(s,part);axes.append(members)
        if axes[0].keys()!=axes[1].keys():raise ValueError('axis universes differ')
        def normalized():
            for x in spec['coordinates']:
                if 'var' in x or 'source_id' in x:
                    v = _ref_id(s,x['var'],'var') if 'var' in x else x['source_id']
                    a,b = _ref_id(s,x['i'],'row'),_ref_id(s,x['j'],'row')
                    if v not in axes[0] or v not in axes[1] or axes[0][v][0]!=a or axes[1][v][0]!=b:
                        raise ValueError('coordinate disagrees with declared membership')
                    a,b = axes[0][v][1],axes[1][v][1]
                else:v,a,b = x['variable'],x['axis0'],x['axis1']
                yield dict(variable=v,axis0=a,axis1=b)
        mapping = coordinates(normalized())
        if mapping!={v:(axes[0][v][1],axes[1][v][1]) for v in axes[0]}:
            raise ValueError('coordinate universe or mapping mismatch')
    except (ValueError,KeyError,TypeError) as error:errors.append(dict(reason='invalid Cartesian hypothesis',detail=str(error)))
    n = len(mapping)
    return report(s,errors,operation='cartesian',complete_product=not errors and n==sizes[0]*sizes[1],
        checked_variables=n,missing_cells=sizes[0]*sizes[1]-n)


def recipe_spec(value):
    from mipframe.frame import Frame, Record
    from mipframe.frame.schema import encode
    if isinstance(value,Frame):return {'$frame':value.to_spec()}
    if isinstance(value,Record):value=value.to_record()
    if isinstance(value,dict):return {k:recipe_spec(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [recipe_spec(v) for v in value]
    return encode(value)


def restore_spec(value, owner):
    from mipframe.frame.serialization import frame_from_spec
    from mipframe.frame.schema import decode_tree
    return decode_tree(value,frame_decoder=lambda spec:frame_from_spec(spec,models={owner.uid:owner}))
