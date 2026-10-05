"""Factual, traceable model maps over exact numerical snapshots.

The default map is a bounded numerical scan. Structural summaries are explicit
and lazy; default rendering and serialization never run analyses.
"""

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from decimal import localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import numpy as np

from mipframe.frame.entity import _text
from mipframe.ops._kernels.union_find import DSU
from mipframe.options import options
from mipframe.frame.row import Formula
from mipframe.ir.display import canonical_pattern_index

SCHEMA_VERSION = "0.4"
from mipframe.store.scan import NEAR_ZERO, weighted_profile as _profile


def _fraction(n, total):
    return Fraction(n, total) if total else Fraction(0)


def _degree_profile(ptr, members):
    counts = Counter(int(ptr[i + 1]) - int(ptr[i]) for i in members)
    return _profile(sorted(counts.items()))


def _tokens(signature):
    if not signature.depth:
        return [], []

    def pattern_record(pattern):
        result = []
        for coefficient, color, count in pattern:
            multiplicity = (
                dict(kind="constant", value=count.min)
                if count.min == count.max
                else dict(
                    kind="varying", min=count.min, max=count.max, mode_id=count.mode
                )
            )
            result.append(
                dict(
                    sign="+" if coefficient > 0 else "-",
                    var_role=f"V{color}",
                    var_role_depth=signature.depth - 1,
                    multiplicity=multiplicity,
                )
            )
        return result

    orientations = [pattern_record(p) for p in signature.patterns]
    return orientations[canonical_pattern_index(signature)], orientations


def _signature_id(signature):
    # Model/config scoped identity, stable across save/load. Colors alone are
    # intentionally not advertised as globally comparable structural hashes.
    digest = hashlib.sha256(signature.key().encode()).hexdigest()
    return f"{'rf' if signature.kind == 'row' else 'vr'}:{signature.depth}:{digest}"


def _varying_roles(provider, entry, primary):
    """Observe variation without building per-member coefficient histograms."""
    result = provider.result
    store, m, config = result._store, provider.owner.model, result._config
    signature = provider.signature(entry)
    members = provider.members(entry)

    def histogram():
        return [r['role'] for r in provider.numeric(entry, 'parameters', bases=('canonical',), filters={'quantity':('coefficient',)})
                if r['basis']=='canonical' and r['orientation']==canonical_pattern_index(signature)
                and r['quantity']=='coefficient' and r['varying']]

    if (hasattr(m,"f") or signature.mode != 'template' or not provider.depth
            or len(primary) > min(32, store.memory // 1024)):
        return histogram()
    pos, neg = (store.array(config, kind, provider.depth) for kind in ('pos', 'neg'))
    # Numerically tied dual orientations use the histogram's exact tie-break.
    for row in members.ids:
        if neg[row] >= 0 and pos[row] == neg[row]:
            return histogram()
    colors = store.array(config, 'var', provider.depth - 1)
    edge = store.array(config, 'edge', -1)
    orientation = canonical_pattern_index(signature)
    roles = {(1 if r['sign'] == '+' else -1, int(r['var_role'][1:])): None
             for r in primary}
    varying = set()
    for row in members.ids:
        reverse = -1 if neg[row] >= 0 and pos[row] > neg[row] else 1
        if orientation:
            reverse = -reverse
        for start in range(int(m.row_ptr[row]), int(m.row_ptr[row + 1]), store.block):
            stop = min(start + store.block, int(m.row_ptr[row + 1]))
            neighbors = colors[m.col_idx[start:stop]]
            signs = edge[start:stop] * reverse
            for role in roles.keys() - varying:
                sign, neighbor = role
                mask = (neighbors == neighbor) & ((signs > 0) if sign > 0 else (signs < 0))
                for ident in np.unique(m.coeff_id[start:stop][mask]):
                    value = abs(m.num(int(ident)))
                    if roles[role] is None:
                        roles[role] = value
                    elif roles[role] != value:
                        varying.add(role)
                        break
        if len(varying) == len(roles):
            break
    return [f"{'+' if sign > 0 else '-'}V{neighbor}" for sign, neighbor in sorted(varying)]


class _Objective(Mapping):
    """Cheap objective mapping; legacy role fields require an explicit lookup."""
    def __init__(self, ir): self.ir = ir
    def __getitem__(self, key):
        self.ir._guard()
        if key in ('role_support', 'role_depth'):
            return deepcopy(self.ir._objective_roles()[key])
        return deepcopy(self.ir._data['objective'][key])
    def __iter__(self):
        self.ir._guard()
        for key in tuple(self.ir._data['objective']):
            self.ir._guard()
            yield key
    def __len__(self):
        self.ir._guard()
        return len(self.ir._data['objective'])
    def __contains__(self, key):
        self.ir._guard()
        return key in self.ir._data['objective']
    def __repr__(self): return repr(dict(self))


class CompressedIR(Mapping):
    """
    Cached level-0 scan with explicit, independently cached analyses.

    Default iteration and exports contain only schema 0.4 scan data. Legacy
    structure/topology/indexed lookups are supported but are not default keys.

    MIPFRAME HELP BEGIN
    Bounded factual model map prepared by read/load.

    Full contract: mf.help("api.CompressedIR")
    MIPFRAME HELP END
    """
    def __init__(self, owner, *, depth=2, memory_mb=128):
        if type(depth) is not int or depth < 0:
            raise ValueError('depth must be a nonnegative integer')
        self._owner, self._depth, self._memory_mb = owner, depth, memory_mb
        self._family_frames, self._analyses, self._directories = {}, {}, {}
        self._signature_result = None
        self._data = self._build()

    def _guard(self): self._owner.model

    @property
    def _result(self):
        self._guard()
        if self._signature_result is None:
            from mipframe.ir.signatures import refinement
            self._signature_result = refinement(self._owner, depth=self._depth,
                mode='template', memory_mb=self._memory_mb)
        return self._signature_result

    def _release(self):
        self._data.clear()
        self._family_frames.clear()
        self._analyses.clear()
        self._directories.clear()
        self._signature_result = None

    def __getitem__(self, key):
        self._guard()
        if key == 'objective': return _Objective(self)
        if key == 'structure':
            if key not in self._analyses: self._analyses[key] = self._build_structure()
            return deepcopy(self._analyses[key])
        if key == 'topology':
            if key not in self._analyses: self._analyses[key] = self._topology()
            return deepcopy(self._analyses[key])
        if key == 'indexed':
            self._owner.indexed(memory_mb=self._memory_mb)
            from mipframe.ir.indexing.views import ir_summary
            return ir_summary(self._owner)
        return deepcopy(self._data[key])

    def __iter__(self):
        self._guard()
        for key in tuple(self._data):
            self._guard()
            yield key

    def __len__(self):
        self._guard()
        return len(self._data)

    def __contains__(self, key):
        self._guard()
        return key in self._data

    def __getattr__(self, key):
        if key in {'schema_version','model','size','matrix','domains','numerics',
                   'objective','topology','structure','attention','indexed',
                   'local_row_shapes','names','presolve_cues','scan'}:
            return self[key]
        raise AttributeError(key)

    def to_dict(self, *, include_analysis=False):
        """
        Export a detached scan; explicitly opt into expensive analyses.

        MIPFRAME HELP BEGIN
        Export a detached scan; include_analysis=True explicitly computes analyses.

        Full contract: mf.help("api.CompressedIR.to_dict")
        MIPFRAME HELP END
        """
        self._guard()
        if type(include_analysis) is not bool: raise TypeError('include_analysis must be bool')
        data = deepcopy(self._data)
        if include_analysis:
            data.update(structure=self['structure'],topology=self['topology'],indexed=self['indexed'])
            data['objective'].update(self._objective_roles())
        return data

    def to_json(self, *, indent=2, include_analysis=False):
        """
        Export scan JSON; exact rational values are strings.

        MIPFRAME HELP BEGIN
        Export scan JSON; include_analysis=True explicitly computes analyses.

        Full contract: mf.help("api.CompressedIR.to_json")
        MIPFRAME HELP END
        """
        return json.dumps(self.to_dict(include_analysis=include_analysis),
            default=lambda x:_rational_text(x) if isinstance(x,Fraction) else x,
            ensure_ascii=False,indent=indent,allow_nan=False)

    def _build(self):
        from mipframe.store.scan import Scan
        m = self._owner.model
        data = getattr(m, '_scan_data', None)
        if data is None:
            m.prepare_scan(self._memory_mb)
            data=m._scan_data
        # Ownership transfers to the IR: no second copy of all scan candidates.
        m._scan_data = None
        data.update(schema_version=SCHEMA_VERSION,model=dict(
            name=Path(m.manifest.get('source') or m.path).stem,
            model_version=m.manifest['model_version'],sense=m.manifest['sense']))
        data['objective'].update(sense=m.manifest['sense'],constant=m.num(m.manifest['objective_constant']))
        data['attention'] = [dict(kind='fact',code=k,count=v) for k,v in data['presolve_cues'].items() if v]
        return data

    def _objective_roles(self):
        self._guard()
        if 'objective_roles' not in self._analyses:
            from mipframe.ir.signatures import refinement
            depth = max(0,self._depth-1)
            result = refinement(self._owner,depth=depth,mode='template',memory_mb=self._memory_mb)
            colors = result._store.array(result._config,'var',depth)
            m = self._owner.model
            support = Counter(int(colors[v[0]]) for v in m.variables if m.num(v[5]))
            total = self._data['objective']['nonzeros']
            self._analyses['objective_roles'] = dict(role_depth=depth,role_support=[
                dict(id=f'V{color}',depth=depth,count=count,fraction=_fraction(count,total))
                for color,count in sorted(support.items(),key=lambda x:(-x[1],x[0]))])
        return deepcopy(self._analyses['objective_roles'])

    def _build_structure(self):
        result,depth = self._result,self._depth
        overview=[]
        for d in range(depth+1):
            nr,nv=len(self._family_frame('row',d)),len(self._family_frame('var',d))
            overview.append(dict(depth=d,row_roles=nr,var_roles=nv,
                delta_rows=None if not d else nr-overview[-1]['row_roles'],
                delta_vars=None if not d else nv-overview[-1]['var_roles']))
        return dict(mode='template',depth=depth,variable_role_depth=max(0,depth-1),config=result.config,
            overview=overview,variable_roles=self._families('var',max(0,depth-1)),
            row_families=self._families('row',depth),
            refinements=[self._refinement(kind,d) for d in range(1,depth+1) for kind in ('row','var')])

    def row_shapes(self):
        """
        Compute the full exact local row-shape directory as a navigable Frame.

        MIPFRAME HELP BEGIN
        Compute a full exact local row-shape Frame.

        Full contract: mf.help("api.CompressedIR.row_shapes")
        MIPFRAME HELP END
        """
        return self._directory('row_shapes')

    def name_templates(self, kind='row'):
        """
        Compute full exact lexical templates and integer slots as Frames.

        MIPFRAME HELP BEGIN
        Compute a full exact lexical-template Frame.

        Full contract: mf.help("api.CompressedIR.name_templates")
        MIPFRAME HELP END
        """
        if kind not in ('row','var'): raise ValueError('kind must be row or var')
        return self._directory('names_'+kind)

    def _directory(self, kind):
        self._guard()
        if kind not in self._directories:
            from mipframe.ir.scan_views import Directory
            self._directories[kind] = Directory(self._owner,kind,self._memory_mb).directory()
        return self._directories[kind]

    def _family_frame(self, kind, depth):
        self._guard()
        from mipframe.analysis import _Families
        key = kind, depth
        if key not in self._family_frames:
            entities = self._owner.rows if kind == 'row' else self._owner.vars
            self._family_frames[key] = _Families(entities,'template',depth,self._result).directory()
        return self._family_frames[key]

    def _families(self, kind, depth):
        from mipframe.frame.executor import Context
        frame = self._family_frame(kind,depth)
        provider = frame._query.navigation[0]
        m = self._owner.model
        total = self._owner.shape[0 if kind=='row' else 1]
        summaries = []
        ctx = Context((self._owner,), None)
        try:
            for entry in provider.entries(ctx):
                sig = provider.signature(entry)
                values = provider.record(entry, ('parent','parent_depth'))
                record = dict(id=entry['id'],signature_id=_signature_id(sig),count=entry['count'],
                    fraction=_fraction(entry['count'],total),depth=depth,**values)
                degree = _profile(sorted(Counter(m.degree(kind,int(i)) for i in entry['ids']).items()))
                if kind=='var':
                    typ,bounds,objective=sig.base
                    record.update(base=dict(type=typ,bounds=bounds,objective=objective),degree=degree)
                else:
                    primary,orientations=_tokens(sig)
                    record.update(relation=sig.base[0],rhs_class=sig.base[1],nnz=degree,
                        structure=primary,structure_available=bool(depth),orientations=orientations,
                        sign_symmetric=sig.base==('=','zero'),
                        parameters=dict(varying_coefficient_roles=_varying_roles(provider,entry,primary)))
                summaries.append(record)
        finally: ctx.close()
        return summaries

    def _refinement(self, kind, depth):
        from mipframe.analysis import _transitions
        from mipframe.ir.signatures import _Signature
        delta = _transitions(self._owner,depth-1,depth,kind=kind)
        parents = {}
        unchanged = dict(families=0,members=0)
        for row in delta:
            if row.unchanged:
                unchanged['families']+=1;unchanged['members']+=row.count
                continue
            split = parents.setdefault(row.parent,dict(parent=row.parent,parent_depth=depth-1,count=0,children=[]))
            split['count']+=row.count
            entry=dict(id=row.child,depth=depth,count=row.count)
            if kind=='row':
                partition=self._result.partition(kind,depth)
                first=partition.find('position',int(row.child[1:]))[3]
                entry['structure']=_tokens(_Signature(self._result,kind,first,depth))[0]
            split['children'].append(entry)
        return dict(kind=kind,from_depth=depth-1,to_depth=depth,
            before_count=self._result.partition(kind,depth-1).total,
            after_count=self._result.partition(kind,depth).total,
            split_count=len(parents),splits=list(parents.values()),unchanged=unchanged)

    def _topology(self):
        m = self._owner.model
        nr,nv=self._owner.shape
        row_ids=m.entity_ids('row');var_ids=m.entity_ids('var')
        row_capacity=m.capacity('row');var_capacity=m.capacity('var')
        from mipframe.data.runtime import WorkContext
        from types import SimpleNamespace
        with WorkContext(self._memory_mb) as work:
            dsu=DSU(row_capacity+var_capacity,SimpleNamespace(work=work,paths=set()))
            for r in row_ids:
                for neighbors,_ in m.sparse.blocks('row',int(r),max(1,work.memory//64)):
                    for v in neighbors:dsu.union(int(r),row_capacity+int(v))
            counts={}
            for axis,ids,offset in (('row',row_ids,0),('var',var_ids,row_capacity)):
                for ident in ids:
                    i=offset+int(ident);root=dsu.find(i)
                    stats=counts.setdefault(root,[0,0,0,i])
                    if axis=='row':stats[0]+=1;stats[2]+=m.degree('row',int(ident))
                    else:stats[1]+=1
        ordered = sorted(
            counts.items(),
            key=lambda item: (-item[1][2], -sum(item[1][:2]), item[1][3]),
        )
        components = [
            dict(
                id=f"C{i}",
                rows=a,
                variables=b,
                nonzeros=z,
                row_fraction=_fraction(a, nr),
                var_fraction=_fraction(b, nv),
                nonzero_fraction=_fraction(z, self._owner.nnz),
            )
            for i, (_, (a, b, z, _)) in enumerate(ordered)
        ]
        return dict(
            connected_components=len(components),
            isolated_rows=sum(
                m.degree('row',int(i))==0 for i in row_ids
            ),
            isolated_variables=sum(
                m.degree('var',int(i))==0 for i in var_ids
            ),
            largest_component=components[0] if components else None,
            components=components,
            convention="bipartite constraint-variable graph; isolated vertices count as components; ranked by nonzeros, then vertices",
        )

    def show(self):
        """
        Render bounded cached scan data without triggering analysis.

        MIPFRAME HELP BEGIN
        Render a bounded factual model overview.

        Full contract: mf.help("api.CompressedIR.show")
        MIPFRAME HELP END
        """
        self._guard()
        return Formula(_render_scan(self._data))

    def __repr__(self):
        return str(self.show())

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


def _rational_text(value):
    """Exact JSON text even when a derived ratio exceeds Python's digit guard."""
    try:
        return str(value)
    except ValueError:
        numerator = format(Decimal(value.numerator), "f")
        return numerator if value.denominator == 1 else numerator + "/" + format(Decimal(value.denominator), "f")


def _number(value):
    if value is None:
        return "—"
    if isinstance(value, (int, Fraction)) and max(value.numerator.bit_length(), value.denominator.bit_length()) > 128:
        return _approx(value)
    text = _text(value)
    if len(text) > 32:
        return _approx(value)
    return text


def _approx(value):
    """Bounded scientific notation without float overflow or underflow."""
    value = Fraction(value)
    with localcontext() as context:
        context.prec = 3
        number = Decimal(value.numerator) / Decimal(value.denominator)
        return format(number, ".2e").replace("E", "e")


def _render_scan(data):
    size,domains,matrix = data['size'],data['domains'],data['matrix']
    numeric,obj = data['numerics'],data['objective']
    limits = options.ir
    def short(value,limit=100):
        text=str(value).replace('\n',' ').replace('\r',' ')
        return text if len(text)<=limit else text[:limit-1]+'…'
    def frequency(e):
        return str(e['count']) if not e.get('error') else f"{e['count_lower']}..{e['count']} (estimate)"
    def common(summary):
        return ', '.join(f"{_number(e['value'])} × {frequency(e)}" for e in summary['items'][:limits.max_values]) or ('none' if not summary['total_count'] else '(not shown)')
    lines=['MIPFrame scan · '+short(data['model']['name'],100),'Model',
        f"  {size['variables']:,} vars · {size['rows']:,} rows · {size['nonzeros']:,} A-nnz · {obj['sense']}",
        f"  density: {_approx(matrix['density']) if matrix['density'] else '0'}",
        '  domains: '+' · '.join(f'{n:,} {k}' for k,n in domains['variable_types'].items()),
        '  rows: '+' · '.join(f'{n:,} {k}' for k,n in domains['row_senses'].items()),
        'Bounds', '  '+' · '.join(f'{k}: {n:,}' for k,n in domains['bounds'].items()),
        f"  [0,1]: {domains['unit_interval']:,}"]
    for title,key in [('Rows','row_nnz'),('Columns','column_nnz')]:
        p=matrix[key]
        lines += [title,f"  nnz min {_number(p['min'])} · p50 {_number(p['median'])} · p90 {_number(p['p90'])} · max {_number(p['max'])}"]
        lines.append('  nnz histogram: '+', '.join(f"{e['value']} × {e['count']:,}" for e in p['histogram']['items'][:limits.max_histogram]))
    a=numeric['absolute_coefficients'];sg=numeric['sign_counts'];rhs=numeric['nonzero_rhs']
    lines += ['Numerics',f"  A signs +{sg['positive']:,} / -{sg['negative']:,}; |A| {_number(a['min'])} .. {_number(a['max'])}",
        '  common coefficients: '+common(numeric['common_coefficients']),
        '  coefficient dynamic range: '+_number(numeric['coefficient_dynamic_range']),
        f"  nonzero RHS: {rhs['count']:,}; "+common(numeric['common_rhs']),
        'Objective',f"  nonzeros: {obj['nonzeros']:,} / {size['variables']:,}; constant: {_number(obj['constant'])}",
        f"  signs +{obj['sign_counts']['positive']:,} / -{obj['sign_counts']['negative']:,}; range {_number(obj['coefficient_profile']['min'])} .. {_number(obj['coefficient_profile']['max'])}",
        '  common coefficients: '+common(obj['common_coefficients']),
        'Presolve cues','  '+' · '.join(f'{k}: {v:,}' for k,v in data['presolve_cues'].items())]
    shapes=data['local_row_shapes'];items=shapes['items'][:limits.max_local_shapes]
    lines += ['Local row shapes (raw coefficients; no neighboring roles)']
    for i,e in enumerate(items):
        rhs = f"[{_number(e['lb'])}, {_number(e['ub'])}]" if e['sense']=='range' else _number(e['lb'] if e['sense']=='>=' else e['ub'])
        coefficients=', '.join(f"{_number(c['value'])}×{c['count']}" for c in e['coefficients'][:6])
        if len(e['coefficients'])>6 or not e['coefficients_complete']: coefficients+=', …'
        sample=e['examples'][0] if e['examples'] else None
        lines.append(f"  S{i} count={frequency(e)} · {e['sense']} {rhs} · nnz={e['nnz']} · {{{coefficients}}}"+(' [fingerprint candidate]' if e['candidate'] else ''))
        if sample: lines.append(f"    example: {short(sample['name'],60)} → m.rows.loc[{sample['id']}].terms")
    total=shapes['total_groups']
    lines.append(f"  showing {len(items)} of {total if total is not None else 'unknown (bounded candidates)'} → m.ir.row_shapes()")
    lines.append('Names (lexical hypothesis; not structural evidence)')
    for axis,kind in [('rows','row'),('vars','var')]:
        summary=data['names'][axis];items=summary['items'][:limits.max_name_templates]
        for e in items:
            slots='; '.join(f"{s['index']}: {'observed ' if not s['complete'] else ''}{s['min']}..{s['max']}, distinct "+(str(s['distinct']) if s['distinct_exact'] else '>='+str(s['distinct_lower_bound'])) for s in e['slots'][:3])
            sample=short(e['examples'][0]['name'],45) if e['examples'] else ''
            lines.append(f"  {axis}: {short(e['template'],65)} × {frequency(e)} · {e['slot_count']} slots · {short(slots,100)} · e.g. {sample}")
        total=summary['total_groups']
        lines.append(f"  {axis}: showing {len(items)} of {total if total is not None else 'unknown'}; unprofiled {summary['unprofiled']} → m.ir.name_templates('{kind}')")
    lines.append('Deeper queries: m.row_families · m.ir.topology · m.indexed() (computed on request)')
    text='\n'.join(lines)
    if len(text)>limits.max_chars:
        footer='\n… display truncated; exact detail: m.ir.row_shapes(), m.ir.name_templates()'
        text=text[:max(0,limits.max_chars-len(footer))]+footer
        text=text[:limits.max_chars]
    return text
