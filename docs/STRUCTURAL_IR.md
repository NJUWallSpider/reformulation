# Shared coordinates and structural affine families

`analyze("structure")` builds an additive, versioned `structure.sqlite` sidecar.
New numerical IR builds run bounded discovery automatically. Reading a supported numerical IR
never creates a missing sidecar. `structure.json` records its model
fingerprint, version, progress, elapsed time and query-cache revision.

The immutable numerical matrix remains authoritative. The layer gives anonymous
algebraic coordinates to repeated variables and rows; it does not classify a
model, infer coefficient formulas, eliminate variables or propose reformulations.

## Public interface

```python
with IR("artifact") as ir:
    result = ir.analyze("structure", budget={"seconds": 30, "memory_mb": 128})
    blocks = ir.inspect("structure_blocks", limit=100)
    families = ir.query({"table": "structure_families"}, limit=100)
    checked = ir.validate({"kind": "structure_family", "id": families["items"][0]["id"]})
    ir.verify()  # Includes independently expanded structural families.
```

All relations support the existing relational query AST and paging:

| Relation | Contents |
|---|---|
| `structure_blocks` | Rank, axes, axis domains, shape, exact support mask, metadata and provenance |
| `structure_coordinates` | Variable identity, arbitrary-rank coordinate tuple, type, bounds and objective coefficient |
| `structure_families` | Affine AST, row domain, coordinate maps, summation axes, definition marker and description length |
| `structure_rows` | Original row, coordinates, exact scale and original local formula family |
| `structure_parameters` | Exact parameter values, retained inputs and proven invariant inputs |
| `structure_links` | Family/block incidence, projections, aggregations and objective participation |
| `structure_evidence` | Rejected family compression candidates |
| `structure_residuals` | Original rows without an accepted structural family |

`scope={"rows": [row_ids...]}` or a materialized row-producing query restricts
which complete numerical rows are analyzed. Scoped runs have separate sidecars;
pass their returned `analysis` identifier to inspect/query/validate. Coordinate
seeds can reference global verified indexed tensors: row scope does not imply
that their supporting global coordinate facts are local to the selected rows.
Continuation returns the original scope and a model-specific `handle`.
If coordinate discovery has not finished, the response also includes a
`dependency` on `analyze("coordinates")`; resume that producer before continuing
the structural search. An unfinished seed search is never interpreted as an
empty completed result.

Budget exhaustion is not a negative result. Scan and family compilation retain
cursors. Discovery and individual family verification are atomic: a memory/time
limit that cannot accommodate one such candidate requires a larger budget.
Only completed verified views are published. `search_completeness="complete"`
means the implemented search finished, not that every possible latent coordinate
system has been discovered. Residual rows remain exact through the numerical IR.

## Representation and exact expansion

Each axis has an identity independent of its position in a block. Equal lengths
do not identify axes. A proven bijection can align two axes while preserving
separate positions (e.g. the two indices of a square tensor). Coordinate values,
axis orientation, and artifact-local readable aliases are not canonical labels
under permutations; recovered structures are compared up to coordinate renaming.

An `affine_range` AST contains terms with `block`, `fixed` pairs
`[block_axis, row_coordinate_position]`, `sum_axes` and a parameter reference.
Expansion selects **all observed block cells** satisfying the fixed indices,
looks up the exact coefficient, and sums them. Row lower/upper parameters and a
nonzero instance scale reconstruct the original row, including its direction.
An absent variable cell is not a zero-valued variable or an inferred parameter.

Parameter inputs refer to row coordinates or free block axes. Projection searches
subsets in increasing dimension, accepting only functional tables on the observed
domain. Constant parameters have zero inputs. Invariance is local to that domain;
no off-domain extrapolation or relation such as `min(U, sum(d)-d)` is asserted.

A linear definition is marked only when a row has one unknown output and a full
fiber of already coordinated inputs. Two equally plausible unknown outputs are
left unmarked. The equality remains in the matrix.

## Discovery and acceptance

1. Import existing verified two-axis incidence seeds, with an explicit bijection
   when a unique missing-cell matching justifies alignment.
2. Propagate coordinates through one-to-one row bindings. When output coordinates
   collide, use connected components of homogeneous signed equality incidence
   to propose a new axis. A unique excluded coordinate may align that axis.
3. Transfer the fixed coordinates of full-fiber exact definitions to their outputs;
   repeat propagation until no further variables are coordinated.
4. Group complete rows by participating blocks, fixed/free positions, coefficient
   signs and normalized direction. Align row-coordinate columns only when their
   equality holds across the entire family, not just one row. Magnitudes and
   coefficient multiplicities do not determine roles.
5. Build parameter projections and independently expand every row. Reject duplicate
   coordinate cells, ambiguous row domains, incomplete fibers, missing or extra
   terms, conflicting parameter cells, unobserved parameter cells and incorrect
   bounds or scales.
6. Accept a family only when canonical compact JSON for its AST, row domain and
   parameter tables is strictly shorter than its flat coordinate/coefficient rows.
   Raw identity/provenance mappings are excluded on both sides. This metric is an
   algebraic description length, **not** a claim about total SQLite file size.

The storage supports arbitrary rank. Discovery is deliberately conservative and
currently grows axes from existing seeds and verified incidence components;
it is not exhaustive Cartesian factorization or graph canonization. Dense tensor
allocation is avoided. Irregular supports remain explicit or fall back to local
formulas. Existing coarse operators remain accessible as diagnostic views.

## Regression artifacts

```sh
PYTHONPATH=src python scripts/evaluate_structure.py \
  --out /tmp/lpir-evaluation --seconds 300 --jobs 2
```

Each instance runs in its own subprocess with a hard 300-second limit including
parsing, discovery, summaries and verification. Results include fresh IR,
`summary.md`, `summary.json`, timing/RSS/coverage metrics and a report. Timeouts
retain available artifacts but never count as verified successes. Existing
artifacts are not overwritten by the evaluator.

## Version 2: exact relational coordinates

Version 2 adds `structure_views`, `structure_index_maps`, `structure_subsets`,
and `structure_objective`. The existing paging, query, scope and continuation
interfaces apply. Validation kinds are `structure_view`, `structure_index_map`,
`structure_subset`, and `structure_objective`; `verify()` also checks all published
relational records. Scoped analyses may use global verified variable coordinates
and objective profiles; their scope limits the constraint rows, not the source
of those coordinate facts.

Only numerical schema 2 and structural sidecar 2.0 are supported. Obsolete
sidecars are rejected without mutation; rebuild from the source LP/MPS in a new
directory. There is no legacy reader, migration command or automatic backup path.
Numerical and explanation stores remain separate from structural analysis.

### Entities and occurrences

A base variable belongs to one structural entity. If two existing entity
coordinate columns are exactly bijective, only one is retained. The old
coordinates remain a verified alias. This is deliberately **not** arbitrary
flattening: a full two-dimensional grid cannot become a vector by enumerating
its cells. Legacy indexed tensors are available through `structure_views`, with
base-variable audit identities and a compact coordinate map. Overlapping aliases
never contribute additional variables to entity counts.

Family compilation searches exact single-input functional coordinate bindings.
It chooses a minimal generator set, comparing serialized costs among alternative
bases; the resulting *whole family*, including mapping tables, must still be
strictly shorter than flat rows. Above twelve distinct coordinate columns,
search conservatively falls back to equal-column consolidation. Thus large
lookup tables cannot force an otherwise unprofitable family into the primary
view. Free-index counts describe this supported reconstruction class, not a
claim to have found an information-theoretic minimum over all possible maps.

The `fixed` field still gives `[block_axis, row_slot]`. A term's optional `maps`
assigns a block-axis position to a family `index_maps` record. Each map stores
source/target axis identities and every observed input/output pair. The verifier
requires its exact observed input domain and expands it against original rows.
Such a map is local evidence, never an assertion that two whole axes are equal.
In particular, a numbered offset such as `40+i` is not assumed under row/column
permutations. Shared map identities are stable across families within an artifact.

`symbolic_domain` recognizes complete Cartesian products minus exact equality
exclusions; otherwise the explicit row domain remains authoritative.

### Restricted supports

Terms may restrict free axes using `restriction` (observed coordinate subsets),
and may carry `support_pattern` for an exact matching or its complement. These
patterns are accepted only when they recover the entire selected support in the
existing variable domain. They do not create absent variables or assign zeros to
missing cells. Coefficient dependence is independently checked after support
recovery. Irregular cases retain explicit/numerical fallback.

A subset with `constraint_support` evidence records a repeated fiber restriction.
Objective subsets instead carry `identical_objective_profiles` or
`profile_complement` evidence. The latter is a collection of remaining values,
**not** an equivalence class. Neither label certifies model symmetry. Equal
cardinality never proves row/column subset alignment.

### Objective and display

Objective tiles cover all coordinated variable cells exactly once, including
zero-cost variables. Each tile stores its observed domain and an exact parameter
projection. Objective constants are checked too. Uncoordinated variables retain
numerical objective coefficients as explicit fallback; verification reports both
counts. The search groups identical complete coefficient profiles and compresses
within disjoint product tiles. To bound work it falls back conservatively above
16 groups per axis or 256 product tiles. It does not search arbitrary low-rank
factorizations or infer hidden coefficient functions.

The human summary shows entities, subsets, shared maps, objective tiles and
indexed families. Legacy operators/aliases remain diagnostic queries. Partially
covered models have an explicit residual query instead of repeating already
covered local families. Long rational numbers are abbreviated with `≈` only in
the reading view; exact values remain in the machine document and query tables.

`description_metrics` reports canonical algebraic bytes, not SQLite file size.
Family maps are charged once across families; compact alias maps are deduplicated
and charged separately, as are objective tiles and subset definitions. Original
variable/row identities used only for auditing are excluded. The report exposes
family-only and full structural totals separately, so adding objective coverage
cannot masquerade as better compression of constraints.
