# MIPFrame

MIPFrame is a Python package for reading, inspecting and analyzing linear LP/MPS
optimization models. It preserves exact coefficients, variable domains and source
locations, and exposes them as Python objects. Reading a file prepares a cheap
numerical scan (`model.ir`): sparse shape, domains, bounds, numeric fingerprints,
local row shapes and lexical name hints. Families, components and indexed
coordinates are computed only when requested. Native loading validates numerical
content and defers saved structural-cache restoration until explicit access.

## Install

From this repository (Python 3.11 or later):

```sh
python -m pip install .
```

Version 1.2.0 is an internal release, not a public PyPI release. NumPy is the
only runtime dependency. For a distributed wheel, install with
`python -m pip install /path/to/mipframe-1.2.0-py3-none-any.whl`.
Install `.[scipy]` from this repository for sparse CSR exports, or `.[parquet]`
for Parquet exports. Offline help is available through
`python -m mipframe help "support containment"`.
See [internal release and verification](docs/RELEASE.md) for reproducible builds,
optional model regressions, and release boundaries.

## Compressed model map

`read()`, `load()` and `Model(path)` cache a level-0 scan before returning.
Default display, Mapping iteration and exports never run structural analysis,
even after other analyses have been computed:

```python
with mf.read("model.mps") as m:
    m.ir.show()                       # bounded text, no printing or discovery
    facts = m.ir.to_dict()            # detached scan, schema 0.4
    json_text = m.ir.to_json()        # exact rational values encoded as strings
    m.ir.size, m.ir.matrix, m.ir.domains
    m.ir.numerics, m.ir.objective, m.ir.local_row_shapes, m.ir.names

    shapes = m.ir.row_shapes()        # explicit full exact local grouping
    R = shapes.where(sense="<=", nnz=2).rows
    R.terms.head()
    names = m.ir.name_templates("var")
    names.related("slots")           # exact minimum/maximum/distinct per slot
    names.vars

    m.ir.structure                   # legacy depth-2 structural summary, lazy
    m.ir.topology                    # lazy bipartite components
    m.ir.objective["role_support"]   # lazy objective support by roles
    m.indexed()                      # explicit coordinate/operator discovery
    full = m.ir.to_dict(include_analysis=True)  # explicitly compute all analyses
```

The scan distinguishes constraint-matrix `A-nnz` from objective nonzeros. Counts,
degrees, signs, bounds and numeric extrema are exact. Local shapes use raw sense,
row bounds and coefficient multiplicities, without normalization or neighboring
roles. Representative row IDs lead back to original terms. Shape labels in text
are preview positions; use fields or original row IDs to query, not those labels
as portable identities. Names preserve delimiters (`x[12,3]` becomes
`x[{int},{int}]`) and are lexical hypotheses, not structural evidence.

Scan working storage targets `min(8 MiB, memory_mb / 8)`, excluding the core store,
degree-array workspace and exported result. A compact coefficient-ID frequency
vector spills to scratch mmap when it exceeds its memory allowance. Numeric
common-value summaries retain at most 32 candidates. Low-cardinality histograms are exact.
Bounded Space-Saving summaries expose `count_lower <= true count <= count`,
`error`, `exact`, `ranking_exact` and unknown group totals when necessary. Wide-row
fingerprints are explicitly marked candidates; they never establish exact row
equality. Slot distinct counts can be lower bounds; incomplete slot ranges are
marked observed. Names longer than 512 characters are counted as unprofiled.
Use the explicit exact directories for complete groups, members, coefficients
and slots. Exact directory relations follow filtering and key-preserving projection.

```python
mf.options.ir.max_local_shapes = 8
mf.options.ir.max_name_templates = 8  # per axis
mf.options.ir.max_histogram = 8
mf.options.ir.max_values = 8
mf.options.ir.max_chars = 6000
```

These are display limits; changing them does not rescan or change machine data.
Native v6 stores shared base arrays and typed deltas; v3/v4 require re-import.
Core parsing merges terms using an external sort;
the scan adds no global sort, graph traversal or family-table materialization.
The normal import/load scan shares the required canonical-content traversal.
Logical Edit snapshots scan lazily on first `ir` access. Lazy analysis failures
leave numerical facts and scan data usable and can be retried. Live views require
an open model; detached dictionaries, JSON and records outlive it. See the
[schema and navigation guide](docs/USER_GUIDE.md#compressed-ir模型地图).

## Read a model

```python
import mipframe as mf

with mf.read("model.mps") as model:
    print(model)                   # MIPModel("model") followed by numerical statistics
    print(model.shape, model.nnz)

    variable = model.vars.iloc[0]
    print(variable.name, variable.kind, variable.lb, variable.ub)

    constraint = model.rows.iloc[0]
    print(constraint.name, constraint.lb, constraint.ub)
    print(constraint.terms)         # original exact (row, var, coef) terms

    print(model.objective.sense)
    print(model.objective.constant)
    print(model.objective.coefficients)
```

Finite values are `fractions.Fraction`; unbounded sides are `-inf` and `inf`.
Position indexing with `.iloc` is zero-based; `.loc` uses original IDs or typed Refs. Use `model.rows.by_name("demand")` or
`model.vars.by_name("x")` for name lookup. Rows and variables include `source_line` (one-based, or `None` for constructed entities).
Use `to_records()` for explicit detached exports.

In notebooks, putting `model` on the last line displays its name, objective sense,
variable/constraint/nonzero counts, matrix density, variable types and objective
nonzeros. Counts use thousands separators and density uses three significant
digits. The name comes from the input filename without its extension and is
preserved when saving and reloading. Reading a model does not print anything.

## Exact Frames and structural checks

The canonical tables are `m.rows`, `m.vars` and `m.terms()`. Rows store original
`lb/ub`; variables store `kind` (`C/I`), bounds and `obj`; Terms store nonzero
`row/var/coef` triples. `type` and `is_binary` are derived from the effective
integer domain. Duplicate terms sum exactly and cancelling terms disappear.

```python
from mipframe import col

r = m.rows.iloc[0]                 # Position -> Row
x = m.vars.by_name("x")           # Explicit name lookup
r.vars, x.rows                    # Entity sets, without coefficients
r.terms, x.terms                   # Terms, with typed endpoints and exact coefficients
m.rows.loc[r.id]                   # Declared key -> Row
m.rows.where(col("ub") <= 1)
m.rows.column("ub").argmax()       # Every tied maximizer

F0 = m.family("F0@2").rows
F1 = m.family("F1@2").rows
F2 = m.family("F2@2").rows
V2 = m.family("V2@1").vars
P = V2.project(i=F0, k=F1, j=F2)
P.check("product", ("i", "j")).require()
P.check("function", key="i", value="k").require()
A = m.terms(rows=F2, vars=V2).select("var", a=-col("coef"))
Q = P.join(A, on="var", cardinality="1:1", require="left")
S = Q.group_by("i").agg(
    distinct=col("a").nunique(), amin=col("a").min(), amax=col("a").max())
S.where(col("distinct") != 1)
```

Family labels depend on the model and signature configuration. `select()` and
`join()` return ordinary Frames; filtering, sorting and slicing preserve entity
or TermFrame types. Bare Frame `[]` is removed: use `.iloc`, `.loc`, `.family`,
`.where` or `.column` explicitly. `.ids` gives original entity IDs. Names never
act as relational identity. `head()` constructs a lazy view, while strict
projection and join validation always check their complete inputs.

`select("var", ...)` on Terms does **not** prove variable uniqueness. Join
`cardinality` validates uniqueness separately from `require`'s match coverage.
Reports retain complete violations and original declared domains; taking a
sample cannot turn partial Cartesian coverage into a full proof.

```python
C = m.matrix.nnz(rows=F0, vars=V2, axis="var")
rare = C.where(col("nnz") <= 2).refs("var")  # Includes zero-degree variables
patterns = V2.pattern_counts()
patterns.relations["members"]               # var Ref, pattern ID
patterns.relations["items"]                 # pattern ID, family, count
```

Frame numeric comparisons, grouping and aggregation remain exact. Use `Fraction`
or `Decimal` for noninteger constants; implicit finite floats are rejected.
`to_records()` explicitly produce detached records, with a default
128 MiB materialization limit. `iter_batches()` and atomic exact JSONL `write()`
stream larger exports. Records contain snapshot-qualified Ref values; bind them
explicitly to an open matching model to resume navigation. Live Frames, records
and iterators become invalid when any dependent model closes.

See [Frame contracts and migration](docs/FRAME.md) and the
[test10 notebook](examples/test10_structure_guide.ipynb).

## Inspect a large constraint

```python
r = m.rows.by_name("capacity_18392")    # Row object; lookup does not scan its terms
r                                  # Statistics only, never an implicit formula
r.terms.head(10)                    # Lazy TermFrame: row/var/coef
r.formula()                        # Largest |coef| first; at most 16 terms
r.formula(order="index")           # Variable ID order; only reads the prefix
r.formula(order="name")            # Variable name order
r.formula(max_terms=30)
m.rows.sample(3, random_state=42).formula(max_terms=8)  # formulas for multiple rows
```

`Row` exposes `nnz`, `lhs`, `rhs`, `sense`, `coef_min`, `coef_max`, `unique_coef`
and `var_types`. Its display contains bounds, nonzeros, participating variable
types, coefficient range and distinct coefficient count. Statistics are cached
after their first scan; no coefficient dictionary or full formula is constructed.
`loc` accepts an original integer row ID and respects frame selections; use
`by_name()` for names. Position access through `.iloc[]` returns a live row entity.

`terms` is a read-only `TermFrame` over sparse storage, supporting `head`, `tail`,
`sample`, `.iloc` slices and exact column operations. Terms are keyed by their
two original endpoints. Display settings also apply to term frames. Only `to_records()`
explicitly materializes all selected terms.

`formula()` returns a notebook-friendly string. Omitted terms appear as `...`
with `[16 of 103,824 terms shown]`; a preview with omissions is not the full
constraint. `abs_coef` and `name` scan the row using a bounded selection heap;
`index` reads only the requested prefix. Absolute-coefficient ties use variable
IDs. Bounds and coefficients remain exact, and empty rows display as `0 = ...`.

`RowFrame.formula()` works on samples, slices and family members, returning
one notebook-friendly string with row IDs and names. It preserves selection
order and previews at most `options.display.max_rows` rows, with 16 terms per
row by default. Only displayed rows are scanned. The existing
`.show(formula=True, ...)` also remains available.

## Local sparse matrices

```python
R = m.family("F0@2").rows
V = R.vars
S = m.matrix.view(rows=R, vars=V)
S                                 # Shape, nnz, density; never a dense preview
S.preview()                       # At most 8 rows × 12 vars, most active vars first
S.preview(choose_vars="first")
S.preview(choose_vars="largest_coef")
S.entries.head(20)                 # Lazy COO records: row, var, coef
S.row_nnz                         # Frame(row, nnz), including zeros
S.var_nnz                         # Frame(var, nnz), including zeros
A = S.to_scipy()                   # Explicit float64 CSR copy; needs mipframe[scipy]
```

`view()` accepts same-model `RowFrame` / `VarFrame` selections; an omitted axis or
`None` selects that complete axis. Selection order is preserved, with duplicate IDs
removed at their first occurrence. `S.rows.ids` and `S.vars.ids` map local
positions to original model IDs, including the row/column order of the SciPy copy.
Only original constraint coefficients belong to the matrix; objectives, bounds and
family normalization are excluded. `shape`, `nnz` and `density` describe the full
selection. Axis counts include selected rows/variables with zero local nonzeros.

Default display is statistics only and never materializes coefficients. Accurate
statistics may scan sparse indices on first access and are cached. `preview()`
returns notebook-friendly text, with exact coefficients, `.` for structural zeros
and explicit omission markers. Its default 8×12 limits are independent of global
frame display options; explicit nonnegative `max_rows` / `max_vars` change them.
It shows the first selected rows and ranks variables over **all selected rows**:
`most_active` uses nonzero counts, `largest_coef` uses maximum absolute coefficient,
and `first` preserves the selected variable order. Ties follow that order; inactive
columns fill remaining slots after active ones. Ranked variables may still be zero
in the first displayed rows.

`entries` is repeatably iterable and supports `len()`, `head()` and `to_records()`.
Rows follow the selected row order; entries within each row follow original variable
ID order. Records contain original names and exact `Fraction` coefficients.
`head(20)` limits sparse traversal to the requested prefix; display is bounded by
`options.display.max_rows`. Only `to_records()` collects the current entries
selection in full. All live views require an open model.

Install `pip install 'mipframe[scipy]'` for `to_scipy()`. Export directly constructs
the selected sparse matrix, with no dense intermediate or full-model export.
Ordinary float64 rounding is allowed; overflow or nonzero underflow to zero raises
`MIPFrameError`. The returned matrix is independent of the model and remains usable
after it closes. For other sparse formats use `A.tocoo()` or `A.tocsc()` explicitly.

## Custom structural queries in Python

Raw sparse access is public. `S.to_scipy()` return independent SciPy CSR
matrices with explicit float64 conversion. Inspect exact coefficients through
Terms; use these exports for NumPy, SciPy, sklearn or graph experimentation.

```python
S = m.matrix.view(rows=R, vars=R.vars)
A = S.to_scipy()
binary = A.copy()
binary.data[:] = 1
row_overlap = binary @ binary.T
row_ids, var_ids = S.rows.ids, S.vars.ids
```

The output axis order follows the selected Frames. Export rejects overflow,
nonfinite coefficients and nonzero underflow. No dense matrix is constructed.

## Exact structural algorithms

Algorithms return completed ordinary Frames and compose with the same query API:

```python
S = m.terms(rows=R, vars=V).select(set=col("row"), member=col("var"))
P = mf.ops.sets.pairs(S, relation="strict_subset")
D = mf.ops.sets.members(S, pairs=P, op="right_only")
C = mf.ops.sets.components(S)

closure = mf.ops.graph.reachability(E, include_self=True)
reduced = mf.ops.graph.transitive_reduction(E)
activity = m.matrix.activity(values, rows=R)
```

Support comparison does not compare coefficients. Graph facts do not prove a MIP
transformation valid. Calls complete fully or fail; output defaults to at most
1,000,000 rows (`max_output_rows=None` explicitly removes that limit). Exact
working data spills under `memory_mb`; optional `disk_mb` limits scratch storage.
Matrix input coverage is strict unless `missing="zero"` is requested.

See [algorithm contracts and examples](docs/OPS.md) and
[the executable closure/resource example](examples/structural_ops.py).

## Explicit graph and integer searches

`mf.alg` keeps candidate discovery separate from exhaustive enumeration:

```python
C = mf.alg.graph.clique_candidates(E, top=100, max_states=1_000_000)
C                              # clique, vertex
C.relations["summary"]         # clique, size, exact score
C.relations["search"]          # candidate mode, scope, stop reason and budgets

all_cliques = mf.alg.graph.maximal_cliques(E)
cycles = mf.alg.graph.simple_cycles(E, directed=False, max_length=9)
```

Candidate results contain verified objects but do not claim global top-k or
exhaustiveness. Exhaustive searches complete within their declared scope or raise
`SearchBudgetExceeded`. Both return ordinary Frames and use paged working storage.
`vertices=` declares a complete domain; `scope=` selects an induced subgraph.
Graph structure does not prove a MIP cut valid. Odd-cycle candidates, exact
whole-graph isomorphism classes and finite pure-integer enumeration share these
contracts. See [ALG.md](docs/ALG.md) and the executable
[discrete search example](examples/discrete_search.py).

## Structural roles and numerical observations

```python
m.row_families                         # Frame; template row depth 2
m.var_families                         # Frame; template variable depth 2
F = m.family("F0", depth=2)            # One-row Frame
F.rows                                # Original row members
F.rows.vars                           # Neighboring variables
F.inspect()                           # Bounded detail text
F.related("pattern")                  # Complete structural tokens and orientations
F.related("parameters").where(basis="canonical")
F.related("histogram").where(basis="raw")
m.rows.head(100).families()            # Global IDs, selected-member statistics
mf.analysis.signatures(m.rows, method="algebraic")
m.refinement(1, 2)                     # Parent/child transition Frame
```

A family directory and a single-family selection are ordinary Frames. Filters
also restrict related members and statistics; retain `family_key` when projecting
if you need later navigation. `count` is selected coverage, `total_count` is the
full-model population. A row at depth d references variable roles at d-1.

Families are model/configuration/depth-dependent observations. Template roles
use coefficient signs and multiplicity classes; exact mode uses normalized
rational values. A shared role does not prove a Cartesian product, symmetry,
constant numerical parameters or a reformulation. Use projections and reports to
check actual membership, and Terms to inspect original coefficients and bounds.

## Save, reload and export

```python
import mipframe as mf

with mf.read("model.lp") as model:
    model.save("saved_model")       # New directory; existing targets are rejected
    model.write("exported.lp")   # Exact LP export

with mf.load("saved_model") as model:
    print(model.verify()["status"])
```

`read()` owns a temporary disk-backed numerical store. `close()` releases file
handles and memory maps and removes that temporary workspace; using `with` is
recommended. `load()` opens a persistent store and never deletes it. `save()`
creates an independent snapshot containing numerical artifacts, refinement layers,
complete indexed results and any edit lineage. Native schema 3 rejects older
stores; reimport their LP/MPS sources.

## Examples and documentation

After installation, run either self-contained repository example:

```sh
python examples/basic_read.py
python examples/save_reload.py
```

- [Structure discovery walkthrough](examples/test10_structure_guide.ipynb): a worked
  Notebook on `data/test10.mps`, from family membership to verified coordinates
  and substitution cost.
- [User guide](docs/USER_GUIDE.md): objects, exact values, formats, persistence and analysis.
- [Frame queries](docs/FRAME.md) and [structural operations](docs/OPS.md).
- [Indexed coordinates](docs/INDEXED_IR.md) and [discrete searches](docs/ALG.md).
- [Architecture and recovery contracts](docs/ARCHITECTURE.md).

LP/MPS support covers linear continuous, integer and binary models, including
ranged constraints and infinite bounds. Unsupported nonlinear, SOS, indicator
and semicontinuous features are rejected rather than silently dropped. MPS
fixed/free records and vector-set selection are supported. The default
`mps_format="auto"` checks each complete record: solver padding does not force
long names or full-precision decimals into fixed-width fields. Explicit
`mps_format="fixed"` or `"free"` selects a layout. Only LP export is currently
available.

## Development

```sh
python -m pip install -e '.[test,scipy]'
python -m pytest -q
```

The move from the unpublished predecessor is intentionally breaking: use
`mipframe.read()`/`load()` and `Model`, not the previous import name or build/CLI
entry points. Numeric storage formats are versioned independently from the package.


### Signature-assisted indexed discovery

```python
I = m.indexed()  # explicit discovery; completed results are saved with the model
I.tensors                 # verified 2D coordinate views, including sparse grids
I.operators               # exact reduction / broadcast operators
I.verify().require()
m.ir.show()               # includes indexed summaries; use typed APIs for details
```

Structural signatures propose variable roles and row supports; exact membership and full-row coefficient
checks establish the result. Colors do not define axes: same-family candidates can still need bipartite
splitting. See [indexed views](docs/INDEXED_IR.md) for scope selection, raw-ID projections, exact kernels,
memory limits and result navigation.

### Exact expressions and certificates

```python
x = m.vars.by_name("x")
e = 2*x + mf.q("0.1")
claim = e <= 10                    # a proposition, not an established fact
report = m.check.linear(claim, using=certificate, residual="bounds")
report.require()
```

`LinExpr`, `Claim`, `BatchProof` and scalar `ProofRef` views support exact identities, explicit
linear certificates, conditional infeasibility, integer rounding, local integer
enumeration, and full-point feasibility. Conditions and integer-only scope persist
through dependent proofs. Diagnostic checks still return `Report`; full-point checks return `PointReport`. [API migration](docs/API_MIGRATION.md) ·
[runnable example](examples/exact_certificates.py).

### Immutable model edits

`m.edit()` stages exact source-relative changes and builds an independent snapshot.
Attach a verified `BatchProof` or `ProofRef` with `because=` to establish preserving changes; omitted
evidence remains explicitly unverified. Draft auxiliary variables, bulk Frame
inputs, grounded deletion certificates, native lineage and exact LP audits share
the same path. See [the Edit guide](docs/EDIT.md) and the runnable
[verified add/drop example](examples/exact_edits.py).


## Offline help for agents and interactive work

```python
import mipframe as mf
mf.help("recover coordinates from row families")
mf.help("recipe.coordinates", detail="full")
mf.help(mf.Frame.join)
# After catching an exception: mf.help(error)
```

`python -m mipframe help "linear certificate" --format json` uses the same
installed-version contracts. Help does not read model data or execute analysis.
See the [help guide](docs/HELP.md), [copyable agent skill](docs/agent/SKILL.md),
and [generated API reference](docs/agent/API.md). All examples state the scope of
their conclusions; candidate discovery does not grant proof or edit authority.

## Runtime v5 migration

See [architecture](docs/ARCHITECTURE.md), [implementation status and remaining work](docs/RUNTIME_V5_STATUS.md), and the deterministic replay scripts under `scripts/benchmark_runtime*.py`.

Measured test32/test33 results and reproducible commands: [runtime performance report](docs/PERFORMANCE_RUNTIME_20261005.md).
