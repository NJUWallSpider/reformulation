# Signature-assisted indexed views

`m.structure.indexed()` provides discovered and exactly verified
2D incidence coordinates and sparse reduction/broadcast operators, then publishes a summary in `m.ir.indexed`.
Reading a model prepares the default full-model result synchronously. Loading restores a saved result or computes it when absent. Calling `m.structure.indexed()` retrieves that result; displaying the model never starts another search.

```python
import mipframe as mf
with mf.read("model.lp") as m:
    I = m.structure.indexed(memory_mb=128)
    for record in I.tensors:
        T = I.tensor(record.id)
        print(T.shape, T.complete_product, T.missing_cells)
        T.coordinates.check("unique", ("i", "j")).require()
    for record in I.operators:
        O = I.operator(record.id)
        O.verify().require()
        print(O.formula())
    m.save("model.native")
```

## Candidate rules and guarantees

Candidate sources include homogeneous type/direction/unit seeds, row and variable families at every available
signature depth, coarser ancestor families and role/direction groups. Only observed role adjacencies are
considered; arbitrary family subsets are not enumerated. A family is not an axis certificate. Two-incidence
set graphs are split by exact bipartite traversal, separately by connected component.

Partitions retain declared zero-degree members and are checked for nonempty sets, disjoint membership and
coverage. Only identical variable universes can form coordinate pairs. Coordinate uniqueness is exact;
a sparse grid can be verified while still having missing cells. Product checks count missing cells without
materializing the Cartesian product. Digests filter candidates; integer comparisons establish equality.

Mixed-row support is only structural evidence. Operators require exactly one external anchor term,
including anchors of the same variable type as the input tensor. Every claimed operator is expanded and
compared with the full raw row and its bounds using Fraction arithmetic. Kernel invariance and unique-maximum
axis alignment are separate checks and do not certify symmetry of the optimization model.

## Results and resources

`I.tensors`, `I.partitions`, `I.operators`, `I.evidence` are ordinary Frames with bounded display, iteration,
`len/head/one/loc/to_records`. Tensors expose `vars`, two original row Frames in `axes`, and a Frame
`coordinates` with snapshot-qualified Ref columns `var`, `i`, `j`. Use
`I.tensor(record.id)` and `I.operator(record.id)` to obtain domain facades. Operators expose `rows`, `bindings`, exact sparse `kernel`,
`alignment`, `formula()` and `verify()`. `I.validate(spec)` and `I.verify()` return ValidationReport objects.

Selection parameters accept same-model Frames and deduplicate original IDs. A supplied `signatures` object
must belong to that model. Omitting signatures reuses the IR's template depth 2 result. All existing levels
of a supplied signature are considered, including exact-mode ancestors.

Execution is synchronous with a bounded working-memory cache (default 128 MiB; minimum 16 KiB) and disk
scratch storage. There is no time/candidate cutoff or partial successful result. Failures clean unpublished
work and preserve prior results. Large graphs, members and sparse kernels stay in SQL tables; no dense
matrix is created. Explicit record exports have a separate materialization budget (default 128 MiB).

Complete results are atomically published in versioned `indexing.sqlite`, bound to the model, scope and
signature configuration. Native save uses a consistent SQLite snapshot; load checks version and content
integrity and reuses complete results; missing default results are computed before load returns.
Human displays omit internal cache IDs; machine summaries retain IDs, scopes and signature configurations.
Detailed objects are accessed through the typed IndexedResult/Tensor/Operator interfaces.
Detached records outlive the model; live views do not.

## Entry points

Use `I = m.structure.indexed()` to retrieve the full-model result or discover a custom scope.
Navigate through `I.tensors`, `I.operators`, `T.coordinates`, `O.kernel` and `O.bindings`.
`I.validate(spec).require()` verifies hypotheses expressed with original IDs or
snapshot-qualified Refs and independent records. Frame checks verify table properties.

See [the user guide](USER_GUIDE.md) for interactive navigation. Benchmark indexed discovery using
`scripts/benchmark_indexed.py BASE --out DIR`; `scripts/evaluate_indexed.py INPUT --out DIR` also checks
exact LP round trips, numerical integrity and the factual IR summary.
