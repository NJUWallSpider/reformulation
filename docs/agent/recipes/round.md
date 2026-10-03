# Verify an exact lower bound and round it

## When to use
A rational linear certificate yields a lower bound on an integer expression.
Inputs: x is a model integer variable; sources explicitly imply x>=43/39.

## Primitive pattern
Exact linear certificate with residual bounds → ceil integer lower bound.

## Minimal code
```python
bound = m.check.linear(x >= mf.q("43/39"), using=sources, residual="bounds")
bound.require()
integer_bound = m.check.round(x >= 2, from_=bound)
integer_bound.require()
```

## What it establishes
An integer-valid bound derived from checked exact sources.

## What it does not establish
A floating solver objective cannot replace sources; the rounded bound is not generally relaxation-valid.
