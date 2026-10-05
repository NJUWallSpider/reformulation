# Internal release: 1.5.0

This is the 1.5.0 internal release for Python 3.11 or later. It is not intended
for PyPI. Package metadata includes
`Private :: Do Not Upload`. This release does not assign an open-source license.

## Changes in 1.5.0

- Defer Frame-derived linear expressions within `check.batch()`. Share parent
  queries and exact equality indexes, then release query scratch before kernels.
- Replace overlay reference membership scans and per-nonzero point searches
  with validated model ID indexes. Matrix activity validates coverage in the
  same pass as exact multiply-accumulate.
- Share component-local condition sets and schedule common-source tasks together.
  Wide integer residuals retain exact arithmetic without Fraction-tree expansion.
- Replay multi-node v3 graphs directly through the typed engine. Audit receipts
  expose serialization, replay, edit, comparison and readback timings.
- Support complete exact LP point checks through
  `check.solution(..., domain="relaxation")`, including batch registration,
  explicit scope and saved-point replay. Point reports cannot authorize edits.
- Update help and SKILL with deferred query boundaries and batch-first guidance.
- A fresh-process B3-style full workload measured 57.40 seconds, including cold
  indexed discovery, both fractional witnesses, both integer point checks,
  edits, integrity, write/readback and independent audit. The aggregate batch
  took 9.35 seconds compared with 117.87 seconds in the preceding replay.
  These are measured workload results, not universal runtime guarantees.

### Known validation limitations

The complete checkout regression recorded 686 passed, 8 skipped and one existing
failure: `test_high_degree_sparse_source` exceeds the check memory budget at
16 KiB. The original 30-second core performance gate measured 31.25 seconds;
three-process acceptance was not completed after the user accepted the current
performance and requested that further tuning stop. The 60-second total gate
was met in the recorded run, using the B3 caller's 512 MiB finite budget.

This internal release does not claim completion of the entire unified-check
architecture plan. Distribution-specific verification results, including the
known failure, are recorded alongside the artifacts in `dist`.

### Indexed improvements carried forward

- `Model.indexed()` skips single-set partitions during discovery by default.
  Use `include_singletons=True` to retain the complete partition directory.
  The two modes have separate cache identities and survive save/load.
- Component discovery gathers local edges and shares immutable arrays instead
  of repeatedly scanning all members. Full-mode singleton artifacts are packed
  into shared columns with compact metadata.
- Operator recovery builds typed columns in batches and avoids redundant scalar
  and metadata decoding. Independent exact verification is retained.
- Installed help, API documentation and the agent guide describe the new option.

## Install

Use an isolated environment and a wheel supplied by the project:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install /path/to/mipframe-1.5.0-py3-none-any.whl
python -m mipframe help
python -m mipframe help "recover coordinates" --detail full
```

NumPy is the only core dependency. For optional exports, install the same wheel
with extras, for example
`python -m pip install '/path/to/mipframe-1.5.0-py3-none-any.whl[scipy]'`.
The `parquet` extra provides PyArrow. For offline installation, prepare a wheel
folder containing NumPy and any required optional dependencies, then pass
`--no-index --find-links /path/to/wheels` to pip. The mipframe wheel alone does
not bundle dependencies.

## Build and verify

From the repository, or the extracted source distribution:

```sh
python -m pip install '.[test,scipy]' build
python scripts/build_help.py --check
python -m pytest -q -rs
python -m build
python scripts/verify_release.py \
  --wheel dist/mipframe-1.5.0-py3-none-any.whl \
  --sdist dist/mipframe-1.5.0.tar.gz
```

The verifier checks wheel contents and private-release metadata, exercises
Python help and the CLI outside the repository with network access disabled
and a read-only installation, runs tests from an extracted source archive
without research data, and executes six examples against the installed wheel.
It prints artifact sizes and SHA-256 checksums. Build tooling may fetch its own
dependencies unless they are already installed and `--no-isolation` is used.

The source archive includes test helpers, synthetic fixtures, examples and
public documentation. Wheels contain only the library and its help resources.
Research models, papers, personal notebooks, generated experiment results and
local editor settings are not release inputs.

## Optional research regressions

The default suite uses synthetic fixtures. Tests that need research models
explicitly skip if those files are unavailable; a skip is not a passing
verification. To run them, supply a folder containing `test1.lp`, `danoint.lp`
and `assign1-5-8.lp`:

```sh
MIPFRAME_MODEL_DATA_ROOT=/path/to/models python -m pytest -q -rs
```

If unset, this variable defaults to the checkout's local `data/` folder. The
presolved-parser regressions separately accept `MIPFRAME_EXTERNAL_DATA_ROOT`,
pointing to the external research repository containing
`playground/test*/presolved.mps`. Neither data source is bundled.

Larger corpus evaluations remain explicit scripts; full end-to-end execution
on every research model is not a release claim. Presolved parser fidelity and
completion of subsequent IR/indexed analysis are separate checks.

## Compatibility and limits

- Exact rational values are the default; floating-point export is explicit.
- Native storage uses v7. Older native formats must be reimported from LP/MPS.
- Read/load prepare a cheap numerical scan. Structural families, topology and
  indexed analysis run only on explicit access.
- APIs are evolving; use installed `mf.help()` for the matching contracts.
- Candidate searches and structural observations do not establish valid cuts,
  model symmetry or safe edits. Mathematical certificates retain their scope.
- LP/MPS export can reject values or row forms that cannot be represented exactly;
  native storage is the lossless alternative. Model.write supports audited
  canonical output and supported MPS source patches.
- No solver, online Agent evaluation, public upload or automatic update is part
  of this internal distribution.

Before any public release, choose licensing and redistribution terms, package
ownership and supported platforms explicitly. Internal installation does not
resolve those separate decisions.
