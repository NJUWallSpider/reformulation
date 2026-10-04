from model_helpers import refinement_probe, signature_key
from mipframe.ir.signatures import _Signature as StructuralSignature
"""Exact algebraic grouping and sparse exploration without structural analysis."""
from fractions import Fraction as Q
import gc
import sqlite3
import pytest
import mipframe as mf

@pytest.fixture
def model(tmp_path):
    source = tmp_path / 'families.lp'
    source.write_text('Minimize\n obj: 0x + 0y + 0z + 0w + 0p + 0q + 0s + 0t + 0unused\nSubject To\n a: 4x - 2y - 2z - 2w <= 0\n b: 0.5p - 0.25q - 0.25s - 0.25t <= 0\n reverse: -4x + 2y + 2z + 2w >= 0\n other_rhs: 4x - 2y - 2z - 2w <= 2\n eq: x - y - z = 3\n eq_reverse: -2p + 2q + 2s = -6\n symmetric: x - y = 3\n symmetric_reverse: -x + y = -3\n near: 2.000000000000000000001x - y - z - w <= 0\n ranged: -2 <= 2x + 4y <= 6\n ranged_scaled: -1 <= p + 2q <= 3\n empty: x - x = 0\n empty_scaled: y - y = 0\n infeasible_empty: x - x = 1\n single: z <= 1\nBounds\n unused free\nEnd\n')
    with mf.read(source) as m:
        yield m

def test_exact_signatures_scale_direction_rhs_and_counts(model, monkeypatch):
    a = signature_key(model.rows.by_name('a'))
    assert a.sense == '<=' and a.nnz == 4 and (a.rhs == 0) and (a.lhs is None)
    assert a.coeff_multiset == ((Q(-1), 3), (Q(2), 1))
    assert a.scale == 2 and a.numeric_kind == 'exact'
    assert a == signature_key(model.rows.by_name('b')) == signature_key(model.rows.by_name('reverse'))
    assert signature_key(model.rows.by_name('b')).scale == Q(1, 4)
    assert signature_key(model.rows.by_name('reverse')).scale == -2
    assert a != signature_key(model.rows.by_name('other_rhs'))
    assert a != signature_key(model.rows.by_name('near'))
    assert signature_key(model.rows.by_name('eq')) == signature_key(model.rows.by_name('eq_reverse'))
    assert signature_key(model.rows.by_name('eq')).pattern == ((Q(-1), 1), (Q(1), 2))
    assert signature_key(model.rows.by_name('eq')).rhs == -3
    assert signature_key(model.rows.by_name('symmetric')) == signature_key(model.rows.by_name('symmetric_reverse'))
    assert signature_key(model.rows.by_name('symmetric')).rhs == -3
    ranged = signature_key(model.rows.by_name('ranged'))
    assert ranged == signature_key(model.rows.by_name('ranged_scaled'))
    assert (ranged.lhs, ranged.rhs) == (-1, 3)
    assert signature_key(model.rows.by_name('empty')).pattern == ()
    assert signature_key(model.rows.by_name('empty')) != signature_key(model.rows.by_name('infeasible_empty'))
    assert a == signature_key(model.rows.by_name('a'), 'algebraic')
    assert a != signature_key(model.rows.by_name('near'))

def test_canonical_ratio_is_exact():
    assert mf.canonical_ratio([4, -2, -2, -2]) == 2
    assert mf.canonical_ratio(['0.5', '-0.25', '-0.25']) == Q(1, 4)
    assert mf.canonical_ratio([Q(2, 3), Q(-4, 5)]) == Q(2, 15)
    assert mf.canonical_ratio([]) == mf.canonical_ratio([0]) == 1
    with pytest.raises(TypeError, match='exact'):
        mf.canonical_ratio([0.5])

def test_neighbors_degrees_and_set_operations_do_not_load_coefficients(model, monkeypatch):

    def forbidden(*args, **kwargs):
        raise AssertionError('Incidence must not read coefficients')
    monkeypatch.setattr(model.model, 'num', forbidden)
    monkeypatch.setattr(model.model, 'iter_terms', forbidden)
    r = model.rows.head(3)
    v = r.vars
    assert isinstance(v, mf.VarFrame) and list(v.ids) == list(range(8))
    counts = r.incidence_counts()
    assert {r.var.id: r.nnz for r in counts} == {0: 2, 1: 2, 2: 2, 3: 2, 4: 1, 5: 1, 6: 1, 7: 1}
    assert counts.column('nnz').sum() / len(counts) == Q(3, 2)
    assert counts.column('nnz').min() == 1 and counts.column('nnz').max() == 2
    assert counts.column('nnz').value_counts().to_records() == [{'nnz': 1, 'count': 4}, {'nnz': 2, 'count': 4}]
    assert len(counts.head(2)) == 2
    left, right = (model.rows.head(1).vars, model.rows.iloc[1:2].vars)
    assert list((left & right).ids) == []
    assert list((left | right).ids) == list(range(8))
    assert list((v - left).ids) == [4, 5, 6, 7]
    assert left.jaccard(v) == 0.5
    assert left.jaccard(right) == 0
    assert left.head(0).jaccard(right.head(0)) == 1
    assert isinstance(left.rows, mf.RowFrame)
    expected = {rid: sum((int(var) in set(left.ids) for var in model.model.col_idx[int(model.model.row_ptr[rid]):int(model.model.row_ptr[rid + 1])])) for rid in model.rows.ids}
    expected = {r: n for r, n in expected.items() if n}
    assert {r.row.id: r.nnz for r in left.incidence_counts()} == expected
    assert list(left.rows.ids) == sorted(expected)
    assert list((r & model.rows.iloc[1:5]).ids) == [1, 2]
    with pytest.raises(TypeError):
        left & r
    with pytest.raises(TypeError):
        left & [1]

def test_unused_numeric_options_removed():
    assert not hasattr(mf.options, 'numeric')
