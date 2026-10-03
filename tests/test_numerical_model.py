"""Numerical fidelity and round trips through the current Model only."""

from fractions import Fraction as F
import random

from reference_test1 import read as independent_read
from external_models import model_path

import mipframe as mf



def canonical(m):
    variables = {
        v[1]: (v[2], m.num(v[3]), m.num(v[4]), m.num(v[5])) for v in m.variables
    }
    rows = {
        r[1]: (
            {m.variables[v][1]: c for v, c in m.iter_terms(r[0])},
            m.num(r[2]),
            m.num(r[3]),
        )
        for r in m.rows
    }
    return variables, rows, m.manifest["sense"], m.num(m.manifest["objective_constant"])


def test_test1_against_independent_parser(test1):
    m = test1.model
    o, rs, b, bins = independent_read(model_path("test1.lp"))
    assert (len(m.variables), len(m.rows), len(m.col_idx)) == (11090, 4630, 41550)
    assert sum(bool(m.num(v[5])) for v in m.variables) == 7705
    for v in m.variables:
        old = int(v[1][1:])
        assert (m.num(v[3]), m.num(v[4])) == b[old]
        assert m.num(v[5]) == o.get(old, F(0))
        assert (m.variable_type(v) == "B") == (old in bins)
    for row in rs:
        rid = m.row_names[row.name]
        assert {int(m.variables[v][1][1:]): c for v, c in m.iter_terms(rid)} == row.a
        assert m.num(m.rows[rid][2]) == (row.rhs if row.sense != "<=" else None)
        assert m.num(m.rows[rid][3]) == (row.rhs if row.sense != ">=" else None)
    assert test1.verify()["rows_verified"] == 4630


def test_renaming_permutation_and_index_mapping(test1, tmp_path):
    m = test1.model
    rng = random.Random(41)
    ids = list(range(len(m.variables)))
    rng.shuffle(ids)
    vn = {v[0]: f"v_{ids[v[0]]}" for v in m.variables}
    ids = list(range(len(m.rows)))
    rng.shuffle(ids)
    rn = {r[0]: f"row_{ids[r[0]]}" for r in m.rows}
    order = list(range(len(m.rows)))
    rng.shuffle(order)
    path = tmp_path / "shuffled.lp"
    test1.write_lp(
        path, rename_vars=vn, rename_rows=rn, row_order=order, reverse_terms=True
    )
    with mf.read(path) as other:
        n = other.model
        assert other.verify()["rows_verified"] == 4630
        for r in m.rows:
            rid = n.row_names[rn[r[0]]]
            assert dict(n.iter_terms(rid)) == {
                n.var_names[vn[v]]: c for v, c in m.iter_terms(r[0])
            }
