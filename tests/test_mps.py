from fractions import Fraction as F
import pytest
from lpir import LPIRError

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


def test_free_mps_exact(make_v2, tmp_path):
    ir = make_v2(MPS, ".mps", mps_format="free")
    m = ir.model
    assert m.manifest["sense"] == "max" and m.num(m.manifest["objective_constant"]) == 3
    assert m.variables[0][2] == "I" and m.num(m.variables[0][4]) == 8
    assert m.variables[1][3:5] == (None, None)
    assert [(m.num(r[2]), m.num(r[3])) for r in m.rows] == [(2, 4), (1, 6), (7, 10)]
    assert m.row(0) == {0: F(1), 1: F(-1)} and ir.verify()["status"] == "PASS"
    p = tmp_path / "out.lp"
    ir.export(p)
    from lpir import IR

    assert IR.build(p, tmp_path / "roundtrip").verify()["status"] == "PASS"


def fixed_record(name, row, value, row2="", value2=""):
    return f"    {name:<8}  {row:<8}  {value:>12}   {row2:<8}  {value2:>12}\n"


def test_fixed_mps_markers_and_continuation(make_v2):
    s = "NAME F\nROWS\n N  COST\n L  LIMIT\nCOLUMNS\n"
    s += "    MARK0     'MARKER'                 'INTORG'\n"
    s += fixed_record("X", "COST", "1", "LIMIT", "1")
    s += fixed_record("", "LIMIT", "1")
    s += "    MARK1     'MARKER'                 'INTEND'\n"
    s += "RHS\n" + fixed_record("R", "LIMIT", "3") + "ENDATA\n"
    for fmt in ("auto", "fixed"):
        ir = make_v2(s, ".mps", mps_format=fmt)
        assert (
            ir.model.variables[0][2] == "I"
            and ir.model.num(ir.model.variables[0][4]) == 1
        )
        assert ir.model.row(0) == {0: F(2)}


def test_named_vectors_require_selection(make_v2):
    s = MPS.replace("RHS1 LOW 1 HIGH 10", "RHS1 LOW 1 HIGH 10\n RHS2 EQ 7")
    with pytest.raises(LPIRError, match="multiple RHS"):
        make_v2(s, ".mps")
    ir = make_v2(s, ".mps", rhs="RHS2")
    assert ir.model.num(ir.model.rows[0][3]) == 7
    with pytest.raises(LPIRError, match="Unknown RHS"):
        make_v2(MPS, ".mps", rhs="absent")
    s = MPS.replace(" FR BND Y", " FR BND Y\n UP SECOND X 4")
    with pytest.raises(LPIRError, match="multiple BOUNDS"):
        make_v2(s, ".mps")
    assert make_v2(s, ".mps", bounds="BND").verify()["status"] == "PASS"


@pytest.mark.parametrize("section", ["SOS", "QUADOBJ", "INDICATORS"])
def test_mps_rejects_special_sections(make_v2, section):
    with pytest.raises(LPIRError, match="unsupported MPS section"):
        make_v2(MPS.replace("ENDATA", section + "\nENDATA"), ".mps")


def test_mps_semicontinuous_rejected(make_v2):
    with pytest.raises(LPIRError, match="unsupported bound"):
        make_v2(MPS.replace("UI BND X", "SC BND X"), ".mps")


def test_vector_selection_versions_differ(make_v2):
    s = MPS.replace("RHS1 LOW 1 HIGH 10", "RHS1 LOW 1 HIGH 10\n RHS2 EQ 7")
    a = make_v2(s, ".mps", rhs="RHS1")
    b = make_v2(s, ".mps", rhs="RHS2")
    assert a.model.manifest["source_sha256"] == b.model.manifest["source_sha256"]
    assert a.model.manifest["model_version"] != b.model.manifest["model_version"]
