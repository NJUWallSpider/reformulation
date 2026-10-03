# Recover directed incidence from signed terms

## When to use
Each selected variable should have exactly one +1 and one -1 in the chosen row submatrix.
Inputs: R and V declare the full row and variable selections.

## Primitive pattern
Sign filtering → strict endpoint join → complete variable coverage → graph operations.

## Minimal code
```python
T = m.terms(rows=R, vars=V)
assert T.where((mf.col("coef") != 1) & (mf.col("coef") != -1)).count() == 0
tail = T.where(coef=1).select("var", u=mf.col("row"))
head = T.where(coef=-1).select("var", v=mf.col("row"))
E = tail.join(head, on="var", cardinality="1:1", require="both")
assert len(E) == len(V)
C = mf.ops.graph.components(E, vertices=R)
order = mf.ops.graph.topological_order(E, tail="u", head="v", vertices=R)
```

## What it establishes
Complete signed incidence and DAG/component facts for the chosen submatrix.

## What it does not establish
This alone does not prove a unit-flow network, integrality, or independence from other rows.
