from pathlib import Path
import pytest
from lpir import IR

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='session')
def test1(tmp_path_factory):
    return IR.build(ROOT/'test1/test1.lp',tmp_path_factory.mktemp('test1')/'ir')
@pytest.fixture
def make_ir(tmp_path):
    counter=[0]
    def make(text):
        counter[0]+=1;p=tmp_path/f'm{counter[0]}.lp';p.write_text(text)
        return IR.build(p,tmp_path/f'ir{counter[0]}')
    return make
