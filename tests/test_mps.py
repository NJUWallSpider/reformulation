from fractions import Fraction as F
from contextlib import closing
import os
from pathlib import Path
import pytest
from mipframe import MIPFrameError
from mipframe import read

MPS = """NAME SAMPLE
OBJSENSE
 MAX
OBJNAME
 COST
ROWS
 N COST
 E EQ
 G LOW
 L HIGH
COLUMNS
 MARK0 'MARKER' 'INTORG'
 X COST 2 EQ 1
 X LOW 3 HIGH 1
 MARK1 'MARKER' 'INTEND'
 Y COST 0.125 EQ -1
 Y HIGH 2
RHS
 RHS1 COST -3 EQ 4
 RHS1 LOW 1 HIGH 10
RANGES
 RNG1 EQ -2 LOW 5
 RNG1 HIGH 3
BOUNDS
 UI BND X 8
 FR BND Y
ENDATA
"""


def test_free_mps_exact(make_model, tmp_path):
    ir = make_model(MPS, ".mps", mps_format="free")
    m = ir.model
    assert m.manifest["sense"] == "max" and m.num(m.manifest["objective_constant"]) == 3
    assert m.variables[0][2] == "I" and m.num(m.variables[0][4]) == 8
    assert m.variables[1][3:5] == (None, None)
    assert [(m.num(r[2]), m.num(r[3])) for r in m.rows] == [(2, 4), (1, 6), (7, 10)]
    assert m.row(0) == {0: F(1), 1: F(-1)} and ir.verify().status == "PROVED"
    p = tmp_path / "out.lp"
    ir.write_lp(p)

    with read(p) as restored:
        assert restored.verify().status == "PROVED"


def fixed_record(name, row, value, row2="", value2=""):
    return f"    {name:<8}  {row:<8}  {value:>12}   {row2:<8}  {value2:>12}\n"


@pytest.mark.parametrize("mps_format", ["auto", "free"])
@pytest.mark.parametrize("continuous_prefix", [False, True])
def test_free_mps_with_fixed_width_markers(tmp_path, mps_format, continuous_prefix):
    # Free-format data and fixed-width markers, as emitted in test11.mps.
    def record(name, row, value):
        return f"    {name:<24}{row:<24}{value:>12}\n"

    s = "NAME F\nROWS\n N obj\n L pmin_1_1_1\nCOLUMNS\n"
    if continuous_prefix:
        s += record("P(1,1,1)", "pmin_1_1_1", "-1")
    s += "    MARK0000  'MARKER'                 'INTORG'\n"
    s += record("B(1,1,1)", "pmin_1_1_1", "5")
    s += "    MARK0001  'MARKER'                 'INTEND'\n"
    s += record("Bg(1,1)", "pmin_1_1_1", "-1")
    s += "BOUNDS\n"
    s += f" UP {'bnd':<24}{'B(1,1,1)':<24}{8:>12}\n"
    s += f" FR {'bnd':<24}{'Bg(1,1)':<24}\n"
    s += "ENDATA\n"
    path = tmp_path / "markers.mps"
    path.write_text(s)
    with read(path, mps_format=mps_format) as model:
        m = model.model
        integer = 1 if continuous_prefix else 0
        expected = {integer: F(5), integer + 1: F(-1)}
        if continuous_prefix:
            expected[0] = F(-1)
        assert m.row(0) == expected
        assert [v[2] for v in m.variables] == (
            (["C"] if continuous_prefix else []) + ["I", "C"]
        )
        assert m.num(m.variables[integer][4]) == 8
        assert m.variables[integer + 1][3:5] == (None, None)


@pytest.mark.parametrize("mps_format", ["auto", "fixed"])
def test_fixed_mps_continuation_across_markers(tmp_path, mps_format):
    s = "NAME F\nROWS\n N  COST\n L  LIMIT\nCOLUMNS\n"
    s += fixed_record("X", "COST", "1", "LIMIT", "1")
    s += "    MARK0000  'MARKER'                 'INTORG'\n"
    s += fixed_record("", "LIMIT", "1")
    s += "    MARK0001  'MARKER'                 'INTEND'\n"
    s += fixed_record("", "LIMIT", "1")
    s += "BOUNDS\n"
    s += f" UP {'BND':<8}  {'X':<8}  {8:>12}\n"
    s += "ENDATA\n"
    path = tmp_path / "continuation.mps"
    path.write_text(s)
    with read(path, mps_format=mps_format) as model:
        m = model.model
        assert m.row(0) == {0: F(3)}
        assert m.variables[0][2] == "I"
        assert m.num(m.variables[0][4]) == 8


def test_fixed_mps_markers_and_continuation(make_model):
    s = "NAME F\nROWS\n N  COST\n L  LIMIT\nCOLUMNS\n"
    s += "    MARK0     'MARKER'                 'INTORG'\n"
    s += fixed_record("X", "COST", "1", "LIMIT", "1")
    s += fixed_record("", "LIMIT", "1")
    s += "    MARK1     'MARKER'                 'INTEND'\n"
    s += "RHS\n" + fixed_record("R", "LIMIT", "3") + "ENDATA\n"
    for fmt in ("auto", "fixed"):
        ir = make_model(s, ".mps", mps_format=fmt)
        assert (
            ir.model.variables[0][2] == "I"
            and ir.model.num(ir.model.variables[0][4]) == 1
        )
        assert ir.model.row(0) == {0: F(2)}


def test_named_vectors_require_selection(make_model):
    s = MPS.replace("RHS1 LOW 1 HIGH 10", "RHS1 LOW 1 HIGH 10\n RHS2 EQ 7")
    with pytest.raises(MIPFrameError, match="multiple RHS"):
        make_model(s, ".mps")
    ir = make_model(s, ".mps", rhs="RHS2")
    assert ir.model.num(ir.model.rows[0][3]) == 7
    with pytest.raises(MIPFrameError, match="Unknown RHS"):
        make_model(MPS, ".mps", rhs="absent")
    s = MPS.replace(" FR BND Y", " FR BND Y\n UP SECOND X 4")
    with pytest.raises(MIPFrameError, match="multiple BOUNDS"):
        make_model(s, ".mps")
    assert make_model(s, ".mps", bounds="BND").verify().status == "PROVED"


def test_mps_rejects_special_sections(make_model):
    for section in ['SOS', 'QUADOBJ', 'INDICATORS']:
        with pytest.raises(MIPFrameError, match="unsupported MPS section"):
            make_model(MPS.replace("ENDATA", section + "\nENDATA"), ".mps")


def test_mps_semicontinuous_rejected(make_model):
    with pytest.raises(MIPFrameError, match="unsupported bound"):
        make_model(MPS.replace("UI BND X", "SC BND X"), ".mps")


def test_vector_selection_versions_differ(make_model):
    s = MPS.replace("RHS1 LOW 1 HIGH 10", "RHS1 LOW 1 HIGH 10\n RHS2 EQ 7")
    a = make_model(s, ".mps", rhs="RHS1")
    b = make_model(s, ".mps", rhs="RHS2")
    assert a.model.manifest["source_sha256"] == b.model.manifest["source_sha256"]
    assert a.model.manifest["model_version"] != b.model.manifest["model_version"]


@pytest.mark.parametrize("mps_format", ["auto", "free"])
def test_padded_free_records_keep_long_names_and_exact_numbers(make_model, mps_format):
    # HiGHS-style padding looks fixed until a name or full-precision number
    # crosses a field boundary. Short records precede long ones in every section.
    text = """NAME PADDED
ROWS
 N  OBJ
 L  SHORT
 E  completion_1
 G  completion_2
COLUMNS
    X         SHORT     1
    X         OBJ       22.0000000000001
    X         SHORT     -1.123456789012
    X         completion_1  1
    X         completion_2  2
    Atp_0tr_0_li1  SHORT  3
    Atp_0tr_0_li2  completion_1  -0.9625
RHS
    RHS       SHORT     383.335706298385
    RHS       completion_1  0.0580000000000001
    RHS       completion_2  -1
RANGES
    RNG       SHORT     0.0000000000000001
    RNG       completion_1  2
BOUNDS
 UP BOUND     X         1.23456789012345
 BV BOUND     Atp_0tr_0_li1
 FX BOUND     Atp_0tr_0_li2  0.123456789012345
ENDATA
"""
    model = make_model(text, ".mps", mps_format=mps_format)
    x = model.vars.by_name("X")
    assert x.obj == F("22.0000000000001")
    assert x.ub == F("1.23456789012345")
    assert model.rows.by_name("SHORT").terms.where(var=x).one().coef == F("-0.123456789012")
    assert list(model.rows.column("name")) == ["SHORT", "completion_1", "completion_2"]
    assert list(model.vars.column("name")) == ["X", "Atp_0tr_0_li1", "Atp_0tr_0_li2"]
    short = model.rows.by_name("SHORT")
    assert short.ub == F("383.335706298385")
    assert short.lb == short.ub - F("0.0000000000000001")
    completion = model.rows.by_name("completion_1")
    assert completion.lb == F("0.0580000000000001")
    assert completion.ub == completion.lb + 2
    assert model.vars.by_name("Atp_0tr_0_li1").is_binary
    fixed = model.vars.by_name("Atp_0tr_0_li2")
    assert fixed.lb == fixed.ub == F("0.123456789012345")


@pytest.mark.parametrize("value", ["383.335706298385", "0.0580000000000001", "1.234567890123D-100"])
@pytest.mark.parametrize("slot", [1, 2])
def test_auto_never_truncates_a_padded_numeric_token(make_model, value, slot):
    # Exercise both 12-character numeric slots, including the last field.
    first, second = (value, "1") if slot == 1 else ("1", value)
    record = fixed_record("X", "A", first, "B", second)
    text = "NAME F\nROWS\n N  OBJ\n L  A\n L  B\nCOLUMNS\n"
    text += fixed_record("X", "OBJ", "1") + record + "ENDATA\n"
    auto = make_model(text, ".mps")
    free = make_model(text, ".mps", mps_format="free")
    expected = F(value.replace("D", "E"))
    assert auto.rows.by_name("A" if slot == 1 else "B").terms.one().coef == expected
    assert auto.version == free.version
    with pytest.raises(MIPFrameError, match="invalid fixed-format COLUMNS record"):
        make_model(text, ".mps", mps_format="fixed")


@pytest.mark.parametrize("mps_format", ["auto", "fixed"])
def test_fixed_embedded_spaces_and_blank_vector_continuations(make_model, mps_format):
    text = "NAME F\nROWS\n N  O BJ\n L  R ONE\n G  R TWO\nCOLUMNS\n"
    text += fixed_record("X ONE", "O BJ", "1D-2", "R ONE", "2")
    text += fixed_record("", "R TWO", "3")
    text += "RHS\n" + fixed_record("R HS", "R ONE", "5")
    text += fixed_record("", "R TWO", "-1")
    text += "RANGES\n" + fixed_record("R NG", "R ONE", "2")
    text += fixed_record("", "R TWO", "4")
    text += "BOUNDS\n" + f" UP {'B ND':<8}  {'X ONE':<8}  {'7':>12}\nENDATA\n"
    model = make_model(text, ".mps", mps_format=mps_format)
    x = model.vars.by_name("X ONE")
    assert x.obj == F("0.01") and x.ub == 7
    assert list(model.rows.column("name")) == ["R ONE", "R TWO"]
    assert [(r.lb, r.ub) for r in model.rows] == [(3, 5), (-1, 3)]
    assert [t.coef for t in model.terms()] == [2, 3]


@pytest.mark.parametrize("record, message", [
    ("    X         ABSENT_LONG_ROW  1\n", "unknown row ABSENT_LONG_ROW"),
    ("    X         LIMIT     123456789012.invalid\n", "invalid MPS number"),
])
def test_auto_keeps_errors_on_invalid_free_records(make_model, record, message):
    text = "NAME F\nROWS\n N  OBJ\n L  LIMIT\nCOLUMNS\n"
    text += fixed_record("X", "OBJ", "1") + record + "ENDATA\n"
    with pytest.raises(MIPFrameError, match=message):
        make_model(text, ".mps")


@pytest.mark.parametrize("case", [16, 30, 31, 32, 34])
def test_external_presolved_auto_matches_explicit_free(tmp_path, case):
    """Opt-in full-fact comparison; no source scripts, solvers or IR searches."""
    root = os.environ.get("MIPFRAME_EXTERNAL_DATA_ROOT")
    if not root:
        pytest.skip("Set MIPFRAME_EXTERNAL_DATA_ROOT to validate external presolved inputs")
    source = Path(root) / "playground" / f"test{case}" / "presolved.mps"
    if not source.is_file():
        pytest.skip(f"Required external snapshot is missing: {source}")
    from mipframe.store.model_store import build_numerical

    facts = []
    for mode in ("auto", "free"):
        with closing(build_numerical(source, tmp_path / mode, format="mps", mps_format=mode)) as store:
            # This canonical fingerprint covers every original name, coefficient,
            # row/variable bound, domain and objective value, in original order.
            facts.append(tuple(store.manifest[k] for k in (
                "model_version", "source_sha256", "rows", "variables", "nonzeros"
            )))
    assert facts[0] == facts[1]
