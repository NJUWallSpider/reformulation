"""Exact algebraic grouping and sparse exploration without structural analysis."""

from model_helpers import family_catalog
from mipframe.ir.families import FamilyFrame


from fractions import Fraction as Q
import gc
import sqlite3

import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    source = tmp_path / 'families.lp'
    source.write_text(
        'Minimize\n obj: 0x + 0y + 0z + 0w + 0p + 0q + 0s + 0t + 0unused\nSubject To\n'
        ' a: 4x - 2y - 2z - 2w <= 0\n'
        ' b: 0.5p - 0.25q - 0.25s - 0.25t <= 0\n'
        ' reverse: -4x + 2y + 2z + 2w >= 0\n'
        ' other_rhs: 4x - 2y - 2z - 2w <= 2\n'
        ' eq: x - y - z = 3\n eq_reverse: -2p + 2q + 2s = -6\n'
        ' symmetric: x - y = 3\n symmetric_reverse: -x + y = -3\n'
        ' near: 2.000000000000000000001x - y - z - w <= 0\n'
        ' ranged: -2 <= 2x + 4y <= 6\n ranged_scaled: -1 <= p + 2q <= 3\n'
        ' empty: x - x = 0\n empty_scaled: y - y = 0\n'
        ' infeasible_empty: x - x = 1\n'
        ' single: z <= 1\n'
        'Bounds\n unused free\nEnd\n'
    )
    with mf.read(source) as m:
        yield m


def test_exact_signatures_scale_direction_rhs_and_counts(model, monkeypatch):
    a = model.rows.by_name('a').signature()
    assert a.sense == '<=' and a.nnz == 4 and a.rhs == 0 and a.lhs is None
    assert a.coeff_multiset == ((Q(-1), 3), (Q(2), 1))
    assert a.scale == 2 and a.numeric_kind == 'exact'
    assert a == model.rows.by_name('b').signature() == model.rows.by_name('reverse').signature()
    assert model.rows.by_name('b').signature().scale == Q(1, 4)
    assert model.rows.by_name('reverse').signature().scale == -2
    assert a != model.rows.by_name('other_rhs').signature()
    assert a != model.rows.by_name('near').signature()
    assert model.rows.by_name('eq').signature() == model.rows.by_name('eq_reverse').signature()
    assert model.rows.by_name('eq').signature().pattern == ((Q(-1), 1), (Q(1), 2))
    assert model.rows.by_name('eq').signature().rhs == -3
    assert model.rows.by_name('symmetric').signature() == model.rows.by_name('symmetric_reverse').signature()
    assert model.rows.by_name('symmetric').signature().rhs == -3
    ranged = model.rows.by_name('ranged').signature()
    assert ranged == model.rows.by_name('ranged_scaled').signature()
    assert (ranged.lhs, ranged.rhs) == (-1, 3)
    assert model.rows.by_name('empty').signature().pattern == ()
    assert model.rows.by_name('empty').signature() != model.rows.by_name('infeasible_empty').signature()
    assert a == model.rows.by_name('a').signature('algebraic')
    monkeypatch.setattr(mf.options.numeric, 'rtol', 0.1)
    monkeypatch.setattr(mf.options.numeric, 'atol', 0.1)
    assert a != model.rows.by_name('near').signature()  # Exact input never uses tolerances.


def test_canonical_ratio_is_exact():
    assert mf.canonical_ratio([4, -2, -2, -2]) == 2
    assert mf.canonical_ratio(['0.5', '-0.25', '-0.25']) == Q(1, 4)
    assert mf.canonical_ratio([Q(2, 3), Q(-4, 5)]) == Q(2, 15)
    assert mf.canonical_ratio([]) == mf.canonical_ratio([0]) == 1
    with pytest.raises(TypeError, match='exact'):
        mf.canonical_ratio([0.5])


def test_equality_sign_ties_and_permutation_invariance(tmp_path):
    source = tmp_path / 'permutations.lp'
    source.write_text(
        'Minimize\n obj: 0\nSubject To\n'
        ' a: x - y = 0\n b: -0.25p + 0.25q = 0\n'
        ' c: -y + x = 0\n d: q - p = 0\n'
        'Bounds\nEnd\n'
    )
    with mf.read(source) as model:
        assert len({model.rows.loc[i].signature() for i in range(4)}) == 1
        family = family_catalog(model.rows, ).loc['F0']
        assert family.count == 4
        repeated = mf.RowFrame(model, (0, 0, 1))
        assert family_catalog(repeated, ).loc['F0'].count == 2
        assert repeated.incidence_counts().collect() == model.rows.head(2).incidence_counts().collect()


def test_family_membership_signatures_selection_and_empty(model, capsys):
    families = family_catalog(model.rows, signature='algebratic')
    assert isinstance(families, FamilyFrame)
    assert sum(r['count'] for r in families) == len(model.rows)
    assert families[0] == {'id': 'F0', 'count': 3, 'nnz': 4, 'sense': '<=', 'rhs': Q(0),
                           'pattern': ((Q(-1), 3), (Q(2), 1))}
    family = families.loc['F0']
    assert isinstance(family, mf.ConstraintFamily)
    assert list(family.rows.ids) == [0, 1, 2]
    assert [r['name'] for r in family.rows] == ['a', 'b', 'reverse']
    assert list(family.rows.head(1).ids) == [0]
    assert list(family.rows.tail(1).ids) == [2]
    assert repr(family.rows.head(1)).startswith('<RowFrame')
    assert list(family.rows.iloc[::-1].ids) == [2, 1, 0]
    assert family.rows.by_name('b').id == 1
    with pytest.raises(KeyError):
        family.rows.head(1).by_name('b')
    assert list(family.rows.sample(2, random_state=42).ids) == list(family.rows.sample(2, random_state=42).ids)
    assert repr(families).startswith(f'<FamilyFrame: {len(families)} of {len(families)} families>')
    assert '[+2, -1 × 3]' in repr(families)
    assert repr(family) == ('ConstraintFamily("F0")\n'
        '  rows          : 3\n  nnz / row     : 4\n  sense         : <=\n'
        '  normalized rhs: 0\n  pattern       : [+2, -1 × 3]')
    assert families.head(1)[0] == families[0]
    assert families.tail(1)[0] == families[-1]
    assert families.sample(2, random_state=9).ids == families.sample(2, random_state=9).ids
    assert families.value_counts('sense')['<='] >= 1
    assert families.describe()['count']['count'] == len(families)
    assert capsys.readouterr().out == ''
    for label in ('F999', 'F-1', 'F01', 'bad', ''):
        with pytest.raises(KeyError):
            families.loc[label]
    with pytest.raises(TypeError):
        families.loc[0]
    with pytest.raises(KeyError):
        families.head(1).loc['F1']
    selected = family_catalog(model.rows.tail(2), )
    assert sum(f['count'] for f in selected) == 2
    assert all(r in model.rows.tail(2).ids for f in selected for r in selected.loc[f['id']].rows.ids)
    # Membership remains alive after the original FamilyFrame is collected.
    del families
    gc.collect()
    assert family.rows.tail(1).by_name('reverse').id == 2
    with pytest.raises(sqlite3.OperationalError):
        family._store.db.execute('DELETE FROM members')

    sigs = model.rows.head(3).signature()
    assert isinstance(sigs, mf.Frame)
    assert list(sigs.column('relation')) == ['<='] * 3
    assert {r.relation: r.count for r in sigs.column('relation').value_counts()} == {'<=': 3}
    assert sigs.iloc[0].scale == 2 and sigs.iloc[1].scale == Q(1, 4)
    assert sigs.tail(1).one().scale == -2
    assert list(sigs.sample(2, random_state=4).column('row')) == list(sigs.sample(2, random_state=4).column('row'))
    for call in (model.rows.signature, model.rows.families, model.rows.loc[0].signature):
        with pytest.raises(ValueError, match='signature'):
            call('unknown_signature')

    rows = model.rows.head(0)
    families = family_catalog(rows, )
    assert len(families) == 0 and '0 of 0 families' in repr(families)
    assert len(rows.neighbors()) == len(model.rows.by_name('empty').terms) == 0
    counts = rows.incidence_counts()
    assert counts.column('nnz').count()==0 and counts.column('nnz').sum()==0
    assert counts.column('nnz').value_counts().head().collect()==[]
    assert len(model.vars.tail(1).neighbors()) == 0  # Isolated objective-only variable.
    assert repr(model.rows.head(3).neighbors().head(1)).startswith('<VarFrame')
    assert 'Frame' in repr(counts)


def test_neighbors_degrees_and_set_operations_do_not_load_coefficients(model, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Incidence must not read coefficients')
    monkeypatch.setattr(model.model, 'num', forbidden)
    monkeypatch.setattr(model.model, 'iter_terms', forbidden)
    r = model.rows.head(3)
    v = r.neighbors()
    assert isinstance(v, mf.VarFrame) and list(v.ids) == list(range(8))
    counts = r.incidence_counts()
    assert {r.var.id:r.nnz for r in counts} == {0: 2, 1: 2, 2: 2, 3: 2, 4: 1, 5: 1, 6: 1, 7: 1}
    assert counts.column('nnz').sum()/len(counts)==Q(3,2)
    assert counts.column('nnz').min()==1 and counts.column('nnz').max()==2
    assert counts.column('nnz').value_counts().collect()==[{'nnz':1,'count':4},{'nnz':2,'count':4}]
    assert len(counts.head(2)) == 2
    left, right = model.rows.head(1).neighbors(), model.rows.iloc[1:2].neighbors()
    assert list((left & right).ids) == []
    assert list((left | right).ids) == list(range(8))
    assert list((v - left).ids) == [4, 5, 6, 7]
    assert left.jaccard(v) == 0.5
    assert left.jaccard(right) == 0
    assert left.head(0).jaccard(right.head(0)) == 1
    assert isinstance(left.neighbors(), mf.RowFrame)
    expected = {rid: sum(int(var) in set(left.ids) for var in model.model.col_idx[
        int(model.model.row_ptr[rid]):int(model.model.row_ptr[rid + 1])]) for rid in model.rows.ids}
    expected = {r: n for r, n in expected.items() if n}
    assert {r.row.id:r.nnz for r in left.incidence_counts()} == expected
    assert list(left.neighbors().ids) == sorted(expected)
    assert list((r & model.rows.iloc[1:5]).ids) == [1, 2]
    with pytest.raises(TypeError):
        left & r
    with pytest.raises(TypeError):
        left & [1]


def test_save_reload_close_and_model_identity(model, tmp_path):
    families = family_catalog(model.rows, )
    family, sigs = families.loc['F0'], model.rows.signature()
    counts, neighbors = family.rows.incidence_counts(), family.rows.neighbors()
    snapshot = families.to_records()
    path = model.save(tmp_path / 'saved')
    with mf.load(path) as reloaded:
        assert family_catalog(reloaded.rows, ).to_records() == snapshot
        assert len(model.vars & reloaded.vars)==len(model.vars)
    model.close()
    for call in (lambda: repr(families), lambda: family.rows.head(), lambda: repr(family),
                 lambda: sigs.head(), lambda: counts.column('nnz').max(), lambda: neighbors.head()):
        with pytest.raises(mf.MIPFrameError, match='closed'):
            call()


def test_numeric_options_validation():
    for setting in ['rtol', 'atol']:
        for value in (-1, float('nan'), float('inf')):
            with pytest.raises(ValueError):
                setattr(mf.options.numeric, setting, value)
        for value in (True, '0.01', None):
            with pytest.raises(TypeError):
                setattr(mf.options.numeric, setting, value)
