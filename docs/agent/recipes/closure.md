# Aggregate resource use of closure unions

## When to use
Directed implications and nonnegative resource data motivate a pair compatibility test.
Inputs: E(u,v), pairs(left,right), values(member,resource,coef), capacities(resource,capacity) are exact Frames; capacity keys are unique.

## Primitive pattern
Reachability including self → set membership → union aggregation → exact comparison.

## Minimal code
```python
closure = mf.ops.graph.reachability(E, include_self=True)
C = closure.select(set=mf.col("source"), member=mf.col("target"))
usage = mf.ops.sets.pair_aggregate(
    C, pairs, values, by="resource", value="coef", op="union")
conflicts = usage.join(capacities, on="resource", cardinality="m:1", require="left")
conflicts = conflicts.where(mf.col("value") > mf.col("capacity"))
```

## What it establishes
Exact union resource totals with shared members counted once.

## What it does not establish
A graph implication must be proved from original rows; resource excess alone does not produce a reusable MIP cut certificate.
