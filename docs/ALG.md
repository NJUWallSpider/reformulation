# Explicit discrete searches

`mf.alg` searches for discrete structures over ordinary Frames. Every returned
object is verified. A **candidate search** may omit other objects and may miss the
best object; it does not return approximately correct cliques or cycles.
Graph adjacency alone does not establish integer conflict, a valid MIP inequality,
a fixing, a redundant row, or a model symmetry. Evidence and proof remain separate.

## Completion, scope and resources

| API | Search contract |
| --- | --- |
| `mf.alg.graph.clique_candidates` | Deterministic greedy maximal cliques; best found, not globally best |
| `mf.alg.graph.maximal_cliques` | Every inclusion-maximal clique meeting `min_size`, or an exception |
| `mf.alg.graph.odd_cycle_candidates` | Verified odd cycles meeting the requested lengths/chord condition |
| `mf.alg.graph.simple_cycles` | Every simple cycle in the declared direction/length scope, or an exception |
| `mf.alg.graph.isomorphism_classes` | Exact equivalence of whole declared graph instances, or an exception |
| `mf.alg.integer.enumerate` | All feasible assignments of an explicit finite integer system, or an exception |

Calls execute synchronously. Exhaustive searches default to `max_states=None`;
candidate searches default to `max_states=1_000_000`. All accept
`time_limit=None`, `memory_mb=128`, `max_output_rows=1_000_000`, and `disk_mb=None`.
Budgets and stops are independent:

- Candidate state/time exhaustion returns only completed, verified objects, with
  the stop reason visible in the Frame representation and `relations["search"]`.
  Even `heuristic_finished` does not claim exhaustive structure discovery.
- Exhaustive state/time exhaustion raises `mf.SearchBudgetExceeded`, with
  `algorithm`, `reason`, `states`, `max_states`, `time_limit`, and `scope`.
  No partial successful Frame is attached.
- Time limits are cooperative, measured from call setup through preparation,
  search and verification. Candidate inputs must finish validation before a
  budget stop can return a result. Publishing verified candidates after a stop
  may require additional time. Exhaustive deadlines also cover publication.
- Working memory has a 16 KiB minimum. Indexes, suspended search stacks, object
  members, deduplication and exact ranking use paged SQLite storage; it is not a
  process RSS limit. No optional graph or solver dependency is required.
- Output limits count **member plus summary/mapping rows**, excluding the one-row
  search metadata relation. They raise `QueryTooLarge`; they never truncate an
  object or return a partial exhaustive enumeration. `None` removes the limit.
- Disk limits, memory failures, and cancellation always fail the entire call.
  See [OPS.md](OPS.md) for disk accounting and platform limitations.

`max_states` counts deterministic expansion attempts: clique recursion states or
greedy vertex additions; cycle roots and considered adjacency transitions;
isomorphism refinement vertices and attempted vertex mappings; integer root and
attempted variable assignments. It is versioned with the algorithm, not a measure
of wall time. Input validation and individual SQL operations can do substantial
work between expansion checkpoints. Without a time limit, identical inputs and
parameters produce identical results.

`vertices=` declares the entire graph domain, preserving isolated vertices.
Missing declarations use column domains, then observed endpoints. Endpoints
outside the declared domain are errors. **`scope=` instead selects an induced
subgraph**; paths and cliques cannot use vertices outside it. Both accept an entity
Frame or a single-column Frame of the matching scalar/Ref type. No finite float
scores are implicitly converted: provide exact `Fraction`/`Decimal` values.

## Cliques and normalized results

```python
import mipframe as mf
from mipframe import col

# E has typed u/v endpoints; frac_vars selects the induced search support.
# lp_values has var/value columns, and values have been converted explicitly.
C = mf.alg.graph.clique_candidates(
    E, scope=frac_vars,
    score=lp_values.select(vertex=col("var"), value=col("value")),
    top=100,
)
C                             # clique, vertex
C.relations["summary"]        # clique, size, score
C.relations["search"]         # mode, stop reason, scope, budgets, counts, ranking
```

`u="u"`, `v="v"`, `min_size=2` are defaults. Cliques use simple undirected
adjacency. Parallel and reversed edges are deduplicated. Self-loops do not provide
adjacency; their count is recorded in search details. `min_size=1` includes
isolated singleton maximal cliques. No empty clique is emitted.

Candidate cliques are maximal **within the selected induced graph**, not
necessarily the original full graph. Roots and extensions use exact score, degree,
then original key. Completed objects are deduplicated using full canonical member
sequences; hashes are only a filter. `maximal_cliques` uses paged pivoted
backtracking and has no `top` parameter. Maximal means inclusion-maximal, not
maximum cardinality or maximum weight.

Optional `score` must contain unique `vertex` keys and finite, nonmissing exact
values in `value="value"`. It must cover the selected scope; extra vertices within
the full declared domain are allowed. Object score is the sum of its vertex
values, or cardinality when scores are absent. Negative scores are allowed;
maximal-clique search does not optimize over nonmaximal cliques. Ranking is by
score descending, then canonical vertex sequence ascending.

`top=100` limits retained **objects**, not membership rows and not search work.
`top=0` retains none. IDs are local positive integers assigned after ranking;
they are not stable cross-run identities. `head()` retains ordinary Frame row
semantics. Select the top ten whole objects with:

```python
best = C.relations["summary"].head(10)
members = C.join(best.select("clique"), on="clique", cardinality="m:1")
```

The original edge evidence is not copied into every object. Join member pairs to
source edge/witness relations when it is needed. A score above 1 is only a numeric
observation until an independent checker establishes the corresponding MIP facts.

## Cycles

```python
Y = mf.alg.graph.odd_cycle_candidates(
    E, lengths=(5, 7, 9), chordless=True, top=100,
)
Y                             # cycle, position, vertex
Y.relations["summary"]        # cycle, length, score

all_cycles = mf.alg.graph.simple_cycles(
    E, directed=False, min_length=3, max_length=9,
)
```

Odd-cycle candidates use undirected adjacency. `lengths` must be a nonempty
sequence of odd integers >= 3. Scoring and `top` follow the clique contract.
`chordless=True` rejects any edge between nonconsecutive cycle vertices.

`simple_cycles` requires an explicit `directed` boolean. Undirected cycles have
minimum length 3; directed cycles default to minimum length 1 and include self
loops and reciprocal 2-cycles. `max_length=None` imposes no length ceiling.
`chordless=True` is supported only for undirected input. Restricting lengths or
scope limits the exhaustive claim to those declared conditions.

Cycles have distinct vertices; positions start at zero, with no duplicated closing
vertex. Rotate to start at the smallest key. Undirected reversal is equivalent,
so retain the lexicographically smaller direction. Directed reversal is a different
cycle. Candidate discovery uses bounded path search with stable score-based
neighbor ordering, not a claim of top-k global optimality or cut violation.

## Exact whole-graph isomorphism

```python
K = mf.alg.graph.isomorphism_classes(
    E, graph="graph", u="u", v="v",
    vertices=V,                 # graph, vertex, optional scalar label columns
    graphs=G,                   # optional one-column domain, including empty graphs
    directed=False,
    vertex_labels=("color",),
    edge_labels=("weight",),
)
K                             # graph, class
K.relations["mapping"]        # graph, vertex, representative_vertex
```

Vertices are declared per graph; an edge cannot introduce a vertex, even if that
key exists in another graph. Omit label arguments for unlabeled comparison. Graph
keys and vertex keys have independent, consistent scalar types. Labels are exact
scalar values; `None` is a meaningful label. Vertex IDs identify members but are
not themselves labels. `graph` may be renamed, but cannot use the reserved result
names `class`, `vertex`, or `representative_vertex`.

This API compares whole simple graphs, including loops and isolated vertices.
Duplicate endpoints must have identical labels; conflicting labels raise a
pageable `CardinalityError` report. The representative is the smallest graph key;
each mapping is a complete label- and adjacency-preserving bijection to that
representative. Joint color refinement and fingerprint buckets prune comparison;
full feature comparisons resolve collisions, and exact backtracking verifies the
bijection. Stable colors alone never prove isomorphism. Budget exhaustion raises
an error rather than assigning unfinished comparisons to separate classes.

This is not subgraph matching, enumeration of all automorphisms, or a MIP symmetry
certificate. Model domains, objectives, boundaries and all relevant labels would
need independent interpretation and verification.

## Finite integer systems

```python
A = mf.alg.integer.enumerate(
    variables,                 # var, lb, ub
    terms,                     # row, var, coef
    rows,                      # row, lb, ub
    max_states=100_000,
)
A                             # assignment, var, value
A.relations["summary"]        # assignment, including an empty assignment
```

These inputs explicitly declare a **pure integer** linear system; the function
does not infer integrality from a MIP or silently eliminate continuous variables.
Variable bounds must be finite exact values. Their effective domains are
`ceil(lb)..floor(ub)`; an empty domain means zero feasible assignments. Row bounds
may be infinite. Coefficients must be finite exact values. Keys are unique and
term endpoints must occur in their respective declared domains.

Enumeration follows variable key order and ascending integer values. Explicit
paged backtracking uses exact remaining-activity interval pruning and verifies all
constraints at each complete assignment. No objective function is used. Missing
terms are structural zeros. The zero-variable system has one empty assignment if
all empty rows are satisfied, represented in `relations["summary"]`; otherwise it
has none. There is no implicit top-k, continuous projection, or solver fallback.

## Composition, provenance and lifecycle

The complete public example is [discrete_search.py](../examples/discrete_search.py).
It builds conflict edges and witnesses with set primitives, then searches for
cliques. The test suite also covers closure → resource aggregation → candidate
search. Synthetic fixtures represent the suggested test15/17/18/33 structures;
those model files are not present, so no real-model acceptance is claimed.

Every result and attached relation is an ordinary Frame. Search provenance stays
visible through selection, filtering, joins and aggregation, and is included in
exact JSONL/Parquet schema metadata. Imported provenance is descriptive; it never
grants a uniqueness fact, a search-completeness proof or a MIP certificate.

`to_spec()` records the versioned search recipe, including input plans, scope and
budgets. Restore with explicit `models`/`relations` bindings reruns validation and
search; it does not trust a supplied completion flag. Time-limited candidates need
not reproduce the same objects. Model-dependent results and paused iterators fail
after any dependency closes; detached records remain readable. Results and derived
views share temporary ownership. No `mf.alg` searches run during model read/load or display (default IR/indexed preparation remains separate),
and arbitrary search results are not added to native model artifacts by `save()`.
