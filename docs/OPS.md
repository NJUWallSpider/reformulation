# Exact structural algorithms

`mf.ops` computes complete mathematical facts over Frames. Calls finish before
returning a normal Frame, or raise an exception. They do not generate partial
successful results, infer business meaning, prove a MIP transformation valid, or
modify the model. Explicit candidate and exhaustive searches live in [`mf.alg`](ALG.md).

## Shared contracts

All new algorithms accept `memory_mb=128`, `max_output_rows=1_000_000`, and
`disk_mb=None`. The minimum working budget is 16 KiB. Large indexes, queues and
intermediate relations spill to SQLite; this budget is not a process RSS limit.
`max_output_rows=None` explicitly removes the output limit. Exceeding the limit
raises `mf.QueryTooLarge`, with `limit` and the known lower bound `count`; it does
not return a truncated Frame. A disk quota failure raises `mf.DiskBudgetExceeded`.
There is no default time limit or candidate cutoff. Disk accounting includes
SQLite sorter files that have already been unlinked, using file descriptors on
Linux/macOS. Platforms without descriptor enumeration reject an explicit
`disk_mb` rather than silently ignoring it. Accounting is conservative if unrelated
new scratch files are opened concurrently in the same process.

Input identity types must agree, including Ref snapshot uid/version and axis.
Missing identities are rejected. A domain is an entity Frame or single-column
Frame. Explicit domains take precedence; otherwise column domains are used, then
observed keys. Declared empty sets and isolated vertices remain present. Inputs
outside a declared domain raise an error rather than expanding the domain.

TermFrame declares its row and variable selections as domains. Direct projection
and renaming preserve them. For arbitrary membership Frames, supply `sets=` or
`vertices=` when unobserved members matter. Group-by continues to return only
observed groups.

The results support ordinary Frame composition, `.refs()`, exact export and
`.to_spec()`. Restoring a specification requires explicit model/relation bindings
and reruns validation and computation. `explain()` describes the completed
algorithm and access path. Model-dependent results, including cached results and
iterators, become invalid when a dependency closes. Detached records survive.
Results are not automatically included in `Model.save()`; use `Frame.write()`.

## Sets: supports, differences and exact equivalence

A set system is a unique `(set, member)` relation. Duplicate memberships are an
error even if their values agree. Use an explicit `distinct()` when duplicates
are intentionally irrelevant. Violations are available through
`CardinalityError.report.violations`.

```python
import mipframe as mf
from mipframe import col

S = m.terms(rows=R, vars=V).select(set=col("row"), member=col("var"))
P = mf.ops.sets.pairs(S, relation="strict_subset")
D = mf.ops.sets.members(S, pairs=P, op="right_only")
```

`pairs` supports `equal`, `subset`, `strict_subset`, `superset`,
`strict_superset`, `overlap`, and `disjoint`. It returns:

```text
left right left_n right_n intersection left_only right_only union_n
```

`left=` and `right=` restrict domains. `candidates=` instead supplies an explicit,
unique `left/right` Frame; it cannot be combined with left/right selections.
Candidates retain their direction. Self-pairs are excluded unless
`include_self=True`.

For symmetric relations over identical domains, the default is one unordered
pair, with the smaller key on the left. Different domains preserve left/right
roles. `ordered=True` keeps both directions for equal domains. Unordered mode is
rejected for containment relations or unequal domains.

`members` explicitly expands only the supplied pairs. Its `op` is one of
`intersection`, `union`, `left_only`, `right_only`; output columns are
`left, right, member`. It computes members from the original set system, not from
any supplied summary counts. Output limits also apply to this expansion.

Support equality does not compare coefficients or row bounds:

```python
support_classes = mf.ops.sets.equal_groups(
    m.terms(rows=R), set="row", member="var",
)
coefficient_classes = mf.ops.sets.equal_groups(
    m.terms(rows=R), set="row", member="var", value="coef",
)
```

Both return the input set column and `class`. Class identifiers are the smallest
original keys. Hashes select buckets; sorted members/values are compared exactly
before declaring equality. Row bounds remain a separate fact.

## Hypergraph and graph structure

```python
C = mf.ops.sets.components(S, vertices=V)
# member, component; no pairwise expansion of a large hyperedge
```

The member column retains the name passed via `member=`. Empty/isolated members
from its declared domain have their own component. Components use the smallest
original member as representative.

Graph functions consume compatible endpoint columns from an ordinary Frame:

```python
C = mf.ops.graph.components(E, u="u", v="v", vertices=V)
S = mf.ops.graph.scc(E, tail="u", head="v", vertices=V)
O = mf.ops.graph.topological_order(E, vertices=V)
R = mf.ops.graph.reachability(E, sources=A, targets=B, include_self=True)
reduced = mf.ops.graph.transitive_reduction(E)
path = mf.ops.graph.path(reduced, source=u, target=v)
```

| Function | Output | Meaning |
|---|---|---|
| components | vertex, component | Undirected endpoint connectivity |
| scc | vertex, scc | Directed strong connectivity |
| topological_order | vertex, order, level | Stable zero-based order; longest predecessor depth |
| reachability | source, target | Unique reachable endpoint pairs |
| transitive_reduction | Original input schema | Original records for retained endpoint pairs |
| path | step, u, v | Stable shortest path by number of edges |

Sources and targets limit endpoints, not intermediate vertices.
`include_self=False` excludes every diagonal result even inside a cycle.
`include_self=True` adds the selected vertices' own pairs.

Topological ordering and transitive reduction require a DAG, including absence
of self-loops. `GraphCycleError` / `NotDAGError` carry complete cyclic-member
reports. `error.report.violations.relations["witness"]` contains a concrete cycle
for each cyclic component.

Duplicate input edges are deduplicated for graph computation. Transitive
reduction retains **all original records** for each necessary endpoint relation,
including extra fields and parallel sources, in input order. It preserves
simple-graph reachability; it does not prove each retained MIP row indispensable.

An unreachable path is empty. A path to the same vertex is also empty but
reachable with length zero. Inspect `path.relations["status"]` to distinguish
these cases. It contains `source, target, reachable, length`.

## Exact resource aggregation

```python
A = mf.ops.sets.aggregate(
    membership, values,
    set="set", member="member", by="resource", value="coef",
)
U = mf.ops.sets.pair_aggregate(
    membership, pairs, values,
    set="set", member="member", by="resource", value="coef", op="union",
)
```

This release supports `agg="sum"` only. Attributes must be unique on
`(member, *by)` and contain finite exact values. Missing attribute records are
sparse zero; explicit None is an error. `by` accepts one column or a tuple.

Output is the set key (or left/right), grouping columns, and `value`.
With `by=()`, every declared set or candidate pair has an output, including zero
for an empty sum. With grouping columns, only groups associated with resulting
members appear. An observed group's exact cancellation to zero remains a record.

Pair aggregation uses per-set sums and pair intersections, including presence
counts, rather than constructing every union's members. The identity
`sum(A union B) = sum(A) + sum(B) - sum(A intersection B)` prevents double
counting shared descendants. Negative and fractional weights stay exact.

```python
closure = mf.ops.graph.reachability(E, include_self=True)
membership = closure.select(set=col("source"), member=col("target"))
usage = mf.ops.sets.pair_aggregate(
    membership, pairs, resource_use, by="resource", value="coef",
)
conflicts = usage.join(
    capacities, on="resource", cardinality="m:1", require="left",
).where(col("value") > col("capacity"))
```

Whether these graph/resource facts imply a valid MIP inequality requires separate
mathematical assumptions and checks.

## Entire grouped multisets

```python
classes = mf.ops.equal_groups(
    canonical, by="unit", rows=("role", "time", "coef", "rhs"), groups=units,
)
```

Groups are compared as multisets of the specified payload columns. Row order is
irrelevant; repetition is significant. None is a comparable missing payload
marker. The `groups` domain includes empty groups. The output is the original
`by` column and its minimum-key `class`. This does not discover a variable
renaming, canonical coordinate system, scaling or symmetry.

## Sparse Ax and A-transpose-y

```python
activity = m.matrix.activity(values, rows=R)       # row, activity
combined = m.matrix.transpose_activity(y, vars=V) # var, value
```

Inputs contain a unique `var` or `row` Ref and a numeric `value` column (rename via
`value=`). Multiplication uses the original matrix, exact Fractions and selected
axis order. Zero rows/columns remain in the output.

By default all values required by the selected matrix support must be present.
Missing entries produce a complete report; unrelated valid inputs are allowed.
Use `missing="zero"` to explicitly supply a sparse vector. Neither objective nor
bounds participate, and there is no implicit floating-point conversion.

## Local acceptance runs

`python scripts/evaluate_ops.py test9`, `test10`, or `test11` checks the available
real fixtures using public APIs. The test9 oracle does not assume an unprovided
coupling-row scope. Test10 compares support and coefficients separately and
rechecks all 190000 coordinates. Test11 cross-checks grouped vectors and set maps;
it does not claim automatic symmetry discovery. Missing test12/31/33 source files
are covered by synthetic containment, network and closure/resource tests.
