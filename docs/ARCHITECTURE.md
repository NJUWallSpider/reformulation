# Runtime architecture

The snapshot's immutable typed arrays own numerical facts. SQLite records identities,
exact-number dictionaries, names, provenance and completed cache manifests. Model,
Frame, checker and editing views borrow that snapshot; they do not each construct
a numerical model.

```text
Model / Frame / check / edit
             |
      logical operations
             |
      array planner / exact kernels
             |
 SnapshotData: mmap dense arrays + CSR + CSC
             |
 control catalogs: identities, provenance, artifact manifests
```

**Status:** this describes the implemented storage direction, not a declaration
that every resource and performance acceptance criterion has passed. See the
[current remediation status](ARCHITECTURE_REMEDIATION_20261005.md).

## Modules and ownership

| Module | Responsibility |
| --- | --- |
| `data/snapshot.py` | Shared mmap lease, metadata projections, packed UTF-8 names |
| `data/sparse.py` | CSR/CSC adjacency and borrowed exact slices |
| `data/numbers.py` | int64 and rational caches, lazy mmap big-integer text |
| `data/builder.py` | LP/MPS array construction and exact sorted term runs |
| `data/runtime.py` | Array workspace allocation, mmap spill and cleanup |
| `data/columns.py`, `ids.py`, `overrides.py` | Compact certificate columns, selections and delta overrides |
| `data/exact_sparse.py` | Exact accumulation and external merge of sparse expressions |
| `data/csr_graph.py`, `graph_kernels.py`, `sorting.py` | Schema-independent graph and ordering kernels |
| `execution/`, `alg/_graph.py` | Frame/Report adapters and algorithm orchestration |
| `control/catalog.py`, `proofs.py` | Persistent catalogs and portable proof/delta manifests |
| `frame/planner.py`, `frame/executor.py` | One typed relation executor, streaming scans, bounded ordering and complete validation |
| `check/kernel.py`, `compiler.py`, `compact.py` | Shared facts, certificate compilation, exact verification and lazy proof views |
| `edit/delta.py`, `store/overlay.py` | Certified operations and a logical sparse overlay |
| `ir/refinement_store.py`, `ir/indexing/` | Explicit structural analysis and versioned array artifacts |
| `store/native7.py`, `mps_output.py` | Portable native snapshots, stream export and independent readback |

`SnapshotData.acquire/release` keeps mapped numerical data and its owned temporary
directory alive while a child snapshot or proof needs it. Closing a parent Model
does not invalidate an independently live child. A public view of a closed Model
still fails its guard before resuming a generator or reading mapped data.
Algorithm results own completed array artifacts separately from algorithm scratch.
Discarding the last result releases those artifacts.

Row/variable IDs are stable identities, not positions in a live selection. `.loc`
uses identity; `.iloc` uses position. Snapshot-qualified references validate uid,
version and entity axis. Names, Rows, Vars and Fractions are constructed at API or
presentation boundaries. Scalar exact slices do not populate an unbounded row cache.

## Numeric representation

Dense arrays hold bounds, objective IDs, variable domains, source lines and finite
bound flags. CSR contains variable IDs and value IDs; CSC contains row IDs and value
IDs directly. Offsets are uint64. Entity and number arrays use uint32 where their
range permits and widen to uint64. Checked conversions reject invalid IDs.

The exact dictionary has int64 numerators, denominators, flags and approximate
values. Rare big integers have packed mmap text and are decoded on demand. SQLite
retains the persistent exact dictionary; arithmetic does not query it. Approximate
values never establish proof or equality. Unbounded integer calculations use Python
integers, and rational fallback uses Fraction. Fixed-width vector operations are
used only where their range is safe.

## Reading and native v7

Both readers use one array builder. Coefficients enter typed bounded buffers;
repeated entries coalesce exactly and zero sums disappear. Sorted runs merge with
bounded fan-in, then the builder writes CSC and constructs CSR by counts and prefix
sums. Reading prepares the cheap scan only. Families, refinement, topology and
indexed discovery require explicit requests.

Native v7 contains a portable base directory, typed proof/delta artifacts and a
checksummed manifest. Saving copies immutable base files without materializing an
overlay matrix. The bundle includes required base data and proof dependencies, so
it survives relocation and deletion of the original temporary directory. Completed
analysis artifacts can be copied with their control catalogs. Cached scan data is
bound to the base revision.

`load()` rejects every pre-v7 archive and asks the caller to re-import LP/MPS. It validates the
artifact inventory, checksums, sparse dimensions, exact-number references and
CSR/CSC agreement. Saved proof status is historical: loading cannot confer proof
authority. Explicit replay/audit checks the mathematical recipe again.

## Frame and algorithm execution

Frame is a logical DAG, not a SQL query. The array backend handles entity slices,
projection, predicates, sparse incidence, joins, exact groups, distinct and stable
ordering. Entity-ID association uses indexed arrays; selected-variable terms use
CSC when that is the smaller traversal. Cardinality and coverage barriers remain
complete even when a caller subsequently asks for `head(1)`.

`Frame.explain()` identifies input storage, selection storage, physical operators,
full-validation barriers and spill strategy. All record sources enter typed
builders; there is no SQLite numerical executor. Execution records ledger peaks,
although complete control-access and independent allocation accounting remain open.

Graph kernels use integer adjacency, queues, sparse workspaces and bitsets. Search
uses explicit state and undo structures. Set/signature hashes only select buckets;
full exact contents are compared before accepting equality. Complete failure sets
are retained independently from bounded display previews.

## IR

Refinement colors, oriented states, sparse role labels, token witnesses and observed
multiplicity ranges are array artifacts. Template refinement groups sign-labelled
skeletons and then partitions adjacent multiplicities by the requested ratio. Exact
mode retains normalized weighted signatures. Neither grouping proves equivalence or
symmetry. Zero-RHS equalities retain both orientations.

A completed refinement round publishes its arrays, definitions and depth together.
Indexed discovery stores domains, memberships, coordinates, bindings and exact
kernel columns as mmap artifacts; SQLite holds the completed run catalog. Replay
checks full sparse expansions, bounds, coverage and coordinate identities. Previous
complete results remain available after a failed unpublished run.

Cache identity includes snapshot identity/version, algorithm version and parameters;
indexed selection scopes include their domain digest and full membership check.
Families and attributes expose ordinary Frames over these results. They preserve
original IDs, exact normalization factors and full distributions.

## Proofs and edits

`Targets` and `CertificateBatch` are the numerical checker inputs. Ragged offsets
associate targets with sources, sides and exact multipliers. Frame certificates are
streamed into this representation without creating a Claim or Report per target.
`LinearBatch` accumulates typed buffers. `BatchProof` owns typed status and dependency
columns; `for_target()` is a lightweight ProofRef.

The exact sparse checker borrows the snapshot arrays. Small residuals use a bounded
accumulator; larger residuals use exact sorted-run merging. Specialized path checking
still validates the complete source/target conditions. Diagnostic Reports use lazy
presentation artifacts rather than numerical relation tables.

An edit records immutable selections, bounds, domains, fixed substitutions and new
entities. Summary/build/commit derive semantics from operations and checked
premises. Dependency closure uses reverse edges and a queue; deleted final premises
and circular justifications are rejected. LP, MIP, coordinate mapping, assumptions,
objective preservation and lifting are separate conclusions.

An overlay shares the base matrix and applies live masks and overrides. It has no
implicit CSR rebuild or SQLite materialization method. Nonzero fixed substitutions
update affected row bounds and the objective constant exactly through CSC incidence.
Diff reads recorded deltas instead of comparing every coefficient.

## Export and resources

The MPS writer patches eligible source records, including row deletion and supported
variable/domain changes. Complex edits use logical CSC streaming. Both paths verify
complete numerical facts with an independent parser before atomic publication.
Readback normally accumulates one column; non-contiguous repeated columns use exact
external coalescing. Nonterminating decimal rationals fail MPS export explicitly;
The current LP writer also rejects nonterminating decimal values.

Memory budgets concern working storage, not process RSS or the OS page cache.
`WorkContext` spills primitive buffers to mmap and enforces its disk allowance.
Sparse expression merge checks both run files and mapped workspace against its disk
budget. Output limits are checked before publishing algorithm results. Exceptions,
including interruption, close owned scratch files and preserve previously published
artifacts. Resource enforcement and correctness must be tested independently of
small benchmark speed; a fast main path alone does not establish all architecture
acceptance conditions.

## Validation and replay

`tests/test_runtime_arrays.py` checks shared ownership, forbidden numerical SQL,
exact spill, integer overflow, duplicate cancellation, native relocation, certificate
streaming and source patching. Existing Frame, graph, indexed, refinement, parser and
proof tests retain their mathematical contracts; private SQL-layout assertions are
migrated to behavior and ownership checks.

`scripts/benchmark_runtime.py` runs test32/test33 with fixed certificates or public
operations. `scripts/benchmark_runtime_pairs.py` runs separate serial processes in
alternating order, freezes source/input hashes and reports medians, ranges, peak RSS,
sampled temporary disk and artifact sizes. SQL tracing is a separate observability
run, excluded from timing comparisons. No solver or agent participates.

For the current implementation's remaining quota and container migrations, see
[ARCHITECTURE_REMEDIATION_20261005.md](ARCHITECTURE_REMEDIATION_20261005.md). This document describes the migrated
runtime; it does not certify that every path already satisfies the full plan.


## Three primitives on the shared Data Plane (native v7)

`FixedSubstitutionDelta` contains immutable fixed IDs/values, sparse exact row
shifts, affected live degrees, derived trivial/contradictory rows and the objective
constant increment. Overlay reads consume it directly. Live masks use persistent
128-entry pages; only touched pages are copied. Exact overrides and extension maps
borrow typed layers. Sparse nonzero accumulation switches to bounded sorted runs
when its hash workspace would exceed the shared budget. Automatic discovery scans
variable metadata; an explicit selector traverses only its selected CSC incidence,
plus bounded sorting when required. Zero substitution still visits incidence IDs
for empty-row detection, so its complete cost is not merely O(number of variables).

MPS import retains an immutable byte artifact and a typed physical-record index
in the same input pass. Repeated column contexts use run encoding. Native saves
clone artifacts when the filesystem supports it, otherwise stream-copy; they do
not hard-link mutable external inputs. `PatchPlan` records physical copy, skip and
rewrite actions against a source digest and target revision. Its planner filters
historical changes through the final live domain, preflights exact representation,
and never proves mathematical claims. Patch and canonical output share atomic
no-overwrite publication and numerical readback. Source and output scans, hashes,
and readback are O(file size) work reported separately from delta compilation.

DAG reduction encodes endpoints and record identities into typed ordinals. Weak
components use packed reachability bitsets only when they fit the remaining
workspace; larger components use generation arrays and bounded traversal. Witness
recovery uses retained-edge reachability or grouped BFS with reusable predecessor
storage, rather than one BFS per removed record. Ragged offsets and paths are
published only after all limits pass. The certificate bridge gathers row IDs in
chunks, shares offsets, and broadcasts multiplier one. The existing exact checker
validates actual rows; its telescoping fast path validates all intermediate joins
and falls back to generic exact checking outside its supported row form.

`WorkContext.fork()` shares one ledger; typed arrays spill to mmap, and explicit
transfer/leases retain result arrays independently of scratch lifetime. External
sort, exact accumulation, masks, override indices, graph results and readback use
that ledger. Bounded parser/name caches reserve memory from it. This is a working
storage contract, not an RSS limit: immutable inputs, retained source bytes,
interpreter objects and mapped-page residency must be measured separately. General
legacy Frame/IR paths outside these primitives retain the limitations documented
in the current remediation status report. Native replay reads typed recipes without expanding their
large vectors to JSON and does not apply compiled shifts a second time.
