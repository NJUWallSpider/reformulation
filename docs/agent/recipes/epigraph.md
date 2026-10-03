# Inspect one-hot epigraph candidates

## When to use
One-hot groups and coefficient bounds suggest disjunctive strengthening.
Inputs: T is selected original Terms; membership(var,group) is a unique explicit coordinate relation.

## Primitive pattern
Exact coefficient association → grouped observations → stop at the proof boundary.

## Minimal code
```python
Q = T.select("var", "coef").join(membership, on="var", cardinality="m:1", require="left")
parameters = Q.group_by("group").agg(
    amin=mf.col("coef").min(), amax=mf.col("coef").max(),
)
parameters.head()
```

## What it establishes
Exact associated coefficient ranges on the supplied group relation.

## What it does not establish
This does not prove a one-hot convex hull. General continuous disjunction certificates are not implemented; check.cases only discharges a finite model-integer split with matching target proofs.
