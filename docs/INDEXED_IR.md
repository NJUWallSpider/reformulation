# Indexed coordinates and operators as Frames

`I = m.indexed()` retrieves a completed analysis bound to a snapshot, structural configuration and declared scope. Read prepares the default run; native load restores its saved cache. Display does not start another search.

```python
I = m.indexed()
T = I.tensors.head(1)
T.vars
T.related("axes").where(axis=0).rows
T.related("coordinates").check("product", ("i", "j")).require()

O = I.operators.where(mf.col("row_count") > 100)
O.rows
O.related("kernel")
O.related("bindings")
I.verify(O).require()
print(O.inspect())
```

`I.tensors`, `I.operators`, `I.partitions` and `I.evidence` are ordinary Frames. The singular methods `tensor(id)`, `operator(id)`, `partition(id)` and `evidence_item(id)` return exactly one-row Frames and reject unknown IDs. Read scalar metadata through `one()`, `loc` or iteration. Keep the Frame for relation navigation.

Counts use `row_count` and `var_count`; `.rows/.vars` expose declared members. `Frame.shape` remains the table dimensions. Indexed dimensions live in `related("dimensions")`, with `component` (`shape` or `kernel_shape`), `axis` and `size`.

| Directory | Members and relations |
| --- | --- |
| tensors | `.vars`; `axes`, `axis_members`, `coordinates`, `dimensions`, `partitions` |
| operators | `.rows`; `bindings`, `kernel`, `alignment`, `dimensions`, `ast`, `statistics`, `tensor` |
| partitions | `.rows`, `.vars`; `members`, `origins` |
| evidence | `details` |

All relation records retain their parent `indexed_key`. It binds snapshot identity, version, run, category and original indexed ID. Axes additionally use `axis`. Filtering, sorting, slicing, direct renaming and key-preserving projection restrict navigation; dropping the linking keys removes it. Aggregation removes navigation. A join retains valid left-side navigation, and repeated parent keys do not duplicate related results. Empty selections retain the correct relation schema.

Tensor axes are complete ordered row domains; `axes.where(axis=0).rows` selects the first axis. Coordinates retain typed `var`, `i`, `j` references plus positional `axis0/axis1`. A product check uses the declared domains, including unobserved cells. An operator's adjacent variables are `O.rows.vars`; they are not a separate declared member axis.

Bindings retain original rows, anchors, scale, lower and upper bounds. Kernel values remain exact Fractions; omitted sparse entries are implicit zeros. Invariant kernels have nullable `k`. Alignment is a separate exact observation, not a symmetry certificate.

AST, origins and diagnostic details are lossless scalar node tables: `path`, `node_type`, `text`, `number`, `boolean`. Paths use JSON Pointer escaping, containers have their own nodes, array order is explicit in path indices, and parents precede children. Thus empty containers, null, booleans and precise numbers remain distinguishable. `statistics` uses the same representation with numeric observations converted to exact numeric columns. Formula is display text (`O.one().formula`), not the machine definition.

## Verification

`I.verify()` replays the prepared tensors and operators. `I.verify(selection)` checks exactly a directory selection, including partitions; evidence cannot be verified as a proposition. An empty selection is PROVED with `checked=0`, and does not verify any other items. Foreign snapshots, runs and projections without identity navigation are rejected.

`summary["checked"]` and `summary["failures"]` count primary checked items, regardless of how many diagnostics one item produces. `evidence["checks"]` lists primary checks; `evidence["details"]` includes subordinate diagnostic rows. REFUTED takes precedence over INCONCLUSIVE; inconclusive or skipped checks never become an overall PROVED result.

`I.validate(spec)` retains independent hypotheses for Cartesian coordinates, partitions, row bundles, operators and alignment. Candidate records and Frame relations are accepted; supplying an existing directory identity never bypasses the exact checker. For example:

```python
O = I.operators.head(1)
I.validate({"kind": "indexed_operator", "id": O.one().id,
            "kernel": O.related("kernel"),
            "rows": O.related("bindings"),
            "ast": O.related("ast")}).require()
```

Both methods return `Report`, as does `m.verify()`. The latter checks numerical storage and the runs prepared when it was called. Failed assertions return REFUTED with pageable violations; `.require()` raises `ValidationError`. A corrupt base skips dependent indexed replay. Bad arguments, closed models and execution failures still raise directly.

Integrity/indexed scope is distinct from a mathematical Claim: these reports do not establish MIP feasibility, optimality or a valid reformulation, and cannot serve as linear certificates. Exported recipes replay checks against their bound snapshot rather than trusting saved verdicts.

## Algorithms, persistence and resources

Candidate discovery is unchanged: homogeneous type/direction/unit seeds, all requested structural levels, role direction groups, and exact bipartite components. Only observed adjacencies are considered; discovery is not exhaustive. Exact operator checks expand every original row and compare coefficients and bounds.

Default structural depth is 2 with `method="template"`; explicit exact configuration and custom row/variable scopes remain available. Working buffers spill to disk using the run's memory budget. Full record export uses the separate Frame materialization budget.

Views and diagnostic reports share model-owned storage. Creating thousands of views does not create thousands of database connections. Dropped diagnostic results release their rows; related evidence keeps a result alive when needed. Closing the model invalidates every live view. Detached records remain independent.

Native v3 and existing `indexing.sqlite` caches retain their algorithms, original IDs and storage format. Public field normalization happens at the read boundary. New directory/relation query specifications bind the saved run and can be restored without manual relation bindings; legacy explicit relation bindings remain supported.

## Small partitions

`m.indexed()` defaults to `include_singletons=False`: partitions with only one
row set are skipped during discovery, before allocating per-component artifacts.
They cannot participate in tensor pairing, so tensor/operator discovery and exact
verification are unchanged. Use `m.indexed(include_singletons=True)` for the full
partition directory. The option has a separate cache identity and survives native
save/load. Full-mode component arrays and metadata use compact shared storage.
