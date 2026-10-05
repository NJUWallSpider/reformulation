"""Independent small-structure oracles for explicit searches."""

from fractions import Fraction as Q
from itertools import combinations, permutations, product
import json
import random
import pytest
import mipframe as mf
from mipframe import Frame, DType, col


def frame(rows, schema):
    return Frame.from_records(rows, schema=schema)


def edges(es):
    return frame((dict(u=a, v=b) for a, b in es), {"u": "int", "v": "int"})


def domain(xs):
    return frame((dict(vertex=x) for x in xs), {"vertex": "int"})


def objects(f, kind):
    out = {}
    for r in f.to_records():
        out.setdefault(r[kind], []).append(r["vertex"])
    return [tuple(v) for v in out.values()]


def clique_oracle(n, es):
    es = {tuple(sorted(e)) for e in es if e[0] != e[1]}
    result = set()
    for size in range(1, n + 1):
        for c in combinations(range(n), size):
            if all(e in es for e in combinations(c, 2)) and not any(
                all(tuple(sorted((v, w))) in es for w in c)
                for v in range(n)
                if v not in c
            ):
                result.add(c)
    return result


def cycle_oracle(n, es, directed):
    es = set(es)
    if not directed:
        es |= {(b, a) for a, b in es}
    result = set()
    for k in range(1 if directed else 3, n + 1):
        for seq in permutations(range(n), k):
            if seq[0] != min(seq) or not directed and seq[1] > seq[-1]:
                continue
            if all((seq[i], seq[(i + 1) % k]) in es for i in range(k)):
                result.add(seq)
    return result


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_graph_oracles(memory):
    rng = random.Random(42)
    for n in range(6):
        for trial in range(3):
            es = [(a, b) for a in range(n) for b in range(n) if rng.random() < 0.3]
            E = edges(es)
            expected = clique_oracle(n, es)
            result = mf.alg.graph.maximal_cliques(
                E, vertices=domain(range(n)), min_size=1, memory_mb=memory
            )
            assert set(objects(result, "clique")) == expected
            assert result.relations["search"].one().mode == "exhaustive"
            candidates = mf.alg.graph.clique_candidates(
                E, vertices=domain(range(n)), min_size=1, memory_mb=memory
            )
            assert set(objects(candidates, "clique")) <= expected
            for directed in (True, False):
                result = mf.alg.graph.simple_cycles(
                    E, vertices=domain(range(n)), directed=directed, memory_mb=memory
                )
                assert set(objects(result, "cycle")) == cycle_oracle(n, es, directed)


def test_candidate_budget_output_limit_and_exact_ranking():
    E = edges([(0, 1), (1, 2), (3, 4)])
    scores = frame(
        [
            dict(vertex=v, value=x)
            for v, x in enumerate([Q(1), Q(1), Q(1) + Q(1, 10**50), Q(0), Q(0)])
        ],
        {"vertex": "int", "value": "number"},
    )
    r = mf.alg.graph.clique_candidates(E, score=scores, top=1)
    assert objects(r, "clique") == [(1, 2)]
    assert r.relations["summary"].one().score == 2 + Q(1, 10**50)
    assert r.relations["search"].one().found == 3
    assert "Candidate search" in repr(r)
    r = mf.alg.graph.clique_candidates(E, max_states=0)
    assert len(r) == 0 and r.relations["search"].one().stop_reason == "max_states"
    for budget in range(15):
        r = mf.alg.graph.clique_candidates(E, max_states=budget)
        assert set(objects(r, "clique")) <= clique_oracle(5, [(0, 1), (1, 2), (3, 4)])
    with pytest.raises(mf.SearchBudgetExceeded) as exc:
        mf.alg.graph.maximal_cliques(E, max_states=0)
    assert exc.value.states == 0 and not hasattr(exc.value, "result")
    with pytest.raises(mf.QueryTooLarge):
        mf.alg.graph.clique_candidates(E, top=1, max_output_rows=2)
    assert len(mf.alg.graph.clique_candidates(E, top=0, max_output_rows=0)) == 0
    assert len(mf.alg.graph.maximal_cliques(edges([]), max_output_rows=0)) == 0


def test_domains_scores_scope_and_validation():
    E = edges([(0, 1), (1, 2), (2, 0), (3, 3), (0, 1), (1, 0)])
    r = mf.alg.graph.maximal_cliques(
        E, vertices=domain(range(5)), scope=domain([0, 1, 4]), min_size=1
    )
    assert set(objects(r, "clique")) == {(0, 1), (4,)}
    assert len(r.domains["vertex"]) == 3
    with pytest.raises(mf.CardinalityError):
        mf.alg.graph.clique_candidates(E, vertices=domain([0, 1]))
    with pytest.raises(mf.CardinalityError):
        mf.alg.graph.clique_candidates(E, scope=domain([9]))
    with pytest.raises(mf.CardinalityError):
        mf.alg.graph.clique_candidates(
            E,
            score=frame(
                [dict(vertex=0, value=1)], {"vertex": "int", "value": "number"}
            ),
        )
    for value in (None, float("inf")):
        with pytest.raises(mf.CardinalityError):
            mf.alg.graph.clique_candidates(
                E,
                score=frame(
                    [dict(vertex=0, value=value)],
                    {"vertex": "int", "value": DType("number", True)},
                ),
            )
    for kw in (
        dict(top=True),
        dict(min_size=False),
        dict(max_states=-1),
        dict(time_limit=0),
        dict(time_limit=float("inf")),
    ):
        with pytest.raises((ValueError, TypeError)):
            mf.alg.graph.clique_candidates(E, **kw)


def test_odd_cycles_chords_lengths_and_direction():
    ring = [(i, (i + 1) % 5) for i in range(5)]
    E = edges(ring)
    r = mf.alg.graph.odd_cycle_candidates(E)
    assert objects(r, "cycle") == [(0, 1, 2, 3, 4)]
    assert len(mf.alg.graph.odd_cycle_candidates(edges(ring + [(0, 2)]))) == 0
    assert objects(
        mf.alg.graph.odd_cycle_candidates(edges(ring + [(0, 2)]), chordless=False),
        "cycle",
    ) == [(0, 1, 2, 3, 4)]
    assert len(mf.alg.graph.simple_cycles(E, directed=False, max_length=4)) == 0
    E = edges([(0, 0), (0, 1), (1, 0)])
    assert set(objects(mf.alg.graph.simple_cycles(E, directed=True), "cycle")) == {
        (0,),
        (0, 1),
    }
    assert len(mf.alg.graph.simple_cycles(E, directed=False)) == 0
    with pytest.raises(ValueError):
        mf.alg.graph.odd_cycle_candidates(E, lengths=(4,))
    with pytest.raises(ValueError):
        mf.alg.graph.simple_cycles(E, directed=True, chordless=True)


def test_recipes_relations_provenance_and_exports(tmp_path):
    E = edges([(0, 1), (1, 2)])
    r = mf.alg.graph.clique_candidates(E)
    for f in (
        r,
        r.relations["summary"],
        r.relations["search"],
        r.select("vertex").distinct(),
        r.join(domain(range(3)), on="vertex", cardinality="m:1"),
    ):
        assert "Candidate search" in repr(f)
        assert "Candidate search" in str(f.explain())
    bindings = {E.to_spec()["plan"]["relation"]: E}
    for f in (r, r.relations["summary"], r.relations["search"]):
        restored = mf.frame_from_spec(
            json.loads(json.dumps(f.to_spec())), models={}, relations=bindings
        )
        assert restored.to_records() == f.to_records()
        assert "Candidate search" in repr(restored)
    path = tmp_path / "candidates.jsonl"
    r.select("vertex").write(path)
    loaded = mf.read_frame(path)
    assert loaded.to_records() == r.select("vertex").to_records()
    assert "Candidate search" in repr(loaded)


def graph_instances(items, nodes):
    E = frame(
        (dict(graph=g, u=u, v=v) for g, es in items.items() for u, v in es),
        {"graph": "int", "u": "int", "v": "int"},
    )
    V = frame(
        (dict(graph=g, vertex=v) for g, vs in nodes.items() for v in vs),
        {"graph": "int", "vertex": "int"},
    )
    return E, V


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_isomorphism_exact_not_only_refinement(memory):
    # C6 versus two triangles: same degree and stable colors, not isomorphic.
    instances = {
        0: [(i, (i + 1) % 6) for i in range(6)],
        1: [(10 + i, 10 + (i + 1) % 6) for i in range(6)],
        2: [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)],
        3: [],
        4: [],
    }
    E, V = graph_instances(
        instances, {0: range(6), 1: range(10, 16), 2: range(6), 3: [], 4: []}
    )
    graphs = frame([dict(g=i) for i in range(5)], {"g": "int"})
    r = mf.alg.graph.isomorphism_classes(E, vertices=V, graphs=graphs, memory_mb=memory)
    assert r.to_records() == [
        {"graph": 0, "class": 0},
        {"graph": 1, "class": 0},
        {"graph": 2, "class": 2},
        {"graph": 3, "class": 3},
        {"graph": 4, "class": 3},
    ]
    maps = r.relations["mapping"].to_records()
    for g in range(3):
        mapping = {
            x["vertex"]: x["representative_vertex"] for x in maps if x["graph"] == g
        }
        assert len(mapping) == 6 and len(set(mapping.values())) == 6
        target = {tuple(sorted(e)) for e in instances[0 if g < 2 else 2]}
        assert {
            tuple(sorted((mapping[a], mapping[b]))) for a, b in instances[g]
        } == target
    with pytest.raises(mf.SearchBudgetExceeded):
        mf.alg.graph.isomorphism_classes(E, vertices=V, max_states=0)


def test_isomorphism_labels_and_loop_validation():
    E = frame(
        [dict(graph=g, u=0, v=1, label=Q(1) + g * Q(1, 10**50)) for g in range(2)],
        {"graph": "int", "u": "int", "v": "int", "label": "number"},
    )
    V = frame(
        [dict(graph=g, vertex=v, label=None) for g in range(2) for v in range(2)],
        {"graph": "int", "vertex": "int", "label": DType("str", True)},
    )
    r = mf.alg.graph.isomorphism_classes(
        E, vertices=V, edge_labels=("label",), vertex_labels=("label",)
    )
    assert [x["class"] for x in r.to_records()] == [0, 1]
    assert [
        x["class"] for x in mf.alg.graph.isomorphism_classes(E, vertices=V).to_records()
    ] == [0, 0]
    conflict = frame(
        [dict(graph=0, u=0, v=1, label=1), dict(graph=0, u=1, v=0, label=2)],
        {"graph": "int", "u": "int", "v": "int", "label": "int"},
    )
    with pytest.raises(mf.CardinalityError):
        mf.alg.graph.isomorphism_classes(conflict, vertices=V, edge_labels=("label",))


def integer_system(bounds, cs, limits):
    return (
        frame(
            [dict(var=i, lb=l, ub=h) for i, (l, h) in enumerate(bounds)],
            {"var": "int", "lb": "number", "ub": "number"},
        ),
        frame(
            [dict(row=r, var=v, coef=c) for r, v, c in cs],
            {"row": "int", "var": "int", "coef": "number"},
        ),
        frame(
            [dict(row=i, lb=l, ub=h) for i, (l, h) in enumerate(limits)],
            {"row": "int", "lb": "number", "ub": "number"},
        ),
    )


@pytest.mark.parametrize("memory", [128, 0.015625])
def test_integer_enumeration_fraction_and_empty(memory):
    data = integer_system(
        [(Q(-3, 2), Q(5, 2)), (-2, 2)],
        [(0, 0, Q(1, 3)), (0, 1, Q(-1, 7))],
        [(Q(-1, 4), Q(1, 4))],
    )
    r = mf.alg.integer.enumerate(*data, memory_mb=memory)
    expected = [
        (a, b)
        for a, b in product(range(-1, 3), range(-2, 3))
        if Q(-1, 4) <= Q(a, 3) - Q(b, 7) <= Q(1, 4)
    ]
    got = {}
    for x in r.to_records():
        got.setdefault(x["assignment"], []).append(x["value"])
    assert list(map(tuple, got.values())) == expected
    assert len(r.relations["summary"]) == len(expected)
    assert r.relations["search"].one().stop_reason == "exhausted"
    empty = mf.alg.integer.enumerate(*integer_system([], [], []))
    assert len(empty) == 0 and len(empty.relations["summary"]) == 1
    assert (
        len(
            mf.alg.integer.enumerate(*integer_system([], [], [(1, 2)])).relations[
                "summary"
            ]
        )
        == 0
    )
    assert len(mf.alg.integer.enumerate(*integer_system([(1, 0)], [], []))) == 0
    with pytest.raises(mf.QueryTooLarge):
        mf.alg.integer.enumerate(*integer_system([(0, 1)], [], []), max_output_rows=3)
    with pytest.raises(mf.SearchBudgetExceeded):
        mf.alg.integer.enumerate(*data, max_states=1)
    with pytest.raises(mf.CardinalityError):
        mf.alg.integer.enumerate(*integer_system([(0, float("inf"))], [], []))


def test_hash_collisions_are_not_identity(monkeypatch):
    import hashlib

    class Collision:
        def update(self, *args):
            pass

        def hexdigest(self):
            return "same"

    monkeypatch.setattr(hashlib, "sha256", lambda *args, **kwargs: Collision())
    r = mf.alg.graph.clique_candidates(edges([(0, 1), (1, 2), (3, 4)]))
    assert set(objects(r, "clique")) == {(0, 1), (1, 2), (3, 4)}
    E, V = graph_instances(
        {0: [(0, 1), (1, 2)], 1: [(0, 1), (0, 2)]}, {0: range(3), 1: range(3)}
    )
    assert [
        x["class"]
        for x in mf.alg.graph.isomorphism_classes(
            E, vertices=V, directed=True
        ).to_records()
    ] == [0, 1]


def test_time_stops_atomic_object_and_cleanup(monkeypatch):
    import pathlib
    import mipframe.store.search_store as store

    contexts = []
    original = store.SearchStore.__init__

    def init(self, *a, **kw):
        original(self, *a, **kw)
        contexts.append(self)

    monkeypatch.setattr(store.SearchStore, "__init__", init)
    # Fail during input preparation: this is not a successful empty search.
    monkeypatch.setattr(store, "monotonic", lambda: 0 if not contexts else 2)
    with pytest.raises(mf.SearchBudgetExceeded):
        mf.alg.graph.clique_candidates(edges([(0, 1)]), time_limit=1)
    assert contexts[-1].work.closed and contexts[-1].work.temp is None
    monkeypatch.setattr(store, "monotonic", lambda: 0)
    original_add = store.Objects._add

    def stop_mid_object(self, sequence):
        def interrupted():
            yield next(iter(sequence))
            self.ctx.stop("time_limit")

        original_add(self, interrupted())

    monkeypatch.setattr(store.Objects, "_add", stop_mid_object)
    r = mf.alg.graph.clique_candidates(edges([(0, 1)]))
    assert len(r) == 0 and len(r.relations["summary"]) == 0
    assert r.relations["search"].one().found == 0
    assert r.relations["search"].one().stop_reason == "time_limit"
    monkeypatch.setattr(store.Objects, "_add", original_add)

    def cancel(self):
        raise KeyboardInterrupt()

    monkeypatch.setattr(store.SearchStore, "tick", cancel)
    with pytest.raises(KeyboardInterrupt):
        mf.alg.graph.maximal_cliques(edges([(0, 1)]))
    assert contexts[-1].work.closed and contexts[-1].work.temp is None
    with pytest.raises(mf.DiskBudgetExceeded):
        mf.alg.graph.clique_candidates(edges((i,i+1) for i in range(2000)), memory_mb=.015625,disk_mb=0.001)
    assert contexts[-1].work.closed and contexts[-1].work.temp is None


def test_long_directed_cycle_uses_paged_nonrecursive_stack():
    n = 1050
    E = edges((i, (i + 1) % n) for i in range(n))
    r = mf.alg.graph.simple_cycles(E, directed=True, memory_mb=0.015625)
    assert len(r) == n and r.relations["summary"].one().length == n
    assert objects(r, "cycle")[0] == tuple(range(n))


def test_search_ref_lifecycle_structure_only(tmp_path, monkeypatch):
    p = tmp_path / "search.lp"
    p.write_text(
        "Minimize\n obj:0x+0y+0z\nSubject To\n a:2x-3y<=5\n b:x+4y=2\n empty:0z=0\nBounds\n x free\n y free\n z free\nEnd\n"
    )
    m = mf.read(p)
    membership = m.terms().select(set=col("var"), member=col("row"))
    E = mf.ops.sets.pairs(membership).select(u=col("left"), v=col("right"))

    def forbidden(*a, **kw):
        raise AssertionError("search decoded names or coefficients")

    monkeypatch.setattr(m.model, "num", forbidden)
    monkeypatch.setattr(m.model, "gather", forbidden)
    r = mf.alg.graph.maximal_cliques(E, vertices=m.vars, min_size=1)
    detached = r.to_records()
    iterator = iter(r)
    next(iterator)
    assert detached
    m.close()
    for action in (
        lambda: len(r),
        lambda: r.schema,
        lambda: r.relations,
        lambda: repr(r),
        lambda: next(iterator),
    ):
        with pytest.raises(mf.MIPFrameError):
            action()
    assert isinstance(detached[0]["vertex"], mf.Ref)


def test_conflict_and_closure_composition():
    membership = frame(
        [
            dict(set=0, member="a"),
            dict(set=1, member="a"),
            dict(set=1, member="b"),
            dict(set=2, member="b"),
        ],
        {"set": "int", "member": "str"},
    )
    P = mf.ops.sets.pairs(membership)
    E = P.select(u=col("left"), v=col("right"))
    C = mf.alg.graph.clique_candidates(E)
    assert set(objects(C, "clique")) == {(0, 1), (1, 2)}
    W = mf.ops.sets.members(membership, pairs=P)
    assert W.to_records() == [
        dict(left=0, right=1, member="a"),
        dict(left=1, right=2, member="b"),
    ]
    closure = mf.ops.graph.reachability(edges([(0, 2), (1, 2)]), include_self=True)
    M = closure.select(set=col("source"), member=col("target"))
    pairs = frame([dict(left=0, right=1)], {"left": "int", "right": "int"})
    values = frame(
        [dict(member=i, value=Q(1, 3)) for i in range(3)],
        {"member": "int", "value": "number"},
    )
    usage = mf.ops.sets.pair_aggregate(M, pairs, values)
    assert usage.one().value == 1
    E = usage.where(col("value") > Q(2, 3)).select(u=col("left"), v=col("right"))
    assert objects(mf.alg.graph.clique_candidates(E), "clique") == [(0, 1)]


def test_isomorphism_random_oracle_and_all_specs():
    rng = random.Random(847)
    for directed in (False, True):
        items = {
            g: [(a, b) for a in range(4) for b in range(4) if rng.random() < 0.2]
            for g in range(8)
        }
        # Always include a permuted copy with different vertex IDs.
        perm = [2, 0, 3, 1]
        items[8] = [(10 + perm[a], 10 + perm[b]) for a, b in items[0]]
        E, V = graph_instances(
            items, {g: range(10, 14) if g == 8 else range(4) for g in items}
        )
        R = mf.alg.graph.isomorphism_classes(E, vertices=V, directed=directed)
        known = {}

        def canonical(es, vs):
            representations = []
            for p in permutations(vs):
                ids = {v: i for i, v in enumerate(p)}
                edge = {(ids[a], ids[b]) for a, b in es}
                if not directed:
                    edge |= {(b, a) for a, b in edge}
                representations.append(tuple(sorted(edge)))
            return min(representations)

        expected = []
        for g, es in items.items():
            key = canonical(es, range(10, 14) if g == 8 else range(4))
            known.setdefault(key, g)
            expected.append(dict(graph=g, **{"class": known[key]}))
        assert R.to_records() == expected
        bindings = {f.to_spec()["plan"]["relation"]: f for f in (E, V)}
        for f in (R, R.relations["mapping"]):
            restored = mf.frame_from_spec(
                json.loads(json.dumps(f.to_spec())), models={}, relations=bindings
            )
            assert restored.to_records() == f.to_records()
    data = integer_system([(0, 2)], [(0, 0, Q(1, 2))], [(Q(1, 2), 1)])
    r = mf.alg.integer.enumerate(*data)
    bindings = {f.to_spec()["plan"]["relation"]: f for f in data}
    restored = mf.frame_from_spec(
        json.loads(json.dumps(r.to_spec())), models={}, relations=bindings
    )
    assert restored.to_records() == r.to_records()


def test_candidate_clock_during_array_search_preserves_previous_objects(monkeypatch):
    import mipframe.store.search_store as store
    from mipframe.frame.executor import Context
    expired=False
    monkeypatch.setattr(store,'monotonic',lambda:2 if expired else 0)
    monkeypatch.setattr(__import__('sqlite3'),'connect',lambda *a,**kw:pytest.fail('numeric SQL'))
    original=store.Objects.add
    def after_first(self,sequence):
        nonlocal expired
        original(self,sequence);expired=True
    monkeypatch.setattr(store.Objects,'add',after_first)
    r=mf.alg.graph.clique_candidates(edges([(0,1),(2,3)]),time_limit=1)
    assert objects(r,'clique')==[(0,1)]
    assert r.relations['search'].one().stop_reason=='time_limit'


def test_search_scope_provenance_survives_ops():
    r = mf.alg.graph.clique_candidates(edges([(0, 1), (1, 2)]))
    s = r.select(set=col("clique"), member=col("vertex"))
    c = mf.ops.sets.equal_groups(s)
    assert "Candidate search" in repr(c)


def test_ranking_prefix_negative_scores_and_member_limit():
    E = edges([(0, 1), (2, 3), (4, 5)])
    scores = frame(
        [dict(vertex=v, value=-1) for v in range(6)],
        {"vertex": "int", "value": "number"},
    )
    r = mf.alg.graph.clique_candidates(E, score=scores, top=2)
    assert objects(r, "clique") == [(0, 1), (2, 3)]
    assert [x.score for x in r.relations["summary"]] == [Q(-2), Q(-2)]
    assert len(mf.alg.graph.clique_candidates(E, top=2, max_output_rows=6)) == 4
    with pytest.raises(mf.QueryTooLarge):
        mf.alg.graph.clique_candidates(E, top=2, max_output_rows=5)


def test_float_seconds_recipe_is_a_resource_not_a_number_constant():
    E = edges([(0, 1)])
    result = mf.alg.graph.clique_candidates(E, time_limit=60.5)
    recipe = json.loads(json.dumps(result.to_spec()))
    assert recipe["plan"]["arguments"]["time_limit"] == {"seconds": "60.5"}
    binding = {E.to_spec()["plan"]["relation"]: E}
    restored = mf.frame_from_spec(recipe, models={}, relations=binding)
    assert restored.to_records() == result.to_records()
    assert restored.relations["search"].one().time_limit == "60.5"
