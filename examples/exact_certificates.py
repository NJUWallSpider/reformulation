"""Exact bounds, conditions, local witnesses and certificate replay."""

from pathlib import Path
from tempfile import TemporaryDirectory
import mipframe as mf


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "example.lp"
        path.write_text("""Minimize
 obj: x + y
Subject To
 lower: x >= 1.1
 equality: x - y = 0
 gate: x - 4 z >= -2
 cap: x + y <= 4
Bounds
 0 <= x <= 4
 0 <= y <= 4
 0 <= z <= 1
Generals
 x y
Binaries
 z
End
""")
        with mf.read(path) as m:
            x, y, z = (m.vars.by_name(name) for name in ("x", "y", "z"))
            e = 2 * x - y + mf.q("0.1")
            print(e.formula())
            m.check.identity(e.substitute({y: x}), x + mf.q("0.1")).require()
            lower = m.check.linear(
                x >= mf.q("1.1"), using=[m.rows.by_name("lower").lb_claim]
            ).require()
            rounded = m.check.round(x >= 2, from_=lower).require()
            conditional = m.check.linear(
                x >= 2, using=[m.rows.by_name("gate").lb_claim], assume=[z.eq(1)]
            ).require()
            impossible = m.check.infeasible(
                using=[conditional, x <= 1], assume=[x <= 1], residual="bounds"
            ).require()
            print(impossible.explain())
            output = Path(directory) / "rounding.jsonl"
            rounded.write(output)
            mf.Report.replay(output, models=[m], memory_mb=0.015625).require()
            m.check.solution({x: 2, y: 2, z: 0}).require()
            # The local point x=0 refutes this implication but violates the model's lower row.
            local = m.check.enumerate(x >= 1, vars=[x])
            assert local.status == "REFUTED"
            assert local.counterexample.one().value == 0
            print(local)
            m.check.enumerate(
                x + y <= 4, vars=[x, y], using=[m.rows.by_name("cap").ub_claim]
            ).require()


if __name__ == "__main__":
    main()
