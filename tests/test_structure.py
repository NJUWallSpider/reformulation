from lpir import IR
from lpir.verify import write_model
from fractions import Fraction as F
from pathlib import Path
import random,importlib.util,sys

ROOT=Path(__file__).resolve().parents[1]

def reference():
    spec=importlib.util.spec_from_file_location('old_model_io',ROOT/'test1/analysis/model_io.py')
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
    return mod.read(ROOT/'test1/test1.lp')

def canonical(m):
    variables={v[1]:(v[2],m.num(v[3]),m.num(v[4]),m.num(v[5])) for v in m.variables}
    rows={r[1]:({m.variables[v][1]:c for v,c in m.row(r[0]).items()},m.num(r[2]),m.num(r[3])) for r in m.rows}
    return variables,rows,m.manifest['sense'],m.num(m.manifest['objective_constant'])

def test_test1_against_independent_parser(test1):
    m=test1.model;o,rs,b,bins=reference();stats=test1.overview()
    assert (stats['variables'],stats['rows'],stats['nonzeros'],stats['objective_nonzeros'])==(11090,4630,41550,7705)
    assert stats['variable_types']=={'B':11025,'C':65}
    for v in m.variables:
        old=int(v[1][1:]);assert (m.num(v[3]),m.num(v[4]))==b[old]
        assert m.num(v[5])==o.get(old,F(0));assert (v[2]=='B')==(old in bins)
    for r in rs:
        rid=m.row_names[r.name]
        assert {int(m.variables[v][1][1:]):c for v,c in m.row(rid).items()}==r.a
        assert m.num(m.rows[rid][2])==(r.rhs if r.sense!='<=' else None)
        assert m.num(m.rows[rid][3])==(r.rhs if r.sense!='>=' else None)
    b=test1.structures['assignments'][0]
    assert b['shape']==[105,105] and b['complete'] and len(b['bindings'])==11025
    t=test1.structures['temporal'];assert len(t['rows'])==4160 and len(t['time_bounds'])==65
    assert all(m.num(r['negative_M'])==-60000 and r['assignment'] is not None for r in t['rows'])
    assert test1.query('filter','temporal','L[source] + q > U[target]')['total']==3341
    assert test1.verify()['coverage']==1
    assert len(test1.render().encode())<=20000

def test_export_exact_roundtrip(test1,tmp_path):
    path=tmp_path/'export.lp';test1.export(path);other=IR.build(path,tmp_path/'other')
    assert canonical(other.model)==canonical(test1.model)

def test_renaming_permutation_and_index_mapping(test1,tmp_path):
    m=test1.model;rng=random.Random(41)
    ids=list(range(len(m.variables)));rng.shuffle(ids)
    vn={v[0]:f'v_{ids[v[0]]}' for v in m.variables}
    ids=list(range(len(m.rows)));rng.shuffle(ids);rn={r[0]:f'row_{ids[r[0]]}' for r in m.rows}
    order=list(range(len(m.rows)));rng.shuffle(order)
    path=tmp_path/'shuffled.lp';write_model(m,path,vn,rn,order,True)
    other=IR.build(path,tmp_path/'shuffled');n=other.model
    assert other.overview()['assignment_blocks'][0]['shape']==[105,105]
    assert other.overview()['temporal_rows']==4160
    assert other.query('filter','temporal','L[source] + q > U[target]')['total']==3341
    block=other.structures['assignments'][0]
    for v,i,j in block['bindings']:
        assert v in n.row(block['left_rows'][i]) and v in n.row(block['right_rows'][j])
    for r in m.rows:
        rid=n.row_names[rn[r[0]]]
        assert n.row(rid)=={n.var_names[vn[v]]:c for v,c in m.row(r[0]).items()}
    old_time={r['row']:r for r in test1.structures['temporal']['rows']}
    new_time={r['row']:r for r in other.structures['temporal']['rows']}
    for old,rec in old_time.items():
        new=new_time[n.row_names[rn[old]]]
        for role in ('source','target','switch','arc'):assert n.variables[new[role]][1]==vn[rec[role]]
        assert new['assignment'] is not None

def assignment_lp(edges,nleft,nright,extra=''):
    objective=' + '.join(f'e{i}_{j}' for i,j in edges)
    lines=['Minimize',' obj: '+objective,'Subject To']
    for i in range(nleft):lines.append(f' left{i}: '+' + '.join(f'e{a}_{b}' for a,b in edges if a==i)+' = 1')
    for j in range(nright):lines.append(f' right{j}: '+' + '.join(f'e{a}_{b}' for a,b in edges if b==j)+' = 1')
    if extra:lines.append(extra)
    lines+=['Binaries',' '.join(f'e{i}_{j}' for i,j in edges),'End']
    return '\n'.join(lines)+'\n'

def test_sparse_assignment_and_boundary(make_ir):
    edges=[(0,0),(0,1),(1,0),(1,2),(2,1),(2,2)]
    ir=make_ir(assignment_lp(edges,3,3,' coupling: 2 e0_0 + 3 e1_2 <= 7'))
    b=ir.structures['assignments'][0]
    assert b['shape']==[3,3] and b['missing_pairs']==3
    q=ir.query('external',b['id']);assert q['total']==1 and q['items'][0]['name']=='coupling'
    assert q['objective_variable_count']==6

def test_odd_cycle_not_assignment(make_ir):
    ir=make_ir('''Minimize
 o: a+b+c
Subject To
 r: a+b=1
 s: b+c=1
 t: c+a=1
Binaries
 a b c
End
''')
    assert not ir.structures['assignments'] and ir.structures['assignment_rejections']

def test_temporal_near_equality_is_exception(make_ir):
    ir=make_ir('''Minimize
 o: 5 s + 2 a
Subject To
 good: v-u+7s-100a >= -93
 bad: v-u+7s-100a >= -92.99999999999
Bounds
 2 <= u <= 8
 5 <= v <= 12
Binaries
 s a
End
''')
    assert ir.overview()['temporal_rows']==1
    assert ir.overview()['temporal_exceptions']==1
    assert ir.verify()['rows_verified']==2
    assert ir.query('relation','temporal','q == cost_switch + cost_arc')['all_satisfied']

def test_repeated_and_irregular_rows(make_ir):
    ir=make_ir('''Minimize
 o: x + 7 y + z
Subject To
 a: 2x+3y<=5
 b: 2x+3y<=5
 c: x+9y+11z >= 1
End
''')
    assert ir.overview()['residual_rows']>=1
    assert ir.verify()['rows_verified']==3

def test_test1_parameter_objective_and_coupling_changes(test1,tmp_path):
    path=tmp_path/'changed.lp';test1.export(path)
    text=path.read_text()
    # A tiny RHS perturbation must be excluded from the exact time template.
    import re
    match=re.search(r'( c276:.* >= )([^\n]+)',text)
    original=match[2]
    from lpir.numbers import decimal
    text=text[:match.start(2)]+decimal(F(original)+F('0.00000000001'))+text[match.end(2):]
    # Objective changes must survive and remain visible independently of structural roles.
    text=text.replace(' obj: ', ' obj: +0.125 x0 ',1)
    text=text.replace('Bounds\n',' cross_block: +2 x0 +3 x4241 <= 7\nBounds\n',1)
    path.write_text(text)
    changed=IR.build(path,tmp_path/'changed')
    assert changed.overview()['temporal_rows']==4159
    assert changed.overview()['temporal_exceptions']==1
    assert changed.model.num(changed.model.variables[changed.model.var_names['x0']][5])==F('0.125')
    q=changed.query('external','assignment_0',limit=1000)
    handle=q['handle'];allrows=[]
    for offset in range(0,q['total'],1000):allrows+=changed.query(handle=handle,offset=offset,limit=1000)['items']
    assert any(r['name']=='cross_block' for r in allrows)
    assert changed.verify()['rows_verified']==4631

def test_duplicate_assignment_pairs(make_ir):
    ir=make_ir('''Minimize
 o: a+b
Subject To
 one: a+b=1
 two: a+b=1
Binaries
 a b
End
''')
    b=ir.structures['assignments'][0]
    assert b['shape']==[1,1] and b['duplicate_pairs']==[[0,0,2]]
    assert ir.inspect('assignment',b['id'])['total']==2
