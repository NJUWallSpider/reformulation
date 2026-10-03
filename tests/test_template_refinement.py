"""Parameterized refinement and independently aggregated numerical profiles."""

from model_helpers import family_catalog

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
            groups[(old[i], tuple(sorted(tokens)))].append(i)
        keys = [None]*len(old)
        for skeleton, members in groups.items():
            modes = {}
            for role in skeleton[1]:
                values = sorted({counts[i][role] for i in members})
                mode = 0
                modes[role] = {values[0]: mode}
                for a, b in zip(values, values[1:]):
                    mode += b > ratio*a
                    modes[role][b] = mode
            for i in members:
                keys[i] = (skeleton, tuple((role, modes[role][counts[i][role]]) for role in skeleton[1]))
        return intern(keys)

    def category(v):
        return 'infinite' if v is None else 'zero' if v == 0 else 'positive' if v > 0 else 'negative'

    row_base, edges, dual = [], [], []
    for row in m.rows:
        lo, hi = m.num(row[2]), m.num(row[3])
        sense = _sense(lo, hi)
        direction = 1
        if sense == '>=':
            lo, hi, sense, direction = None, -lo, '<=', -1
        elif sense == '=' and hi < 0:
            lo, hi, direction = -hi, -lo, -1
        rhs = (category(lo), category(hi)) if sense in ('range', 'free') else category(hi)
        row_base.append((sense, rhs))
        edges.append([(v, direction*(1 if c > 0 else -1)) for v, c in m.iter_terms(row[0])])
        dual.append(sense == '=' and hi == 0)
    vb = [(v[2], bound_class(m.num(v[3]), m.num(v[4])), category(m.num(v[5]))) for v in m.variables]
    rc, vc = [intern(row_base)], [intern(vb)]
    pos, neg = intern(row_base), [x if d else -1 for x, d in zip(intern(row_base), dual)]
    for d in range(depth):
        state_old, state_tokens, indexes = [], [], []
        vt = [Counter() for _ in vb]
        for r, terms in enumerate(edges):
            p, n = len(state_old), -1
            state_old.append(pos[r])
            state_tokens.append(Counter((sign, vc[d][v]) for v, sign in terms))
            if dual[r]:
                n = len(state_old)
                state_old.append(neg[r])
                state_tokens.append(Counter((-sign, vc[d][v]) for v, sign in terms))
            indexes.append((p, n))
            for v, sign in terms:
                vt[v][(sign, pos[r])] += 1
                if dual[r]:
                    vt[v][(-sign, neg[r])] += 1
        colors = partition(state_old, state_tokens)
        new_pos = [colors[p] for p, _ in indexes]
        new_neg = [colors[n] if n >= 0 else -1 for _, n in indexes]
        rc.append(intern([(rc[d][r], tuple(sorted([new_pos[r], new_neg[r]] if dual[r] else [new_pos[r]]))) for r in range(len(edges))]))
        vc.append(partition(vc[d], vt))
        pos, neg = new_pos, new_neg
    return rc, vc


def assert_partition(a, b):
    assert len(a) == len(b)
    assert all((a[i] == a[j]) == (b[i] == b[j]) for i, j in combinations(range(len(a)), 2))


def test_assign_families_and_all_numeric_observations():
    with mf.read(model_path("assign1-5-8.lp")) as m:
        s = m.structure.signatures(depth=4)
        assert s.mode == 'template'
        for d in range(1, 5):
            assert sorted(Counter(s.row_color[d]).values(), reverse=True) == [130, 26, 5]
            assert sorted(Counter(s.var_color[d]).values(), reverse=True) == [130, 26]
        families = s._row_families(depth=2)
        f = families.loc['F0']
        a = f.attributes
        assert a._db is None  # Access alone is lazy.
        cont, binary = a.roles
        assert dict(cont.coefficients) == {Q(-1): 130}
        assert dict(cont.multiplicity) == {1: 130}
        assert dict(binary.multiplicity) == {22: 5, 24: 5, 25: 20, 26: 100}
        assert dict(binary.coefficient_multiplicity(5)) == {1: 130}
        assert dict(binary.coefficient_multiplicity(999)) == {0: 130}
        expected = Counter()
        per_value = defaultdict(Counter)
        for row in f.rows.ids:
            per_row = Counter(-c for v, c in m.model.iter_terms(row) if v >= 26)
            expected.update(per_row)
            for value in range(1, 6):
                per_value[value][per_row[value]] += 1
        assert dict(binary.coefficients) == expected
        for value, histogram in per_value.items():
            assert dict(binary.coefficient_multiplicity(value)) == histogram
        assert dict(a.rhs) == {5: 130}
        assert dict(families.loc['F2'].attributes.rhs) == {5: 4, 6: 1}
        assert dict(families.loc['F1'].attributes.roles[0].multiplicity) == {5: 26}
        assert a.sources[0]['factor'] == -1
        assert a.sources[0]['normalization_scale'] == -5
        assert dict(a.view(basis='raw').roles[0].coefficients) == {1: 130}
        assert dict(a.view(basis='raw').lhs) == {-5: 130}
        assert a.view(basis='scale_normalized').rhs.constant == 1
        assert a.view(basis='scale_normalized').roles[0].coefficients.constant == Q(-1, 5)
        assert f.rhs.describe()['min'] == 1  # Existing API retains its units.
        assert 'coefficient 5 / member: constant(1)' in repr(f.inspect(verbose=True))
        assert 'coefficients=' not in repr(families.head(1))
        assert len(family_catalog(f.rows, signature='exact_structural', depth=1)) == 17
        assert m.rows.loc[0].signature('exact_strctural') == m.rows.loc[0].signature('exact_structural')
        assert m.rows.loc[0].signature('structural') != m.rows.loc[0].signature('exact_structural')


@pytest.mark.parametrize('text', [TEXT, 'Minimize\n o: 0\nSubject To\nEnd\n',
    'Minimize\n o: 0x + 0y + 0z\nSubject To\n a: x-y=0\n b: y-z=0\n c: -2 <= x+z <= 3\n empty: x-x=0\nBinaries\n x z\nEnd\n'])
def test_independent_oracle_and_monotonicity(tmp_path, text):
    with read_text(tmp_path, text) as m:
        actual = m.structure.refine(depth=4)
        rows, variables = template_reference(m.model, 4)
        for d in range(5):
            assert_partition(actual.row_color[d], rows[d])
            assert_partition(actual.var_color[d], variables[d])
            if d:
                for levels in (actual.row_color, actual.var_color):
                    assert all(levels[d-1][i] == levels[d-1][j] for i, j in combinations(range(len(levels[d])), 2) if levels[d][i] == levels[d][j])


def counts_model(counts, negative=None):
    rows, variables = [], []
    for i, count in enumerate(counts):
        names = [f'x{i}_{j}' for j in range(count)]
        terms = names[:]
        variables.extend(names)
        if negative:
            names = [f'y{i}_{j}' for j in range(negative[i])]
            variables.extend(names)
            terms.extend('- '+name for name in names)
        rows.append(f'r{i}: ' + ' + '.join(terms).replace('+ -', '-') + ' <= 1')
    return 'Minimize\n o: 0\nSubject To\n'+'\n'.join(rows)+'\nBinaries\n'+' '.join(variables)+'\nEnd\n'


def test_count_modes_boundaries_and_chaining(tmp_path):
    for (counts, ratio, sizes) in [
        ([22, 24, 25, 26], 2, [4]),
        ([5, 26], 2, [1, 1]),
        ([1, 2], 2, [2]),
        ([1, 2, 4, 8], 2, [4]),
        ([5, 10, 11], Q(3, 2), [2, 1]),
        ([2, 5], 2, [1, 1]),
    ]:
        with read_text(tmp_path, counts_model(counts)) as m:
            s=m.structure.refine(depth=1,multiplicity_ratio=ratio)
            assert sorted(Counter(s.row_color[1]).values(),reverse=True)==sizes
            expected,_=template_reference(m.model,1,Q(ratio))
            assert_partition(s.row_color[1],expected[1])


def test_joint_modes_and_role_presence(tmp_path):
    with read_text(tmp_path, counts_model([1,1,8,8], [1,8,1,8])) as m:
        assert len(m.structure.refine(depth=1)._row_families()) == 4
    with read_text(tmp_path, counts_model([5,5], [0,1]), 'presence.lp') as m:
        assert len(m.structure.refine(depth=1)._row_families()) == 2


def test_perturbation_scaling_and_numerical_precision(tmp_path):
    text = 'Minimize\n o: x+y\nSubject To\n a: x+2a+3b<=5\n b: y+7c+11d<=9\nBinaries\n a b c d\nEnd\n'
    changed = text.replace('x+2a+3b<=5','4x+8a+12b<=20').replace('y+7c+11d<=9','y+0.00000000000000000001c+13d<=12')
    with read_text(tmp_path,text,'a.lp') as a, read_text(tmp_path,changed,'b.lp') as b:
        sa,sb=a.structure.refine(depth=4),b.structure.refine(depth=4)
        for d in range(5):
            assert_partition(sa.row_color[d],sb.row_color[d])
            assert_partition(sa.var_color[d],sb.var_color[d])
        assert len(sa._row_families())==1
        assert len(a.structure.refine(mode='exact',depth=1)._row_families())==2
        assert sa._row_families().loc['F0'].attributes.rhs.max==9
        assert sb._row_families().loc['F0'].attributes.rhs.max==20
        coeff=sb._row_families().loc['F0'].attributes.roles[1].coefficients
        assert Q(1,10**20) in coeff


def test_subset_global_colors_local_attributes(tmp_path):
    text='Minimize\n o: 0x+0y+0z\nSubject To\n a: 2x<=4\n b: 3y<=9\n c: 5z<=25\nBinaries\n x y z\nEnd\n'
    with read_text(tmp_path,text) as m:
        whole=family_catalog(m.rows, signature='structural')
        selected=family_catalog(m.rows.iloc[1:],signature='structural')
        f=selected.loc['F0']
        assert f.signature.color==whole.loc['F0'].signature.color
        assert dict(f.attributes.rhs)=={9:1,25:1}
        assert dict(f.attributes.roles[0].coefficients)=={3:1,5:1}
        assert dict(f.attributes.view(basis='scale_normalized').roles[0].coefficients)=={1:2}
        assert list(f.rows.ids)==[1,2]
        assert list(m.rows.iloc[1:].signature('structural').column('row').ids)==list(m.rows.iloc[1:].ids)


def test_dual_profiles_no_double_count_and_direction_invariance(tmp_path):
    text='Minimize\n o: 0x+0y+0z+0w\nSubject To\n a: x-2y=0\n b: 3z-4w=0\nBinaries\n x z\nEnd\n'
    with read_text(tmp_path,text,'a.lp') as a, read_text(tmp_path,text.replace('x-2y','-x+2y'),'b.lp') as b:
        sa,sb=a.structure.refine(depth=3),b.structure.refine(depth=3)
        for d in range(4):
            assert_partition(sa.row_color[d],sb.row_color[d])
            assert_partition(sa.var_color[d],sb.var_color[d])
        attrs=sa._row_families().loc['F0'].attributes
        assert len(attrs.orientations)==2
        for profile in attrs.orientations:
            assert profile.count==2
            assert sum(role.coefficients.count for role in profile.roles)==4
            assert all(role.multiplicity.constant==1 for role in profile.roles)
        assert len(attrs.sources)==4
        # Direction slots, like colors, are model-local. Compare their unordered profiles.
        def profiles(a):
            return {tuple(tuple(role.coefficients.items()) for role in p.roles) for p in a.orientations}
        assert profiles(attrs)==profiles(sb._row_families().loc['F0'].attributes)


def test_high_cardinality_and_lazy_complete_access(tmp_path, monkeypatch):
    n=60
    text='Minimize\n o: 0\nSubject To\n r: '+' + '.join(f'{i+1}x{i}' for i in range(n))+'<=3\nBinaries\n'+' '.join(f'x{i}' for i in range(n))+'\nEnd\n'
    with read_text(tmp_path,text) as m:
        f=family_catalog(m.rows, signature='structural', memory_mb=0.0625).loc['F0']
        monkeypatch.setattr(type(m.model),'iter_terms',lambda *a: pytest.fail('no Python row dict'))
        profile=f.attributes.roles[0]
        monkeypatch.setattr(mf.options.display,'max_cols',3)
        summary=repr(profile.coefficients)
        assert '60 distinct' in summary and 'range=[1,60]' in summary
        assert len(dict(profile.coefficients))==n
        assert dict(profile.coefficient_multiplicity(57))=={1:1}
        assert '...' in repr(f.inspect(verbose=True, samples=0))
        assert profile.coefficients[Q(57)]==1
        with pytest.raises(KeyError):
            profile.coefficients[100]


def test_validation_before_cache_changes(tmp_path):
    for kwargs in [
        {'mode': 'other'},
        {'coefficient': 'scale_normalized'},
        {'mode': 'exact', 'coefficient': 'sign'},
        {'multiplicity_ratio': 1},
        {'multiplicity_ratio': True},
        {'multiplicity_ratio': float('inf')},
        {'multiplicity_ratio': float('nan')},
        {'mode': 'exact', 'multiplicity_ratio': 3},
    ]:
        with read_text(tmp_path) as m:
            before = tuple(m._refinement_store.db.iterdump())
            with pytest.raises(ValueError):
                m.structure.refine(**kwargs)
            assert tuple(m._refinement_store.db.iterdump()) == before


def test_cache_save_load_rollback_and_older_config(tmp_path, monkeypatch):
    with read_text(tmp_path) as m:
        s=m.structure.refine(depth=2)
        store=s._store
        colors=list(s.row_color[2])
        exact=m.structure.refine(mode='exact',depth=1)
        custom=m.structure.refine(depth=1,multiplicity_ratio=3)
        assert len({s._config,exact._config,custom._config})==3
        original=store._round
        def fail(*args):
            original(*args)
            raise RuntimeError('failed template round')
        with monkeypatch.context() as patch:
            patch.setattr(store,'_round',fail)
            with pytest.raises(RuntimeError):
                m.structure.refine(depth=3)
        assert list(s.row_color[2])==colors
        assert store.db.execute('SELECT count(*) FROM template_ranges WHERE depth=3').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM definitions WHERE depth=3').fetchone()[0]==0
        s=m.structure.refine(depth=3)
        expected,_=template_reference(m.model,3)
        assert_partition(s.row_color[3],expected[3])
        # Add an old-format config; loading leaves it intact but never uses it
        # as either of the new algorithm's configurations.
        old={'coefficient':'scale_normalized','rhs':'sign','objective':'sign','equality':'dual_zero_rhs','algorithm':'1.0'}
        store.db.execute('INSERT INTO configs(key,depth) VALUES(?,0)',(json.dumps(old),))
        store.db.commit()
        target=m.save(tmp_path/'saved')
    with mf.load(target) as m:
        with monkeypatch.context() as patch:
            patch.setattr(engine.RefinementStore,'_prepare',lambda *a: pytest.fail('reuse prepared data'))
            patch.setattr(engine.RefinementStore,'_round',lambda *a: pytest.fail('reuse completed colors'))
            s=m.structure.refine(depth=3)
        f=s._row_families().loc['F0']
        attrs=f.attributes
        values=attrs.roles[0].coefficients
        list(values)
        sources=attrs.sources
        m.structure.refine(depth=3)
    for access in (lambda: repr(attrs),lambda: len(values),lambda: list(sources),lambda: attrs.view(basis='raw')):
        with pytest.raises(mf.MIPFrameError,match='closed'):
            access()


def test_template_collision_and_prepare_failure(tmp_path, monkeypatch):
    class Collision:
        def update(self,data): pass
        def digest(self): return b'collision'
    monkeypatch.setattr(engine,'_new_hash',Collision)
    with read_text(tmp_path) as m:
        maps=[id(a) for a in m._refinement_store.maps]
        before=tuple(m._refinement_store.db.iterdump())
        original=engine.RefinementStore._prepare
        def fail(*args):
            original(*args)
            raise RuntimeError('failed template prepare')
        with monkeypatch.context() as patch:
            patch.setattr(engine.RefinementStore,'_prepare',fail)
            with pytest.raises(RuntimeError): m.structure.refine(depth=2, rhs="nonzero")
        assert [id(a) for a in m._refinement_store.maps] == maps
        assert tuple(m._refinement_store.db.iterdump()) == before
        m.structure.refine(depth=2, rhs="nonzero")
        s=m.structure.refine(depth=3)
        rows,variables=template_reference(m.model,3)
        for d in range(4):
            assert_partition(s.row_color[d],rows[d])
            assert_partition(s.var_color[d],variables[d])


def test_low_memory_external_sort(tmp_path, monkeypatch):
    with read_text(tmp_path,counts_model([2000,1900])) as m:
        used=[]
        original=engine.RefinementStore._aggregate
        def aggregate(self,blocks,size):
            if size>self.memory//128: used.append(size)
            yield from original(self,blocks,size)
        monkeypatch.setattr(engine.RefinementStore,'_aggregate',aggregate)
        s=m.structure.refine(depth=3,memory_mb=0.0625)
        assert used and len(s._row_families())==1
        f=s._row_families().loc['F0']
        assert dict(f.attributes.roles[0].multiplicity)=={1900:1,2000:1}
        assert f.signature.pattern[0][2].min==1900
        assert f.signature.pattern[0][2].max==2000


def test_variable_attributes_isolated_members_and_bounds(tmp_path):
    text='Minimize\n o: 0x+0y+0unused\nSubject To\n a: 2x+3y<=4\n b: x<=5\nBinaries\n x y unused\nEnd\n'
    with read_text(tmp_path,text) as m:
        f=m.structure.refine(depth=0)._var_families().loc['F0']
        attrs=f.attributes
        assert f.count==3
        assert dict(attrs.roles[0].multiplicity)=={0:1,1:1,2:1}
        assert dict(attrs.roles[0].coefficient_multiplicity(2))=={0:2,1:1}
        assert dict(attrs.lhs)=={0:3} and dict(attrs.rhs)=={1:3}
        assert dict(attrs.objective)=={0:3}
        assert len(attrs.sources)==3
        assert attrs.roles[0].neighbor_kind=='Rstate'
        assert attrs.roles[0].neighbor_base==['<=','positive']
        assert attrs.sources[::-1]==tuple(reversed(tuple(attrs.sources)))
        assert [(r.sign,r.neighbor) for r in attrs.roles[::-1]]==[(r.sign,r.neighbor) for r in reversed(attrs.roles[:])]
        with pytest.raises(ValueError): attrs.view(basis='invalid')
        with pytest.raises(ValueError): family_catalog(m.rows, signature='structural',mode='exact')
        # An entirely isolated family still has bounds and no term roles.
        isolated=m.structure.refine(depth=2)._var_families()
        family=next(isolated.loc[r['id']] for r in isolated if r['nnz']==0)
        assert len(family.attributes.roles)==0
        assert family.attributes.rhs.constant==1


def test_depth_zero_optional_role_statistics(tmp_path):
    with read_text(tmp_path,counts_model([2,2],[0,1])) as m:
        f=m.structure.refine(depth=0)._row_families().loc['F0']
        negative=f.attributes.roles[0]
        assert negative.sign==-1
        assert dict(negative.multiplicity)=={0:1,1:1}
        assert negative.multiplicity_mode is None
        assert negative.coefficients.count==1
        assert f.attributes.rhs.count==2


def test_profile_sources_replay_exact_coefficients(tmp_path):
    text='Minimize\n o: 0x+0y+0z\nSubject To\n a: -2x-4y>=-8\n b: 3y-6z=0\n c: -9x-3z=-12\n d: -2 <= 0.5x+1.5y <= 6\nBounds\n x free\n y free\n z free\nEnd\n'
    with read_text(tmp_path,text) as m:
        families=family_catalog(m.rows, signature='structural',depth=2)
        for record in families:
            f=families.loc[record['id']]
            for basis in ('raw','canonical','scale_normalized'):
                a=f.attributes.view(basis=basis)
                values=defaultdict(Counter)
                for source in a.sources:
                    values[source['orientation']].update(c*source['factor'] for _,c in m.model.iter_terms(source['row']))
                for p in a.orientations:
                    observed=Counter()
                    for role in p.roles:
                        observed.update(dict(role.coefficients))
                    assert observed==values[p.orientation]


def test_symmetric_template_numeric_orientation_ties(tmp_path):
    text='Minimize\n o: 0x+0y+0z+0w\nSubject To\n a: 2x-y=0\n b: 3z-w=0\nBinaries\n x y z w\nEnd\n'
    with read_text(tmp_path,text,'a.lp') as a, read_text(tmp_path,text.replace('2x-y','-2x+y'),'b.lp') as b:
        fa=family_catalog(a.rows, signature='structural').loc['F0']
        fb=family_catalog(b.rows, signature='structural').loc['F0']
        assert len(fa.signature.patterns)==1
        assert len(fa.attributes.orientations)==2
        for pa,pb in zip(fa.attributes.orientations,fb.attributes.orientations):
            assert [dict(r.coefficients) for r in pa.roles]==[dict(r.coefficients) for r in pb.roles]
            assert sum(r.coefficients.count for r in pa.roles)==4
        attrs=fa.attributes
        db=attrs._ensure()
        a.close()
        assert attrs._db is None
        with pytest.raises(sqlite3.ProgrammingError): db.execute('SELECT 1')


def test_older_cache_without_template_tables(tmp_path):
    with read_text(tmp_path) as m:
        m.structure.refine(mode='exact',depth=1)
        path=m.save(tmp_path/'older-cache')
    db=sqlite3.connect(path/'refinement.sqlite')
    db.execute('DROP TABLE template_ranges')
    # Simulate real old caches, with their original configuration keys.
    for config,key in db.execute('SELECT id,key FROM configs').fetchall():
        old=json.loads(key)
        old.pop('mode')
        old.pop('multiplicity_ratio')
        old['algorithm']='1.0'
        db.execute('UPDATE configs SET key=? WHERE id=?',(json.dumps(old),config))
    db.commit()
    db.close()
    with mf.load(path) as m:
        template=m.structure.refine(depth=2)
        exact=m.structure.refine(mode='exact',depth=2)
        assert template._config!=exact._config
        assert m._refinement_store.db.execute('SELECT count(*) FROM configs').fetchone()[0]==4
        rows,variables=template_reference(m.model,2)
        assert_partition(template.row_color[2],rows[2])
        assert_partition(template.var_color[2],variables[2])
