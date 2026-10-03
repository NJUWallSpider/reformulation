# Remove transitive rows using retained-path certificates

## When to use
A DAG reduction suggests redundant precedence rows.
Inputs: E(u,v,row) retains original row identity. targets/terms/using are explicit linear_many Frames built from paths in the retained graph; dropped is the matching RowFrame.

## Primitive pattern
Graph reduction → retained path → exact batched certificates → grounded Edit deletion.

## Minimal code
```python
reduced = mf.ops.graph.transitive_reduction(E)
B = m.check.linear_many(targets=targets, terms=terms, using=using)
B.require()
edit = m.edit()
edit.drop_rows(dropped, because=B)
m2 = edit.build()
assert m2.diff(m).semantic["lp"] == "SAME"
```

## What it establishes
The supplied row deletions preserve LP/MIP feasible sets when proof closure succeeds.

## What it does not establish
Reduction alone is not deletion authorization. Paths must use retained facts; parallel source records are not interchangeable identities.
