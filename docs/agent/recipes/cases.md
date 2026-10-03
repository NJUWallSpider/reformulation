# Discharge binary cases and replace a big-M row

## When to use
Continuous activity and a binary switch need different certificates in each branch.
Inputs: x is continuous and nonnegative, y is binary; cap is x<=6; old is x+1197y<=1200.

## Primitive pattern
Prove each branch → exhaust the integer domain → add and build → prove and delete the old row.

## Minimal code
```python
target = x + 3*y <= 6
p0 = m.check.linear(target, using=[cap.ub_claim], assume=[y.eq(0)], residual="bounds")
p1 = m.check.linear(target, using=[old.ub_claim], assume=[y.eq(1)], residual="bounds")
proof = m.check.cases(target, var=y, cases={0:p0, 1:p1})
proof.require()
assert proof.scope["assumptions"] == 0
with m.edit() as edit:
    edit.add_row(target, name="tight", because=proof)
    middle = edit.build()
try:
    assert middle.diff(m).semantic["mip"] == "SAME"
    old2 = middle.rows.by_name("old")
    y2 = middle.vars.by_name("y")
    reverse = middle.check.linear(old2.ub_claim,
        using=[middle.rows.by_name("tight").ub_claim, (1194, y2.ub_claim)])
    with middle.edit() as edit:
        edit.drop_rows(old2, because=reverse)
        m2 = edit.build()
    assert m2.diff(middle).semantic["lp"] == "SAME"
    assert m2.diff(middle).semantic["mip"] == "SAME"
finally:
    middle.close()
```

## What it establishes
Each direct-parent Diff certifies MIP SAME and optimal_value PRESERVED. Addition is LP SUBSET; deletion is LP SAME.

## What it does not establish
SUBSET does not establish strictness. No cross-generation Diff, optimal value computation, continuous disjunction or infeasible-branch elimination.
