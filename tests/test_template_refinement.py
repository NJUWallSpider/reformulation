from model_helpers import refinement_probe, signature_key
from mipframe.ir.signatures import _Signature as StructuralSignature
"""Parameterized refinement and independently aggregated numerical profiles."""
from collections import Counter
from collections import defaultdict
from fractions import Fraction as Q
from itertools import combinations
import json
import sqlite3
from external_models import model_path
import pytest
import mipframe as mf
from mipframe.frame.entity import _sense
from mipframe.ir import refinement_store as engine
from mipframe.ir.typed import bound_class
from test_refinement import read_text
from test_refinement import TEXT

def template_reference(m, depth, ratio=Q(2)):
    """Small-graph dictionary oracle; no production grouping/key routines."""

    def intern(keys):
        seen = {}
        return [seen.setdefault(k, len(seen)) for k in keys]

    def partition(old, counts):
        groups = defaultdict(list)
        for i, tokens in enumerate(counts):
            groups[old[i], tuple(sorted(tokens))].append(i)
        keys = [None] * len(old)
        for skeleton, members in groups.items():
            modes = {}
            for role in skeleton[1]:
                values = sorted({counts[i][role] for i in members})
                mode = 0
                modes[role] = {values[0]: mode}
                for a, b in zip(values, values[1:]):
                    mode += b > ratio * a
                    modes[role][b] = mode
            for i in members:
                keys[i] = (skeleton, tuple(((role, modes[role][counts[i][role]]) for role in skeleton[1])))
        return intern(keys)

    def category(v):
        return 'infinite' if v is None else 'zero' if v == 0 else 'positive' if v > 0 else 'negative'
    row_base, edges, dual = ([], [], [])
    for row in m.rows:
        lo, hi = (m.num(row[2]), m.num(row[3]))
        sense = _sense(lo, hi)
        direction = 1
        if sense == '>=':
            lo, hi, sense, direction = (None, -lo, '<=', -1)
        elif sense == '=' and hi < 0:
            lo, hi, direction = (-hi, -lo, -1)
        rhs = (category(lo), category(hi)) if sense in ('range', 'free') else category(hi)
        row_base.append((sense, rhs))
        edges.append([(v, direction * (1 if c > 0 else -1)) for v, c in m.iter_terms(row[0])])
        dual.append(sense == '=' and hi == 0)
    vb = [(v[2], bound_class(m.num(v[3]), m.num(v[4])), category(m.num(v[5]))) for v in m.variables]
    rc, vc = ([intern(row_base)], [intern(vb)])
    pos, neg = (intern(row_base), [x if d else -1 for x, d in zip(intern(row_base), dual)])
    for d in range(depth):
        state_old, state_tokens, indexes = ([], [], [])
        vt = [Counter() for _ in vb]
        for r, terms in enumerate(edges):
            p, n = (len(state_old), -1)
            state_old.append(pos[r])
            state_tokens.append(Counter(((sign, vc[d][v]) for v, sign in terms)))
            if dual[r]:
                n = len(state_old)
                state_old.append(neg[r])
                state_tokens.append(Counter(((-sign, vc[d][v]) for v, sign in terms)))
            indexes.append((p, n))
            for v, sign in terms:
                vt[v][sign, pos[r]] += 1
                if dual[r]:
                    vt[v][-sign, neg[r]] += 1
        colors = partition(state_old, state_tokens)
        new_pos = [colors[p] for p, _ in indexes]
        new_neg = [colors[n] if n >= 0 else -1 for _, n in indexes]
        rc.append(intern([(rc[d][r], tuple(sorted([new_pos[r], new_neg[r]] if dual[r] else [new_pos[r]]))) for r in range(len(edges))]))
        vc.append(partition(vc[d], vt))
        pos, neg = (new_pos, new_neg)
    return (rc, vc)

def assert_partition(a, b):
    assert len(a) == len(b)
    assert all(((a[i] == a[j]) == (b[i] == b[j]) for i, j in combinations(range(len(a)), 2)))

@pytest.mark.parametrize('text', [TEXT, 'Minimize\n o: 0\nSubject To\nEnd\n', 'Minimize\n o: 0x + 0y + 0z\nSubject To\n a: x-y=0\n b: y-z=0\n c: -2 <= x+z <= 3\n empty: x-x=0\nBinaries\n x z\nEnd\n'])
def test_independent_oracle_and_monotonicity(tmp_path, text):
    with read_text(tmp_path, text) as m:
        actual = refinement_probe(m, depth=4)
        rows, variables = template_reference(m.model, 4)
        for d in range(5):
            assert_partition(actual.row_color[d], rows[d])
            assert_partition(actual.var_color[d], variables[d])
            if d:
                for levels in (actual.row_color, actual.var_color):
                    assert all((levels[d - 1][i] == levels[d - 1][j] for i, j in combinations(range(len(levels[d])), 2) if levels[d][i] == levels[d][j]))

def counts_model(counts, negative=None):
    rows, variables = ([], [])
    for i, count in enumerate(counts):
        names = [f'x{i}_{j}' for j in range(count)]
        terms = names[:]
        variables.extend(names)
        if negative:
            names = [f'y{i}_{j}' for j in range(negative[i])]
            variables.extend(names)
            terms.extend(('- ' + name for name in names))
        rows.append(f'r{i}: ' + ' + '.join(terms).replace('+ -', '-') + ' <= 1')
    return 'Minimize\n o: 0\nSubject To\n' + '\n'.join(rows) + '\nBinaries\n' + ' '.join(variables) + '\nEnd\n'

def test_count_modes_boundaries_and_chaining(tmp_path):
    for counts, ratio, sizes in [([22, 24, 25, 26], 2, [4]), ([5, 26], 2, [1, 1]), ([1, 2], 2, [2]), ([1, 2, 4, 8], 2, [4]), ([5, 10, 11], Q(3, 2), [2, 1]), ([2, 5], 2, [1, 1])]:
        with read_text(tmp_path, counts_model(counts)) as m:
            s = refinement_probe(m, depth=1, multiplicity_ratio=ratio)
            assert sorted(Counter(s.row_color[1]).values(), reverse=True) == sizes
            expected, _ = template_reference(m.model, 1, Q(ratio))
            assert_partition(s.row_color[1], expected[1])

def test_validation_before_cache_changes(tmp_path):
    for kwargs in [{'mode': 'other'}, {'coefficient': 'scale_normalized'}, {'mode': 'exact', 'coefficient': 'sign'}, {'multiplicity_ratio': 1}, {'multiplicity_ratio': True}, {'multiplicity_ratio': float('inf')}, {'multiplicity_ratio': float('nan')}, {'mode': 'exact', 'multiplicity_ratio': 3}]:
        with read_text(tmp_path) as m:
            m.row_families
            before = tuple(m._refinement_store.db.iterdump())
            with pytest.raises(ValueError):
                refinement_probe(m, **kwargs)
            assert tuple(m._refinement_store.db.iterdump()) == before

def test_template_collision_and_prepare_failure(tmp_path, monkeypatch):

    class Collision:

        def update(self, data):
            pass

        def digest(self):
            return b'collision'
    monkeypatch.setattr(engine, '_new_hash', Collision)
    with read_text(tmp_path) as m:
        m.row_families
        maps = [id(a) for a in m._refinement_store.maps]
        before = tuple(m._refinement_store.db.iterdump())
        original = engine.RefinementStore._prepare

        def fail(*args):
            original(*args)
            raise RuntimeError('failed template prepare')
        with monkeypatch.context() as patch:
            patch.setattr(engine.RefinementStore, '_prepare', fail)
            with pytest.raises(RuntimeError):
                refinement_probe(m, depth=2, rhs='nonzero')
        assert [id(a) for a in m._refinement_store.maps] == maps
        assert tuple(m._refinement_store.db.iterdump()) == before
        refinement_probe(m, depth=2, rhs='nonzero')
        s = refinement_probe(m, depth=3)
        rows, variables = template_reference(m.model, 3)
        for d in range(4):
            assert_partition(s.row_color[d], rows[d])
            assert_partition(s.var_color[d], variables[d])

def test_older_cache_without_template_tables(tmp_path):
    with read_text(tmp_path) as m:
        refinement_probe(m, mode='exact', depth=1)
        path = m.save(tmp_path / 'older-cache')
    db = sqlite3.connect(path / 'base' / 'refinement.sqlite')
    db.execute('DROP TABLE array_artifacts')
    for config, key in db.execute('SELECT id,key FROM configs').fetchall():
        old = json.loads(key)
        old.pop('mode')
        old.pop('multiplicity_ratio')
        old['algorithm'] = '1.0'
        db.execute('UPDATE configs SET key=? WHERE id=?', (json.dumps(old), config))
    db.commit()
    db.close()
    with pytest.raises(mf.MIPFrameError,match='Corrupt native artifact'):
        mf.load(path)

def test_assign_family_numeric_baseline_as_relations():
    """Preserve the pre-refactor real-model coefficient and multiplicity oracle."""
    with mf.read(model_path('assign1-5-8.lp')) as m:
        for depth in range(1,5):
            assert sorted(m.families(depth=depth).column('count'),reverse=True)==[130,26,5]
            assert sorted(m.families(kind='var',depth=depth).column('count'),reverse=True)==[130,26]
        family=m.family('F0')
        roles={r.type:r.role_key for r in family.related('pattern')}
        def histogram(group=family,**filters):
            records=group.related('histogram').where(**dict({'basis':'canonical','orientation':0},**filters))
            return {r.value:r.count for r in records}
        assert histogram(role=roles['C'],quantity='coefficient')=={Q(-1):130}
        assert histogram(role=roles['C'],quantity='multiplicity')=={1:130}
        assert histogram(role=roles['I'],quantity='multiplicity')=={22:5,24:5,25:20,26:100}
        assert histogram(role=roles['I'],quantity='coefficient_multiplicity',coefficient=5)=={1:130}
        expected=Counter(); per_value=defaultdict(Counter)
        for row in family.rows.ids:
            per_row=Counter(-coef for var,coef in m.model.iter_terms(row) if var>=26)
            expected.update(per_row)
            for value in range(1,6): per_value[value][per_row[value]]+=1
        assert histogram(role=roles['I'],quantity='coefficient')==expected
        for value,counts in per_value.items():
            assert histogram(role=roles['I'],quantity='coefficient_multiplicity',coefficient=value)==counts
        assert histogram(role='ub',quantity='value')=={5:130}
        assert histogram(m.family('F2'),role='ub',quantity='value')=={5:4,6:1}
        source=family.related('sources').where(basis='canonical').head(1).one()
        assert source.factor==-1 and source.scale==-5
        assert histogram(basis='raw',role=roles['C'],quantity='coefficient')=={1:130}
        assert histogram(basis='raw',role='lb',quantity='value')=={-5:130}
        assert histogram(basis='scale_normalized',role='ub',quantity='value')=={1:130}
        assert histogram(basis='scale_normalized',role=roles['C'],quantity='coefficient')=={Q(-1,5):130}
        assert mf.analysis.families(family.rows,method='exact_structural',depth=1).count()==17
