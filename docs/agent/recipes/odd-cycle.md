# Verify an odd-cycle inequality by exact rounding

## When to use
Verified pair bounds around an odd cycle can be added and rounded.
Inputs: V is exactly the 2k+1 binary cycle vertices; edges is a small list of PROVED unconditional Reports xi+xj<=1, one per cycle edge.

## Primitive pattern
Half-sum edge certificates → exact linear bound → integer rounding.

## Minimal code
```python
n = len(V)
assert n >= 3 and n % 2 == 1
s = m.linexpr(vars=V, coef=1)
upper = m.check.linear(s <= mf.q(n) / 2,
                       using=[(mf.q("1/2"), edge) for edge in edges])
upper.require()
cut = m.check.round(s <= n // 2, from_=upper)
cut.require()
```

## What it establishes
An integer-valid cycle inequality with explicit linear and domain dependencies.

## What it does not establish
It is not LP-valid. Candidate cycle search does not supply edge proofs by itself.
