# Find blocks after removing coupling rows

## When to use
A small, explicitly chosen set of rows may couple otherwise separate blocks.
Inputs: m is open; coupling is a same-snapshot RowFrame. Its selection is a hypothesis, not inferred here.

## Primitive pattern
Row set difference → hypergraph components → crossing-row verification.

## Minimal code
```python
local = m.rows - coupling
S = m.terms(rows=local).select(set=mf.col("row"), member=mf.col("var"))
C = mf.ops.sets.components(S, vertices=m.vars)
joined = m.terms().join(C, on={"var": "member"}, cardinality="m:1")
crossing = joined.group_by("row").agg(n=mf.col("component").nunique())
crossing = crossing.where(mf.col("n") > 1)
```

## What it establishes
Components in the selected hypergraph, including isolates, and the rows crossing them.

## What it does not establish
The full model need not decompose: excluded constraints and objective still matter.
