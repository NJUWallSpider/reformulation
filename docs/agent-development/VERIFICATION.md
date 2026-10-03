# Help-system verification record

Machine-readable scopes, input SHA-256 values and snapshot identities are in
[verification.json](verification.json). Reproduction commands are in [HELP.md](../HELP.md).

- 377 topics and 339 explicit public bindings; all registered names/signatures and
  generated cross-references checked.
- All 16 installed recipe snippets executed unchanged on independent fixtures.
- Fixed intent set: Recall@3 **29/30 (96.67%)**, exceeding the 95% gate. The recorded
  miss remains visible. High-risk boundary set: **10/10**.
- Last full repository run: **352 passed**. Final help suite after static object
  lookup hardening: **47 passed**. The extra regression prevents help from reading
  an arbitrary object's `__class__` property or `repr`.
- Python 3.11 and 3.13 generated-signature and release-fingerprint checks are
  portable; empty AST fields are normalized across interpreter versions.
- Wheel and sdist build hooks validate content, code and generated docstrings.
  Installation isolation tests run Python, CLI and Skill outside the checkout,
  with socket access disabled, no writable package directory and no user cache.
- The copyable Skill passes the skill format validator; PyYAML was used only in
  a temporary development tool directory, not added to mipframe dependencies.

## MPS parser follow-up

The five load failures below are historical and have been fixed. Auto detection
now validates the complete fixed-width layout per record, preserving genuinely
fixed continuations while reading padded free-format long names and numeric
tokens without truncation. Explicit fixed format rejects out-of-field data.

All five original presolved files passed complete canonical-fact comparison
between auto and explicit free parsing, including every original coefficient,
name, bound, domain and objective. See
[mps-parser-verification.json](mps-parser-verification.json) for their fingerprints,
row/variable counts and nnz. Reproduce with:

```sh
MIPFRAME_EXTERNAL_DATA_ROOT=/path/to/reform_test \
  python -m pytest -q tests/test_mps.py -k external_presolved
```

The full regression suite passed **365 tests**, with **5** opt-in external tests
skipped in the default run; those five were separately enabled and all passed.
An extra default-read trial advanced past parsing into IR/signature preparation
and was interrupted after parser acceptance finished. Full end-to-end structural
checks are not claimed for these five snapshots by this follow-up.

## Original external acceptance (before the parser fix)

| Case | Recorded result | Scope |
| --- | --- | --- |
| 9 | PASS | Ten explicitly named coupling rows; ten components and all crossing rows checked |
| 10 | PASS | 190,000 unique coordinates, 950×200 complete product, both exact coefficient joins and indexed verification |
| 11 | PASS | First 128 original rows, two independent primitive formulations of exact coefficient-vector equality |
| 12 | PASS | c22/c24 containment, exact equality difference, and both difference variables proved <=0 |
| 16 | FAIL during load | `unknown row 1` at line 71795 of the declared presolved file |
| 17 | PASS | Seven explicitly declared original conflict sources, exact half-sum, integer upper bound 3; one candidate cycle |
| 30 | FAIL during load | `unknown row 5` at line 111072 |
| 31 | FAIL during load | `invalid MPS number li1` at line 1631398 |
| 32 | FAIL during load | `unknown row completi` at line 842 |
| 33 | PASS | 145,331 precedence candidates, roots with original IDs 0/1, 1,764 reachability pairs and eight resource-union records |
| 34 | FAIL during load | `unknown row 001` at line 106728 |

The historical load failures above occurred before the original help checks ran.
Their parser regressions now pass as recorded in the follow-up; the original
mathematical scopes are not retroactively marked as proved. They are not counted as mathematical verification or silently replaced
with different snapshots. The case 17 harness uses the declared seven-cycle from
the narrative; an earlier exploratory prefix did not contain the requested small
packing row and is not evidence that the model lacks packing structure.

No online agent experiment, historical solver rerun, model rewrite, general
symmetry certificate, optimality proof or speedup claim was made. These external
inputs are exposed development material, not held-out data.
