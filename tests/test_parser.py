from fractions import Fraction as F
import pytest
from lpir import LPIRError


def test_linear_syntax_and_exactness(make_ir,tmp_path):
    ir=make_ir('''Maximize
 cost: x + 2e-2 y - x + 3.125 + z
Subject To
 two_sided: -2 <= x + y + y <= 7
 equal: x - y = 1e-9
 unnamed: z
 + y >= -3
 2 x <= 8
 - y <= 4
Bounds
 x free
 -inf <= y <= 5
 -2 <= z
Binaries
 z
Generals
 y
End
''')
    m=ir.model
    x,y,z=(m.var_names[k] for k in ('x','y','z'))
    assert m.num(m.variables[x][5])==0
    assert m.num(m.variables[y][5])==F('0.02')
    assert m.num(m.manifest['objective_constant'])==F('3.125')
    assert m.manifest['sense']=='max'
    assert m.variables[x][3:5]==(None,None)
    assert m.variables[y][2]=='I' and m.variables[y][3] is None
    assert m.num(m.variables[z][3])==0 and m.num(m.variables[z][4])==1
    assert m.row(m.row_names['two_sided'])=={x:F(1),y:F(2)}
    assert len(m.rows)==5 and ir.verify()['status']=='PASS'
    out=tmp_path/'round.lp';ir.export(out)
    from lpir import IR
    new=IR.build(out,tmp_path/'round')
    assert new.overview()['objective_constant']=='3.125'

@pytest.mark.parametrize('text,fragment',[
 ('Minimize\n o: x*x\nSubject To\n r: x <= 1\nEnd\n','line'),
 ('Minimize\n o: x\nSubject To\n r: x <= 1\nSOS\nEnd\n','unsupported section'),
 ('Minimize\n o: x\nSubject To\n r: x <= 1\n r: x >= 0\nEnd\n','duplicate'),
 ('Minimize\n o: x\nSubject To\n r: x <= 1\n','End'),
 ('Minimize\n o: x\nSubject To\n r: x -> y\nEnd\n','unsupported'),
 ('Minimize\n o: "quoted"\nSubject To\n r: x <= 1\nEnd\n','unsupported'),
 ('Minimize\n o: x\nSubject To\n r: x <= 1\nEnd\n bad\n','after End'),
 ('Minimize\n o: x\nSubject To\n r: x <= inf\nEnd\n','infinity'),
])
def test_rejections(make_ir,text,fragment):
    with pytest.raises(LPIRError,match=fragment):make_ir(text)

def test_defaults_cancellation_and_zero_row(make_ir):
    ir=make_ir('''Minimize
 obj: a - a + b
Subject To
 cancel: 2a - 2a = 0
 unit: b <= 4
Bounds
 b <= +inf
Generals
 a
End
''')
    m=ir.model
    assert m.variables[m.var_names['a']][2]=='I'
    assert m.num(m.variables[m.var_names['a']][3])==0
    assert m.variables[m.var_names['b']][4] is None
    assert m.row(0)=={}
    assert ir.verify()['rows_verified']==2

def test_empty_constraint_set_and_bound_only_variable(make_ir):
    ir=make_ir('''Minimize
 obj: 2
Subject To
Bounds
 only = 4
End
''')
    assert ir.overview()['rows']==0
    assert ir.model.num(ir.model.variables[0][3])==4
    assert ir.verify()['status']=='PASS'

def test_multiline_rhs_and_reverse_range(make_ir):
    ir=make_ir('''Minimize
 obj: x
Subject To
 r: x + y >=
 -2
 s: 8 >= x-y >= 1
Bounds
 5 >= x
 y free
End
''')
    m=ir.model
    assert m.num(m.rows[0][2])==-2
    assert m.num(m.rows[1][2])==1 and m.num(m.rows[1][3])==8

def test_infinite_bound_orientations(make_ir):
    ir=make_ir('''Minimize
 o: x+y
Subject To
 r: x-y<=2
Bounds
 x >= -inf
 +inf >= y
End
''')
    m=ir.model
    assert m.variables[m.var_names['x']][3] is None
    assert m.variables[m.var_names['y']][4] is None
