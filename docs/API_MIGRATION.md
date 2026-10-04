# Public API simplification

This unreleased API changes Python entry points without changing the underlying mathematical algorithms or requiring LP/MPS re-import. Existing native v3 models and indexed caches remain readable.

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
