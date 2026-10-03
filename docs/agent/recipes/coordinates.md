# Recover Cartesian coordinates

## When to use
Every variable should touch exactly one row in each proposed coordinate domain.
Inputs: V is a VarFrame; A and B are same-snapshot RowFrames with declared full axis domains.

## Primitive pattern
Strict project → product check → coefficient join → group-by.

## Minimal code
```python
P = V.project(i=A, j=B)
P.check("product", ("i", "j")).require()
W = m.terms(rows=A, vars=V).select("var", a=mf.col("coef"))
Q = P.join(W, on="var", cardinality="1:1", require="both")
stats = Q.group_by("i").agg(distinct=mf.col("a").nunique())
```

## What it establishes
Unique and complete coordinates in the declared product, plus exact selected coefficients.

## What it does not establish
Coordinates do not imply equal coefficients, separability, or an optimization reformulation.
