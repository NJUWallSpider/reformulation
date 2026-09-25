"""Independent expansion and exact comparison. No solver and no asserts."""
from fractions import Fraction as F
from .numbers import LPIRError,decimal
from .store import sha256

def expand_family(m,family):
    if family['kind']=='temporal':
        for inst in family['instances']:
            source,target,sw,arc=inst['variables']
            q=m.num(family['q']['constant'] if 'constant' in family['q'] else inst['q'])
            negm=m.num(family['negative_M']['constant'] if 'constant' in family['negative_M'] else inst['negative_M'])
            yield inst['row'],{source:F(-1),target:F(1),sw:q,arc:negm},q+negm,None
    elif family['kind']=='linear_slots':
        for inst in family['instances']:
            params=iter(inst['parameters']);a={}
            if len(inst['variables'])!=len(family['coefficients']):raise LPIRError('Template slot count mismatch')
            for v,desc in zip(inst['variables'],family['coefficients']):
                cid=desc['constant'] if 'constant' in desc else next(params)
                if v in a:raise LPIRError('Duplicate variable binding in template instance')
                a[v]=m.num(cid)
            if next(params,None) is not None:raise LPIRError('Unused template parameters')
            lo=m.num(family['lower']['constant'] if 'constant' in family['lower'] else inst['lower'])
            hi=m.num(family['upper']['constant'] if 'constant' in family['upper'] else inst['upper'])
            yield inst['row'],a,lo,hi
    else:raise LPIRError(f'Unknown template kind {family["kind"]}')

def expanded(m):
    for family in m.compressed['families']:yield from expand_family(m,family)
    for rec in m.compressed['residual']:
        if len({v for v,c in rec['terms']})!=len(rec['terms']):raise LPIRError('Duplicate variable in residual')
        yield rec['row'],{v:m.num(c) for v,c in rec['terms']},m.num(rec['lower']),m.num(rec['upper'])

def verify(m):
    for name,expected in m.manifest['base_sha256'].items():
        if sha256(m.path/name)!=expected:raise LPIRError(f'Base artifact checksum mismatch: {name}')
    c=m.compressed
    for key in ('model_version','sense','objective_constant'):
        if c[key]!=m.manifest[key]:raise LPIRError(f'Model metadata mismatch: {key}')
    if c['variables']!=[list(v) for v in m.variables]:raise LPIRError('Variable domains/objective/provenance mismatch')
    if c['row_metadata']!=[[r[0],r[1],r[4]] for r in m.rows]:raise LPIRError('Row metadata mismatch')
    seen=set();nonzeros=0
    for r,a,lo,hi in expanded(m):
        if r in seen:raise LPIRError(f'Row {r} reconstructed twice')
        if not 0<=r<len(m.rows):raise LPIRError(f'Invalid reconstructed row {r}')
        seen.add(r);nonzeros+=len(a)
        if a!=m.row(r) or lo!=m.num(m.rows[r][2]) or hi!=m.num(m.rows[r][3]):
            raise LPIRError(f'Exact reconstruction mismatch at row {r} ({m.rows[r][1]})')
    if seen!=set(range(len(m.rows))):raise LPIRError('Missing reconstructed rows')
    return {'status':'PASS','exactness':'exact','rows_verified':len(seen),'nonzeros_verified':nonzeros,'coverage':1.0,'objective_and_domains':'PASS','model_version':m.manifest['model_version']}

def expression(terms,names):
    parts=[]
    for v,c in terms.items():
        if c:parts.append(('+' if c>=0 else '-')+decimal(abs(c))+' '+names[v])
    return ' '.join(parts) or '0'

def write_model(m,path,rename_vars=None,rename_rows=None,row_order=None,reverse_terms=False):
    verify(m)
    names=rename_vars or {v[0]:v[1] for v in m.variables}
    def emit(f,r,a,lo,hi):
        label=rename_rows[r] if rename_rows else m.rows[r][1]
        if reverse_terms:a=dict(reversed(list(a.items())))
        exp=expression(a,names)
        if lo is None and hi is None:
            # An entirely free row has no ordinary finite LP comparison.
            raise LPIRError('Cannot emit a row with no finite side')
        body=f'{exp} = {decimal(lo)}' if lo==hi else f'{exp} <= {decimal(hi)}' if lo is None else f'{exp} >= {decimal(lo)}' if hi is None else f'{decimal(lo)} <= {exp} <= {decimal(hi)}'
        f.write(f' {label}: {body}\n')
    with open(path,'w') as f:
        f.write('Minimize\n' if m.compressed['sense']=='min' else 'Maximize\n')
        obj={v[0]:m.num(v[5]) for v in m.compressed['variables'] if m.num(v[5])}
        if reverse_terms:obj=dict(reversed(list(obj.items())))
        constant=m.num(m.compressed['objective_constant'])
        exp=expression(obj,names)
        if constant:exp+=(' +' if constant>0 else ' -')+decimal(abs(constant))
        f.write(' obj: '+exp+'\nSubject To\n')
        if row_order is None:
            for rec in expanded(m):emit(f,*rec)
        else:
            rr={r:(a,lo,hi) for r,a,lo,hi in expanded(m)}
            for r in row_order:emit(f,r,*rr[r])
        f.write('Bounds\n')
        for v in m.compressed['variables']:
            name=names[v[0]];lo=m.num(v[3]);hi=m.num(v[4])
            if lo is None and hi is None:f.write(f' {name} free\n')
            elif lo is None:f.write(f' -inf <= {name} <= {decimal(hi)}\n')
            elif hi is None:f.write(f' {decimal(lo)} <= {name}\n')
            else:f.write(f' {decimal(lo)} <= {name} <= {decimal(hi)}\n')
        for kind,title in [('B','Binaries'),('I','Generals')]:
            variables=[names[v[0]] for v in m.compressed['variables'] if v[2]==kind]
            if variables:
                f.write(title+'\n')
                for start in range(0,len(variables),20):f.write(' '+' '.join(variables[start:start+20])+'\n')
        f.write('End\n')
