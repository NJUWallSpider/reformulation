# LPIR

LPIR turns linear LP/MPS models into exact numerical data, shared variable
coordinates, parameterized constraint families and queryable structural evidence.
It retains original coefficients and row provenance. It does not rewrite the
model, infer application semantics or claim symmetry from matching fingerprints.

## Quick start

```sh
python -m pip install -e '.[test]'
lpir build data/test1.lp --out /tmp/test1-ir
lpir render /tmp/test1-ir
lpir verify /tmp/test1-ir
```

`build` writes `summary.md` and `summary.json` inside the output directory.
Give an LLM `summary.md` as its initial model context and access to LPIR's
`inspect`/`query`/`validate` tools for details. `summary.json` is the same
structured document used to render that Markdown, suitable for programmatic
consumers. Neither snapshot replaces the complete numerical and relational IR.
The default Markdown budget is 20 KB; pagination and incomplete analysis remain
explicit. After additional analysis, use `describe()`/`render()` for current
views; the files written by `build` are build-time snapshots.

```python
from lpir import IR

with IR("evaluation/test1/ir") as ir:
    print(ir.render())
    families = ir.inspect("structure_families", limit=20)
    result = ir.query({"table": "structure_objective"}, limit=10)
    checked = ir.verify()
```

The repository keeps one evaluated set in [`evaluation`](evaluation/report.md).
[`evaluation/SUMMARIES.md`](evaluation/SUMMARIES.md) links all eight current summaries.
`data` contains only the eight original `.lp` files. Each `evaluation/<case>`
contains `ir/`, `summary.md`, `summary.json` and `report.json`.
Public benchmark inputs remain in `benchmarks/public`; reference literature is
in `literature`.

## Current interfaces

- `IR.build(source, output, **options)` creates a new numerical IR and attempts
  bounded formula, indexed-coordinate and structural analysis.
- `overview()`, `describe()` and `render()` return model statistics, machine data
  and human Markdown. Markdown is a reading view, not a machine serialization.
- `inspect(kind, key=None, offset=0, limit=20)` reads rows, variables, formula
  families, indexed diagnostics and `structure_*` relations.
- `query(spec, ...)` executes a restricted JSON relational AST. `query(handle=...)`
  pages a materialized result. `neighbors` and `external` provide incidence queries.
- `analyze(kind, scope=None, budget=None, handle=None, ...)` performs resumable
  discovery. `validate(spec, budget=None)` checks a hypothesis; `verify()` checks
  numerical files and published structural views.
- `export(path, **options)` writes the exact numerical model as LP.
- `probe(seconds=30, memory_mb=256)` runs the optional continuous LP relaxation;
  its results are numerical, not exact proofs.

Only numerical schema 2 and structural sidecar 2.0 are supported. Old readers,
string-expression queries, `template`/`parameter` aliases and migration commands
have been removed. Rebuild obsolete artifacts from their source LP/MPS.

```sh
lpir query evaluation/test1/ir --spec '{"table":"structure_families"}' --limit 2
lpir analyze evaluation/test1/ir structure --seconds 30 --memory-mb 128
lpir inspect evaluation/test1/ir structure_objective --limit 5
```

Use `--raw-ids` to display internal identities; readable references are local to
an artifact. Scope, pagination and continuation retain the current query contract.

## Structural representation

The primary view contains:

- Base variable entities with arbitrary-rank coordinates and exact sparse domains.
- Coordinate views and explicit index mappings, keeping aliases separate from entities.
- Constraint families with algebraic skeletons, row domains, restricted sums,
  coefficient tensors and original-row mappings.
- Objective coefficient tiles, verified axis invariance, and axis subsets with
  explicit evidence. Equal objective profiles do not prove whole-model symmetry.

All published families expand exactly to numerical rows, including coefficients,
bounds and normalization scales. Missing cells are never silently filled with
zero. Uncovered rows retain their numerical representation. Long rationals may be
abbreviated with `≈` in Markdown; exact values remain queryable.

The local formula compiler and indexed-coordinate/operator modules remain active
building blocks for structural discovery. Their diagnostic relations are not old
API implementations.

See [structural representation and validation](docs/STRUCTURAL_IR.md),
[indexed coordinates](docs/INDEXED_IR.md) and
[relational query recipes](docs/EVIDENCE_QUERIES.md).

## Exact queries and validation

The query AST supports scans, filters, projections, joins, grouping, set
operations, exact arithmetic, selected terms and column incidence. Use rational
strings for exact decimal constants. Arbitrary Python/SQL expressions are rejected.

Validation includes formulas, structural entities/families/maps/subsets/objective
tiles, support relations, supplied coordinate mappings, cliques and decomposition
hypotheses. A failed check returns a counterexample; an exhausted budget returns
an incomplete result rather than a proof.

Budget fields include `seconds`, `memory_mb` and `candidates`. Most internal
budgets are cooperative working-set estimates; native solver/graph calls and an
atomic candidate can overshoot a time quantum. The evaluation runner enforces
an independent subprocess deadline per model. Partial discovery is not evidence
that a structure does not exist.

## Input, storage and export

LP parsing streams bounded chunks and reports unsupported syntax with locations.
Supported models are linear, with continuous, integer and binary variables,
finite or infinite bounds, equalities, inequalities and ranged rows. Nonlinear,
SOS, indicator and semicontinuous features are rejected.
HiGHS LP output is accepted, including objective/left-hand-side wraps between
a coefficient and its variable and an empty `semi` section. A nonempty `semi`
section still raises an unsupported-domain error. Completed terms require
explicit `+` or `-`; a scalar right-hand side ends at the physical newline.

MPS supports fixed/free records, integer markers, RHS/RANGES/BOUNDS sets and
objective offsets. Multiple vector sets require explicit selection. INTORG
columns default to `[0,1]` unless overridden by BOUNDS. LP export requires names
compatible with its grammar and rejects completely free rows it cannot represent;
explicit rename maps are supported.

`metadata.sqlite` and CSR/CSC arrays form the immutable, checksummed numerical
store. Analysis, local formulas, indexed coordinates and structure sidecars are
separate. Description-length metrics measure canonical algebraic descriptions,
not SQLite disk size. Raw identity audit mappings are excluded; coordinate maps,
views, subsets and objective representations are counted.

## Tests and evaluation

```sh
pytest -q
PYTHONPATH=src python scripts/evaluate_structure.py \
  --out /tmp/lpir-evaluation --seconds 300 --jobs 2
PYTHONPATH=src python scripts/evaluate_public.py --out /tmp/lpir-public
```

The structural runner builds danoint, assign1-5-8 and test1–test6 independently,
including parsing, summaries and verification within each five-minute cap.
It never overwrites an existing output directory. The public benchmark runner
checks small MPS models and optional independent SCIP comparisons.

Tests cover exact parsing, corruption rejection, numerical round trips, random
renaming/permutation/scaling, masked domains, parameter perturbations, paging,
scopes and continuation. [The offline AI evaluation protocol](docs/AI_EVALUATION.md)
describes the limits of claims based on deterministic regression checks.

Optional dependencies: `.[probe]` supplies SciPy and `.[graph]` supplies igraph.
`register_analysis(name, version, discover)` supports trusted Python extensions;
CLI JSON cannot install or execute plugins.
