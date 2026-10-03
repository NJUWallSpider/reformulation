"""Exact local matrices, sparse work bounds, axis identities and lifecycle."""

import builtins
import gc
from fractions import Fraction as Q
from itertools import islice

import numpy as np
import pytest

import mipframe as mf
from mipframe.frame.row import Formula

def counts(frame):
    axis=frame.key[0]
    return {r[axis].id:r["nnz"] for r in frame.collect()}

def ref(model,axis,ident):
    return mf.Ref(model.uid,model.version,axis,ident)


@pytest.fixture
def model(tmp_path):
    source = tmp_path / "matrix.lp"
    source.write_text(
        "Minimize\n obj: 0x0 + 0x1 + 0x2 + 0x3 + 99unused\nSubject To\n"
        " c0: 2x0 - x1 + 0.5x3 <= 10\n"
        " c1: -3x1 + 4x2 >= -1\n"
        " c2: x0 + 2x2 = 3\n"
        " c3: 0 = 0\n"
        " c4: -4x0 + 2x1 - x3 >= -20\nEnd\n"
    )
    with mf.read(source) as model:
        yield model


def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected materialization or coefficient access")


def test_full_matrix_metadata_counts_and_display(model, monkeypatch, capsys):
    monkeypatch.setattr(model.model, "num", forbidden)
    monkeypatch.setattr(model.model, "iter_terms", forbidden)
    monkeypatch.setattr(mf.MatrixView, "preview", forbidden)
    monkeypatch.setattr(mf.MatrixView, "to_scipy", forbidden)
    s = model.matrix.view()
    assert isinstance(s, mf.MatrixView)
    assert s.shape == (5, 5)
    assert s.nnz == 10 and s.density == 0.4
    assert counts(s.row_nnz) == {0: 3, 1: 2, 2: 2, 3: 0, 4: 3}
    assert counts(s.var_nnz) == {0: 3, 1: 3, 2: 2, 3: 2, 4: 0}
    assert isinstance(s.row_nnz, mf.Frame)
    assert repr(s) == str(s) == (
        "MatrixView\n  shape   : 5 × 5\n  nnz     : 10\n"
        "  density : 0.4\n\n  display : not materialized"
    )
    assert capsys.readouterr().out == ""
    pretty = pytest.importorskip("IPython.lib.pretty").pretty
    assert pretty(s) == repr(s)


def test_local_counts_and_entries(model):
    for (row_ids, var_ids) in [
        ((2, 0, 3), (3, 0, 4)),
        ((4, 3, 2, 1, 0), (4, 3, 2, 1, 0)),
        ((), (2,)),
        ((1,), ()),
    ]:
        expected = {
            0: {0: Q(2), 1: Q(-1), 3: Q(1, 2)},
            1: {1: Q(-3), 2: Q(4)}, 2: {0: Q(1), 2: Q(2)},
            3: {}, 4: {0: Q(-4), 1: Q(2), 3: Q(-1)},
        }
        names = {0: "x0", 1: "x1", 2: "x2", 3: "x3", 4: "unused"}
        s = model.matrix.view(rows=mf.RowFrame(model, row_ids), vars=mf.VarFrame(model, var_ids))
        records = [
            {"row": ref(model,"row",r), "var": ref(model,"var",v), "coef": value}
            for r in sorted(row_ids) for v, value in expected[r].items() if v in var_ids
        ]
        assert s.shape == (len(row_ids), len(var_ids))
        assert tuple(s.rows.ids) == row_ids and tuple(s.vars.ids) == var_ids
        assert s.entries.columns == ("row", "var", "coef")
        assert s.entries.to_records() == records == [t.to_record() for t in s.entries]
        assert all(isinstance(r["coef"], Q) for r in s.entries)
        assert s.nnz == len(records) == len(s.entries)
        assert s.nnz == s.row_nnz.column("nnz").sum() == s.var_nnz.column("nnz").sum()
        assert counts(s.row_nnz) == {r: sum(v in var_ids for v in expected[r]) for r in row_ids}
        assert counts(s.var_nnz) == {v: sum(v in expected[r] for r in row_ids) for v in var_ids}
        assert s.density == (len(records) / (len(row_ids) * len(var_ids)) if row_ids and var_ids else 0)
        assert s.entries.head(2).to_records() == records[:2]
        assert s.entries.head(2).head(9).to_records() == records[:2]
        assert len(s.entries.head(99)) == len(records)
        assert len(s.entries.head(0)) == 0


def test_selection_deduplicates_and_validates(model, tmp_path):
    s = model.matrix.view(rows=mf.RowFrame(model, (2, 0, 2)), vars=mf.VarFrame(model, (3, 0, 3)))
    assert tuple(s.rows.ids) == (2, 0) and tuple(s.vars.ids) == (3, 0)
    assert s.shape == (2, 2) and s.nnz == 3
    for name, bad in [("rows", model.vars), ("vars", model.rows), ("rows", [0]), ("vars", ["x0"])]:
        with pytest.raises(TypeError):
            model.matrix.view(**{name: bad})
    for ids in [(-1,), (5,), range(-1, 2), range(7)]:
        with pytest.raises(ValueError, match="invalid"):
            model.matrix.view(rows=mf.RowFrame(model, ids))
    for ids in [(True,), ("c0",)]:
        with pytest.raises(TypeError, match="integer"):
            model.matrix.view(rows=mf.RowFrame(model, ids))
    with mf.load(model.save(tmp_path / "saved")) as other:
        assert model.matrix.view(rows=other.rows).shape == model.shape
        assert model.matrix.view(vars=other.vars).shape == model.shape


def test_family_members_remain_lazy_and_coefficients_original(model, monkeypatch):
    families = model.rows.families()
    rows = families.relations["members"].where(family="F0").refs("row")
    assert tuple(rows.ids) == (0, 4)
    index_type = type(rows.ids)
    with monkeypatch.context() as patch:
        patch.setattr(index_type, "__iter__", forbidden)
        patch.setattr(index_type, "__getitem__", forbidden)
        s = model.matrix.view(rows=rows)
        assert s.rows.ids is rows.ids
        assert s.rows.ids is rows.ids
    local = model.matrix.view(rows=rows, vars=rows.neighbors())
    del families, rows
    gc.collect()
    assert local.shape == (2, 3)
    assert [r["coef"] for r in local.entries] == [Q(2), Q(-1), Q(1, 2), Q(-4), Q(2), Q(-1)]
    assert set(local.var_nnz.column("var").ids) == {0, 1, 3}
    assert 4 not in local.rows.incidence_counts().column("var").ids  # Existing positive-only semantics.
    sampled = model.rows.sample(3, random_state=42)
    assert tuple(model.matrix.view(rows=sampled).rows.ids) == tuple(sampled.ids)


def test_partial_statistics_do_not_decode_and_are_cached(model, monkeypatch):
    s = model.matrix.view(rows=model.rows.head(3), vars=model.vars.head(2))
    monkeypatch.setattr(model.model, "num", forbidden)
    assert s.nnz == 4
    monkeypatch.setattr(s, "_positions", forbidden)
    assert s.nnz == 4
    assert counts(s.row_nnz) == {0: 2, 1: 1, 2: 1}
    assert counts(s.var_nnz) == {0: 2, 1: 2}
    assert "not materialized" in repr(s)


def test_complete_axis_statistics_use_pointers(model, monkeypatch):
    monkeypatch.setattr(mf.MatrixView, "_positions", forbidden)
    a = model.matrix.view(rows=model.rows.head(2))
    assert a.nnz == 5 and counts(a.row_nnz) == {0: 3, 1: 2}
    b = model.matrix.view(vars=model.vars.head(2))
    assert b.nnz == 6 and counts(b.var_nnz) == {0: 3, 1: 3}
    whole = model.matrix.view(rows=model.rows.iloc[::-1], vars=model.vars.iloc[::-1])
    assert whole.nnz == 10 and whole.var_nnz.column("nnz").sum() == 10


def preview_columns(text):
    return str(text).splitlines()[3].split()


def test_preview_strategies_exact_cells_and_global_limits(model, monkeypatch, capsys):
    s = model.matrix.view(vars=model.vars.iloc[::-1])
    monkeypatch.setattr(mf.options.display, "max_rows", 1)
    monkeypatch.setattr(mf.options.display, "max_cols", 1)
    text = s.preview(max_rows=2, max_vars=2)
    assert isinstance(text, Formula)
    assert text.startswith("2 rows × 2 vars shown\nselected from 5 × 5; choose_vars=most_active")
    assert preview_columns(text) == ["x1", "x0", "..."]
    assert text.splitlines()[4].split() == ["c0", "-1", "2", "..."]
    assert text.splitlines()[5].split() == ["c1", "-3", ".", "..."]
    assert preview_columns(s.preview(max_vars=2, choose_vars="largest_coef")) == ["x2", "x0", "..."]
    assert preview_columns(s.preview(max_vars=2, choose_vars="first")) == ["unused", "x3", "..."]
    assert "0.5" in s.preview(choose_vars="first")
    assert preview_columns(s.preview()) == ["x1", "x0", "x3", "x2", "unused"]
    assert capsys.readouterr().out == ""
    pretty = pytest.importorskip("IPython.lib.pretty").pretty
    assert pretty(text) == str(text)


def test_ranking_uses_all_selected_rows_and_exact_magnitudes(tmp_path):
    path = tmp_path / "ranking.lp"
    path.write_text(
        "Minimize\n o: " + " + ".join(f"0x{i}" for i in range(16)) + "\nSubject To\n"
        + "".join(f" r{i}: x12 <= 1\n" for i in range(8))
        + "".join(f" r{i}: x13 <= 1\n" for i in range(8, 17))
        + " r17: -2x14 - 2.000000000000000000001x15 <= 0\nEnd\n"
    )
    with mf.read(path) as m:
        s = m.matrix.view()
        assert preview_columns(s.preview(max_vars=1)) == ["x13", "..."]
        assert preview_columns(s.preview(max_vars=1, choose_vars="largest_coef")) == ["x15", "..."]
        assert preview_columns(s.preview(max_vars=1, choose_vars="first")) == ["x0", "..."]
        assert preview_columns(s.preview())[:4] == ["x13", "x12", "x14", "x15"]
        restricted = m.matrix.view(rows=m.rows.head(8), vars=m.vars.head(15))
        assert preview_columns(restricted.preview(max_vars=1)) == ["x12", "..."]
        assert preview_columns(restricted.preview(max_vars=1, choose_vars="largest_coef")) == ["x12", "..."]


def test_preview_limit_validation(model):
    for keyword in ['max_rows', 'max_vars']:
        for value, error in [(True, TypeError), (1.5, TypeError), (-1, ValueError)]:
            with pytest.raises(error):
                model.matrix.view().preview(**{keyword: value})


def test_empty_preview_and_entries_validation(model, monkeypatch):
    s = model.matrix.view()
    with pytest.raises(ValueError, match="choose_vars"):
        s.preview(choose_vars="unknown")
    for bad, error in [(True, TypeError), (1.5, TypeError), (-1, ValueError)]:
        with pytest.raises(error):
            s.entries.head(bad)
    monkeypatch.setattr(model.model, "num", forbidden)
    monkeypatch.setattr(mf.MatrixView, "_positions", forbidden)
    assert "empty preview" in s.preview(max_rows=0)
    assert "empty preview" in s.preview(max_vars=0)
    assert "empty preview" in model.matrix.view(rows=model.rows.head(0)).preview()
    assert "empty preview" in model.matrix.view(vars=model.vars.head(0)).preview()


class ReadTracker:
    def __init__(self, values):
        self.values = values
        self.reads = []

    def __len__(self):
        return len(self.values)

    def __getitem__(self, key):
        assert isinstance(key, int), "Sparse iteration should not copy array slices"
        self.reads.append(key)
        return self.values[key]


def test_entries_prefix_is_bounded_and_repeatable(model, monkeypatch):
    cols, coeff = ReadTracker(model.model.col_idx), ReadTracker(model.model.coeff_id)
    monkeypatch.setattr(model.model, "col_idx", cols)
    monkeypatch.setattr(model.model, "coeff_id", coeff)
    monkeypatch.setattr(mf.MatrixView, "nnz", property(forbidden))
    entries = model.matrix.view().entries
    head = entries.head(2)
    assert cols.reads == coeff.reads == []
    expected = [{"row": ref(model,"row",0), "var": ref(model,"var",0), "coef": Q(2)},
                {"row": ref(model,"row",0), "var": ref(model,"var",1), "coef": Q(-1)}]
    assert head.to_records() == expected
    assert cols.reads == coeff.reads == [0, 1]
    cols.reads.clear()
    coeff.reads.clear()
    assert len(head) == 2
    assert cols.reads == [0, 1] and coeff.reads == []
    cols.reads.clear()
    monkeypatch.setattr(mf.options.display, "max_rows", 2)
    assert "..." in repr(entries)
    assert cols.reads == [0, 1, 2] and coeff.reads == [0, 1, 2]
    cols.reads.clear()
    coeff.reads.clear()
    assert "..." not in repr(head)
    assert cols.reads == coeff.reads == [0, 1]
    assert [t.to_record() for t in islice(entries, 2)] == expected
    pretty = pytest.importorskip("IPython.lib.pretty").pretty
    assert pretty(head) == repr(head)


def test_scipy_order_independence_and_rounding(model, monkeypatch):
    sparse = pytest.importorskip("scipy.sparse")
    s = model.matrix.view(rows=mf.RowFrame(model, (2, 3, 0)), vars=mf.VarFrame(model, (3, 0, 4, 2)))
    with monkeypatch.context() as patch:
        patch.setattr(sparse.csr_matrix, "toarray", forbidden)
        patch.setattr(sparse.csr_matrix, "__getitem__", forbidden)
        a = s.to_scipy()
    assert isinstance(a, sparse.csr_matrix)
    assert a.shape == s.shape and a.nnz == s.nnz == 4
    assert a.dtype == np.float64 and a.has_canonical_format
    np.testing.assert_array_equal(a.toarray(), [[0, 1, 0, 2], [0, 0, 0, 0], [0.5, 2, 0, 0]])
    for array in (a.data, a.indices, a.indptr):
        for original in (model.model.col_idx, model.model.coeff_id, model.model.row_ptr):
            assert not np.shares_memory(array, original)
    a.data[:] = 42
    assert s.entries.to_records()[0]["coef"] == 2
    model.close()
    assert a.sum() == 168


def test_scipy_empty_axes(model):
    for rows, variables in [(0, 5), (5, 0), (0, 0)]:
        pytest.importorskip("scipy")
        s = model.matrix.view(rows=model.rows.head(rows), vars=model.vars.head(variables))
        a = s.to_scipy()
        assert a.shape == (rows, variables) and a.nnz == 0


def test_scipy_unrepresentable_values(tmp_path):
    for coef, message in [('1e1000', 'finite float64'), ('1e-1000', 'underflows')]:
        pytest.importorskip("scipy")
        path = tmp_path / "extreme.lp"
        path.write_text(f"Minimize\n o: 0\nSubject To\n r: {coef}x <= 1\nEnd\n")
        with mf.read(path) as m:
            s = m.matrix.view()
            assert s.entries.to_records()[0]["coef"] == Q(coef)
            with pytest.raises(mf.MIPFrameError, match=message):
                s.to_scipy()


def test_scipy_is_optional(model, monkeypatch):
    original = builtins.__import__

    def without_scipy(name, *args, **kwargs):
        if name == "scipy" or name.startswith("scipy."):
            raise ImportError("test: scipy unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_scipy)
    s = model.matrix.view()
    assert s.nnz == 10 and s.entries.head(1).to_records()
    assert "5 rows × 5 vars" in s.preview()
    with pytest.raises(mf.MIPFrameError, match=r"mipframe\[scipy\]"):
        s.to_scipy()


def test_all_live_views_guard_model_close(model):
    accessor = model.matrix
    s = accessor.view()
    rows, variables, entries = s.rows, s.vars, s.entries
    counts = (s.row_nnz, s.var_nnz)
    assert s.nnz == 10
    iterator = iter(entries)
    next(iterator)
    model.close()
    for action in (
        lambda: model.matrix, lambda: accessor.view(), lambda: s.rows, lambda: s.vars,
        lambda: s.shape, lambda: s.nnz, lambda: s.density, lambda: s.row_nnz,
        lambda: s.var_nnz, lambda: s.entries, lambda: s.preview(), lambda: s.to_scipy(),
        lambda: repr(s), lambda: rows.ids, lambda: variables.ids,
        lambda: entries.head(), lambda: entries.to_records(), lambda: iter(entries),
        lambda: len(entries), lambda: repr(entries), lambda: next(iterator),
        lambda: counts[0].collect(), lambda: counts[1].iloc[0],
    ):
        with pytest.raises(mf.MIPFrameError, match="closed"):
            action()


def test_large_sparse_matrix_never_allocates_a_full_grid(model, monkeypatch):
    import mipframe.ops.matrix as matrix_module

    class Labels:
        def __init__(self, size, prefix):
            self.size, self.prefix = size, prefix
            self.reads = []

        def __len__(self):
            return self.size

        def __getitem__(self, ident):
            self.reads.append(ident)
            return ident, f"{self.prefix}{ident}"

    nrows, nvars = 29_991, 48_217
    nnz = nrows * 5
    store = model.model
    rows, variables = Labels(nrows, "c"), Labels(nvars, "x")
    row_ptr = np.arange(0, nnz + 1, 5, dtype=np.int64)
    col_ptr = np.zeros(nvars + 1, dtype=np.int64)
    col_ptr[-6:] = np.arange(6) * nrows
    coeff = ReadTracker(np.full(nnz, store.coeff_id[0], dtype=np.int64))
    cols = ReadTracker(np.tile(np.arange(nvars - 5, nvars), nrows))
    for name, value in (("rows", rows), ("variables", variables), ("row_ptr", row_ptr),
                        ("col_ptr", col_ptr), ("coeff_id", coeff), ("col_idx", cols)):
        monkeypatch.setattr(store, name, value)

    # All numerical allocations in inspection must be one-dimensional and bounded.
    def bounded_factory(original):
        def allocate(shape, *args, **kwargs):
            if isinstance(shape, tuple):
                assert len(shape) == 1
                shape = shape[0]
            assert shape <= max(nnz, nvars + 1)
            return original(shape, *args, **kwargs)
        return allocate

    for name in ("empty", "zeros", "ones", "full"):
        monkeypatch.setattr(np, name, bounded_factory(getattr(np, name)))
    original_grid = matrix_module._grid

    def bounded_grid(lines):
        # Header + 8 data rows + omission; row name + 12 vars + omission.
        assert len(lines) == 10 and all(len(line) == 14 for line in lines)
        return original_grid(lines)

    monkeypatch.setattr(matrix_module, "_grid", bounded_grid)
    s = model.matrix.view()
    assert s.shape == (nrows, nvars)
    assert "149,955" in repr(s) and "1.04e-4" in repr(s)
    assert not cols.reads and not coeff.reads and not rows.reads and not variables.reads
    text = s.preview()
    assert text.startswith("8 rows × 12 vars shown\nselected from 29,991 × 48,217")
    assert preview_columns(text)[:5] == [f"x{i}" for i in range(nvars - 5, nvars)]
    assert cols.reads == coeff.reads == list(range(40))
    assert len(rows.reads) == 8 and len(variables.reads) == 12
    cols.reads.clear()
    coeff.reads.clear()
    assert len(s.entries.head(20).to_records()) == 20
    assert cols.reads == coeff.reads == list(range(20))


def test_all_zero_slice_and_zero_model(model, tmp_path):
    s = model.matrix.view(rows=model.rows.iloc[3:4], vars=model.vars.iloc[::-1])
    assert s.nnz == 0 and s.density == 0
    assert counts(s.row_nnz) == {3: 0}
    assert counts(s.var_nnz) == dict.fromkeys(range(5), 0)
    assert s.var_nnz.column("nnz").count() == 5
    assert s.var_nnz.column("nnz").value_counts().collect() == [{"nnz":0,"count":5}]
    assert s.entries.to_records() == []
    for strategy in ("first", "most_active", "largest_coef"):
        text = s.preview(choose_vars=strategy)
        assert preview_columns(text) == ["unused", "x3", "x2", "x1", "x0"]
        assert text.splitlines()[4].split() == ["c3", ".", ".", ".", ".", "."]
    source = tmp_path / "empty.lp"
    source.write_text("Minimize\n o: 0\nSubject To\nEnd\n")
    with mf.read(source) as empty:
        s = empty.matrix.view()
        assert s.shape == (0, 0) and s.nnz == 0 and s.density == 0
        assert counts(s.row_nnz) == counts(s.var_nnz) == {}
        assert "empty preview" in s.preview()
        assert "TermFrame" in repr(s.entries)


def test_scipy_sampled_axes_match_exact_entries(model):
    pytest.importorskip("scipy")
    s = model.matrix.view(rows=model.rows.sample(5, random_state=10),
                          vars=model.vars.sample(5, random_state=20))
    a = s.to_scipy()
    for i, row in enumerate(s.rows.ids):
        exact = model.constraints[row].coefficients
        for j, var in enumerate(s.vars.ids):
            assert a[i, j] == float(exact.get(var, 0))


def test_mps_matrix_and_save_reload(tmp_path):
    path = tmp_path / "matrix.mps"
    path.write_text(
        "NAME local\nROWS\n N OBJ\n L A\n G B\nCOLUMNS\n"
        " x OBJ 100 A 0.1\n x B -3\n y A -2 B 0.5\n"
        "RHS\n rhs A 1 B 2\nENDATA\n"
    )
    with mf.read(path, mps_format="free") as model:
        s = model.matrix.view(rows=model.rows.iloc[::-1], vars=model.vars.iloc[::-1])
        expected = s.entries.to_records()
        assert s.nnz == 4
        assert [r["coef"] for r in expected] == [Q(1, 10), Q(-2), Q(-3), Q(1, 2)]
        saved = model.save(tmp_path / "native")
    with mf.load(saved) as restored:
        s = restored.matrix.view(rows=restored.rows.iloc[::-1], vars=restored.vars.iloc[::-1])
        assert s.entries.to_records() == expected
        assert "0.1" in s.preview()
        pytest.importorskip("scipy")
        assert s.to_scipy()[1, 1] == float(Q(1, 10))


def test_counts_use_sparse_opposite_axis_and_family_masks(tmp_path, monkeypatch):
    from mipframe.frame.executor import Context
    from mipframe.store.sparse_store import SparseStore

    path = tmp_path / 'disjoint.lp'
    rows = '\n'.join(f'r{i}:x{2*i}+x{2*i+1}=1' for i in range(12))
    path.write_text('Minimize\n obj:0\nSubject To\n' + rows + '\nEnd\n')
    with mf.read(path) as m:
        monkeypatch.setattr(Context, 'contains',
                            lambda *args: pytest.fail('per-entry SQL membership'))
        original = SparseStore.blocks
        axes = []

        def blocks(store, axis, source, block):
            axes.append(axis)
            yield from original(store, axis, source, block)

        monkeypatch.setattr(SparseStore, 'blocks', blocks)
        result = m.matrix.nnz(rows=m.rows.iloc[::-1], vars=m.vars.head(2)).to_records()
        assert [r['row'].id for r in result] == list(range(11, -1, -1))
        assert [r['nnz'] for r in result] == [0] * 11 + [2]
        assert axes == ['var', 'var']

        axes.clear()
        result = m.matrix.nnz(rows=m.rows.head(1), axis='var').to_records()
        assert [r['nnz'] for r in result] == [1, 1] + [0] * 22
        assert axes == ['row']

        family = m.vars.family('V0@1')
        assert list(counts(m.matrix.nnz(vars=family)).values()) == [2] * 12

        stage = Context.stage

        def bounded_stage(ctx, frame, fields=None):
            if frame is family:
                pytest.fail('expanded the entire family for set membership')
            return stage(ctx, frame, fields)

        monkeypatch.setattr(Context, 'stage', bounded_stage)
        selected = m.vars._select((5, 1, 5, 3))
        assert list((selected & family).ids) == [1, 3, 5]
        assert list((selected - family).ids) == []
        # A sliced family must still obey its current selection, not its color.
        assert list((selected & family.head(2)).ids) == [1]
        assert list((selected - family.head(2)).ids) == [3, 5]
