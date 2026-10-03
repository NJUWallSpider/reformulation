"""Independent exact oracles for the public algorithm layer."""

from fractions import Fraction as Q
from itertools import product
import json
import random
import pytest
import mipframe as mf
from mipframe import Frame, DType, col


def frame(rows, schema):
    return Frame.from_records(rows, schema=schema)


def domain(ids, name="id"):
    return frame(({name: i} for i in ids), {name: "int"})


def membership(sets):
    return frame(
        (dict(set=s, member=m) for s, vs in sets.items() for m in vs),
        {"set": "int", "member": "int"},
    )


def edges(items):
    return frame((dict(u=u, v=v) for u, v in items), {"u": "int", "v": "int"})


def rec(f):
    return f.to_records()


def test_pairs_all_relations_and_empty_sets():
    budget = 0.015625
    sets = {0: set(), 1: {1, 2}, 2: {1, 2}, 3: {1, 2, 3}, 4: {4}, 5: set()}
    s = membership(sets)
    d = domain(sets)
    for relation in [
        'equal',
        'subset',
        'strict_subset',
        'superset',
        'strict_superset',
        'overlap',
        'disjoint',
    ]:
        result = mf.ops.sets.pairs(
            s, sets=d, relation=relation, memory_mb=budget
        )
        symmetric = relation in ("equal", "overlap", "disjoint")
        expected = []
        for a, b in product(sets, repeat=2):
            if a == b or symmetric and a > b:
                continue
            x, y = sets[a], sets[b]
            yes = {
                "equal": x == y,
                "subset": x <= y,
                "strict_subset": x < y,
                "superset": x >= y,
                "strict_superset": x > y,
                "overlap": bool(x & y),
                "disjoint": not x & y,
            }[relation]
            if yes:
                expected.append(
                    dict(
                        left=a,
                        right=b,
                        left_n=len(x),
                        right_n=len(y),
                        intersection=len(x & y),
                        left_only=len(x - y),
                        right_only=len(y - x),
                        union_n=len(x | y),
                    )
                )
        assert rec(result) == expected
        assert len(result) == len(expected)
        assert result.key == ("left", "right")
        assert "complete" in str(result.explain())


def test_pair_scopes_candidates_self_and_members():
    sets = {0: set(), 1: {1, 2}, 2: {2, 3}, 3: {1, 2}}
    s = membership(sets)
    d = domain(sets)
    p = mf.ops.sets.pairs(
        s, sets=d, left=domain([2]), right=domain([1, 3]), relation="overlap"
    )
    assert [(r.left, r.right) for r in p] == [(2, 1), (2, 3)]
    p = mf.ops.sets.pairs(s, sets=d, relation="equal", ordered=True, include_self=True)
    assert len(p) == 6
    candidates = frame(
        [{"left": 3, "right": 1}, {"left": 0, "right": 2}],
        {"left": "int", "right": "int"},
    )
    p = mf.ops.sets.pairs(s, sets=d, candidates=candidates, relation="subset")
    assert [(r.left, r.right) for r in p] == [(0, 2), (3, 1)]
    for op in ("intersection", "union", "left_only", "right_only"):
        result = mf.ops.sets.members(s, sets=d, pairs=candidates, op=op)
        expected = []
        for a, b in [(0, 2), (3, 1)]:
            x, y = sets[a], sets[b]
            vals = {
                "intersection": x & y,
                "union": x | y,
                "left_only": x - y,
                "right_only": y - x,
            }[op]
            expected.extend(dict(left=a, right=b, member=m) for m in sorted(vals))
        assert rec(result) == expected
    with pytest.raises(ValueError):
        mf.ops.sets.pairs(s, left=domain([1]), right=domain([2]), ordered=False)


def test_validation_reports_and_budgets():
    s = membership({1: {1}, 2: {2}})
    duplicate = frame([dict(set=1, member=1)] * 3, {"set": "int", "member": "int"})
    with pytest.raises(mf.CardinalityError) as e:
        mf.ops.sets.pairs(duplicate)
    assert e.value.report.violations.one().count == 3
    with pytest.raises(mf.CardinalityError) as e:
        mf.ops.sets.pairs(s, sets=domain([1]))
    assert len(e.value.report.violations) == 1
    with pytest.raises(mf.QueryTooLarge) as e:
        mf.ops.sets.pairs(s, relation="disjoint", max_output_rows=0)
    assert e.value.count == 1
    assert len(mf.ops.sets.pairs(s, relation="equal", max_output_rows=0)) == 0
    with pytest.raises(mf.DiskBudgetExceeded):
        mf.ops.sets.pairs(s, disk_mb=0.001)
    for bad in [True, -1, 0.1]:
        with pytest.raises((TypeError, ValueError)):
            mf.ops.sets.pairs(s, max_output_rows=bad)
    with pytest.raises(ValueError):
        mf.ops.sets.pairs(s, memory_mb=0.001)


def test_exact_classes_collisions_multisets_and_empty(monkeypatch):
    import mipframe.ops.grouping as impl

    monkeypatch.setattr(
        impl, "fingerprint", lambda rows: (sum(1 for _ in rows), b"collision")
    )
    s = membership({0: {1, 2}, 1: {2, 1}, 2: {1, 3}})
    assert rec(mf.ops.sets.equal_groups(s, sets=domain(range(5)))) == [
        {"set": 0, "class": 0},
        {"set": 1, "class": 0},
        {"set": 2, "class": 2},
        {"set": 3, "class": 3},
        {"set": 4, "class": 3},
    ]
    f = frame(
        [
            dict(g=0, m=1, v=Q(1)),
            dict(g=1, m=1, v=Q(1) + Q(1, 10**40)),
            dict(g=2, m=1, v=Q(1)),
            dict(g=2, m=1, v=Q(1)),
        ],
        {"g": "int", "m": "int", "v": "number"},
    )
    assert list(mf.ops.equal_groups(f, by="g", rows=("m", "v")).column("class")) == [
        0,
        1,
        2,
    ]
    maps = f.where(col("g") < 2)
    assert list(
        mf.ops.sets.equal_groups(maps, set="g", member="m").column("class")
    ) == [0, 0]
    assert list(
        mf.ops.sets.equal_groups(maps, set="g", member="m", value="v").column("class")
    ) == [0, 1]
    nulls = frame(
        [dict(g="b", p=None), dict(g="a", p=None)],
        {"g": "str", "p": DType("number", True)},
    )
    assert list(mf.ops.equal_groups(nulls, by="g", rows=("p",)).column("class")) == [
        "a",
        "a",
    ]


def oracle_reach(E, n):
    result = set(E)
    for k in range(n):
        result |= {
            (a, b)
            for a in range(n)
            for b in range(n)
            if (a, k) in result and (k, b) in result
        }
    return result


@pytest.mark.parametrize("budget", [128, 0.015625])
def test_graph_random_oracles(budget):
    rng = random.Random(101)
    n = 12
    items = {(a, b) for a in range(n) for b in range(n) if rng.random() < 0.09}
    e = edges(sorted(items))
    d = domain(range(n))
    reachable = oracle_reach(items, n)
    expected = sorted((a, b) for a, b in reachable if a != b)
    assert [
        (r.source, r.target)
        for r in mf.ops.graph.reachability(e, vertices=d, memory_mb=budget)
    ] == expected
    scc = mf.ops.graph.scc(e, vertices=d, memory_mb=budget)
    assert [(r.vertex, r.scc) for r in scc] == [
        (
            a,
            min(
                [a]
                + [b for b in range(n) if (a, b) in reachable and (b, a) in reachable]
            ),
        )
        for a in range(n)
    ]
    undirected = oracle_reach(items | {(b, a) for a, b in items}, n)
    cc = mf.ops.graph.components(e, vertices=d, memory_mb=budget)
    assert [(r.vertex, r.component) for r in cc] == [
        (a, min([a] + [b for b in range(n) if (a, b) in undirected])) for a in range(n)
    ]
    targeted = mf.ops.graph.reachability(
        e,
        vertices=d,
        sources=domain([1, 3]),
        targets=domain([4, 6]),
        include_self=True,
        memory_mb=budget,
    )
    assert [(r.source, r.target) for r in targeted] == sorted(
        (a, b) for a, b in reachable if a in (1, 3) and b in (4, 6)
    )


def test_dag_path_reduction_provenance_and_cycles():
    items = [(0, 1), (0, 2), (1, 3), (2, 3), (0, 3), (0, 1)]
    e = frame(
        (dict(u=a, v=b, row=i) for i, (a, b) in enumerate(items)),
        {"u": "int", "v": "int", "row": "int"},
    )
    order = mf.ops.graph.topological_order(e, vertices=domain(range(5)))
    assert rec(order) == [
        dict(vertex=i, order=i, level=[0, 1, 1, 2, 0][i]) for i in range(5)
    ]
    reduced = mf.ops.graph.transitive_reduction(e)
    assert list(reduced.column("row")) == [0, 1, 2, 3, 5]
    assert rec(mf.ops.graph.reachability(e)) == rec(mf.ops.graph.reachability(reduced))
    path = mf.ops.graph.path(e, source=0, target=3)
    assert rec(path) == [dict(step=0, u=0, v=3)]
    assert path.relations["status"].one().length == 1
    path = mf.ops.graph.path(reduced, source=0, target=3)
    assert rec(path) == [dict(step=0, u=0, v=1), dict(step=1, u=1, v=3)]
    assert (
        not mf.ops.graph.path(e, source=3, target=0).relations["status"].one().reachable
    )
    own = mf.ops.graph.path(e, source=0, target=0, max_output_rows=0)
    assert len(own) == 0 and own.relations["status"].one().length == 0
    cyclic = edges([(0, 1), (1, 0), (2, 2), (3, 4)])
    for fn, error in [
        (mf.ops.graph.topological_order, mf.GraphCycleError),
        (mf.ops.graph.transitive_reduction, mf.NotDAGError),
    ]:
        with pytest.raises(error) as caught:
            fn(cyclic)
        report = caught.value.report
        assert list(report.violations.column("vertex")) == [0, 1, 2]
        assert len(report.violations.relations["witness"]) == 3


@pytest.mark.parametrize("budget", [128, 0.015625])
def test_aggregates_and_pair_arithmetic(budget):
    sets = {0: {1, 2}, 1: {2, 3}, 2: set()}
    s = membership(sets)
    d = domain(sets)
    vals = {(1, "a"): Q(1, 3), (2, "a"): Q(-1, 3), (3, "a"): Q(5, 7), (1, "b"): Q(0)}
    v = frame(
        (dict(member=m, resource=g, coef=x) for (m, g), x in vals.items()),
        {"member": "int", "resource": "str", "coef": "number"},
    )
    result = mf.ops.sets.aggregate(
        s, v, sets=d, by="resource", value="coef", memory_mb=budget
    )
    assert rec(result) == [
        dict(set=0, resource="a", value=Q(0)),
        dict(set=0, resource="b", value=Q(0)),
        dict(set=1, resource="a", value=Q(8, 21)),
    ]
    p = frame(
        (dict(left=a, right=b) for a, b in product(sets, repeat=2)),
        {"left": "int", "right": "int"},
    )
    for op in ("union", "intersection", "left_only", "right_only"):
        expected = []
        for a, b in product(sets, repeat=2):
            x, y = sets[a], sets[b]
            chosen = {
                "union": x | y,
                "intersection": x & y,
                "left_only": x - y,
                "right_only": y - x,
            }[op]
            for g in sorted({g for m, g in vals if m in chosen}):
                expected.append(
                    dict(
                        left=a,
                        right=b,
                        resource=g,
                        value=sum(
                            (v for (m, k), v in vals.items() if k == g and m in chosen),
                            Q(0),
                        ),
                    )
                )
        assert (
            rec(
                mf.ops.sets.pair_aggregate(
                    s,
                    p,
                    v,
                    sets=d,
                    by="resource",
                    value="coef",
                    op=op,
                    memory_mb=budget,
                )
            )
            == expected
        )
    ungrouped = v.where(resource="a").select("member", value=col("coef"))
    sums = mf.ops.sets.aggregate(s, ungrouped, sets=d, memory_mb=budget)
    assert list(sums.column("value")) == [Q(0), Q(8, 21), Q(0)]
    assert (
        len(mf.ops.sets.pair_aggregate(s, p, ungrouped, sets=d, memory_mb=budget)) == 9
    )


def test_algorithm_specs_and_exact_exports(tmp_path):
    s = membership({0: {1}, 1: {1, 2}, 2: set()})
    d = domain(range(3))
    result = mf.ops.sets.pairs(s, sets=d, relation="subset")
    spec = json.loads(json.dumps(result.to_spec()))
    relations = {s.to_spec()["plan"]["relation"]: s, d.to_spec()["plan"]["relation"]: d}
    restored = mf.frame_from_spec(spec, models={}, relations=relations)
    assert rec(restored) == rec(result)
    result.write(tmp_path / "result.jsonl")
    assert rec(mf.read_frame(tmp_path / "result.jsonl", models=())) == rec(result)
    spec["plan"]["algorithm_version"] = 100
    with pytest.raises(mf.SchemaError):
        mf.frame_from_spec(spec, models={}, relations=relations)


@pytest.fixture
def model(tmp_path):
    p = tmp_path / "ops.lp"
    p.write_text(
        "Minimize\n obj:0x+0y+0z\nSubject To\n a:2x-3y<=5\n b:x+4y=2\n empty:0z=0\nBounds\n x free\n y free\n z free\nEnd\n"
    )
    with mf.read(p) as m:
        yield m


def test_matrix_exact_coverage_domains_and_lifecycle(model, tmp_path):
    m = model
    values = m.vars.select("var", value=col("var").is_null())
    # Explicitly exact vectors, keyed by snapshot-qualified identity.
    values = Frame.from_records(
        [
            dict(var=m.vars.iloc[0], value=Q(1, 3)),
            dict(var=m.vars.iloc[1], value=Q(2, 7)),
        ],
        schema={"var": m.vars.schema["var"], "value": DType("number")},
        models=(m,),
    )
    result = m.matrix.activity(values)
    assert list(result.column("activity")) == [Q(-4, 21), Q(31, 21), Q(0)]
    reverse = m.matrix.activity(values, rows=m.rows.iloc[::-1], memory_mb=0.015625)
    assert list(reverse.column("activity")) == [Q(0), Q(31, 21), Q(-4, 21)]
    assert list(reverse.column("row").ids) == [2, 1, 0]
    multipliers = m.rows.select("row", value=Q(1, 3))
    assert list(m.matrix.transpose_activity(multipliers).column("value")) == [
        Q(1),
        Q(1, 3),
        Q(0),
    ]
    with pytest.raises(mf.CardinalityError) as caught:
        m.matrix.activity(values.head(1))
    assert list(caught.value.report.violations.column("var").ids) == [1]
    assert list(
        m.matrix.activity(values.head(1), missing="zero").column("activity")
    ) == [Q(2, 3), Q(1, 3), Q(0)]
    assert len(m.matrix.activity(values.head(0), rows=m.rows.iloc[2:])) == 1
    terms = m.terms().select(set=col("row"), member=col("var"))
    classes = mf.ops.sets.equal_groups(terms)
    assert len(classes) == 3
    components = mf.ops.sets.components(terms)
    assert len(components) == 3  # includes z, even with no matrix entries
    sparse = m.matrix.activity(values, rows=m.rows.head(1))
    spec = json.loads(json.dumps(sparse.to_spec()))
    key = values.to_spec()["plan"]["relation"]
    assert rec(
        mf.frame_from_spec(spec, models={m.uid: m}, relations={key: values})
    ) == rec(sparse)
    detached = rec(sparse)
    iterator = iter(sparse)
    saved = m.save(tmp_path / "saved")
    other = mf.load(saved)
    both = m.matrix.activity(values, rows=other.rows)
    other.close()
    with pytest.raises(mf.MIPFrameError):
        len(both)
    m.close()
    with pytest.raises(mf.MIPFrameError):
        next(iterator)
    with pytest.raises(mf.MIPFrameError):
        len(sparse)
    assert detached[0]["activity"] == Q(-4, 21)


def test_structural_algorithms_do_not_decode_coefficients_or_names(model, monkeypatch):
    m = model

    def forbidden(*args, **kwargs):
        raise AssertionError("Structure must not decode numerical data or names")

    monkeypatch.setattr(m.model, "num", forbidden)
    monkeypatch.setattr(m.model, "gather", forbidden)
    terms = m.terms().select(set=col("row"), member=col("var"))
    assert len(mf.ops.sets.equal_groups(terms)) == 3
    p = mf.ops.sets.pairs(terms, relation="equal")
    assert len(p) == 1
    assert len(mf.ops.sets.components(terms)) == 3


def test_disk_paths_long_chains_and_output_limits():
    n = 700
    e = edges((i, i + 1) for i in range(n - 1))
    d = domain(range(n + 1))
    a = mf.ops.graph.components(e, vertices=d, memory_mb=0.015625)
    assert "paged union-find" in str(a.explain())
    assert list(a.column("component")) == [0] * n + [n]
    reach = mf.ops.graph.reachability(
        e, sources=domain([0]), targets=domain([n - 1]), memory_mb=0.015625
    )
    assert rec(reach) == [dict(source=0, target=n - 1)]
    assert len(mf.ops.graph.scc(e, memory_mb=0.015625)) == n
    hyper = membership({0: set(range(n))})
    assert (
        list(mf.ops.sets.components(hyper, memory_mb=0.015625).column("component"))
        == [0] * n
    )
    identical = membership({i: {1} for i in range(1800)})
    with pytest.raises(mf.QueryTooLarge):
        mf.ops.sets.pairs(identical, relation="equal")
    with pytest.raises(mf.QueryTooLarge):
        mf.ops.graph.reachability(e, max_output_rows=5, memory_mb=0.015625)


def test_no_partial_publish_on_interrupt(monkeypatch):
    from mipframe.store.algorithm_store import AlgorithmStore
    import pathlib

    paths = []
    original = AlgorithmStore.__init__

    def init(self, *a, **kw):
        original(self, *a, **kw)
        paths.append(pathlib.Path(self.temp.name))

    monkeypatch.setattr(AlgorithmStore, "__init__", init)
    import mipframe.ops.grouping as grouping

    def stop(rows):
        raise KeyboardInterrupt

    monkeypatch.setattr(grouping, "fingerprint", stop)
    with pytest.raises(KeyboardInterrupt):
        mf.ops.sets.equal_groups(membership({1: {1}}))
    assert paths and all(not p.exists() for p in paths)


def test_random_dag_reduction_oracle_and_scc_fallback():
    rng = random.Random(900)
    for _ in range(12):
        n = 9
        items = {
            (a, b) for a in range(n) for b in range(a + 1, n) if rng.random() < 0.3
        }
        e = edges(sorted(items))
        d = domain(range(n))
        reduced = mf.ops.graph.transitive_reduction(e, vertices=d, memory_mb=0.015625)
        got = {(r.u, r.v) for r in reduced}
        expected = {
            edge for edge in items if edge not in oracle_reach(items - {edge}, n)
        }
        assert got == expected
    # Force the full-domain closure off bitsets, while keeping output small.
    n = 100
    e = edges([(i, i + 1) for i in range(0, n, 2)])
    a = mf.ops.graph.reachability(e, memory_mb=128, include_self=True)
    b = mf.ops.graph.reachability(e, memory_mb=0.015625, include_self=True)
    assert rec(a) == rec(b)
    assert "paged visited" in str(b.explain())
    assert "bitset closure" in str(a.explain())


def test_composition_closure_resource_conflicts_and_network():
    precedence = edges([(0, 1), (1, 2), (0, 2), (3, 2)])
    closure = mf.ops.graph.reachability(precedence, include_self=True)
    c = closure.select(set=col("source"), member=col("target"))
    candidates = frame([dict(left=0, right=3)], {"left": "int", "right": "int"})
    values = frame(
        [dict(member=i, resource="r", coef=Q(1, 3)) for i in range(4)],
        {"member": "int", "resource": "str", "coef": "number"},
    )
    usage = mf.ops.sets.pair_aggregate(
        c, candidates, values, by="resource", value="coef"
    )
    assert usage.one().value == Q(4, 3)  # shared descendant 2 counted once
    capacities = frame(
        [dict(resource="r", capacity=Q(1))], {"resource": "str", "capacity": "number"}
    )
    conflicts = usage.join(
        capacities, on="resource", cardinality="m:1", require="left"
    ).where(col("value") > col("capacity"))
    assert len(conflicts) == 1
    assert rec(
        mf.ops.graph.path(
            mf.ops.graph.transitive_reduction(precedence), source=0, target=2
        )
    ) == [dict(step=0, u=0, v=1), dict(step=1, u=1, v=2)]


def test_declared_domains_type_errors_and_invalid_vectors(model):
    nulls = frame(
        [dict(set=None, member=1)], {"set": DType("int", True), "member": "int"}
    )
    with pytest.raises(mf.CardinalityError):
        mf.ops.sets.pairs(nulls)
    e = edges([(0, 1)])
    with pytest.raises(mf.CardinalityError):
        mf.ops.graph.components(e, vertices=domain([0]))
    with pytest.raises(mf.SchemaError):
        mf.ops.graph.components(frame([dict(u=1, v="1")], {"u": "int", "v": "str"}))
    with pytest.raises(mf.CardinalityError):
        mf.ops.graph.reachability(e, sources=domain([99]))
    empty = edges([])
    assert (
        len(mf.ops.graph.components(empty))
        == len(mf.ops.graph.scc(empty))
        == len(mf.ops.graph.topological_order(empty))
        == 0
    )
    assert rec(
        mf.ops.graph.reachability(empty, vertices=domain([1]), include_self=True)
    ) == [dict(source=1, target=1)]
    m = model
    schema = {"var": m.vars.schema["var"], "value": DType("number", True)}
    for values in [
        [dict(var=m.vars.iloc[0], value=None)],
        [dict(var=m.vars.iloc[0], value=float("inf"))],
        [dict(var=m.vars.iloc[0], value=Q(1))] * 2,
    ]:
        vector = Frame.from_records(values, schema=schema, models=(m,))
        with pytest.raises(mf.CardinalityError):
            m.matrix.activity(vector, missing="zero")
    wrong = m.rows.select("row", value=1)
    with pytest.raises(mf.SchemaError):
        m.matrix.activity(wrong)
    assert len(m.matrix.transpose_activity(wrong, vars=m.vars.head(0))) == 0


def test_graph_specs_all_operators():
    e = edges([(0, 1), (1, 2), (0, 2)])
    d = domain(range(4))
    relations = {f.to_spec()["plan"]["relation"]: f for f in (e, d)}
    results = [
        mf.ops.graph.components(e, vertices=d),
        mf.ops.graph.scc(e, vertices=d),
        mf.ops.graph.topological_order(e, vertices=d),
        mf.ops.graph.reachability(e, vertices=d),
        mf.ops.graph.transitive_reduction(e, vertices=d),
        mf.ops.graph.path(e, vertices=d, source=0, target=2),
    ]
    for f in results:
        copy = mf.frame_from_spec(
            json.loads(json.dumps(f.to_spec())), models={}, relations=relations
        )
        assert rec(copy) == rec(f)


def test_materialized_sources_share_cache_allowance(monkeypatch):
    from mipframe.store.algorithm_store import AlgorithmStore

    f = membership({0: {1}, 1: {1, 2}})
    ctx = f._resource
    original = ctx.rows
    cache_limits = []

    def rows(*args, **kwargs):
        cache_limits.append(ctx.db.execute("PRAGMA cache_size").fetchone()[0])
        yield from original(*args, **kwargs)

    monkeypatch.setattr(ctx, "rows", rows)
    mf.ops.sets.pairs(f, memory_mb=0.015625)
    assert cache_limits and all(-x <= 2 for x in cache_limits)


def test_unlinked_sorter_scratch_is_accounted():
    from tempfile import TemporaryFile
    from mipframe.store.algorithm_store import AlgorithmStore

    ctx = AlgorithmStore(disk_mb=0.1)
    try:
        with TemporaryFile() as scratch:
            scratch.write(b"x" * 200000)
            scratch.flush()
            with pytest.raises(mf.DiskBudgetExceeded):
                ctx.disk_check()
    finally:
        ctx.close()


def test_matrix_extreme_exact_values(model):
    v = Frame.from_records(
        [
            dict(var=model.vars.iloc[0], value=Q(10**500)),
            dict(var=model.vars.iloc[1], value=Q(1, 10**500)),
        ],
        schema={"var": model.vars.schema["var"], "value": "number"},
        models=(model,),
    )
    result = model.matrix.activity(v)
    assert result.iloc[0].activity == 2 * Q(10**500) - 3 * Q(1, 10**500)
    assert result.iloc[1].activity == Q(10**500) + 4 * Q(1, 10**500)


def test_reused_selection_never_opens_an_extra_execution_budget(model, monkeypatch):
    from mipframe.frame.executor import Context

    # Construct the lazy selection first; execution itself must use one context.
    rows = model.rows.iloc[::-1]
    terms = model.terms(rows=rows).select(set=col("row"), member=col("var"))
    original = Context.__init__
    instances = []

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        instances.append(self)

    monkeypatch.setattr(Context, "__init__", init)
    result = mf.ops.sets.equal_groups(terms, memory_mb=0.015625)
    assert len(result) == 3
    assert len(instances) == 1
    assert instances[0].memory == 16384
