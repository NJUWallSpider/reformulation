from pathlib import Path
import pytest
from lpir import IR

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def test1(tmp_path_factory):
    with IR.build(
        ROOT / "data/test1.lp", tmp_path_factory.mktemp("test1") / "ir"
    ) as ir:
        yield ir


@pytest.fixture
def make_ir(tmp_path):
    opened = []
    count = 0

    def make(text, suffix=".lp", **options):
        nonlocal count
        count += 1
        p = tmp_path / f"m{count}{suffix}"
        p.write_text(text)
        ir = IR.build(p, tmp_path / f"ir{count}", **options)
        opened.append(ir)
        return ir

    yield make
    for ir in opened:
        ir.close()


@pytest.fixture
def make_v2(make_ir):
    return make_ir
