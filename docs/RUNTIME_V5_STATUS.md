# Runtime v5 implementation status

This document distinguishes implemented changes from full-plan acceptance. The
migration is substantial, but the complete architecture contract is **not yet
satisfied**. Benchmark improvements must not be read as full acceptance.

## Implemented

- One shared `SnapshotData` owns dense metadata, exact-number caches, CSR and CSC.
  Facts and child overlays borrow it with explicit leases. Parent closure does not
  invalidate a live child. There is no overlay method that silently reconstructs
  CSR or a numeric SQLite database.
- LP and MPS use one typed builder and exact external coefficient coalescing.
  Numeric lookup uses array dictionaries, including packed bigint fallback.
- Frame's logical DAG selects array execution for model entities, terms,
  predicates, projection, joins, groups, stable sort and distinct. SQLite remains
  a generic relation fallback with an explicit planner reason.
- Graph, set, integer, search and isomorphism operations dispatch to integer-array
  kernels. Graph sort can merge bounded typed runs; search uses explicit state.
- Structural colors, role labels, token witnesses, multiplicity ranges and indexed
  domains/operators use array artifacts. Read only builds the cheap scan.
- Compact target/certificate/status/dependency columns share numerical facts.
  Scalar proof objects are views; Frame certificate compilation streams records.
  Exact residuals and point vectors can spill without numeric SQL.
- Edits use immutable typed selections/overrides and shared overlay masks. Combined
  overrides retain typed layers and integer lookup indices. Semantic computation
  uses certified operations and a dependency work queue.
- Native v6 preserves the portable base, typed deltas and proof recipes. It rejects
  v3/v4, validates both sparse orientations and exact content, and requires replay
  to trust a stored proof. A loaded overlay defers IR until it is requested.
- Export supports source patching and canonical CSC streaming, exact independent
  readback and atomic publication. Nonterminating rationals are rejected by MPS.
- Deterministic fixed-witness and public-operation test32/test33 runners freeze
  both sources, alternate independent processes, record phase timings/RSS/artifacts
  and validate the specified numerical outcomes without a solver.

## Remaining before full architecture acceptance

1. Resource accounting is not global across all algorithms. `WorkContext`, sparse
   external accumulation, edit selection bitmaps and graph sorting enforce their
   budgets, but some Frame joins/groups, graph preprocessing, search result
   collections and IR signature preparation still allocate Python or NumPy scratch
   outside a shared quota. A low-budget result on a single kernel is insufficient.
2. Parser symbol tables and exact interning still have resident Python maps.
   Normal MPS construction uses sorted typed runs rather than a separately
   optimized direct CSC path for already contiguous columns.
3. Added row/variable payloads, some proof-DAG/assumption recipes and finite-case
   staging retain Python containers. Solution checking now streams its input into
   exact sparse arrays; its complete violation collection still needs bounded
   artifact publication.
4. `round`, `cases` and enumeration resource options require an API/resource audit;
   not all accepted options yet constrain their complete working set.
5. Legacy internal SQLite workspace helpers remain reachable outside the migrated
   model kernels. Their removal must follow behavior coverage, rather than treating
   absence on test32/test33 as evidence that every path has migrated.
6. Existing directed/legacy API tests are not all green. The user excluded the
   concurrently changing directed tests from this fix list. Baseline failures are
   recorded separately; they are not removed or relabelled as successful tests.

These items are outstanding engineering work, not merely documentation tasks. Full
acceptance requires resolving them and rerunning the affected correctness and
resource-contract tests. The performance report identifies the exact frozen source
measured, including its limitations and baseline reconstruction.


## Later primitives/native v6 validation

The newer task baseline was green (604 passed, 13 skipped), and the primitives
implementation finished with 629 passed, 8 skipped. The historical directed-test
failures described above are not failures of this newer frozen baseline. The
additional implementation, alternating benchmarks and remaining resource/timing
gates are recorded in the final section of
[the performance report](PERFORMANCE_RUNTIME_20261005.md#三个-primitives--native-v6本次实施与验收).
The newer tests do not turn the remaining full-plan gates into completed work.
