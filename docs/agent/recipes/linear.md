# Combine rows and compensate exact residuals

## When to use
A supplied source combination differs from a target only by bound-controlled coefficients.
Inputs: x,y are variables; r is a source row x+y<=b with y>=0; target_rhs is a candidate bound on x.

## Primitive pattern
Explicit source side → linear residual → sign-selected original variable bounds.

## Minimal code
```python
target = x <= target_rhs
proof = m.check.linear(target, using=[r.ub_claim], residual="bounds")
proof.require()
proof.evidence
with m.check.linear_batch(residual="bounds") as batch:
    batch.add(key="bound", target=target, using=[r.ub_claim])
    batch.add(key="conditional", target=x <= 0, assume=[x.eq(0)])
    proofs = batch.run()
proofs.require()
assert proofs.for_target("conditional").scope["assumptions"] == 1
```

## What it establishes
The exact supplied certificate implies the target within its recorded domain.

## What it does not establish
No search for multipliers; failure is INCONCLUSIVE, not proof that the target is false.
