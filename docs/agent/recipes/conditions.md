# Check a conditional contradiction

## When to use
A binary switch value may conflict with original bounds and rows.
Inputs: x is continuous with ub=3; z is binary; r is the original row x-4z>=0.

## Primitive pattern
Assume one value → substitute → exact contradiction certificate.

## Minimal code
```python
conditional = m.check.infeasible(
    using=[r.lb_claim, x.ub_claim], assume=[z.eq(1)],
)
conditional.require()
assert conditional.scope["assumptions"] > 0
```

## What it establishes
No point satisfies the supplied original facts together with z=1.

## What it does not establish
Assumption discharge is not automatic. This Report cannot directly justify edit.fix(z,0).
