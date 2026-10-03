"""Run with: python examples/basic_read.py (after installing mipframe)."""

from pathlib import Path
import mipframe as mf


def main():
    source = Path(__file__).parent / "data" / "tiny.lp"
    with mf.read(source) as model:
        print(model)
        print(model.rows.head())
        print(model.vars.head())
        print("Objective:", model.objective.sense, model.objective.constant)
        for variable in model.vars:
            print(variable.name, variable.kind, variable.lb, variable.ub)
        row = model.rows.by_name("demand")
        print(row.name, row.lb, row.ub, row.terms.to_records())
        print("Exact coefficient:", model.vars.iloc[0].obj)
        print("Verification:", model.verify()["status"])


if __name__ == "__main__":
    main()
