"""Structural refinement contracts, including an independent small-graph oracle."""

from model_helpers import family_catalog
from mipframe.ir.signatures import VarFamilyFrame
from mipframe.ir.families import FamilyFrame


from collections import Counter
from fractions import Fraction as Q
from itertools import combinations
from pathlib import Path
import sqlite3

import numpy as np
import pytest

import mipframe as mf
from mipframe.frame.entity import _sense
from mipframe.ir.signatures import StructuralSignature
from mipframe.ir import refinement_store as engine
from mipframe.ir.typed import bound_class


TEXT = '''Minimize
 obj: 0 a + 0 b + 0 c + 0 d + 0 e + 0 f + 2 g - 3 h + 0 unused
Subject To
 assign_a: a + b + c = 1
 assign_b: d + e + f = 1
 cap_a: 7a + 3g <= 10
 cap_b: 7b + 3g <= 10
 cap_c: 7c + 3g <= 10
 link_d: d - h <= 0
 link_e: e - h <= 0
 link_f: f - h <= 0
 eq_zero: a - b = 0
 eq_neg: -0.5a + 0.5b = 0
 eq_positive: c - g = 2
 eq_reversed: -2c + 2g = -4
 greater: -2d + 2h >= 0
 ranged: -2 <= 2g + 4h <= 6
 empty: a - a = 0
Bounds
 g free
 h <= 0
 h >= -inf
Binaries
 a b c d e f unused
End
'''


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
    rows, variables = list(m.rows), list(m.variables)
    rb, vb, edges, duals = [], [], [], []
    for row in rows:
        terms = list(m.iter_terms(row[0]))
        scale = max((abs(c) for _, c in terms), default=Q(1))
        lo, hi = m.num(row[2]), m.num(row[3])
        sense = _sense(lo, hi)
        if sense == '>=':
            lo, hi, scale, sense = None, -lo, -scale, '<='
        elif sense == '=' and hi < 0:
            lo, hi, scale = -hi, -lo, -scale
        lo = None if lo is None else lo/abs(scale)
        hi = None if hi is None else hi/abs(scale)
        classify = lambda x: engine.number_class(x, policy)
        rhs = (classify(lo), classify(hi)) if sense in ('range', 'free') else classify(hi)
        rb.append((sense, rhs))
        duals.append(sense == '=' and hi == 0)
        edges.append([(v, c/scale) for v, c in terms])
    for v in variables:
        vb.append((v[2], bound_class(m.num(v[3]), m.num(v[4])), engine.number_class(m.num(v[5]))))
    def intern(keys):
        seen = {}
        return [seen.setdefault(key, len(seen)) for key in keys]
    rc, vc = [intern(rb)], [intern(vb)]
    pos, neg = intern(rb), [color if dual else -1 for color, dual in zip(intern(rb), duals)]
    def counted(tokens):
        return tuple(sorted(Counter(tokens).items()))
    for t in range(depth):
        state_keys = []
        indexes = []
        for r, terms in enumerate(edges):
            p = len(state_keys)
            state_keys.append((pos[r], counted((c, vc[t][v]) for v, c in terms)))
            n = -1
            if duals[r]:
                n = len(state_keys)
                state_keys.append((neg[r], counted((-c, vc[t][v]) for v, c in terms)))
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
        pos, neg = new_pos, new_neg
    return rc, vc


def test_reference_sync_depth_and_monotonicity(model):
    s = model.structure.signatures(mode="exact", depth=3)
    rc, vc = reference(model.model, 3)
    for d in range(4):
        assert list(s.row_color[d]) == rc[d]
        assert list(s.var_color[d]) == vc[d]
        for levels in (rc, vc):
            if d:
                assert all(levels[d-1][a] == levels[d-1][b]
                           for a, b in combinations(range(len(levels[d])), 2) if levels[d][a] == levels[d][b])
    assert s.rows.columns == ('row', 'name', 'R0', 'R1', 'R2', 'R3')
    assert s.vars.columns == ('var', 'name', 'V0', 'V1', 'V2', 'V3')
    assert s.row_color[0].dtype == np.dtype('int32')
    with pytest.raises(TypeError):
        s.row_color[0][0] = 99
    assert not np.asarray(s.row_color[0]).flags.writeable
    assert not s.row_color[0][:3].flags.writeable
    for d in (-1, True, 4, 1.0):
        with pytest.raises(ValueError):
            s.row_color[d]


def test_two_hop_roles_not_algebraic(model):
    rows = model.rows
    a, b = rows.by_name('assign_a').id, rows.by_name('assign_b').id
    s = model.structure.refine(mode="exact", depth=3)
    assert rows.by_name('assign_a').signature('typed') == rows.by_name('assign_b').signature('typed')
    assert s.row_color[0][a] == s.row_color[0][b]
    assert s.row_color[1][a] == s.row_color[1][b]
    assert s.row_color[2][a] != s.row_color[2][b]
    assert s.depth == 3 and set(s.row_color) == {0, 1, 2, 3}


def test_exact_normalization_direction_and_patterns(model):
    s = model.structure.signatures(mode="exact", depth=2)
    rows = model.rows
    for (left, right) in [
        ('eq_zero', 'eq_neg'),
        ('eq_positive', 'eq_reversed'),
        ('link_d', 'greater'),
    ]:
        for d in range(3):
            assert s.row_color[d][rows.by_name(left).id] == s.row_color[d][rows.by_name(right).id]
    sig = rows.by_name('cap_a').signature('exact_structural', depth=1)
    assert isinstance(sig, mf.StructuralSignature)
    assert sorted(c for c, _, _ in sig.pattern) == [Q(3, 7), Q(1)]
    assert sig.base == ('<=', 'positive')
    assert sig.parent_color is not None
    assert sig == rows.by_name('cap_b').signature('exact_structural', depth=1)
    assert sig != rows.by_name('cap_b').signature('exact_structural', depth=2)
    eq = rows.by_name('eq_zero').signature('exact_structural', depth=1)
    assert len(eq.patterns) in (1, 2)
    assert rows.by_name('empty').signature('exact_structural', depth=2).pattern[:] == ()
    ranged = rows.by_name('ranged').signature('exact_structural', depth=0)
    assert ranged.base == ('range', ('negative', 'positive'))


@pytest.mark.parametrize("mode", ["exact", "template"])
def test_symmetric_zero_equality_never_chooses_input_sign(tmp_path, mode):
    base = 'Minimize\n o: 0x + 0y\nSubject To\n r: %s = 0\nBounds\n x free\n y free\nEnd\n'
    with read_text(tmp_path, base % 'x - y', 'a.lp') as a, read_text(tmp_path, base % '-x + y', 'b.lp') as b:
        sa, sb = a.structure.refine(mode=mode, depth=4), b.structure.refine(mode=mode, depth=4)
        for d in range(5):
            assert sa.var_color[d][0] == sa.var_color[d][1]
            assert list(sa.var_color[d]) == list(sb.var_color[d])
            assert list(sa.row_color[d]) == list(sb.row_color[d])
        assert len(sa._row_families().loc['F0'].signature.patterns) == 1


@pytest.mark.parametrize("mode", ["exact", "template"])
def test_isomorphic_permutations_and_whole_row_sign(tmp_path, mode):
    atext = 'Minimize\n o: 0x + 0y + 0z\nSubject To\n a: x-y=0\n b: 2y+z<=3\n c: z<=1\nBinaries\n x y z\nEnd\n'
    btext = 'Minimize\n o: 0Z + 0Y + 0X\nSubject To\n C: 3Z<=9\n B: 0.25Z+0.5Y<=0.75\n A: Y-X=0\nBinaries\n Z Y X\nEnd\n'
    with read_text(tmp_path, atext, 'a.lp') as a, read_text(tmp_path, btext, 'b.lp') as b:
        sa, sb = a.structure.refine(mode=mode, depth=4), b.structure.refine(mode=mode, depth=4)
        for kind in ('row', 'var'):
            names = a.model.row_names if kind == 'row' else a.model.var_names
            other = b.model.row_names if kind == 'row' else b.model.var_names
            for d in range(5):
                ca, cb = getattr(sa, f'{kind}_color')[d], getattr(sb, f'{kind}_color')[d]
                for x, y in combinations(names, 2):
                    assert (ca[names[x]] == ca[names[y]]) == (cb[other[x.upper()]] == cb[other[y.upper()]])


def test_families_selection_exploration_and_bound_stats(model):
    s = model.structure.refine(mode="exact", depth=2)
    families = s._row_families(depth=1)
    assert isinstance(families, FamilyFrame)
    assert sum(record['count'] for record in families) == model.shape[0]
    f = families.loc['F0']
    assert f.rows.sample(min(2, f.count), random_state=7).show(formula=True, max_terms=2)
    assert f.rows.neighbors().incidence_counts().column('nnz').count() > 0
    assert f.rows.jaccard(f.rows) == 1
    assert len(f.rows & f.rows) == f.count
    assert f.rhs.describe()['count'] == f.count
    assert len(f.rhs.head(1)) == 1
    selected = model.rows.sample(6, random_state=2)
    sf = family_catalog(selected, signature='exact_structural', depth=2)
    assert sum(record['count'] for record in sf) == len(selected)
    for record in sf:
        family = sf.loc[record['id']]
        assert all(s.row_color[2][r] == family.signature.color for r in family.rows.ids)
    assert list(selected.signature('exact_structural',depth=2).column('row').ids)==list(selected.ids)
    variables = s._var_families(depth=2)
    assert isinstance(variables, VarFamilyFrame)
    assert sum(record['count'] for record in variables) == model.shape[1]
    vf = variables.loc['F0']
    assert isinstance(vf, mf.VariableFamily)
    assert len(vf.vars) == vf.count
    assert vf.vars.neighbors().shape[0] > 0
    assert 'Rstate' in repr(vf.signature.pattern)
    assert 'exact' in repr(vf)
    assert families.head(2).shape == (2, 7)
    assert len(families.tail(1)) == 1
    assert len(families.sample(1, random_state=3)) == 1
    assert len(family_catalog(model.rows, signature='exact_structural', depth=1)) == len(s._row_families(depth=1))
    with pytest.raises(TypeError, match='level'):
        model.rows.families(signature='typed', level='structural')
    with pytest.raises(TypeError, match='level'):
        model.rows.families(level='structural', depth=1)
    with pytest.raises(ValueError):
        model.rows.signature('typed', depth=2)


def test_rhs_parameter_distribution_not_exact_family(tmp_path):
    text = 'Minimize\n o: 17x + 18y\nSubject To\n a: 2x <= 4\n b: 2y <= 10\n c: -2x <= -4\n d: 2y >= 4\nBinaries\n x y\nEnd\n'
    with read_text(tmp_path, text) as m:
        s = m.structure.refine(mode="exact", depth=0)
        assert s.var_color[0][0] == s.var_color[0][1]
        assert s.row_color[0][0] == s.row_color[0][1]
        assert s.row_color[0][0] != s.row_color[0][2]
        assert s.row_color[0][2] == s.row_color[0][3]
        family = s._row_families().loc['F0']
        stats = family.rhs.describe()
        assert stats['count'] == 2 and stats['min'] == 2 and stats['max'] == 5
        assert stats['median'] == Q(7,2)
        coarse = m.structure.refine(mode="exact", depth=0, rhs='nonzero')
        assert len(coarse._row_families()) == 1
        assert coarse.config != s.config


def test_invalid_parameters_do_not_change_eager_cache(model):
    for kwargs in [
        {'depth': -1},
        {'depth': True},
        {'depth': 1.0},
        {'rhs': 'exact'},
        {'coefficient': 'raw'},
        {'objective': 'exact'},
        {'memory_mb': 0},
        {'memory_mb': True},
        {'memory_mb': float('nan')},
    ]:
        before = tuple(model._refinement_store.db.iterdump())
        with pytest.raises(ValueError):
            model.structure.signatures(mode="exact", **kwargs)
        assert tuple(model._refinement_store.db.iterdump()) == before


def test_read_eager_ir_display_and_saved_reuse(tmp_path, monkeypatch):
    with read_text(tmp_path) as m:
        before = tuple(m._refinement_store.db.iterdump())
        repr(m)
        repr(m.rows.head())
        assert m.ir.structure['depth'] == 2
        assert tuple(m._refinement_store.db.iterdump()) == before
        s = m.structure.refine(mode="exact", depth=2)
        previous = np.asarray(s.row_color[2])
        target = m.save(tmp_path/'snapshot')
    with mf.load(target) as m:
        with monkeypatch.context() as patch:
            patch.setattr(engine.RefinementStore, '_prepare', lambda *a: pytest.fail('must reuse saved base'))
            patch.setattr(engine.RefinementStore, '_round', lambda *a: pytest.fail('must reuse saved layers'))
            cached = m.structure.signatures(mode="exact", depth=2)
            assert np.array_equal(np.asarray(cached.row_color[2]), previous)
        expanded = m.structure.refine(mode="exact", depth=3)
        assert np.array_equal(np.asarray(expanded.row_color[2]), previous)
        assert set(cached.row_color) == {0,1,2}
        assert expanded.depth == 3
        frame = cached.rows.head()
        vector = cached.row_color[2]
        family = cached._row_families().loc['F0']
    for operation in (lambda: len(frame), lambda: vector[0], lambda: repr(family), lambda: repr(family.rhs)):
        with pytest.raises(mf.MIPFrameError, match='closed'):
            operation()


def test_failed_round_preserves_checkpoint(model, monkeypatch):
    s = model.structure.refine(mode="exact", depth=1)
    store = s._store
    previous = np.asarray(s.row_color[1])
    original = store._round
    def fail(*args):
        original(*args)
        raise RuntimeError('injected publication failure')
    with monkeypatch.context() as patch:
        patch.setattr(store, '_round', fail)
        with pytest.raises(RuntimeError, match='injected'):
            model.structure.refine(mode="exact", depth=2)
    assert store.db.execute('SELECT depth FROM configs WHERE id=?', (s._config,)).fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM definitions WHERE config=? AND depth=2', (s._config,)).fetchone()[0] == 0
    assert np.array_equal(np.asarray(s.row_color[1]), previous)
    recovered = model.structure.refine(mode="exact", depth=2)
    expected, _ = reference(model.model, 2)
    assert list(recovered.row_color[2]) == expected[2]


def test_hash_collision_compares_exact_keys(model, monkeypatch):
    class ConstantHash:
        def update(self, data):
            pass
        def digest(self):
            return b'collision'
    monkeypatch.setattr(engine, '_new_hash', ConstantHash)
    s = model.structure.refine(mode="exact", depth=2)
    rows, vars = reference(model.model, 2)
    assert list(s.row_color[2]) == rows[2]
    assert list(s.var_color[2]) == vars[2]


def test_version_mismatch(tmp_path):
    with read_text(tmp_path) as m:
        m.structure.refine(mode="exact", depth=0)
        target = m.save(tmp_path/'snapshot')
    db = sqlite3.connect(target/'refinement.sqlite')
    db.execute("UPDATE meta SET value='[\"wrong\",\"0\"]' WHERE key='source'")
    db.commit()
    db.close()
    with pytest.raises(mf.MIPFrameError, match='mismatch'):
        mf.load(target)


@pytest.mark.parametrize("mode", ["exact", "template"])
def test_range_free_isolated_and_depth_zero_nnz_range(tmp_path, mode):
    text = 'Minimize\n o: 0x + 0y + 0isolated\nSubject To\n r: -2 <= 2x+4y <= 6\n s: x+y <= 5\n t: x<=7\n empty: x-x=0\nEnd\n'
    with read_text(tmp_path, text) as m:
        s = m.structure.refine(mode=mode, depth=0)
        ranged = next(f for f in s._row_families() if f['base'][0] == 'range')
        f = s._row_families().loc[ranged['id']]
        assert f.lhs.describe()['min'] == Q(-1,2)
        assert f.rhs.describe()['max'] == Q(3,2)
        assert any(f['nnz'] == (1,2) for f in s._row_families())
        isolated = m.model.var_names['isolated']
        s2 = m.structure.refine(mode=mode, depth=2)
        assert len(StructuralSignature(s2, 'var', isolated).pattern) == 0
    with read_text(tmp_path, 'NAME FREE\nROWS\n N OBJ\n N FREE_ROW\nCOLUMNS\n X OBJ 1 FREE_ROW 1\nBOUNDS\n FR B X\nENDATA\n', 'free.mps') as m:
        s = m.structure.refine(mode=mode, depth=1)
        assert StructuralSignature(s, 'row', 0).base == ('free', ('infinite','infinite'))


def test_large_row_disk_rle_and_bounded_display(tmp_path, monkeypatch):
    n = 100_000
    text = 'Minimize\n o: 0x0\nSubject To\n huge: ' + ' + '.join(f'x{i}' for i in range(n)) + ' <= 1\nBinaries\n' + ' '.join(f'x{i}' for i in range(n)) + '\nEnd\n'
    with read_text(tmp_path, text) as m:
        used_disk = []
        original = engine.RefinementStore._aggregate
        def aggregate(self, blocks, size):
            if size > self.memory//128:
                used_disk.append(size)
            yield from original(self, blocks, size)
        monkeypatch.setattr(engine.RefinementStore, '_aggregate', aggregate)
        monkeypatch.setattr(type(m.model), 'row', lambda *a: pytest.fail('no row dict'))
        monkeypatch.setattr(type(m.model), 'row_ids', lambda *a: pytest.fail('no row list'))
        monkeypatch.setattr(type(m.model), 'iter_terms', lambda *a: pytest.fail('use integer sparse arrays'))
        s = m.structure.refine(mode="exact", depth=1, memory_mb=0.0625)
        assert used_disk == [n]
        pattern = m.rows.loc[0].signature('exact_structural', depth=1, memory_mb=0.0625).pattern
        assert len(pattern) == 1 and pattern[0] == (Q(1), 0, n)
        assert '100,000' in repr(pattern) and len(repr(pattern)) < 100
        assert len(s._var_families()) == 1
        assert s._var_families().loc['F0'].nnz == 1
        assert s._store.db.execute("SELECT sum(length(data)) FROM key_chunks WHERE config=? AND kind='state' AND depth=1", (s._config,)).fetchone()[0] == engine.TOKEN.size


@pytest.mark.parametrize("mode", ["exact", "template"])
def test_dual_states_preserve_strict_variable_hop_depth(tmp_path, mode):
    text = 'Minimize\n o: 0x + 0y + 0z + 0w\nSubject To\n a: x-y=0\n b: z-w=0\nBinaries\n x z w\nEnd\n'
    reversed_text = text.replace('x-y=0', '-x+y=0')
    with read_text(tmp_path, text, 'a.lp') as a, read_text(tmp_path, reversed_text, 'b.lp') as b:
        sa, sb = a.structure.refine(mode=mode, depth=3), b.structure.refine(mode=mode, depth=3)
        x, z = a.model.var_names['x'], a.model.var_names['z']
        assert sa.var_color[0][x] == sa.var_color[0][z]
        assert sa.var_color[1][x] == sa.var_color[1][z]  # no premature look-through
        assert sa.var_color[2][x] != sa.var_color[2][z]
        assert sa.row_color[1][0] != sa.row_color[1][1]
        assert len(a.rows.loc[0].signature('structural' if mode == 'template' else 'exact_structural', depth=1).patterns) == 2
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
            model.structure.refine(mode="exact", depth=1)
    store = model._refinement_store
    assert [id(a) for a in store.maps] == maps
    assert set(Path(store.temp.name).iterdir()) == files
    assert tuple(store.db.iterdump()) == before
    s = model.structure.refine(mode="exact", depth=1)
    rc, vc = reference(model.model, 1)
    assert list(s.row_color[1]) == rc[1]
    assert list(s.var_color[1]) == vc[1]


def test_empty_graph_and_precision_without_tolerance(tmp_path, monkeypatch):
    with read_text(tmp_path, 'Minimize\n o: 0\nSubject To\nEnd\n', 'empty.lp') as m:
        s = m.structure.refine(mode="exact", depth=3)
        assert s.rows.shape == (0, 6) and s.vars.shape == (0, 6)
        assert len(s._row_families()) == len(s._var_families()) == 0
        assert list(s.row_color[3]) == []
    text = 'Minimize\n o: 0x + 0y + 0z + 0w\nSubject To\n a: 2x-y<=0\n b: 2.000000000000000000001z-w<=0\nBinaries\n x y z w\nEnd\n'
    monkeypatch.setattr(mf.options.numeric, 'rtol', 0.5)
    monkeypatch.setattr(mf.options.numeric, 'atol', 0.5)
    with read_text(tmp_path, text, 'precision.lp') as m:
        s = m.structure.refine(mode="exact", depth=1)
        assert s.row_color[1][0] != s.row_color[1][1]
