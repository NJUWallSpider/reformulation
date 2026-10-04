# MIPFrame architecture

The immutable numerical model is authoritative. Analysis sidecars publish exact
derived facts; they are not replacement models. Discovery, exact expansion and
presentation have separate responsibilities.

## Module boundaries

```text
mipframe/
    model.py              immutable snapshot facade and lifecycle
    entity.py             lightweight read-only entity handles
    io.py                 public read/load
    options.py
    frame/
        base.py           Frame and live/detached records
        entity.py         RowFrame / VarFrame
        terms.py          TermFrame / Term
        row.py, var.py     scalar entity access and navigation
        column.py         exact reductions and extrema
        schema.py         logical types and snapshot-qualified Ref
        expr.py           serializable QueryExpr
        plan.py           non-executing plan inspection
        executor.py       shared query budget, exact spill and joins
        sources.py        column-pruned canonical scans
        adapters.py       analysis tables through the same Frame core
        serialization.py  shared query DAG bindings and exact exports
        materialized.py   shared completed-result ownership
    store/
        model_store.py    native v3 metadata, identity and exact numbers
        sparse_store.py   bounded CSR/CSC traversal
        incidence.py      family selectors and integer model adjacency
        algorithm_store.py shared exact scratch budget and typed key indexes
        names.py          name indexes and original numerical views
        query_store.py    integer query buffers and temporary relations
        stream_lp.py, mps.py, parser.py, exporter.py
        artifacts.py, numbers.py
    ops/
        support.py        entity sets and neighborhood counts
        graph.py          complete public graph algorithms
        sets.py, grouping.py exact set relations and multiset equality
        _kernels/         integer graph traversal and paged union-find
        _runtime.py       synchronous execution and versioned recipes
        matrix.py         sparse matrix views and SciPy export
    ir/
        compressed.py     factual model map
        signatures.py, refinement_store.py
        families.py, algebraic.py, typed.py
        family_attributes.py, display.py
        indexing/         discovery, storage, domain facades and exact replay
    check/
        report.py         complete reports and shared query errors
        frame.py          strict projection and relational checks
        model.py          numerical model validation
        serialization.py  certificate event export
        replay.py         bounded import, dependency scheduling and checker replay
    edit/
        plan.py, prepare.py exact edit planning and validation
        build.py, lineage.py, diff.py new snapshots and identity maps
```

Top-level imports (`import mipframe as mf`, `mf.Model`, `mf.Frame`, `mf.col`) stay
stable. Internal imports use these package boundaries; no root-module forwarding
shims are retained. The `edit/` package creates new snapshots and explicit identity
maps after validating an exact edit plan.

Rows, Vars and Terms are the only model facts. Domain facades retain their
non-tabular behavior; their tables use Frame. Native v3 stores canonical rational
values independently of source files. New LP/MPS imports receive independent
UUIDs; native copies retain uid/version. V2 is rejected with reimport guidance.
See [Frame contracts](FRAME.md) for schema/key propagation, joins, domains,
nullable semantics, exports and migration.

The execution context shares one working budget across its plan. Streaming
operators request only required columns; blocking operators spill through exact
SQLite encodings. The context registers live dependencies immediately, but creates
its temporary directory and SQLite connection only on the first storage operation.
Validation barriers finish before exposing results. A model close guard runs before
each iterator resume, before touching mapped arrays. Closing an unused context
prevents subsequent storage creation.

## Reading, analysis and ownership

`read()` calls `store.model_store.build_numerical()` in an owned temporary directory.
`load()` opens a persistent directory. Both generate `Model.ir` before returning:
schema 0.3 combines sparse statistics, exact numeric profiles, objective support,
bipartite components, template refinement through depth 2 and the completed default
indexed run. They create or reuse
`refinement.sqlite` and `indexing.sqlite`.
The public model owns SQLite and NumPy memory maps, closes them explicitly,
and deletes only its owned temporary directory. Public records expose exact
numbers and names; disk encodings remain an internal implementation detail.

`ir.compressed` provides a machine representation that stores facts, structural abstractions and model-local
IDs, depths and configurations, never a rendered document as data. Numeric histograms use temporary
SQLite storage, and union-find uses O(rows + variables) workspace without an edge
list or dense matrix. Family summaries retain no full member names or coefficient
histograms. Rendering reads only the completed map and obeys independent
`options.ir` limits. Detached exact dictionaries and rational-string JSON exports
outlive the owner; live IR access and typed queries follow the normal closed-model guard. Saves
retain refinement layers, while loads rebuild the map from authoritative data.

`save()` publishes a new independent directory after copying immutable numerical
files and edit lineage, and taking SQLite backups of refinement and indexed results.
Unrecognized scratch files and retired sidecars are excluded. It refuses existing targets and targets
inside the source workspace. It does not change the live model's ownership.

Read/load synchronously prepare default indexed coordinates/operators, restoring saved results when available.
`model.structure.indexed()` retrieves the default result or computes an uncached custom scope,
with a bounded working-memory budget and atomic complete-result publication.
Numerical verification checks immutable artifact checksums and sparse dimensions;
`Model.verify()` also replays all prepared indexed results.

## Checkpoints and publication

Local structural signatures are independent of shared-coordinate discovery. The default template mode groups sign-labelled
neighbor-role skeletons, then partitions positive multiplicities by adjacent ratios
(default >2) within each skeleton and old color. Both sides use only the previous
round's template colors. Exact mode retains the prior normalized weighted WL.
Neither mode proves semantic templates or symmetry.

`refinement.sqlite` uses versioned, snapshot-dependent caches and template range metadata.
Algorithm 2.0 configuration keys include mode and multiplicity ratio, isolating
new computations from older refinement caches. Mode IDs enter template keys;
observed ranges do not. Coarse grouping and count-mode detection spill to SQLite,
while CSR/CSC and integer mmap buffers retain bounded graph access. Zero-RHS rows
have two oriented states with an unordered public color. Updates are synchronous
and retain old colors. Interning compares complete chunks after digest lookup.

Each requested round publishes definitions, metadata, arrays and completed depth
in one transaction. Failures release unpublished maps and preserve prior rounds.
Native save uses SQLite backup; loaded configurations reuse completed layers and
extend on demand. Reading prepares default refinement and indexed results; rendering does not search.
Family membership is a temporary disk selection over full-model colors.

Family attributes are lazy, separately aggregated numerical observations. They
stream coefficients into temporary SQLite member/role/value counts, derive missing
zero counts sparsely, and expose complete paged/iterable histograms with bounded
previews. Canonical display preserves magnitude; raw and scale-normalized views
retain source row IDs and multipliers. Dual equality orientations are separate
profiles. Selection attributes cover selected members, never a representative row.
`related("parameters")` and `related("histogram")` expose bounds by basis and orientation.

The presentation layer (`ir/display.py`) does not modify refinement keys or
colors. Overview tables read metadata/role definitions only. A summary pairs row
depth d with the variable legend at d-1, then reports changed parent/child families.
Parents resolve to family ranks at the preceding depth, while variable labels
resolve to their actual colors. Full-model family indexes are reused within a
result; variable color order and delta links remain disk-backed.
Public family and role IDs include depth; bound frames accept shorthand but reject
mismatched explicit depths. Labels remain fixed within their model/configuration/depth, even for selected subsets. Full identity uses family_key.
Depth count deltas, legend refinement and count-only coverage metrics explain the
hierarchy without loading coefficient profiles. Prefix previews have independent
family/split caps, exact omitted counts and selected-member coverage. Concentration
uses SQLite aggregation, or a bounded top-5 heap for selected family views.

Compact row structures use `|`-separated signed groups with explicit term counts.
Zero-RHS equality display chooses one structural direction; dual states stay
available in diagnostics. Parameters use the matching numerical orientation and
show constants/ranges/distinct counts. Full histograms are separate Frame relations or explicit verbose diagnostics. Raw signature and normalized attribute views are also available.
Inspection samples show bounded original formulas for short rows and metadata for
long rows by default; callers may explicitly request truncated formulas. Variable
row_degree is original row incidence (including actual min/max when it varies).

- Each refinement round commits its definitions, arrays and completed depth together.
  Failures preserve previously completed rounds.
- Indexed discovery discards its unpublished private database on failure; previously
  published complete results remain usable.
- Native saves snapshot derived SQLite stores with the backup API and publish a new directory.
- Exact exports use same-directory temporary files and atomic replacement; failed
  publication preserves the previous file.

These rules do not provide a transaction across simultaneous writers or whole-build rollback.

## Validation

Run `python -m pytest -q` with `.[test,scipy]` installed.
`tests/test_runtime_contracts.py` checks atomic snapshot publication and import boundaries.
Mathematical regressions cover exact coefficients and expansion, independent parser
comparisons, sparse domains, permutation, scaling, certificates and edits. Lifecycle
and cache tests cover corrupt artifacts, save/load reuse, closed views and rollback.
Table-driven validation cases share setup; resource-sensitive algorithms retain
bounded-memory and failure coverage.

## Source-preserving batch relationships

Entity navigation returns sets: `Row.vars` and `Var.rows` are entity Frames.
`Terms` retain each original coefficient and both typed endpoints. Strict batch
projection belongs to the source entity Frame and returns an ordinary Frame with
declared target domains. Sparse traversal and family selection use integer IDs;
names and exact coefficients are decoded only when requested.

`query_store` owns bounded SQLite scratch storage registered with the public Model.
Integer result columns, exact grouping keys and source membership can remain on disk;
ID-aligned counts and masks use mapping adapters rather than dictionary copies.
Projection checks use declared target domains, and do not enumerate their Cartesian
product. Pattern grouping uses an exact integer trie, with a disk-backed traversal
for deterministic order. No hash collision assumption enters a structural proof.

Live results and suspended cursors check Model lifecycle before accessing storage.
Model.close closes query stores before the model arrays. Detached record exports
remain usable independently. Column queries retrieve only their field dependencies;
rendering requests only visible fields, while explicit entity statistics or formulas
may scan the relevant sparse terms.


### Indexed discovery publication

The `indexing/` package owns discovery, exact validation, live result views and
versioned storage. Integer sparse positions and disk membership domains serve
candidate search; only numeric seed/recovery checks decode coefficients. Domain
digests are followed by exact integer equality checks. Partition and tensor views
retain zero-degree domain members; connected-component candidates carry local scope.

`indexing.sqlite` contains only complete runs. Each uncached search computes in a private
scratch copy, then atomically publishes it after all operator expansions pass. The
artifact includes a content hash and model/config identity. Native save uses SQLite
backup; restore validates the artifact without repeating discovery. Loading without a default result computes it before returning. Temporary stores
are owned by the model and live query objects. IR schema 0.3 summarizes runs;
large parameter tables use family Frame relations; indexed coordinates retain their existing indexed accessors.

## Complete structural algorithms

`mf.ops` consumes Frames and publishes completed Frames through the same query
layer. A call shares one AlgorithmStore for input stages, integer indexes,
algorithm state and results. Input materializations borrow the active cache
allowance while reading. Identity domains retain empty sets and isolated nodes;
TermFrame now declares both axes. Key encodings preserve exact scalar/Ref types.

Fingerprint buckets are followed by full equality comparisons. Graph SCC, queues
and traversal stacks spill; hyperedges use union-find without clique expansion.
Full reachability uses SCC bitsets only if the entire resident estimate fits,
otherwise paged search. Targeted queries do not build unrelated transitive closure.

Algorithms enforce output and optional disk budgets and release unpublished
scratch on failure. Semantic failures retain complete pageable reports. Result
recipes restore through validated explicit bindings; completed query results are
not additional native model artifacts. No new detector or candidate search runs
on read/load. See [OPS.md](OPS.md) for public contracts and proof boundaries.


## Explicit search layer

`alg` adds discrete candidate discovery and exhaustive enumeration over the same
Frame inputs. `SearchStore` extends the shared algorithm context with deterministic
state budgets, cooperative deadlines and paged search state. Candidate stops are
handled only after complete input validation and never publish an unfinished
object. Its SQLite journal supports actual savepoint rollback; normal unpublished
ops scratch storage can keep its existing non-journaled policy.

Clique and cycle members, scores, isomorphism mappings, assignments and search
metadata are normalized ordinary Frames. Exact external merge ranking and complete
sequence/bijection checks avoid relying on hashes or color refinement as proof.
All work shares one memory/disk allowance. Exhaustive failures release unpublished
results. Candidate provenance survives Frame composition and schema exports;
versioned recipes rerun searches with explicit bindings. No search is added to
model startup or native persistence. See [ALG.md](ALG.md).

## Exact algebra and explicit verification

`linear/` owns finite exact literals, canonical affine expressions and unproved
Claims. Borrowed row expressions retain CSR positions; expression arithmetic and
substitution normalize into bounded exact memory or shared sparse spill stores.
Completed spill expressions share a normalized relation per live owner and retain
only their own rows through view leases. `QueryExpr` remains the table
query language. Neither expression evaluation nor substitution asserts feasibility.

`check/` shares one Report implementation across relational assertions, indexed
replay and mathematical checkers. A certificate context stages typed
sources, conditional intervals, exact sparse residuals, dependencies and evidence
under one budget. Report conclusion kinds prevent data assertions and point
feasibility from being consumed as universal linear inequalities. Integer-only
scope and assumptions propagate along a checked dependency DAG.

Completed scalar mathematical reports move from temporary ProofContexts into a
normalized CertificateArchive shared by their live model owner. A Report's
conclusion and evidence views share a node lease; parent references preserve proof
closure until the last dependent view releases them. Node rows are reclaimed and
database pages reused. Model registration is weak; Model.close closes owned
connections before temporary-directory cleanup. SQLite progress and aggregate
callbacks do not strongly own execution contexts.

The linear batch builder incrementally stages the scalar target/using/assume
contract, then invokes the same linear verification kernel as linear_many. Both
use fixed normalized result tables and shared per-target scratch space. run seals
the builder and transfers storage to its Report; abort closes unpublished stores.

The internal finite integer traversal kernel serves both `alg.integer.enumerate`
and `check.enumerate`. The former publishes all assignments or fails; the latter
streams exact extrema and witnesses and distinguishes exhaustive proof, a local
counterexample, and an inconclusive budget stop. Explicit certificates use a
versioned JSONL stream with atomic publication and mandatory replay on import.
They do not change native v3, model startup, or automatic save contents.
See [expression and verification contracts](CHECK.md).

## Source-relative Edit snapshots

`edit/` separates transactional declarations, draft identities/affine construction,
final-fact preparation, grounded certificate closure, numerical compilation,
lineage, and exact direct-parent diffs. Operations use one paged store and are
simultaneous relative to one immutable source. Model facts never acquire draft
identities. Schema has a distinct Edit-qualified `draft_ref` scalar for temporary
Frame construction; model reference consumers continue to require real Ref types.

`check.linear_batch` shares normalized per-target proof tables and the existing linear
checker. Only exact mathematical conclusions may discharge edit obligations.
Forward and reverse feasible-set implications are tracked independently in LP and
integer domains. Reverse evidence must close at retained facts; serialized status
fields are never sufficient. Compilation streams canonical coefficients to a new
native v3 store and publishes the Model after exact validation. Default logical
builds defer IR/indexed preparation; `prepare="indexed"` prepares those analyses
before publication.
Lineage is a versioned immutable attachment copied with native snapshots. It stores
bound proof streams and mappings without embedding the parent numerical model.

The LP writer uses atomic replacement. `audit.roundtrip` compares re-parsed
numerical facts and emits a non-mathematical Report. Details and deliberate scope
boundaries are in [EDIT.md](EDIT.md).


## Offline help contracts

`_help/` owns explicit public bindings, canonical contracts, tested recipe text and
a compiled deterministic search index. `mf.help` and the CLI share lookup and
rendering. Static object resolution never evaluates live properties. Imports and
runtime error hints do not load the corpus; the first help query loads it through
`importlib.resources`. Generated docstrings and reference pages share one source.
Release builds reject stale content or source fingerprints. See [HELP.md](HELP.md).


## Family query navigation

Model family methods publish ordinary Frames. Private refinement contexts bind
an immutable requested depth; configurations and full-model partitions share
storage. No query view owns a family-specific database. Numerical scans use
bounded short-lived scratch storage and always close it on completion or failure.

Declared relation navigation is distinct from legacy attached `relations`.
Frame.related semijoins with selected family keys. Projection keeps navigation
only when linking keys survive, including direct renames; grouping removes it.
Joins retain applicable left navigation. Entity members are deduplicated typed
Frames. Filtering entities changes statistical scope, never refinement identity.

Signature encodings remain private algorithm values. The public analysis module
returns Frames for algebraic, typed, template and exact structural features.


## Shared query and verification internals

Each Frame owns a private `QuerySource` descriptor for its source specification,
navigation, pushdown, selection, display and resource capabilities. Derived
queries retain only capabilities whose identity keys survive. Adjacent filters
combine before pushdown, so equivalent predicates choose the same numerical
basis. Source codecs register reconstruction functions separately from query
operators, including legacy membership specifications. A shared dependency walk
validates nested sources and expression memberships, including aggregate expressions
and expressions inside tuples. Column pruning stops at membership Frame boundaries;
snapshot ownership includes those Frames in every derived query.
The same direct-input traversal drives dependency validation, search provenance,
operator input ownership and plan inspection. Sparse multi-column term selections
use CSC when their combined degree is below half the matrix nonzeros; a budgeted
position sort restores canonical CSR order. Pattern aggregation lives in
`ir/patterns.py`, independently of certificate reports.

Query serialization preserves shared DAG nodes with optional `$id` definitions and
`{"op": "ref", "id": ...}` references in the version-1 plan. Definitions precede
references in traversal order, including membership expressions. Old tree plans
remain readable; duplicate IDs, missing references and cycles are rejected.
`explain()` displays repeated parents as references without executing them.

Proof calculation composes a runtime store; archived certificates own a retained
lease and read-only proof data rather than copied runtime state. Model-owned
stores share registration and release helpers. Closing a model attempts every
connection release even if one cleanup fails, records secondary failures, and
retries deferred directory cleanup.

Storage integrity facts have one implementation shared by model validation and
export. Diagnostic reports distinguish checked propositions from their detail
rows; counts use propositions and status reduction preserves inconclusive or
skipped results. Failed publication deletes only its own unpublished rows.
Indexed candidate adapters normalize Frames, records and independent metadata
before exact validation; directory identity alone never supplies proof.

Edit semantic verification separates claim normalization, grounded facts,
dependency closure and objective comparison. Lookup indexes cover proof outputs,
dependencies, fact claims and roots without changing the mathematical closure.

### Proof/edit runtime performance

Edit builds default to numerical/logical publication, with full exact validation
and independent native storage; IR/indexing are lazy. Internal verified receipts
bind actual live source/target stores and retain only paged coordinate maps plus
semantic results. They are never serialized, never inferred from saved status,
and never retain a complete Edit/proof graph. Diff can always force full replay.
Receipt tables store only `(id,ns,old)` coordinate maps and row replacements;
bound, kind and degree comparisons read the immutable snapshots directly.

Linear batches stage validated normalized inputs without an INCONCLUSIVE report.
The shared exact kernel consumes those inputs directly. A reusable workspace
resets scratch tables and their staging/membership caches between targets, releasing
cached Frame references together; evidence Frames read canonical proof
relations instead of copying all sources/claims/terms into another JSON store.
Frame operators resolve their execution context through `query_context`, avoiding
a second manually forwarded query API on proof views. Scratch indexes reuse names
derived from table/column identities across resets; batch evidence has a
`(target,direction)` index for per-target lookups.
Sparse residual accumulation uses a conservative byte allowance and spills
exactly to SQLite. Case discharge reuses bounded scratch storage and reads
verified dependency relations directly. Native v3 and certificate streams are
unchanged; persistence fingerprints still cover the same canonical facts.

Edit bound updates close their input cursors and drop operation-local selection and
alignment tables on success or failure. Persistent edit tables remain transactional.
Proof replay separates normalized event validation, dependency scheduling, claim
reconstruction and checker dispatch; serialized verdicts never replace verification.
