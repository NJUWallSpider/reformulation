"""Typed edge patterns distinguish types and coarse domains without rounding."""

from model_helpers import family_catalog


from fractions import Fraction as Q
import math

import pytest
import mipframe as mf
from mipframe.ir.typed import bound_class


@pytest.fixture
def model(tmp_path):
    path = tmp_path / 'typed.lp'
    variables = ['cf', 'cf2', 'cn', 'cn2', 'cp', 'cp2', 'cb', 'cb2',
                 'cl', 'cl2', 'cu', 'cu2', 'cx', 'cx2', 'b', 'b2', 'bf', 'i', 'y1', 'y2', 'y3', 'y4']
    rows = [f' r{j}: {v} <= 1' for j, v in enumerate(variables[:14])]
    rows += [' mixed: 4b - 2cn - 2y1 - 2y2 - 2y3 <= 0',
             ' scaled: 0.5b2 - 0.25cn2 - 0.25y1 - 0.25y2 - 0.25y4 <= 0',
             ' reversed: -2b + cn + y1 + y2 + y3 >= 0',
             ' changed_domain: 2b - cp - y1 - y2 - y3 <= 0',
             ' changed_type: 2i - cn - y1 - y2 - y3 <= 0',
             ' changed_rhs: 2b - cn - y1 - y2 - y3 <= 1',
             ' near: 2.000000000000000000001b - cn - y1 - y2 - y3 <= 0',
             ' binary_sum: b + b2 = 1', ' continuous_sum: cn + cn2 = 1',
             ' integer_single: i <= 1', ' binary_single: b <= 1', 'binary_fixed: bf <= 1',
             ' eq: b - cn = 1', 'eq_reversed: -0.25b + 0.25cn = -0.25',
             'eq_type_roles: cn - b = 1',
             'ranged: -2 <= 2b + 4cn <= 6', 'ranged_scaled: -1 <= b2 + 2cn2 <= 3',
             'empty: b - b = 0']
    path.write_text('Minimize\n obj: ' + ' + '.join('0' + v for v in variables) + '\nSubject To\n'
        + '\n'.join(rows) + '\nBounds\n cf free\n cf2 free\n cn2 >= 3\n'
        ' cp <= 0\n cp >= -inf\n cp2 <= -3\n cp2 >= -inf\n'
        ' -2 <= cb <= 7\n 10 <= cb2 <= 20\n cl >= -3\n cl2 >= -8\n'
        ' cu >= -inf\n cu <= 3\n cu2 >= -inf\n cu2 <= 10\n cx = 2\n cx2 = 9\n'
        ' bf = 0\n 0 <= i <= 1\nBinaries\n b b2 bf\nGenerals\n i\nEnd\n')
    with mf.read(path) as model:
        yield model


def test_bound_classes_exact_thresholds():
    for (lo, hi, expected) in [
        (None, None, 'free'),
        (Q(0), None, 'nonnegative'),
        (Q(3), None, 'nonnegative'),
        (None, Q(0), 'nonpositive'),
        (None, Q(-3), 'nonpositive'),
        (Q(-3), None, 'lower_bounded'),
        (None, Q(3), 'upper_bounded'),
        (Q(-2), Q(7), 'boxed'),
        (Q(0), Q(1), 'boxed'),
        (Q(2), Q(2), 'fixed'),
        (Q(0), Q(0), 'fixed'),
        (Q(-1, 10 ** 30), None, 'lower_bounded'),
        (None, Q(1, 10 ** 30), 'upper_bounded'),
    ]:
        assert bound_class(lo, hi) == expected


def test_variable_bounds_group_by_class_not_value(model):
    rows = model.rows.head(14)
    assert len(family_catalog(rows, 'algebratic')) == 1
    families = family_catalog(rows, 'typed')
    assert len(families) == 7 and all(f['count'] == 2 for f in families)
    classes = ['free', 'nonnegative', 'nonpositive', 'boxed', 'lower_bounded', 'upper_bounded', 'fixed']
    for j, expected in enumerate(classes):
        a, b = model.rows.loc[2*j].signature('typed'), model.rows.loc[2*j+1].signature('typed')
        assert a == b and a.edge_multiset == (((Q(1), 'C', expected), 1),)
        assert a.numeric_kind == 'exact'
    assert model.rows.by_name('integer_single').signature('typed') == model.rows.by_name('binary_single').signature('typed')
    assert model.rows.by_name('binary_fixed').signature('typed').pattern == (((Q(1), 'B', 'fixed'), 1),)


def test_typed_scale_direction_coefficients_rhs_and_domains(model, monkeypatch):
    a = model.rows.by_name('mixed').signature('typed')
    assert isinstance(a, mf.TypedSignature)
    assert a.edge_multiset == (((Q(-1), 'C', 'nonnegative'), 4), ((Q(2), 'B', 'boxed'), 1))
    assert a.coeff_multiset == ((Q(-1), 4), (Q(2), 1))
    assert a.sense == '<=' and a.rhs == 0 and a.scale == 2
    assert a == model.rows.by_name('scaled').signature('typed') == model.rows.by_name('reversed').signature('typed')
    assert model.rows.by_name('scaled').signature('typed').scale == Q(1, 4)
    for name in ('changed_domain', 'changed_rhs', 'near'):
        assert a != model.rows.by_name(name).signature('typed')
    assert model.rows.by_name('mixed').signature() == model.rows.by_name('changed_domain').signature()
    assert model.rows.by_name('mixed').signature() == model.rows.by_name('changed_type').signature()
    assert a == model.rows.by_name('changed_type').signature('typed')
    monkeypatch.setattr(mf.options.numeric, 'rtol', 0.1)
    monkeypatch.setattr(mf.options.numeric, 'atol', 0.1)
    assert a != model.rows.by_name('near').signature('typed')
    assert a != model.rows.by_name('mixed').signature()  # Different signature kinds.
    assert mf.TypedSignature.from_key(a.key()) == a


def test_equality_canonicalizes_the_complete_typed_pattern(model):
    a = model.rows.by_name('eq').signature('typed')
    b = model.rows.by_name('eq_reversed').signature('typed')
    assert a == b
    assert a.pattern == (((Q(-1), 'B', 'boxed'), 1), ((Q(1), 'C', 'nonnegative'), 1))
    assert a.rhs == -1
    assert a != model.rows.by_name('eq_type_roles').signature('typed')
    assert model.rows.by_name('eq').signature() == model.rows.by_name('eq_type_roles').signature()
    assert model.rows.by_name('binary_sum').signature() == model.rows.by_name('continuous_sum').signature()
    assert model.rows.by_name('binary_sum').signature('typed') != model.rows.by_name('continuous_sum').signature('typed')
    assert model.rows.by_name('ranged').signature('typed') == model.rows.by_name('ranged_scaled').signature('typed')
    empty = model.rows.by_name('empty').signature('typed')
    assert empty.pattern == () and empty.nnz == 0 and empty.rhs == 0


def test_typed_frames_family_membership_display_and_exploration(model, monkeypatch):
    sigs = model.rows.signature('typed')
    assert isinstance(sigs, mf.Frame)
    patterns = sigs.relations['pattern']
    row = sigs.iloc[14].row
    pattern = tuple(
        ((r.coef, r.kind, r.bounds), r.count)
        for r in patterns.where(mf.col('row') == row)
    )
    assert pattern == model.rows.by_name('mixed').signature('typed').pattern
    assert len(patterns.where(mf.col('row') == sigs.tail(1).one().row)) == 0
    assert list(sigs.sample(4, random_state=42).column('row')) == list(sigs.sample(4, random_state=42).column('row'))
    assert {r.relation: r.count for r in sigs.head(14).column('relation').value_counts()} == {'<=': 14}
    families = family_catalog(model.rows.iloc[14:17],signature='typed')
    assert len(families) == 1
    family = families.loc['F0']
    assert isinstance(family.signature, mf.TypedSignature)
    assert family.count == 3 and list(family.rows.ids) == [14, 15, 16]
    assert '[(+2, B, boxed), (-1, C, nonnegative) × 4]' in repr(family)
    assert '[(+2, B, boxed), (-1, C, nonnegative) × 4]' in repr(families)
    assert family.rows.incidence_counts().column('nnz').max() == 3
    assert len(family.rows.neighbors()) == 8
    assert 'mixed:' in family.rows.head(1).show(formula=True, max_terms=2)
    assert families.head(1)[0]['pattern'] == family.signature.pattern
    monkeypatch.setattr(mf.options.display, 'max_cols', 1)
    assert 'more tokens' in repr(family)
    assert len(family.signature.pattern) == 2  # Display never truncates actual data.
    pretty = pytest.importorskip('IPython.lib.pretty').pretty
    assert pretty(family) == repr(family)
    assert pretty(families) == repr(families)


def test_typed_mps_and_save_reload(tmp_path):
    path = tmp_path / 'typed.mps'
    path.write_text('NAME typed\nROWS\n N OBJ\n L A\n L B\nCOLUMNS\n'
        ' x A 4\n y A -2\n p B 0.5\n q B -0.25\n'
        'RHS\n r A 0 B 0\nBOUNDS\n BV b x\n BV b p\n FR b y\n FR b q\nENDATA\n')
    with mf.read(path, mps_format='free') as model:
        assert model.rows.by_name('A').signature() == model.rows.by_name('B').signature()
        assert family_catalog(model.rows).loc['F0'].count == 2
        families = family_catalog(model.rows, 'typed')
        assert len(families) == 1 and families[0]['count'] == 2
        assert families[0]['pattern'] == (((Q(-1), 'C', 'free'), 1), ((Q(2), 'B', 'boxed'), 1))
        saved = model.save(tmp_path / 'saved')
        records = families.to_records()
        family = families.loc['F0']
    with mf.load(saved) as reloaded:
        assert family_catalog(reloaded.rows, 'typed').to_records() == records
    with pytest.raises(mf.MIPFrameError, match='closed'):
        repr(family)
    with pytest.raises(mf.MIPFrameError, match='closed'):
        families.head()


def test_large_exact_and_typed_patterns_use_bounded_batches(tmp_path, monkeypatch):
    n = 100_000
    variables = ' '.join(f'x{i}' for i in range(n))
    expression = ' + '.join(f'x{i}' for i in range(n))
    source = tmp_path / 'large.lp'
    source.write_text(f'Minimize\n o: 0\nSubject To\n row: {expression} <= 1\nBinaries\n {variables}\nEnd\n')
    with mf.read(source) as model:
        def forbidden(*args, **kwargs):
            raise AssertionError('No complete terms or variable metadata table')
        monkeypatch.setattr(model.model, 'row', forbidden)
        monkeypatch.setattr(model.model, 'row_ids', forbidden)
        monkeypatch.setattr(model.model, 'iter_terms', forbidden)
        original = model.model.num
        calls = []

        def numeric(ident):
            calls.append(ident)
            return original(ident)

        with monkeypatch.context() as patch:
            patch.setattr(model.model, 'num', numeric)
            algebraic = model.rows.loc[0].signature()
            assert algebraic.pattern == ((Q(1), n),) and len(algebraic.key()) < 100
            assert len(calls) == 3  # One distinct coefficient and two bounds.
        families = family_catalog(model.rows)
        assert '[+1 × 100,000]' in repr(families)
        assert len(repr(families.loc['F0'])) < 220
        assert not isinstance(families.loc['F0'].rows.ids, tuple)
        assert list(families.loc['F0'].rows.sample(1).ids) == [0]
        table_type = type(model.model.variables)
        monkeypatch.setattr(table_type, '__iter__', forbidden)
        queries = []
        model.model.db.set_trace_callback(lambda sql: queries.append(sql) if sql.startswith('SELECT id,kind,lo,hi') else None)
        signature = model.rows.loc[0].signature('typed')
        assert signature.pattern == (((Q(1), 'B', 'boxed'), n),)
        assert len(signature.key()) < 120
        assert len(queries) == math.ceil(n / 900)
        assert all(sql.count(',') < 905 for sql in queries)
        assert '(+1, B, boxed) × 100,000' in repr(signature.pattern)
        families = family_catalog(model.rows, 'typed')
        assert families.loc['F0'].signature == signature
        assert len(repr(families.loc['F0'])) < 250
