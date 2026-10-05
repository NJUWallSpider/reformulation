# Public API simplification

The unreleased 2.0 runtime changes the native format and numerical execution backend. Re-import original LP/MPS files to replace every pre-v7 native archive; ordinary load accepts native v7 only. Model facts are shared mmap arrays, and structural analysis remains explicit.

| Previous | Current |
| --- | --- |
| `m.variables.by_name("x")` | `m.vars.by_name("x")` |
| `m.constraints[0]` | `m.rows.iloc[0]` |
| `Variable.lower/upper/cost` | `Var.lb/ub/obj` |
| missing bounds as `None` | unbounded `lb/ub` as `-inf/inf` |
| `Constraint.coefficients` | original `row.terms`; explicitly build a dictionary when needed |
| `Frame.collect()` | `Frame.to_records()` |
| entity `by_id(id)` / `at(position)` | `loc[id_or_ref]` / `iloc[position]` |
| entity `neighbors()` | row `.vars` / variable `.rows` |
| `RowFrame.show(formula=True)` | `RowFrame.formula()`; ordinary display uses `inspect()` |
| `options.numeric.rtol/atol` | removed: they never affected execution |
| `project(exactly_one=True, ...)` | `project(...)`, always strict |
| `pattern_counts(column="family")` | `pattern_counts()` |
| `check.batch("linear", ...)` | `check.linear_batch(...)` |
| checker `arithmetic="exact"` | removed: exact arithmetic is the contract |
| `m.A` | `m.matrix.view().to_scipy()` |
| `ValidationReport` | `Report` |
| `m.verify()["status"]` | `m.verify().status` (`PROVED/REFUTED/INCONCLUSIVE`) |
| verification summary dictionary | `report.summary`; complete diagnostics in evidence/violations |
| `IndexedTensor` / `IndexedOperator` | ordinary one-row Frames from `I.tensor(id)` / `I.operator(id)` |
| `O["rows"]` | `O.one().row_count`; `O.rows` is always the member RowFrame |
| `T.variables` | `T.one().var_count` |
| `T.shape` / `O.kernel_shape` | `related("dimensions")`, filtered by `component` |
| `T.axes[0]` | `T.related("axes").where(axis=0).rows` |
| `T.coordinates` | `T.related("coordinates")` |
| `O.kernel/bindings/alignment` | `O.related("kernel"/"bindings"/"alignment")` |
| `O.ast/kernel_statistics` | `O.related("ast"/"statistics")` |
| `O.formula()` | `O.one().formula` |
| `O.verify()` / `T.verify()` | `I.verify(O)` / `I.verify(T)` |
| `inspect(max_families=...)` | `inspect(max_items=...)` |

Entities and entity Frames both include nullable `source_line`. Positive values are one-based source-file lines; constructed entities have `None`. Explicit records are independent, while all live entity attributes require an open model.

See [family navigation](FAMILIES.md) and [Indexed Frames](INDEXED_IR.md). Keep `read/load`, `ops/alg/check`, `linear_batch/linear_many` and explicit expression methods: these have distinct responsibilities.

## Initial IR schema 0.4

`read/load/Model(path)` now prepare only a cheap scan. Native v7 is the only read/write format. No v5/v6 migration reader is provided; regenerate caches and proofs after re-import.
`m.ir.structure`, `m.ir.topology`, `m.ir.indexed` and objective `role_support` retain
lookup compatibility and compute lazily. Corrupt saved analysis caches fail at
explicit analysis access, while validated numerical data and scan remain usable.

`m.ir.to_dict()` / `to_json()` and Mapping iteration now contain only scan fields;
use `include_analysis=True` to request full analysis export. Basic bounds use
fixed/free/lower_only/upper_only/boxed; `[0,1]` has its own `unit_interval` count.
High-cardinality top-K values can be estimates with error bounds. Numeric quantiles
are null with `quantiles_exact=False` when their exact histogram exceeds budget.

Use `m.ir.row_shapes()` and `m.ir.name_templates(kind="row"|"var")` for complete
exact navigable directories. Keep `group_key` when projecting; `.rows` / `.vars`
and normalized `coefficients` / `slots` relations follow the selection.
`Edit.build(prepare="indexed")` remains an explicit request for eager preparation.

The previous `max_var_roles`, `max_row_families`, `max_attention` and `max_splits`
options remain assignable for compatibility but do not control the initial scan.
Use `max_local_shapes`, `max_name_templates`, `max_histogram`, `max_values` and
`max_chars` for the new display.

## Array runtime and native v7

- `Frame.explain()` reports input and selection storage, typed operators, validation barriers and mmap spill.
- `CertificateBatch`, `Targets` and `BatchProof` replace mathematical Report archives. Use `proof.for_target(key)` for a scalar view; call replay/audit before trusting saved evidence.
- Frame certificates compile once into ragged arrays. `linear_batch` retains compact staged columns. Numerical kernels do not execute SQL.
- Native v7 bundles a shared base plus deltas and dependencies. Saving a child does not flatten its matrix, and closing its parent does not invalidate the child.
- Diagnostics retain ordinary Report/evidence Frames; mathematical batch statuses and target IDs are compact proof fields. Internal `_store`, `_verified_edit`, SQLite table layouts and v1 numerical event recipes are not compatibility APIs.
- Export independently checks numerical facts before publication. MPS rejects exact rationals with no finite decimal representation.

See [runtime ownership and execution](ARCHITECTURE.md) and [the edit guide](EDIT.md).


## Fixed substitution, serialization and graph certificates

| Previous call | Current call |
|---|---|
| `edit.eliminate_fixed(V)` | `edit.substitute_fixed(V)` |
| `model.export(path)` | `model.write(path)` |
| `model.write_mps(path)` | `model.write(path, format="mps")` |

The old public names are removed. `substitute_fixed()` without a selector freezes
all currently fixed source variables, including staged bounds. It compiles exact
row shifts once and exposes an extension map through `child.lift(solution)`.
`because` supplies evidence for bounds; it never supplies an inferred fixed value.
New rows using substituted variables require an intermediate snapshot.

`edit.patch_plan()` does not seal the edit. `write(strategy="patch")` requires
source patch support; `auto` chooses it whenever supported and otherwise uses
canonical output. Both strategies independently read back numerical output by
default. Failed writes leave the Edit retryable and never publish a partial file.
An existing destination is never overwritten. `audit=None` is explicitly unverified.
MPS source bytes/index are retained by default; `mf.read(..., preserve_source=False)`
opts out of retaining source bytes. Native v7 retains whatever source artifacts
were captured. Pre-v7 inputs must be re-imported. Unknown native features fail explicitly.

`ops.graph.transitive_reduction(..., witnesses=False)` still returns a Frame.
With `witnesses=True`, it returns `TransitiveReductionResult` with `kept`, `removed`,
`witnesses` and `stats`. Paths contain only retained records. Parallel records for
a necessary endpoint pair are all retained. `witnesses.as_linear_certificate()`
produces compact arrays accepted directly by `m.check.linear_many(cert)`; only the
exact checker grants proof authority. Full witness size defaults to at most ten
million path edges and can be explicitly uncapped with `max_witness_edges=None`.


## Working and materialization budgets in 2.0

`Frame.from_records`, `RecordBatch`, Frame execution, compact certificate builders,
algorithm calls and read/write entry points accept keyword-only `memory_mb` and
`disk_mb`. Working memory defaults to 128 MiB; temporary disk is unlimited unless
specified. Typed buffers spill to mmap. The minimum generic workspace is 16 KiB;
LP/MPS import retains its 32 MiB minimum. An oversized individual scalar or record
raises a resource error rather than using SQL.

`to_records(materialize_mb=...)`, `Frame.to_spec(materialize_mb=...)` and
`BatchProof.to_spec(materialize_mb=...)` separately limit explicit Python output
(default 128 MiB). `memory_mb` no longer doubles as the output limit. Resource
settings do not appear in mathematical certificate content. `iter_batches` bounds
each batch by `batch_size` and shares a working ledger across the iterator and
retained batch leases. Retaining many batches can consequently cause spill.

`BatchProof.write(path)` and `PointReport.write(path)` write directories containing
versioned typed artifact bundles, not a single JSON file. `Report.replay(path,
models=[model])` validates those artifacts and reruns verification. An explicitly
materialized point specification remains readable. Loading a native archive never
grants historical proofs authority to authorize edits. Derived caches of loaded
archives are built in a separate temporary directory.

There is no numerical SQL fallback, SQLIndex, SQL AlgorithmStore or duplicate
NumPy relation executor. Low-memory input follows the same typed semantics.
Private imports from the removed stores or `data` adapter modules must be removed;
public `Frame`, `ops`, `alg` and `check` entry points remain the integration surface.

**Release gate:** the broad resource contract is still under remediation. Advanced
search, indexed discovery, proof graph metadata and some edit construction retain
unbudgeted containers; runtime performance also has unresolved regressions. Do not
interpret the 2.0 version field as a completed release. See
[the remediation status](ARCHITECTURE_REMEDIATION_20261005.md) for verification and
remaining work.
