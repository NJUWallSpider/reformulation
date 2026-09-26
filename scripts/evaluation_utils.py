"""Exact numerical comparison for evaluation scripts."""

from lpir import LPIRError


def require(ok, message):
    if not ok:
        raise LPIRError(message)


def equal_models(a, b, rename_vars=None, rename_rows=None):
    require(a.manifest["sense"] == b.manifest["sense"], "sense differs")
    require(
        a.num(a.manifest["objective_constant"])
        == b.num(b.manifest["objective_constant"]),
        "offset differs",
    )
    require(
        len(a.variables) == len(b.variables) and len(a.rows) == len(b.rows),
        "sizes differ",
    )
    for v in a.variables:
        w = b.variables[b.var_names[rename_vars[v[0]] if rename_vars else v[1]]]
        require(
            (v[2], a.num(v[3]), a.num(v[4]), a.num(v[5]))
            == (w[2], b.num(w[3]), b.num(w[4]), b.num(w[5])),
            f"variable mismatch {v[1]}",
        )
    for r in a.rows:
        s = b.row_names[rename_rows[r[0]] if rename_rows else r[1]]
        require(
            {
                rename_vars[v] if rename_vars else a.variables[v][1]: c
                for v, c in a.iter_terms(r[0])
            }
            == {b.variables[v][1]: c for v, c in b.iter_terms(s)},
            f"row mismatch {r[1]}",
        )
        require(
            (a.num(r[2]), a.num(r[3])) == (b.num(b.rows[s][2]), b.num(b.rows[s][3])),
            "bounds differ",
        )
