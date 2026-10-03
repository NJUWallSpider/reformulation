# Internal release: 0.8.0

This is an internal preview for Python 3.11 or later. It is not a stable 1.0
release and is not intended for PyPI. Package metadata includes
`Private :: Do Not Upload`. This release does not assign an open-source license.

## Install

Use an isolated environment and a wheel supplied by the project:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install /path/to/mipframe-0.8.0-py3-none-any.whl
python -m mipframe help
python -m mipframe help "recover coordinates" --detail full
```

NumPy is the only core dependency. For optional exports, install the same wheel
with extras, for example
`python -m pip install '/path/to/mipframe-0.8.0-py3-none-any.whl[scipy]'`.
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
  --wheel dist/mipframe-0.8.0-py3-none-any.whl \
  --sdist dist/mipframe-0.8.0.tar.gz
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
- Native storage uses v3. Older native formats must be reimported from LP/MPS.
- Read/load prepare default IR and indexed analysis before returning. Large
  models may require substantial time and disk space.
- APIs are evolving; use installed `mf.help()` for the matching contracts.
- Candidate searches and structural observations do not establish valid cuts,
  model symmetry or safe edits. Mathematical certificates retain their scope.
- LP export can reject values or row forms that cannot be represented exactly;
  native storage is the lossless alternative. MPS export is not provided.
- No solver, online Agent evaluation, public upload or automatic update is part
  of this internal distribution.

Before any public release, choose licensing and redistribution terms, package
ownership and supported platforms explicitly. Internal installation does not
resolve those separate decisions.
