"""Signature-assisted indexed discovery and exact numerical replay."""

from fractions import Fraction as Q
from pathlib import Path
import sqlite3
from external_models import model_path

import pytest
import mipframe as mf
from scripts.indexed_instances import model


def build(tmp_path, text, name="model"):
    p = tmp_path / (name + ".lp")
    p.write_text(text)
    return mf.read(p)


def coordinate_records(t):
    # Independent public axis mappings; independent hypothesis records use positions.
    a = {r: i for i, r in enumerate(t.axes[0].ids)}
    b = {r: j for j, r in enumerate(t.axes[1].ids)}
    return [
        dict(variable=x["var"].id, axis0=a[x["i"].id], axis1=b[x["j"].id])
        for x in t.coordinates.to_records()
    ]


def test_operator_recovery_looks_up_coordinates_from_each_row(tmp_path, monkeypatch):
    from mipframe.ir.indexing.store import Store

    plans = []
    original = Store._initialize

    def initialize(store, owner):
        original(store, owner)

        def trace(sql):
            if sql.startswith(('SELECT e.v FROM q', 'SELECT c.a,c.b,e.pos FROM q')):
                plans.append(list(store.db.execute('EXPLAIN QUERY PLAN ' + sql)))

        store.db.set_trace_callback(trace)

    monkeypatch.setattr(Store, '_initialize', initialize)
    text, _ = model([[5, 2, 0], [2, 5, 1], [0, 1, 5]], groups=2)
    with build(tmp_path, text) as m:
        m.structure.indexed().verify().require()
    assert plans
    for plan in plans:
        # The unrestricted tensor scan must never be the outer loop. This
        # checks the planner used by recovery, without a machine-specific timer.
        assert 'SEARCH e ' in plan[0][3] and 'r=?' in plan[0][3]


@pytest.mark.parametrize("memory", [0.015625])
@pytest.mark.parametrize("permute", [False, True])
def test_operator_permutation_scaled_exact(tmp_path, memory, permute):
    txt, truth = model(
        [[5, 2, 0], [2, 5, 1], [0, 1, 5]],
        groups=2,
        permute=permute,
        scaled=permute,
        seed=17,
    )
    with build(tmp_path, txt) as m:
        assert m.ir.indexed["status"] == "complete"
        i = m.structure.indexed(
            signatures=m.structure.signatures(depth=3), memory_mb=memory
        )
        t = i.tensor(i.tensors.one().id)
        o = i.operator(i.operators.one().id)
        assert t.shape == [3, 2] and t.variables == 6
        t.coordinates.check("product", ("i", "j")).require()
        assert o["rows"] == 6 and o.axis_invariant
        assert o.kernel_statistics["zeros"] == 2
        assert o.kernel_statistics["alignment"]["symmetric_after_mapping"]
        assert o.kernel_statistics["alignment"]["diagonal_constant"] == "5"
        assert all(isinstance(x["value"], Q) for x in o.kernel)
        for kind, key in [
            ("cartesian", t.id),
            ("indexed_operator", o.id),
            ("row_bundle", o.id),
            ("axis_alignment", o.id),
        ]:
            i.validate(dict(kind=kind, id=key)).require()
        i.verify().require()
        assert m.structure.indexed(signatures=m.structure.signatures(depth=3)) is i
        run = next(r for r in m.ir.indexed["runs"] if r["id"] == i.id)
        assert i.tensor(run["tensors"][0]["id"]).id == t.id
        assert o.formula()
        with pytest.raises(AttributeError):
            o.id = "bad"
        with pytest.raises(AttributeError):
            i.tensors = []
        snapshot = o.kernel.to_records()
        saved = m.save(tmp_path / "saved")
    assert snapshot[0]["value"]
    for action in [
        lambda: len(i.tensors),
        lambda: t.shape,
        lambda: o.kernel,
        lambda: i.verify(),
    ]:
        with pytest.raises(mf.MIPFrameError):
            action()
    with mf.load(saved) as loaded:
        restored = loaded.structure.indexed(
            signatures=loaded.structure.signatures(depth=3)
        )
        restored.verify().require()
        assert restored.tensors.one().id == t._data["id"]


def test_same_family_bipartite_and_odd_cycle(tmp_path):
    text = "Minimize\n obj: a+b+c+d\nSubject To\n r1:a+b=1\n r2:c+d=1\n r3:a+c=1\n r4:b+d=1\nBinaries\n a b c d\nEnd\n"
    with build(tmp_path, text) as m:
        assert len(m.row_families) == 1
        i = m.structure.indexed()
        assert i.tensor(i.tensors.one().id).shape == [2, 2]
    odd = "Minimize\n obj:a+b+c\nSubject To\n r1:a+b=1\n r2:b+c=1\n r3:a+c=1\nBinaries\n a b c\nEnd\n"
    with build(tmp_path, odd, "odd") as m:
        i = m.structure.indexed()
        assert not len(i.tensors)
        assert any(x.reason == "non-bipartite candidate" for x in i.evidence)


def test_sparse_perturbed_extra_and_duplicate(tmp_path):
    for kwargs in [
        {},
        dict(missing_cell=(1, 1)),
        dict(duplicate=True),
        dict(extra=True),
        dict(perturb=(0, 1)),
    ]:
        txt, _ = model([[5, 0, 1], [1, 5, 1]], groups=3, **kwargs)
        with build(tmp_path, txt) as m:
            i = m.structure.indexed()
            i.verify().require()
            if "missing_cell" in kwargs:
                t = next(t for t in (i.tensor(r.id) for r in i.tensors) if t.variables == 8)
                assert t.missing_cells == 1 and not t.complete_product
                assert not t.coordinates.check("product", ("i", "j")).ok
            elif "duplicate" in kwargs:
                assert any(x.reason == "duplicate row coordinates" for x in i.evidence)
            elif "extra" in kwargs:
                assert any(
                    o["rows"] == 5 and not o.complete_row_product for o in (i.operator(r.id) for r in i.operators)
                )
            elif "perturb" in kwargs:
                assert any(not o.axis_invariant for o in (i.operator(r.id) for r in i.operators))
            else:
                assert i.operator(i.operators.one().id).axis_invariant


def test_external_hypotheses_and_rejections(tmp_path):
    txt, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    with build(tmp_path, txt) as m:
        i = m.structure.indexed()
        t = i.tensor(i.tensors.one().id)
        o = i.operator(i.operators.one().id)
        candidate = dict(
            operator=o.to_record(),
            coordinates=coordinate_records(t),
            rows=o.bindings.to_records(),
            kernel=o.kernel.to_records(),
        )
        i.validate(dict(kind="indexed_operator", candidate=candidate)).require()
        candidate["kernel"].append(dict(p=999, k=None, j=0, value=1))
        assert not i.validate(dict(kind="indexed_operator", candidate=candidate)).ok
        kernel = o.kernel.to_records()
        kernel[0]["value"] = 999
        assert not i.validate(dict(kind="indexed_operator", id=o.id, kernel=kernel)).ok
        assert not i.validate(dict(kind="indexed_operator", id=o.id, kernel=[])).ok
        assert not i.validate(
            dict(kind="indexed_operator", id=o.id, ast={"op": "eq"})
        ).ok
        coords = coordinate_records(t)
        coords[0]["axis0"] = 999
        assert not i.validate(dict(kind="cartesian", id=t.id, coordinates=coords)).ok
        bindings = o.bindings.to_records()
        bindings[0]["k"] = 999
        assert not i.validate(dict(kind="row_bundle", id=o.id, rows=bindings)).ok
        assert not i.validate(dict(kind="indexed_partition", sets=[])).ok
        with pytest.raises(mf.ValidationError):
            i.validate(dict(kind="indexed_operator", id=o.id, kernel=[])).require()


def test_mixed_roles_same_type_anchor_and_varying_coefficients(tmp_path):
    text = """Minimize
 obj: y
Subject To
 r0: 2 a + 3 b - y <= 0
 r1: 4 c + 5 d - y <= 0
 c0: a+c=1
 c1: b+d=1
Bounds
 0<=a<=1
 0<=b<=1
 0<=c<=1
 0<=d<=1
 y free
End
"""
    with build(tmp_path, text) as m:
        i = m.structure.indexed()
        tensor = next(t for t in (i.tensor(r.id) for r in i.tensors) if t.variables == 4 and t.complete_product)
        o = next(o for o in (i.operator(r.id) for r in i.operators) if o.tensor == tensor.id)
        assert not o.axis_invariant
        assert {x["value"] for x in o.kernel} == {Q(2), Q(3), Q(4), Q(5)}
        o.verify().require()


def test_scope_empty_repeat_wrong_owner_signature(tmp_path):
    txt, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    with build(tmp_path, txt) as m, build(tmp_path, txt, "other") as other:
        for kwargs in [
            dict(rows=other.rows),
            dict(vars=m.rows),
            dict(signatures=other.structure.signatures()),
        ]:
            with pytest.raises(ValueError):
                m.structure.indexed(**kwargs)
        empty = m.structure.indexed(rows=m.rows.head(0))
        assert not len(empty.tensors)
        full = m.structure.indexed()
        assert len(m.ir.indexed["runs"]) == 2
        repeated = m.vars._select(tuple(m.vars.ids) * 2)
        assert m.structure.indexed(vars=repeated).id == full.id
        explicit = m.structure.indexed(
            rows=m.rows.iloc[::-1],
            vars=m.vars.iloc[::-1],
            signatures=m.structure.signatures(depth=3, mode="exact"),
        )
        explicit.verify().require()
        assert len(m.ir.indexed["runs"]) == 3
        for budget in [True, 0, -1, float("inf"), 0.001]:
            with pytest.raises(ValueError):
                m.structure.indexed(memory_mb=budget)


def test_failure_is_atomic(tmp_path, monkeypatch):
    txt, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    with build(tmp_path, txt) as m:
        m.structure.indexed()
        before = m.ir.to_dict()
        contents = (m.model.path / "indexing.sqlite").read_bytes()
        from mipframe.ir.indexing import operators as operators

        def fail(*args):
            raise RuntimeError("injected")

        monkeypatch.setattr(operators, "recover", fail)
        with pytest.raises(RuntimeError):
            m.structure.indexed(rows=m.rows.head(2))
        assert m.ir.to_dict() == before
        assert (m.model.path / "indexing.sqlite").read_bytes() == contents
        assert not (m.model.path / "indexed.sqlite").exists()


def test_assign_public_replay(tmp_path):
    path = model_path("assign1-5-8.lp")
    with mf.read(path) as m:
        i = m.structure.indexed()
        t = next(t for t in (i.tensor(r.id) for r in i.tensors) if t.shape == [26, 5])
        op = next(o for o in (i.operator(r.id) for r in i.operators) if o.tensor == t.id and o["rows"] == 130)
        assert op.kernel_shape == [26, 26]
        assert op.kernel_statistics["zeros"] == 10
        assert op.kernel_statistics["alignment"]["diagonal_constant"] == "5"
        op.verify().require()


def test_structure_discovery_does_not_decode_nonuniform_coefficients(
    tmp_path, monkeypatch
):
    text = """Minimize
 obj: 0 a
Subject To
 r1: 11 a + 13 b = 0
 r2: 17 c + 19 d = 0
 r3: 23 a + 29 c = 0
 r4: 31 b + 37 d = 0
End
"""
    with build(tmp_path, text) as m:
        sig = m.structure.signatures(depth=3)
        raw = m.model
        forbidden = set(map(int, raw.coeff_id))
        original = raw.num

        def guarded(ident):
            if ident is not None and int(ident) in forbidden:
                pytest.fail("structural candidate decoded a varying coefficient")
            return original(ident)

        monkeypatch.setattr(raw, "num", guarded)
        from mipframe.store.model_store import Table

        def forbid_records(*args):
            pytest.fail("structural discovery read a full named metadata record")

        monkeypatch.setattr(Table, "__getitem__", forbid_records)
        result = m.structure.indexed(signatures=sig, memory_mb=0.015625)
        assert result.tensor(result.tensors.one().id).shape == [2, 2]
        result.tensor(result.tensors.one().id).verify().require()


def test_zero_degree_report_and_disconnected_components(tmp_path):
    text = """Minimize
 obj: a+b+c+d+e+f+g+h+z
Subject To
 a0:a+b=1
 a1:c+d=1
 a2:a+c=1
 a3:b+d=1
 b0:e+f=1
 b1:g+h=1
 b2:e+g=1
 b3:f+h=1
Bounds
 0<=z<=1
Binaries
 a b c d e f g h z
End
"""
    with build(tmp_path, text) as m:
        i = m.structure.indexed()
        assert any(i.evidence_item(x.id).to_record().get("uncovered_variables", 0) > 0 for x in i.evidence)
        local = [t for t in (i.tensor(r.id) for r in i.tensors) if t.variables == 4]
        assert len(local) == 2
        assert set(local[0].vars.ids).isdisjoint(local[1].vars.ids)
        for t in local:
            t.coordinates.check("product", ("i", "j")).require()


def test_large_sparse_scope_memory_and_display(tmp_path, monkeypatch):
    n = 200
    rows = [
        "left: " + "+".join(f"a{i}" for i in range(n)) + "=1",
        "right: " + "+".join(f"b{i}" for i in range(n)) + "=1",
    ]
    rows += [f"c{i}: a{i}+b{i}=1" for i in range(n)]
    text = "Minimize\n obj:0 a0\nSubject To\n" + "\n".join(rows) + "\nEnd\n"
    with build(tmp_path, text) as m:
        i = m.structure.indexed(
            signatures=m.structure.signatures(depth=3), memory_mb=0.015625
        )
        t = next(t for t in (i.tensor(r.id) for r in i.tensors) if t.variables == 2 * n)
        t.coordinates.check("product", ("i", "j")).require()
        from mipframe import Frame

        def forbidden(*args):
            pytest.fail("display collected every result")

        monkeypatch.setattr(Frame, "to_records", forbidden)
        assert len(str(i)) < 1000 and len(str(t)) < 1000
        assert len(str(m.ir)) < 20000
        stream = iter(t.coordinates)
        next(stream)
        m.close()
        with pytest.raises(mf.MIPFrameError):
            next(stream)


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE meta SET value='wrong' WHERE key='identity'",
        "UPDATE cards SET record='{}' WHERE kind='tensors'",
    ],
)
def test_load_rejects_incompatible_or_corrupt_results(tmp_path, change):
    txt, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    with build(tmp_path, txt) as m:
        m.structure.indexed()
        path = m.save(tmp_path / "saved")
    with sqlite3.connect(path / "indexing.sqlite") as db:
        db.execute(change)
    with pytest.raises(mf.MIPFrameError):
        mf.load(path)


def test_arbitrary_declared_axes_and_coordinate_collision(tmp_path):
    text = "Minimize\n obj:a+b+c+d\nSubject To\n r1:a+b=1\n r2:c+d=1\n r3:a+c=1\n r4:b+d=1\nEnd\n"
    with build(tmp_path, text) as m:
        i = m.structure.indexed()
        t = i.tensor(i.tensors.one().id)
        parts = [
            [
                dict(row_id=r.id, variables=[v.id for v in r.vars])
                for r in axis
            ]
            for axis in t.axes
        ]
        spec = dict(
            kind="cartesian", partitions=parts, coordinates=t.coordinates.to_records()
        )
        i.validate(spec).require()
        spec["coordinates"][0]["i"] = 999
        assert not i.validate(spec).ok
        spec["coordinates"] = coordinate_records(t)
        i.validate(spec).require()
        spec["coordinates"][1]["axis0"] = spec["coordinates"][0]["axis0"]
        spec["coordinates"][1]["axis1"] = spec["coordinates"][0]["axis1"]
        assert not i.validate(spec).ok


def test_duplicate_cell_counts_and_typed_ir_navigation(tmp_path):
    text = "Minimize\n obj:a+b+c+d+e\nSubject To\n r1:a+b+e=1\n r2:c+d=1\n r3:a+c+e=1\n r4:b+d=1\nEnd\n"
    with build(tmp_path, text) as m:
        i = m.structure.indexed()
        collision = next(
            x for x in i.evidence if x.reason == "multiple variables per cell"
        )
        collision = i.evidence_item(collision.id)
        assert collision.extra_variables == 1 and collision.duplicate_coordinates == 1
    txt, _ = model([[5, 0, 1], [1, 5, 2]], groups=2)
    with build(tmp_path, txt, "typed") as m:
        i = m.structure.indexed()
        run = m.ir.indexed["runs"][0]
        t = run["tensors"][0]
        o = run["operators"][0]
        tensor = i.tensor(t["id"])
        operator = i.operator(o["id"])
        assert len(tensor.coordinates) == 6
        assert len(tensor.vars) == 6
        assert [len(axis) for axis in tensor.axes] == t["shape"]
        assert operator.kernel.to_records() == i.operator(i.operators.one().id).kernel.to_records()
        assert operator.bindings.to_records() == i.operator(i.operators.one().id).bindings.to_records()
