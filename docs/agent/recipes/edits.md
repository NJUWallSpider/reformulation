# Build and audit a verified independent snapshot

## When to use
A completed proof justifies an explicit modification.
Inputs: r is a finite upper-bound row; output_dir is a fresh writable directory; the model is exactly LP-representable.

## Primitive pattern
Source proof → Edit addition → Diff → native save → LP audit.

## Minimal code
```python
proof = m.check.linear(r.ub_claim, using=[r.ub_claim])
edit = m.edit()
edit.add_row(r.ub_claim, name="verified_copy", because=proof)
m2 = edit.build()
assert m2.diff(m).semantic["lp"] == "SAME"
m2.save(output_dir / "snapshot.native")
m2.write_lp(output_dir / "candidate.lp")
mf.audit.roundtrip(m2, output_dir / "candidate.lp").require()
```

## What it establishes
The redundant addition preserves the feasible set and the exported LP matches the target snapshot.

## What it does not establish
Self-implication is valid for a redundant addition, not for deleting its own only source. No optimization improvement is established.
