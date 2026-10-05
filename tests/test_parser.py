from fractions import Fraction as F
import pytest
from mipframe import MIPFrameError
from mipframe import read


def test_linear_syntax_and_exactness(make_model,tmp_path):
    ir=make_model('''Maximize
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
    assert len(m.rows)==5 and ir.verify().status=='PROVED'
    out=tmp_path/'round.lp';ir.write(out)
    with read(out) as new:
        assert new.objective.constant == F('3.125')

def test_rejections(make_model):
    for (text, fragment) in [
        ('Minimize\n o: x*x\nSubject To\n r: x <= 1\nEnd\n', 'line'),
        ('Minimize\n o: x\nSubject To\n r: x <= 1\nSOS\nEnd\n', 'unsupported section'),
        ('Minimize\n o: x\nSubject To\n r: x <= 1\n r: x >= 0\nEnd\n', 'duplicate'),
        ('Minimize\n o: x\nSubject To\n r: x <= 1\n', 'End'),
        ('Minimize\n o: x\nSubject To\n r: x -> y\nEnd\n', 'unsupported'),
        ('Minimize\n o: "quoted"\nSubject To\n r: x <= 1\nEnd\n', 'unsupported'),
        ('Minimize\n o: x\nSubject To\n r: x <= 1\nEnd\n bad\n', 'after End'),
        ('Minimize\n o: x\nSubject To\n r: x <= inf\nEnd\n', 'infinity'),
    ]:
        with pytest.raises(MIPFrameError,match=fragment):make_model(text)

def test_defaults_cancellation_and_zero_row(make_model):
    ir=make_model('''Minimize
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
    assert ir.verify().summary['rows_verified']==2

def test_empty_constraint_set_and_bound_only_variable(make_model):
    ir=make_model('''Minimize
 obj: 2
Subject To
Bounds
 only = 4
End
''')
    assert ir.shape[0]==0
    assert ir.model.num(ir.model.variables[0][3])==4
    assert ir.verify().status=='PROVED'

def test_multiline_rhs_and_reverse_range(make_model):
    ir=make_model('''Minimize
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

def test_infinite_bound_orientations(make_model):
    ir=make_model('''Minimize
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


def test_highs_token_wrapping(make_model, tmp_path):
    ir = make_model(r'''\ File written by HiGHS .lp file handler
min
 obj: -2.1507062784 x +0.42978952
 y -3.288e-2
\ a comment between coefficient and variable

 z +
 2 x + 7
st
 r: +2
 x -3
 y + z <= 8
 s: x >= 2
 y <= 4
bounds
 x free
end
''')
    m = ir.model
    x, y, z = (m.var_names[k] for k in ('x', 'y', 'z'))
    assert [m.num(m.variables[v][5]) for v in (x, y, z)] == [
        F('-0.1507062784'), F('0.42978952'), F('-0.03288')
    ]
    assert m.num(m.manifest['objective_constant']) == 7
    assert m.row(0) == {x: F(2), y: F(-3), z: F(1)}
    # A scalar RHS must not consume the next unnamed row's variable.
    assert m.row(1) == {x: F(1)}
    assert m.row(2) == {y: F(1)}
    assert m.num(m.rows[1][2]) == 2
    assert m.num(m.rows[2][3]) == 4
    out = tmp_path / 'highs_roundtrip.lp'
    ir.write(out)
    with read(out) as rebuilt:
        assert rebuilt.verify().status == 'PROVED'
        for name in ('x', 'y', 'z'):
            assert rebuilt.model.num(rebuilt.model.variables[rebuilt.model.var_names[name]][5]) == m.num(m.variables[m.var_names[name]][5])
        assert rebuilt.model.num(rebuilt.model.manifest['objective_constant']) == 7


def test_missing_term_operator_still_rejected(make_model):
    for expression in ['x y', 'x\n y', '2 x 3 y']:
        with pytest.raises(MIPFrameError, match='expected \\+ or -'):
            make_model(f'Minimize\n obj: {expression}\nSubject To\n r: x <= 1\nEnd\n')


def test_highs_empty_semi_section(make_model):
    ir = make_model('min\n obj: x\nst\n r: x <= 1\nbin\n x\ngen\nsemi\n\\ empty HiGHS domain\nend\n')
    assert ir.model.variables[0][2] == 'I'
    assert ir.vars.iloc[0].is_binary
    assert ir.verify().status == 'PROVED'


def test_highs_nonempty_semi_rejected(make_model):
    with pytest.raises(MIPFrameError, match='line 6.*unsupported section semi'):
        make_model('min\n obj: x\nst\n r: x <= 1\nbounds\nsemi\n x\nend\n')


def test_wide_highs_row_verifies(make_model):
    terms = ' + '.join(f'{i + 1}\n x{i}' for i in range(1001))
    ir = make_model(f'min\n obj: x0\nst\n r: {terms} <= 2000\nend\n')
    assert len(ir.model.row(0)) == 1001
    assert ir.verify().status == 'PROVED'
