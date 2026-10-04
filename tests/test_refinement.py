from model_helpers import refinement_probe, signature_key
from mipframe.ir.signatures import _Signature as StructuralSignature
"""Structural refinement contracts, including an independent small-graph oracle."""
from collections import Counter
from fractions import Fraction as Q
from itertools import combinations
from pathlib import Path
import sqlite3
import numpy as np
import pytest
import mipframe as mf
from mipframe.frame.entity import _sense
from mipframe.ir import refinement_store as engine
from mipframe.ir.typed import bound_class
TEXT = 'Minimize\n obj: 0 a + 0 b + 0 c + 0 d + 0 e + 0 f + 2 g - 3 h + 0 unused\nSubject To\n assign_a: a + b + c = 1\n assign_b: d + e + f = 1\n cap_a: 7a + 3g <= 10\n cap_b: 7b + 3g <= 10\n cap_c: 7c + 3g <= 10\n link_d: d - h <= 0\n link_e: e - h <= 0\n link_f: f - h <= 0\n eq_zero: a - b = 0\n eq_neg: -0.5a + 0.5b = 0\n eq_positive: c - g = 2\n eq_reversed: -2c + 2g = -4\n greater: -2d + 2h >= 0\n ranged: -2 <= 2g + 4h <= 6\n empty: a - a = 0\nBounds\n g free\n h <= 0\n h >= -inf\nBinaries\n a b c d e f unused\nEnd\n'

def read_text(tmp_path, text=TEXT, name='refinement.lp'):
    path = tmp_path / name
    path.write_text(text)
    return mf.read(path)

@pytest.fixture
def model(tmp_path):
    with read_text(tmp_path) as model:
        yield model

def reference(m, depth, policy='sign'):
    """Ordinary dictionary WL on the explicit orientation-expanded small graph."""
    rows, variables = (list(m.rows), list(m.variables))
    rb, vb, edges, duals = ([], [], [], [])
    for row in rows:
        terms = list(m.iter_terms(row[0]))
        scale = max((abs(c) for _, c in terms), default=Q(1))
        lo, hi = (m.num(row[2]), m.num(row[3]))
        sense = _sense(lo, hi)
        if sense == '>=':
            lo, hi, scale, sense = (None, -lo, -scale, '<=')
        elif sense == '=' and hi < 0:
            lo, hi, scale = (-hi, -lo, -scale)
        lo = None if lo is None else lo / abs(scale)
        hi = None if hi is None else hi / abs(scale)
        classify = lambda x: engine.number_class(x, policy)
        rhs = (classify(lo), classify(hi)) if sense in ('range', 'free') else classify(hi)
        rb.append((sense, rhs))
        duals.append(sense == '=' and hi == 0)
        edges.append([(v, c / scale) for v, c in terms])
    for v in variables:
        vb.append((v[2], bound_class(m.num(v[3]), m.num(v[4])), engine.number_class(m.num(v[5]))))

    def intern(keys):
        seen = {}
        return [seen.setdefault(key, len(seen)) for key in keys]
    rc, vc = ([intern(rb)], [intern(vb)])
    pos, neg = (intern(rb), [color if dual else -1 for color, dual in zip(intern(rb), duals)])

    def counted(tokens):
        return tuple(sorted(Counter(tokens).items()))
    for t in range(depth):
        state_keys = []
        indexes = []
        for r, terms in enumerate(edges):
            p = len(state_keys)
            state_keys.append((pos[r], counted(((c, vc[t][v]) for v, c in terms))))
            n = -1
            if duals[r]:
                n = len(state_keys)
                state_keys.append((neg[r], counted(((-c, vc[t][v]) for v, c in terms))))
            indexes.append((p, n))
        colors = intern(state_keys)
        new_pos = [colors[p] for p, _ in indexes]
        new_neg = [colors[n] if n >= 0 else -1 for _, n in indexes]
        row_keys = [(rc[t][r], tuple(sorted([new_pos[r], new_neg[r]] if duals[r] else [new_pos[r]]))) for r in range(len(rows))]
        variable_tokens = [[] for _ in variables]
        for r, terms in enumerate(edges):
            for v, c in terms:
                variable_tokens[v].append((c, pos[r]))
                if duals[r]:
                    variable_tokens[v].append((-c, neg[r]))
        var_keys = [(vc[t][v], counted(variable_tokens[v])) for v in range(len(variables))]
        rc.append(intern(row_keys))
        vc.append(intern(var_keys))
        pos, neg = (new_pos, new_neg)
    return (rc, vc)

def test_reference_sync_depth_and_monotonicity(model):
    s = refinement_probe(model, mode='exact', depth=3)
    rc, vc = reference(model.model, 3)
    for d in range(4):
        assert list(s.row_color[d]) == rc[d]
        assert list(s.var_color[d]) == vc[d]
        for levels in (rc, vc):
            if d:
                assert all((levels[d - 1][a] == levels[d - 1][b] for a, b in combinations(range(len(levels[d])), 2) if levels[d][a] == levels[d][b]))
    rows = mf.analysis.signatures(model.rows, method='exact_structural', depth=3)
    assert {r.depth for r in rows.related('levels')} == {0, 1, 2, 3}
    assert s.row_color[0].dtype == np.dtype('int32')
    assert not s.row_color[0].flags.writeable


def test_two_hop_roles_not_algebraic(model):
    rows = model.rows
    a, b = (rows.by_name('assign_a').id, rows.by_name('assign_b').id)
    s = refinement_probe(model, mode='exact', depth=3)
    assert signature_key(rows.by_name('assign_a'), 'typed') == signature_key(rows.by_name('assign_b'), 'typed')
    assert s.row_color[0][a] == s.row_color[0][b]
    assert s.row_color[1][a] == s.row_color[1][b]
    assert s.row_color[2][a] != s.row_color[2][b]
    assert s.depth == 3 and set(s.row_color) == {0, 1, 2, 3}

def test_exact_normalization_direction_and_patterns(model):
    s = refinement_probe(model, mode='exact', depth=2)
    rows = model.rows
    for left, right in [('eq_zero', 'eq_neg'), ('eq_positive', 'eq_reversed'), ('link_d', 'greater')]:
        for d in range(3):
            assert s.row_color[d][rows.by_name(left).id] == s.row_color[d][rows.by_name(right).id]
    sig = signature_key(rows.by_name('cap_a'), 'exact_structural', depth=1)
    assert isinstance(sig, StructuralSignature)
    assert sorted((c for c, _, _ in sig.pattern)) == [Q(3, 7), Q(1)]
    assert sig.base == ('<=', 'positive')
    assert sig.parent_color is not None
    assert sig == signature_key(rows.by_name('cap_b'), 'exact_structural', depth=1)
    assert sig != signature_key(rows.by_name('cap_b'), 'exact_structural', depth=2)
    eq = signature_key(rows.by_name('eq_zero'), 'exact_structural', depth=1)
    assert len(eq.patterns) in (1, 2)
    assert signature_key(rows.by_name('empty'), 'exact_structural', depth=2).pattern[:] == ()
    ranged = signature_key(rows.by_name('ranged'), 'exact_structural', depth=0)
    assert ranged.base == ('range', ('negative', 'positive'))

@pytest.mark.parametrize('mode', ['exact', 'template'])
def test_isomorphic_permutations_and_whole_row_sign(tmp_path, mode):
    atext = 'Minimize\n o: 0x + 0y + 0z\nSubject To\n a: x-y=0\n b: 2y+z<=3\n c: z<=1\nBinaries\n x y z\nEnd\n'
    btext = 'Minimize\n o: 0Z + 0Y + 0X\nSubject To\n C: 3Z<=9\n B: 0.25Z+0.5Y<=0.75\n A: Y-X=0\nBinaries\n Z Y X\nEnd\n'
    with read_text(tmp_path, atext, 'a.lp') as a, read_text(tmp_path, btext, 'b.lp') as b:
        sa, sb = (refinement_probe(a, mode=mode, depth=4), refinement_probe(b, mode=mode, depth=4))
        for kind in ('row', 'var'):
            names = a.model.row_names if kind == 'row' else a.model.var_names
            other = b.model.row_names if kind == 'row' else b.model.var_names
            for d in range(5):
                ca, cb = (getattr(sa, f'{kind}_color')[d], getattr(sb, f'{kind}_color')[d])
                for x, y in combinations(names, 2):
                    assert (ca[names[x]] == ca[names[y]]) == (cb[other[x.upper()]] == cb[other[y.upper()]])

def test_invalid_parameters_do_not_change_eager_cache(model):
    for kwargs in [{'depth': -1}, {'depth': True}, {'depth': 1.0}, {'rhs': 'exact'}, {'coefficient': 'raw'}, {'objective': 'exact'}, {'memory_mb': 0}, {'memory_mb': True}, {'memory_mb': float('nan')}]:
        before = tuple(model._refinement_store.db.iterdump())
        with pytest.raises(ValueError):
            refinement_probe(model, mode='exact', **kwargs)
        assert tuple(model._refinement_store.db.iterdump()) == before

def test_failed_round_preserves_checkpoint(model, monkeypatch):
    s = refinement_probe(model, mode='exact', depth=1)
    store = s._store
    previous = np.asarray(s.row_color[1])
    original = store._round

    def fail(*args):
        original(*args)
        raise RuntimeError('injected publication failure')
    with monkeypatch.context() as patch:
        patch.setattr(store, '_round', fail)
        with pytest.raises(RuntimeError, match='injected'):
            refinement_probe(model, mode='exact', depth=2)
    assert store.db.execute('SELECT depth FROM configs WHERE id=?', (s._config,)).fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM definitions WHERE config=? AND depth=2', (s._config,)).fetchone()[0] == 0
    assert np.array_equal(np.asarray(s.row_color[1]), previous)
    recovered = refinement_probe(model, mode='exact', depth=2)
    expected, _ = reference(model.model, 2)
    assert list(recovered.row_color[2]) == expected[2]

def test_hash_collision_compares_exact_keys(model, monkeypatch):

    class ConstantHash:

        def update(self, data):
            pass

        def digest(self):
            return b'collision'
    monkeypatch.setattr(engine, '_new_hash', ConstantHash)
    s = refinement_probe(model, mode='exact', depth=2)
    rows, vars = reference(model.model, 2)
    assert list(s.row_color[2]) == rows[2]
    assert list(s.var_color[2]) == vars[2]

def test_version_mismatch(tmp_path):
    with read_text(tmp_path) as m:
        refinement_probe(m, mode='exact', depth=0)
        target = m.save(tmp_path / 'snapshot')
    db = sqlite3.connect(target / 'refinement.sqlite')
    db.execute('UPDATE meta SET value=\'["wrong","0"]\' WHERE key=\'source\'')
    db.commit()
    db.close()
    with pytest.raises(mf.MIPFrameError, match='mismatch'):
        mf.load(target)

@pytest.mark.parametrize('mode', ['exact', 'template'])
def test_dual_states_preserve_strict_variable_hop_depth(tmp_path, mode):
    text = 'Minimize\n o: 0x + 0y + 0z + 0w\nSubject To\n a: x-y=0\n b: z-w=0\nBinaries\n x z w\nEnd\n'
    reversed_text = text.replace('x-y=0', '-x+y=0')
    with read_text(tmp_path, text, 'a.lp') as a, read_text(tmp_path, reversed_text, 'b.lp') as b:
        sa, sb = (refinement_probe(a, mode=mode, depth=3), refinement_probe(b, mode=mode, depth=3))
        x, z = (a.model.var_names['x'], a.model.var_names['z'])
        assert sa.var_color[0][x] == sa.var_color[0][z]
        assert sa.var_color[1][x] == sa.var_color[1][z]
        assert sa.var_color[2][x] != sa.var_color[2][z]
        assert sa.row_color[1][0] != sa.row_color[1][1]
        assert len(signature_key(a.rows.loc[0], 'structural' if mode == 'template' else 'exact_structural', depth=1).patterns) == 2
        for d in range(4):
            assert list(sa.row_color[d]) == list(sb.row_color[d])
            assert list(sa.var_color[d]) == list(sb.var_color[d])

def test_base_failure_can_retry_and_does_not_leak_work_files(model, monkeypatch):
    store = model._refinement_store
    maps = [id(a) for a in store.maps]
    files = set(Path(store.temp.name).iterdir())
    before = tuple(store.db.iterdump())
    original = engine.RefinementStore._prepare

    def fail(self, *args):
        original(self, *args)
        raise RuntimeError('base failure')
    with monkeypatch.context() as patch:
        patch.setattr(engine.RefinementStore, '_prepare', fail)
        with pytest.raises(RuntimeError, match='base failure'):
            refinement_probe(model, mode='exact', depth=1)
    store = model._refinement_store
    assert [id(a) for a in store.maps] == maps
    assert set(Path(store.temp.name).iterdir()) == files
    assert tuple(store.db.iterdump()) == before
    s = refinement_probe(model, mode='exact', depth=1)
    rc, vc = reference(model.model, 1)
    assert list(s.row_color[1]) == rc[1]
    assert list(s.var_color[1]) == vc[1]
