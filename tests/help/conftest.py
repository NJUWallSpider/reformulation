"""Small exact, independently specified setups for the public recipe text."""
from pathlib import Path
import pytest
import mipframe as mf


def frame(records, schema, **kwargs):
    return mf.Frame.from_records(records, schema=schema, **kwargs)


def model(tmp, rows, bounds='', integer='', objective='0 x'):
    p=tmp/'input.lp'
    p.write_text('Minimize\n o: '+objective+'\nSubject To\n'+rows+'\nBounds\n'+bounds+'\n'+('Generals\n'+integer+'\n' if integer else '')+'End\n')
    return mf.read(p)


@pytest.fixture
def recipe_env(tmp_path):
    opened=[]
    def make(ident):
        env={'mf':mf,'output_dir':tmp_path}
        if ident in ('roles','coordinates','decomposition','templates','epigraph'):
            m=model(tmp_path,' a: x + y = 1\n b: z + w = 1\n c: x + z = 1\n d: y + w = 1',
                    ' 0 <= x <= 1\n 0 <= y <= 1\n 0 <= z <= 1\n 0 <= w <= 1', 'x y z w')
            env.update(m=m,R=m.rows,A=m.rows.iloc[:2],B=m.rows.iloc[2:],V=m.vars,coupling=m.rows.iloc[2:])
            env['F']=frame([dict(group=g,slot=s,coef=c) for g in ('A','B') for s,c in [(0,1),(1,2)]],{'group':'str','slot':'int','coef':'number'})
            env['T']=m.terms()
            env['membership']=frame([dict(var=v,group=v.id//2) for v in m.vars],{'var':m.vars.schema['var'],'group':'int'},key=('var',),models=(m,))
        elif ident=='support':
            m=model(tmp_path,' a: x + y = 1\n b: x + y + z = 1',' 0 <= x <= 1\n 0 <= y <= 1\n 0 <= z <= 1','x y z')
            env.update(m=m,R=m.rows,a=m.rows.by_name('a'),b=m.rows.by_name('b'))
        elif ident=='network':
            m=model(tmp_path,' a: x = 1\n b: - x + y = 0\n c: - y = -1',' 0 <= x <= 1\n 0 <= y <= 1','x y')
            env.update(m=m,R=m.rows,V=m.vars)
        elif ident in ('conflicts','odd-cycle'):
            rows='\n'.join(f' e{i}: x{i} + x{(i+1)%5} <= 1' for i in range(5))
            m=model(tmp_path,rows,'\n'.join(f' 0 <= x{i} <= 1' for i in range(5)),' '.join(f'x{i}' for i in range(5)),objective='x0')
            env.update(m=m,R=m.rows,V=m.vars)
            env['edges']=[m.check.linear(r.ub_claim,using=[r.ub_claim]) for r in m.rows]
        elif ident=='closure':
            env['E']=frame([dict(u=0,v=1),dict(u=2,v=1)],{'u':'int','v':'int'})
            env['pairs']=frame([dict(left=0,right=2)],{'left':'int','right':'int'})
            env['values']=frame([dict(member=i,resource='r',coef=v) for i,v in enumerate([2,3,4])],{'member':'int','resource':'str','coef':'number'})
            env['capacities']=frame([dict(resource='r',capacity=8)],{'resource':'str','capacity':'number'},key=('resource',))
            return env
        elif ident=='transitive':
            m=model(tmp_path,' a: x - y <= 0\n b: y - z <= 0\n c: x - z <= 0',' 0 <= x <= 1\n 0 <= y <= 1\n 0 <= z <= 1','x y z')
            x,y,z=[m.vars.by_name(n) for n in ('x','y','z')];a,b,c=[m.rows.by_name(n) for n in ('a','b','c')]
            vt=m.vars.schema['var'];rt=m.rows.schema['row']
            env.update(m=m,dropped=m.rows.where(row=c))
            env['E']=frame([dict(u=x,v=y,row=a),dict(u=y,v=z,row=b),dict(u=x,v=z,row=c)],{'u':vt,'v':vt,'row':rt},models=(m,))
            env['targets']=frame([dict(target='shortcut',relation='<=',rhs=0)],{'target':'str','relation':'str','rhs':'number'})
            env['terms']=frame([dict(target='shortcut',var=x,coef=1),dict(target='shortcut',var=z,coef=-1)],{'target':'str','var':vt,'coef':'number'},models=(m,))
            env['using']=frame([dict(target='shortcut',row=r,side='ub',multiplier=1) for r in (a,b)],{'target':'str','row':rt,'side':'str','multiplier':'number'},models=(m,))
        elif ident in ('linear','edits'):
            m=model(tmp_path,' r: x + y <= 4',' 0 <= x <= 5\n 0 <= y <= 5','x y')
            env.update(m=m,x=m.vars.by_name('x'),y=m.vars.by_name('y'),r=m.rows.by_name('r'),target_rhs=4)
        elif ident=='cases':
            m=model(tmp_path,' cap: x <= 6\n old: x + 1197 y <= 1200',' 0 <= x <= 10\n 0 <= y <= 1','y')
            env.update(m=m,x=m.vars.by_name('x'),y=m.vars.by_name('y'),cap=m.rows.by_name('cap'),old=m.rows.by_name('old'))
        elif ident=='conditions':
            m=model(tmp_path,' r: x - 4 z >= 0',' 0 <= x <= 3\n 0 <= z <= 1','z')
            env.update(m=m,x=m.vars.by_name('x'),z=m.vars.by_name('z'),r=m.rows.by_name('r'))
        elif ident=='enumeration':
            m=model(tmp_path,' r: x + y <= 1',' 0 <= x <= 1\n 0 <= y <= 1','x y')
            env.update(m=m,R=m.rows,V=m.vars,target=m.linexpr(vars=m.vars)<=1,premises=[m.rows.one().ub_claim])
        elif ident=='round':
            m=model(tmp_path,' r: 39 x >= 43',' 0 <= x <= 5','x')
            env.update(m=m,x=m.vars.one(),sources=[(mf.q('1/39'),m.rows.one().lb_claim)])
        else:raise AssertionError(ident)
        opened.append(m)
        return env
    yield make
    for m in reversed(opened):m.close()
