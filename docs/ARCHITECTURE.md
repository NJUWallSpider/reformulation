# LPIR architecture

The immutable numerical model is authoritative. Analysis sidecars publish exact
derived facts; they are not replacement models. Discovery, exact expansion and
presentation have separate responsibilities.

## Module boundaries

| Responsibility | Modules |
|---|---|
| Public facade and CLI | `api`, `cli` |
| Build orchestration | `build` |
| Numerical storage and ingestion | `storage`, `stream_lp`, `parser`, `mps` |
| Cooperative budgets, snapshots and record paging | `budget`, `artifact_io`, `sidecar` |
| Materialized relation handles and dependency checks | `results` |
| Query preparation, relation catalog, dependency resolution, compilation, execution | `query`, `query_catalog`, `query_sources`, `query_sql`, `query_executor` |
| Semantic features and local formulas | `semantic`, `formula`, `compressed` |
| Indexed analysis and its persistence | `indexed`, `indexed_store` |
| Structural orchestration | `structural` |
| Coordinate discovery and candidate compilation | `structural_discovery` |
| Pure coordinate operations | `structural_domain` |
| Affine representation, expansion and family checks | `structural_algebra` |
| Structural persistence | `structural_store` |
| Cross-view artifacts and their checks | `structural_relations` |
| Structural validation and presentation | `structural_validation`, `structural_summary` |
| Cross-layer document composition and Markdown | `document`, `markdown` |

Storage must not import analysis or presentation. Analyses operate on a model
and materialized relations; they must not construct the public `IR` facade.
Physical query sources are derived from relational AST nodes. Literals are data,
including objects that happen to contain `op`, `table`, or `handle` fields.

The `IR` API is retained. Existing helper imports from `structural`,
`structural_relations`, `jobs`, `query`, and the document wrappers in `compressed`
remain available. New implementation code should import the owning module.
Numerical and sidecar schema versions are unchanged. Query executor version 3.5
invalidates older materialized query handles; repeat their AST to regenerate them.

## Construction and budgets

`storage.build_numerical()` performs numerical ingestion only. The application
service `build.build()` constructs required semantic views, then invokes local
formula, indexed-operator and structural stages. It finally writes an entry
document. Failure in a later stage closes the model; after semantic construction,
the numerical manifest and semantic checksums have already been saved.

`IR.build(..., memory_mb=64, analysis_budget={"seconds": 10, "candidates": 5000})`
gives **each resumable stage** its own 10-second, 5000-candidate quantum and a
64-MiB cooperative working-set estimate. `analysis_budget.memory_mb`, when
supplied, overrides the analysis-stage memory estimate. Default analysis limits
are 30 seconds and 1,000,000 candidates per stage. The manifest records the
configured budget and build-time analysis statuses/continuations.

Parsing and semantic classification remain required base construction; this is
not a wall-clock deadline for the entire build. Estimates do not impose a process
RSS limit. Atomic candidates and native calls can overshoot a quantum. Use an
external process deadline when a hard wall-clock cap is required.

If formula compilation exhausts its budget, build still returns the numerical
artifact and writes a clearly marked partial summary. No unverified formula is
published. Resume `analyze("explanations")`; `describe()` remains available only
after the formula sidecar is published. Then resume `operators` and `structure`
as needed. Build-time summary files are snapshots, not live views.

## Checkpoints and publication

- A budget stop preserves a checkpoint and returns `partial` with continuation.
- A regular structural-analysis exception rolls back the current invocation;
  indexed analysis rolls back the current uncommitted work. Previously committed
  checkpoints remain usable.
- SQLite is authoritative for indexed/structural progress. Their JSON manifests
  are atomic export snapshots. Readers and query-cache identity use committed
  database state even if snapshot publication fails after a database commit.
- Formula publication retains its checksum-bearing completion manifest: without
  it, formula records are not a published explanation IR.
- JSON and Markdown snapshots use same-directory temporary files and atomic
  replacement. A failed replacement preserves the previous file.

These rules do not provide a transaction across every file, simultaneous writers,
or whole-build rollback. Published-record pagination and search completeness also
remain distinct: a complete page of facts does not mean discovery is finished.
Shared paging helpers preserve the existing per-analysis response fields.

## Validation

Run `python -m pytest -q` in an environment with `.[test,probe]` installed;
`.[graph]` enables optional graph checks. `tests/test_runtime_contracts.py` covers
literal isolation, rollback, interrupted snapshot publication, stale-cache
avoidance, budget propagation, resume equivalence and import boundaries. Existing
mathematical regressions cover exact expansion, corrupt artifacts, permutation,
scaling, sparse domains and scope continuation.

This refactor keeps numerical algorithms and representations in place. Further
storage or computation optimizations require profiling; line-count reduction is
not a performance claim.


## Relational verification and human previews

`row_relations` owns affine row pairing, streaming exact CSR comparisons and
candidate signatures. Query staging can materialize these operators before SQL
composition; `validate(kind="row_pairs")` calls the same implementation. Each
comparison persists both CSR cursors and bounded difference evidence so a small
budget can make progress inside a large row. All numeric comparisons use exact
rationals. Signatures are candidate filters, not equality certificates.

`query_sql` reads nested paths through one expression evaluator and sorts scalar
values using an exact rational collation. Dot-containing field names retain their
existing top-level semantics. Human `preview` projects existing envelopes, never
reruns analysis. Documents retain complete search state even for zero accepted
families, and report total card counts separately from displayed cards. Cross-block
links describe the accepted family AST; they are not a decomposition certificate.
