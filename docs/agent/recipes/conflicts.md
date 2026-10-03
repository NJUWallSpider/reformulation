# Build conflict relations with original witnesses

## When to use
A selected set of source rows has independently established pairwise mutual exclusion.
Inputs: R are verified mutual-exclusion source rows; V is their variable domain. Binary domains and source row semantics are premises.

## Primitive pattern
Transpose source membership → overlap pairs → witness expansion → clique candidates.

## Minimal code
```python
S = m.terms(rows=R, vars=V).select(set=mf.col("var"), member=mf.col("row"))
P = mf.ops.sets.pairs(S, sets=V, relation="overlap")
W = mf.ops.sets.members(S, pairs=P, op="intersection")
E = P.select(u=mf.col("left"), v=mf.col("right"))
C = mf.alg.graph.clique_candidates(E, vertices=V, top=100)
```

## What it establishes
Each edge has shared original sources; C contains graph-theoretic candidates.

## What it does not establish
Neither C nor W is a linear Report. Never pass the graph result as Edit.because.
