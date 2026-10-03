# Compare supports, containment and differences

## When to use
Two unit-coefficient equality supports may imply a zero difference.
Inputs: R is a RowFrame; a and b are selected unit equalities with equal RHS and support(a) strictly inside support(b).

## Primitive pattern
Set containment → difference members → equality subtraction → nonnegative bounds.

## Minimal code
```python
S = R.terms.select(set=mf.col("row"), member=mf.col("var"))
P = mf.ops.sets.pairs(S, sets=R, relation="strict_subset")
D = mf.ops.sets.members(S, pairs=P, op="right_only")
zero = m.check.linear((b.expr - a.expr).eq(0),
                      using=[b.eq_claim, (-1, a.eq_claim)])
zero.require()
```

## What it establishes
Exact support differences and a checked equality difference for the explicitly selected rows.

## What it does not establish
Containment alone does not fix variables. Unit coefficients, equal bounds and nonnegativity must be verified.
