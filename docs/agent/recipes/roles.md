# Inspect structural roles and raw coefficients

## When to use
A signature suggests repeated rows, but coefficients may vary.
Inputs: m is open; R is an explicitly selected RowFrame.

## Primitive pattern
IR / signature → selected Terms → exact grouped coefficient statistics.

## Minimal code
```python
ir = m.ir
T = m.terms(rows=R)
G = T.group_by("row").agg(
    distinct=mf.col("coef").nunique(),
    amin=mf.col("coef").min(), amax=mf.col("coef").max(),
)
G.head()
```

## What it establishes
Actual observed coefficient statistics of the selected rows.

## What it does not establish
A family label is not an original formula, and observations do not prove a semantic role.
