from model_helpers import refinement_probe, signature_key
from mipframe.ir.signatures import _Signature as StructuralSignature
"""Typed edge patterns distinguish types and coarse domains without rounding."""
from fractions import Fraction as Q
import math
import pytest
import mipframe as mf
from mipframe.ir.typed import bound_class

@pytest.fixture
def model(tmp_path):
    path = tmp_path / 'typed.lp'
    variables = ['cf', 'cf2', 'cn', 'cn2', 'cp', 'cp2', 'cb', 'cb2', 'cl', 'cl2', 'cu', 'cu2', 'cx', 'cx2', 'b', 'b2', 'bf', 'i', 'y1', 'y2', 'y3', 'y4']
    rows = [f' r{j}: {v} <= 1' for j, v in enumerate(variables[:14])]
    rows += [' mixed: 4b - 2cn - 2y1 - 2y2 - 2y3 <= 0', ' scaled: 0.5b2 - 0.25cn2 - 0.25y1 - 0.25y2 - 0.25y4 <= 0', ' reversed: -2b + cn + y1 + y2 + y3 >= 0', ' changed_domain: 2b - cp - y1 - y2 - y3 <= 0', ' changed_type: 2i - cn - y1 - y2 - y3 <= 0', ' changed_rhs: 2b - cn - y1 - y2 - y3 <= 1', ' near: 2.000000000000000000001b - cn - y1 - y2 - y3 <= 0', ' binary_sum: b + b2 = 1', ' continuous_sum: cn + cn2 = 1', ' integer_single: i <= 1', ' binary_single: b <= 1', 'binary_fixed: bf <= 1', ' eq: b - cn = 1', 'eq_reversed: -0.25b + 0.25cn = -0.25', 'eq_type_roles: cn - b = 1', 'ranged: -2 <= 2b + 4cn <= 6', 'ranged_scaled: -1 <= b2 + 2cn2 <= 3', 'empty: b - b = 0']
    path.write_text('Minimize\n obj: ' + ' + '.join(('0' + v for v in variables)) + '\nSubject To\n' + '\n'.join(rows) + '\nBounds\n cf free\n cf2 free\n cn2 >= 3\n cp <= 0\n cp >= -inf\n cp2 <= -3\n cp2 >= -inf\n -2 <= cb <= 7\n 10 <= cb2 <= 20\n cl >= -3\n cl2 >= -8\n cu >= -inf\n cu <= 3\n cu2 >= -inf\n cu2 <= 10\n cx = 2\n cx2 = 9\n bf = 0\n 0 <= i <= 1\nBinaries\n b b2 bf\nGenerals\n i\nEnd\n')
    with mf.read(path) as model:
        yield model

def test_bound_classes_exact_thresholds():
    for lo, hi, expected in [(None, None, 'free'), (Q(0), None, 'nonnegative'), (Q(3), None, 'nonnegative'), (None, Q(0), 'nonpositive'), (None, Q(-3), 'nonpositive'), (Q(-3), None, 'lower_bounded'), (None, Q(3), 'upper_bounded'), (Q(-2), Q(7), 'boxed'), (Q(0), Q(1), 'boxed'), (Q(2), Q(2), 'fixed'), (Q(0), Q(0), 'fixed'), (Q(-1, 10 ** 30), None, 'lower_bounded'), (None, Q(1, 10 ** 30), 'upper_bounded')]:
        assert bound_class(lo, hi) == expected

def test_typed_scale_direction_coefficients_rhs_and_domains(model, monkeypatch):
    a = signature_key(model.rows.by_name('mixed'), 'typed')
    assert a.numeric_kind == 'exact'
    assert a.edge_multiset == (((Q(-1), 'C', 'nonnegative'), 4), ((Q(2), 'B', 'boxed'), 1))
    assert a.coeff_multiset == ((Q(-1), 4), (Q(2), 1))
    assert a.sense == '<=' and a.rhs == 0 and (a.scale == 2)
    assert a == signature_key(model.rows.by_name('scaled'), 'typed') == signature_key(model.rows.by_name('reversed'), 'typed')
    assert signature_key(model.rows.by_name('scaled'), 'typed').scale == Q(1, 4)
    for name in ('changed_domain', 'changed_rhs', 'near'):
        assert a != signature_key(model.rows.by_name(name), 'typed')
    assert signature_key(model.rows.by_name('mixed')) == signature_key(model.rows.by_name('changed_domain'))
    assert signature_key(model.rows.by_name('mixed')) == signature_key(model.rows.by_name('changed_type'))
    assert a == signature_key(model.rows.by_name('changed_type'), 'typed')
    assert a != signature_key(model.rows.by_name('near'), 'typed')
    assert a != signature_key(model.rows.by_name('mixed'))
    assert type(a).from_key(a.key()) == a

def test_equality_canonicalizes_the_complete_typed_pattern(model):
    a = signature_key(model.rows.by_name('eq'), 'typed')
    b = signature_key(model.rows.by_name('eq_reversed'), 'typed')
    assert a == b
    assert a.pattern == (((Q(-1), 'B', 'boxed'), 1), ((Q(1), 'C', 'nonnegative'), 1))
    assert a.rhs == -1
    assert a != signature_key(model.rows.by_name('eq_type_roles'), 'typed')
    assert signature_key(model.rows.by_name('eq')) == signature_key(model.rows.by_name('eq_type_roles'))
    assert signature_key(model.rows.by_name('binary_sum')) == signature_key(model.rows.by_name('continuous_sum'))
    assert signature_key(model.rows.by_name('binary_sum'), 'typed') != signature_key(model.rows.by_name('continuous_sum'), 'typed')
    assert signature_key(model.rows.by_name('ranged'), 'typed') == signature_key(model.rows.by_name('ranged_scaled'), 'typed')
    empty = signature_key(model.rows.by_name('empty'), 'typed')
    assert empty.pattern == () and empty.nnz == 0 and (empty.rhs == 0)
