# Compare supports and exact templates

## When to use
Explicit local coordinates make repeated blocks comparable.
Inputs: F has scalar group, slot and exact coef fields; local slot correspondence is already established.

## Primitive pattern
Explicit local payload → grouped multiset equality.

## Minimal code
```python
classes = mf.ops.equal_groups(F, by="group", rows=("slot", "coef"))
classes.head()
```

## What it establishes
Equal complete payload multisets under the supplied coordinates, including multiplicities.

## What it does not establish
No full-model automorphism: bounds, domains, objective and external couplings require separate checks.
