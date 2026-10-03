"""Individual row inspection and bounded formulas over lazy sparse terms."""

from fractions import Fraction as F
from math import inf

import pytest
import mipframe as mf


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "rows.lp"
    path.write_text(
        "Minimize\n o: 0x18 + 3x91 + 0x107 + 4x381\nSubject To\n"
        " capacity: 42x18 - 17x91 + 12x107 + 9x381 <= 1000\n"
        " lower: x18 >= 17\n equal: x91 - x107 = 1\n"
        " ranged: -2 <= x18 + x91 <= 3\n empty: x18 - x18 = 0\n"
        "Bounds\n x381 free\nBinaries\n x18 x91\nGenerals\n x381\nEnd\n"
    )
    with mf.read(path) as m:
        yield m


def test_loc_properties_and_bounded_row_repr(model, monkeypatch, capsys):
    row = model.rows.by_name("capacity")
    assert isinstance(row, mf.Row)
    assert row.id == 0 and row.name == "capacity"
    assert row.nnz == 4 and row.lhs == -inf and row.rhs == F(1000)
    assert row.sense == "<=" and row.coef_min == -17 and row.coef_max == 42
    assert row.unique_coef == 4
    assert row.var_types == {"B": 2, "C": 1, "I": 1}
    row.var_types["B"] = 999
    assert row.var_types["B"] == 2

    def forbidden(*args, **kwargs):
        raise AssertionError("Row display must not expand the formula")

    monkeypatch.setattr(mf.Row, "formula", forbidden)
    text = repr(row)
    assert text == (
        'Row("capacity")\n'
        "  sense       : <=\n"
        "  rhs         : 1000\n"
        "  nonzeros    : 4\n"
        "  var types   : B 2 | I 1 | C 1\n"
        "  coef range  : [-17, 42]\n"
        "  unique coef : 4"
    )
    assert capsys.readouterr().out == ""
    assert model.rows.loc[0].name == "capacity"
    assert model.rows.tail(2).by_name("empty").id == 4
    with pytest.raises(KeyError):
        model.rows.head(1).by_name("empty")
    for label in (-1, 100):
        with pytest.raises(KeyError):
            model.rows.loc[label]
    with pytest.raises(TypeError):
        model.rows.loc[True]


def test_termframe_records_selection_and_statistics(model):
    terms=model.rows.by_name('capacity').terms
    assert terms.shape==(4,3) and terms.key==('row','var')
    first=terms.iloc[0]
    assert first.coef==42 and first.var.name=='x18' and first.var.type=='B'
    assert terms.iloc[1].var.obj==3 and terms.iloc[1].coef==-17
    assert terms.iloc[-1].var.lb==-inf
    assert list(terms.tail(2).column('var').ids)==[2,3]
    assert terms.iloc[::-1].collect()==list(reversed(terms.collect()))
    assert terms.head(2).tail(1).one()==terms.iloc[1]
    assert terms.sample(3,random_state=7).collect()==terms.sample(3,random_state=7).collect()
    assert terms.column('coef').sum()==46
    assert terms.column('coef').min()==-17 and terms.column('coef').max()==42
    joined=terms.join(model.vars.select('var','type'),on='var',cardinality='m:1',require='left')
    assert {r.type:r.count for r in joined.column('type').value_counts()}=={'B':2,'C':1,'I':1}
    snapshot=terms.collect();snapshot[0]['coef']=999
    assert terms.iloc[0].coef==42
    with pytest.raises(TypeError): first['coef']=999
    assert len(terms.head(0))==len(terms.tail(0))==0
    with pytest.raises(IndexError): terms.iloc[4]
    with pytest.raises(TypeError): terms['coef']


def test_formula_order_bounds_empty_and_validation(model):
    row = model.rows.by_name("capacity")
    assert row.formula() == "42 x18 - 17 x91 + 12 x107 + 9 x381 <= 1000"
    assert row.formula(max_terms=2) == "42 x18 - 17 x91 + ... <= 1000\n[2 of 4 terms shown]"
    assert row.formula(order="index", max_terms=2) == row.formula(max_terms=2)
    assert row.formula(order="name", max_terms=2) == "12 x107 + 42 x18 + ... <= 1000\n[2 of 4 terms shown]"
    assert row.formula(max_terms=30) == row.formula()
    assert row.formula(max_terms=0) == "... <= 1000\n[0 of 4 terms shown]"
    assert isinstance(row.formula(), str)
    for value in (-1,):
        with pytest.raises(ValueError):
            row.formula(max_terms=value)
    for value in (1.5, True):
        with pytest.raises(TypeError):
            row.formula(max_terms=value)
    with pytest.raises(ValueError):
        row.formula(order="unknown")

    assert model.rows.by_name("lower").formula() == "x18 >= 17"
    assert model.rows.by_name("equal").formula() == "x91 - x107 = 1"
    assert model.rows.by_name("ranged").formula() == "-2 <= x18 + x91 <= 3"
    assert "lhs         : 17" in repr(model.rows.by_name("lower"))
    empty = model.rows.by_name("empty")
    assert empty.nnz == empty.unique_coef == 0
    assert empty.coef_min is empty.coef_max is None
    assert empty.var_types == {}
    assert empty.formula() == "0 = 0"
    assert repr(empty.terms).startswith("<TermFrame")
    assert "coef range  : [—, —]" in repr(empty)

    rows = model.rows.family('F0@2').sample(1, random_state=42)
    row = rows.one()
    assert f'{row.id}  {row.name}: {row.formula()}' in rows.formula()
    empty = model.rows.head(0)
    assert empty.formula().startswith('<RowFrame: 0 of ')
    for value, error in ((-1, ValueError), (True, TypeError), (1.5, TypeError)):
        with pytest.raises(error):
            empty.formula(max_terms=value)
    with pytest.raises(ValueError):
        empty.formula(order='unknown')
    model.close()
    with pytest.raises(mf.MIPFrameError, match='closed'):
        rows.formula()


def test_frame_formula_preserves_selection_and_reads_only_visible_rows(model, monkeypatch, capsys):
    from mipframe.frame.row import Formula

    monkeypatch.setattr(mf.options.display, 'max_rows', 3)
    rows = mf.RowFrame(model, (3, 0, 1, 0))
    original = model.model.iter_terms
    scanned = []

    def record(ident):
        scanned.append(ident)
        return original(ident)

    monkeypatch.setattr(model.model, 'iter_terms', record)
    output = rows.formula(order='index', max_terms=2)
    assert isinstance(output, Formula)
    assert scanned == [3, 0, 1]
    assert '3  ranged: -2 <= x18 + x91 <= 3' in output
    assert output.count('0  capacity: 42 x18 - 17 x91 + ... <= 1000') == 1
    assert output.count('[2 of 4 terms shown]') == 1
    assert 'lower:' in output
    assert output == rows.show(formula=True, order='index', max_terms=2)
    assert rows.show() == repr(rows)
    assert capsys.readouterr().out == ''


def test_large_coefficients_exact_order_and_ties(tmp_path):
    path = tmp_path / "exact.lp"
    path.write_text(
        "Minimize\n o: 0\nSubject To\n"
        " r: 1x0 - 1x1 + 1.000000000000000000001x2 <= 3\nEnd\n"
    )
    with mf.read(path) as m:
        row = m.rows.by_name("r")
        assert row.formula(max_terms=1).startswith("1.000000000000000000001 x2")
        assert row.formula(max_terms=2).startswith("1.000000000000000000001 x2 + x0")
        assert row.coef_max == F("1.000000000000000000001")
        assert row.unique_coef == 3


def test_name_and_index_order_differ_and_index_reads_prefix_only(tmp_path, monkeypatch):
    path = tmp_path / "order.lp"
    path.write_text("Minimize\n o: 0z + 0a + 0m\nSubject To\n r: z + 2a - 3m <= 1\nEnd\n")
    with mf.read(path) as m:
        row = m.rows.by_name("r")
        assert row.formula(order="name", max_terms=1).startswith("2 a")
        assert row.formula(max_terms=1).startswith("-3 m")
        original = m.model.iter_terms

        def prefix(ident):
            source = original(ident)
            yield next(source)
            raise AssertionError("index ordering must not scan omitted terms")

        monkeypatch.setattr(m.model, "iter_terms", prefix)
        assert row.formula(order="index", max_terms=1) == "z + ... <= 1\n[1 of 3 terms shown]"


def test_notebook_repr_options_and_closed_lifecycle(model, tmp_path, monkeypatch):
    row = model.rows.by_name("capacity")
    terms = row.terms
    ipython = pytest.importorskip("IPython.lib.pretty")
    assert ipython.pretty(row) == repr(row)
    assert ipython.pretty(row.formula(max_terms=2)) == str(row.formula(max_terms=2))
    assert ipython.pretty(terms.head(2)) == repr(terms.head(2))
    monkeypatch.setattr(mf.options.display, "max_rows", 2)
    monkeypatch.setattr(mf.options.display, "max_cols", 2)
    text = repr(terms)
    assert text.startswith("<TermFrame")
    assert "..." in text and "var" in text and "coef" not in text
    saved = model.save(tmp_path / "saved")
    expected = row.formula()
    repr(row)  # Populate cached statistics before closing.
    model.close()
    for operation in (
        lambda: row.nnz, lambda: row.terms, lambda: row.lhs, lambda: row.rhs,
        lambda: row.sense, lambda: row.coef_min, lambda: row.coef_max,
        lambda: row.formula(), lambda: repr(row), lambda: terms.head(1),
        lambda: terms[0], lambda: terms.column("coef").value_counts(),
    ):
        with pytest.raises(mf.MIPFrameError, match="closed"):
            operation()
    with mf.load(saved) as loaded:
        assert loaded.rows.by_name("capacity").formula() == expected


def test_hundred_thousand_terms_stay_lazy_and_formula_is_bounded(tmp_path, monkeypatch):
    size = 100_000
    path = tmp_path / "huge.lp"
    path.write_text(
        "Minimize\n o: 0\nSubject To\n capacity: "
        + " + ".join(f"x{i}" for i in range(size - 1))
        + f" + 103808x{size - 1} <= 1000\nBinaries\n"
        + " ".join(f"x{i}" for i in range(size)) + "\nEnd\n"
    )
    with mf.read(path) as m:
        def forbidden(*args, **kwargs):
            raise AssertionError("No eager coefficient dictionaries or row ID lists")

        monkeypatch.setattr(m.model, "row", forbidden)
        monkeypatch.setattr(m.model, "row_ids", forbidden)
        row = m.rows.by_name("capacity")
        # Lookup and getting terms must not scan the row.
        assert row._stats is None
        terms = row.terms
        assert terms.key == ('row','var') and row._stats is None
        original = type(m.model.variables).__getitem__
        fetched = []

        def metadata(table, index):
            if table is m.model.variables:
                fetched.append(index)
            return original(table, index)

        with monkeypatch.context() as patch:
            patch.setattr(type(m.model.variables), "__getitem__", metadata)
            preview = repr(terms.head(10))
            assert fetched == []  # Names are gathered directly; no full variable records.
            assert preview.startswith("<TermFrame")
        assert len(repr(row)) < 250
        assert row.var_types == {"B": size}
        assert row.nnz == size and row.unique_coef == 2
        formula = row.formula()
        assert formula.startswith("103808 x99999 + x0")
        assert formula.endswith("[16 of 100,000 terms shown]")
        assert len(formula) < 300
        assert "x99999" not in row.formula(order="index")
