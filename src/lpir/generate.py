"""Disjoint-copy stress fixture, not a claim about arbitrary large MIPs."""
from pathlib import Path
from math import ceil
from .numbers import LPIRError,decimal
from .verify import verify,expression,expanded

def generate(m,path,copies=None,target_mb=None):
    verify(m)
    if (copies is None)==(target_mb is None):raise LPIRError('Specify exactly one of copies or target_mb')
    if copies is not None and copies<1:raise LPIRError('copies must be positive')
    if target_mb is not None and target_mb<=0:raise LPIRError('target_mb must be positive')
    target=int(target_mb*1024*1024) if target_mb is not None else None
    copies=copies or max(1,ceil(target/max(1,m.manifest['source_bytes'])))
    def write(count):
        with open(path,'w') as f:
            f.write('Minimize\n' if m.manifest['sense']=='min' else 'Maximize\n')
            f.write(' obj: 0\n')
            for d in range(count):
                terms=[]
                for v in m.variables:
                    c=m.num(v[5])
                    if c:terms.append(('+' if c>0 else '-')+decimal(abs(c))+f' copy{d}_{v[1]}')
                    if len(terms)==20:f.write(' '+' '.join(terms)+'\n');terms=[]
                if terms:f.write(' '+' '.join(terms)+'\n')
            const=m.num(m.manifest['objective_constant'])*count
            if const:f.write((' +' if const>0 else ' -')+decimal(abs(const))+'\n')
            f.write('Subject To\n')
            for d in range(count):
                names={v[0]:f'copy{d}_{v[1]}' for v in m.variables}
                for r,a,lo,hi in expanded(m):
                    exp=expression(a,names)
                    if lo is None and hi is None:raise LPIRError('Cannot emit a free row')
                    rhs=f'{exp} = {decimal(lo)}' if lo==hi else f'{exp} <= {decimal(hi)}' if lo is None else f'{exp} >= {decimal(lo)}' if hi is None else f'{decimal(lo)} <= {exp} <= {decimal(hi)}'
                    f.write(f' copy{d}_{m.rows[r][1]}: {rhs}\n')
            f.write('Bounds\n')
            for d in range(count):
                for v in m.variables:
                    name=f'copy{d}_{v[1]}';lo=m.num(v[3]);hi=m.num(v[4])
                    if lo is None and hi is None:f.write(f' {name} free\n')
                    elif lo is None:f.write(f' -inf <= {name} <= {decimal(hi)}\n')
                    elif hi is None:f.write(f' {decimal(lo)} <= {name}\n')
                    else:f.write(f' {decimal(lo)} <= {name} <= {decimal(hi)}\n')
            for kind,title in [('B','Binaries'),('I','Generals')]:
                vs=[v for v in m.variables if v[2]==kind]
                if vs:
                    f.write(title+'\n')
                    for d in range(count):
                        for start in range(0,len(vs),20):f.write(' '+' '.join(f'copy{d}_{v[1]}' for v in vs[start:start+20])+'\n')
            f.write('End\n')
    write(copies)
    while target is not None and Path(path).stat().st_size<target:
        copies=max(copies+1,ceil(copies*target/max(1,Path(path).stat().st_size)));write(copies)
    return {'output':str(path),'copies':copies,'bytes':Path(path).stat().st_size,'warning':'Independent repeated blocks test only repetition scalability, not arbitrary large model structure.'}
