# Search and verify a finite local integer claim

## When to use
A small explicitly bounded pure-integer subsystem is suitable for exhaustive verification.
Inputs: V is the integer selection; R is the local row selection; target contains only V; all relevant premises are explicit.

## Primitive pattern
Explicit integer system → assignments; local target → check.enumerate.

## Minimal code
```python
assignments = mf.alg.integer.enumerate(
    V.select("var", "lb", "ub"), R.terms, R.select("row", "lb", "ub"),
    max_states=100_000,
)
proof = m.check.enumerate(target, vars=V, using=premises, max_states=100_000)
proof.require()
```

## What it establishes
A completed checker proves only the declared local implication and retains all its premises.

## What it does not establish
A local counterexample may not extend to a feasible MIP point. Budget exhaustion is not proof.
