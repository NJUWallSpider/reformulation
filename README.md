# lpir — exact, queryable compressed IR for linear LP models

`lpir` builds a persistent numerical model, finds parameterized structural views, and verifies that its compressed representation reconstructs the **entire original model exactly**. It is an analysis substrate for an agent, not an online agent or a presolver: it does not delete constraints, fix variables, optimize a model, or generate a strengthened formulation.

The first fixture is `test1/test1.lp`. Existing files under `test1/analysis` are unchanged and serve as an independent numerical reference.

## Install and run

Python 3.11+ and NumPy are required. Solver libraries are not required.

```sh
python -m pip install -e '.[test]'
lpir build test1/test1.lp --out /tmp/lpir-test1-new
lpir overview test1/mvp_ir
lpir render test1/mvp_ir --format markdown
lpir verify test1/mvp_ir
lpir export test1/mvp_ir --out /tmp/reconstructed.lp
pytest -q
```

`build` requires an absent or empty output directory; it never overwrites an existing artifact. A failed build can leave an incomplete directory, which must not be used as an artifact. Choose another output directory to retry.

On the current development machine, source execution without installing dependencies is also available:

```sh
PYTHONPATH=src /Users/bhz/miniforge3/envs/pdbo/bin/python -m lpir overview test1/mvp_ir
PYTHONPATH=src /Users/bhz/miniforge3/envs/pdbo/bin/python -m pytest -q
```

## Python interface

```python
from lpir import IR

ir = IR.build("problem.lp", "artifact")  # once
ir = IR("artifact")                     # reopen without parsing the LP
print(ir.overview())
print(ir.inspect("row", "c276"))
print(ir.inspect("assignment", "assignment_0", limit=5))
print(ir.inspect("parameter", "q", limit=5))
print(ir.query("external", "assignment_0", limit=5))

result = ir.query("filter", "temporal", "L[source] + q > U[target]", limit=5)
page_two = ir.query(handle=result["handle"], offset=5, limit=5)
print(result["total"])

# all_satisfied + exact counterexamples, not approximate symbolic inference
print(ir.query("relation", "temporal", "q == cost_switch + cost_arc"))
print(ir.verify())
ir.export("reconstructed.lp")
```

Variable and row IDs are internal stable IDs within one artifact, **not** numbers extracted from the input names. Names can be used instead of IDs. Names are checked before interpreting a string as an ID. Assignment orientations are arbitrary: a reordered model may swap the two bipartite sides. Test equivalence using the bindings, not raw internal IDs.

### Bounded query protocol

```sh
lpir inspect ARTIFACT row --key c0 --limit 5
lpir inspect ARTIFACT variable --key x0
lpir inspect ARTIFACT template
lpir inspect ARTIFACT template --key family_0 --limit 5
lpir inspect ARTIFACT assignment --key assignment_0 --limit 5
lpir inspect ARTIFACT parameter --key L
lpir inspect ARTIFACT residual
lpir inspect ARTIFACT exceptions
lpir query ARTIFACT neighbors --key variable:x0
lpir query ARTIFACT external --key assignment_0
lpir query ARTIFACT filter --key temporal --where 'L[source] + q > U[target]'
lpir query ARTIFACT relation --key temporal --where 'q == cost_switch + cost_arc'
lpir query ARTIFACT --handle 'HANDLE_FROM_PREVIOUS_RESULT' --offset 20
```

Inspection and query results include a model version, exactness, scope, total count, page limit, next offset, and truncation flag. Default page size is 20; maximum is 1,000. Large query result sets receive replayable handles containing a query specification, not all matches. Reopening a handle scans the same exact scope; handles are not materialized result caches. Template previews cap nested bindings and coefficient slots at 20; the returned original row ID allows full paginated expansion via `inspect row`.

Relations are interpreted by a restricted AST walker, never `eval`. Supported operations are addition, subtraction, unary signs, comparisons, `and`, `or`, `not`, and `L[source]` / `U[target]` indexed parameter joins. Available scalar names are `q`, `M`, `source`, `target`, `cost_switch`, `cost_arc`. Use integers or **quoted decimal strings** for exact numeric constants, e.g. `q > "10.25"`; floating Python literals are rejected. Calls, attributes, multiplication, comprehensions, and arbitrary code are rejected. Missing or infinite bounds produce an explicit error instead of a guessed comparison.

`filter` returns matches. `relation` returns counterexamples and reports `checked`, `passed`, and `all_satisfied`. Empty relation scopes are explicitly visible through `checked=0`. For a general temporal candidate, a time-window inequality by itself is not an arc-fixing proof: inspect `assignment_verified` and all other prerequisites before applying any reduction. `lpir` performs no such reduction.

`external` returns every constraint outside the chosen primary template or assignment block incident to its variables. It also reports how many block variables carry objective coefficients. This deliberately retains non-template couplings.

## Representation and algorithms

1. **Incremental parsing:** buffer one logical constraint or the objective rather than the complete input text. Combine repeated terms with exact arithmetic and remove only exact zeros. Preserve objective constants, variable domains, all bounds, and input line provenance.
2. **Persistent numerical layer:** NumPy CSR arrays store column and exact-coefficient IDs. A CSC-position index supports variable-to-row queries without duplicating coefficients. SQLite stores names, domains, row bounds, and canonical finite decimal coefficients `(integer mantissa, decimal exponent)` plus approximate numeric values.
3. **Candidate discovery:** structural row fingerprints include sense, length, variable types, signs, and coefficient multiplicities. Three bipartite color-refinement rounds generate role labels; exact numerical labels are retained separately. Colors are not automorphism or interchangeability certificates.
4. **Index recovery:** exactly-one constraints are connected by shared variables; degree checks and bipartite BFS recover assignment incidence, missing index pairs, and parallel pairs. Names, adjacency in the LP, dimensions, and numeric name suffixes are not used.
5. **Time templates:** identify `-t_source + t_target + q*switch - M*arc >= q-M`, check the RHS **exactly**, and join source/target switches to assignment endpoints where this is unambiguous. `M` is extracted, not assumed to be 60,000. Singleton constraints provide derived time bounds without changing the original variables or rows.
6. **Restricted anti-unification:** align generic linear terms by type/sign/unit/coefficient roles; common coefficients and bounds become constants, differences become parameter slots. Time templates additionally factor the exact sum/difference relationship in the RHS. This is a bounded template vocabulary, not unrestricted symbolic regression.
7. **Greedy encoding selection:** select non-overlapping row families using an estimated fixed-width encoding cost that includes bindings, parameters, and template overhead. Unsupported or unprofitable rows remain residual. Structural overlays can overlap independently of primary encoding.
8. **Verification:** independently expand primary templates and residual rows and compare all coefficients and bounds to the numerical layer. Compare objective sense/constant, every objective coefficient, variable type and bound, and provenance. Base storage checksums detect corruption of shared numerical dictionaries and arrays. The checker does not rely on Python assertions and works with `python -O`.

No approximate equality is substituted. In `test1`, the apparent `q = cost_switch + cost_arc` relation has exact counterexamples and remains a query result, not a rewriting rule.

### Artifact layout

- `manifest.json`: source digest, schema version, metrics, checksums and artifact sizes.
- `metadata.sqlite`, `*.npy`: exact numerical model and sparse access indexes.
- `compressed.json`: complete domain/objective snapshot, parameterized families, index bindings, exceptions and residual rows.
- `structures.json`: structural overlays, recovered assignment indices, temporal joins, exact derived time bounds and role statistics.
- `verification.json`: exact reconstruction result.
- `summary.md`, `acceptance.json`: delivered `test1` summary and acceptance measurements.

The artifact intentionally retains the numerical model **and** analysis views. Total disk size may exceed the original LP; the MVP aims to compress the mathematical presentation and repeated template encoding, not to beat a general-purpose file compressor. Estimated encoding savings are labeled estimates, distinct from measured JSON/disk bytes. A self-consistency/round-trip check is not a formal proof of the parser implementation; parser fidelity is also tested against the existing independent parser and optional solver import during development.

## Supported LP subset

Supported section headers: common `Minimize`/`Maximize`, `Subject To`/`Such That`/`st`/`s.t.`, `Bounds`, `Binaries`/`Binary`, `Generals`/`Integers`, and `End` aliases. Headers appear on their own lines. `End` is required. Backslash starts a line comment.

Names match `[A-Za-z_][A-Za-z0-9_.\[\],]*`; names are case sensitive, section headers are not. Constraint labels are optional; duplicate row labels are rejected. Expressions support optional signed coefficients, implicit unit coefficients, decimal/scientific notation, and constants. Products, powers, parentheses, quoted names, SOS, indicator and semi-continuous declarations are not supported and cause errors with source locations. MPS is not supported in this version.

Constraints support `=`, `<=`, `>=`, and finite constant-ended ranges in either direction. A row can span lines before the comparison or after a trailing comparison/sign. A numeric RHS completes the logical row; do not continue a completed RHS on another line. Bounds are one declaration per line and support equality, one/two-sided bounds, `free`, and the usual `-inf`/`+inf` endpoints. Infinity cannot be used as an ordinary row coefficient or variable name.

Default continuous/integer bounds are `[0,+inf)`. Binary domains are intersected with `[0,1]`; explicit empty domains are retained rather than silently repaired. Repeated bound declarations replace the explicitly declared side. Repeated objective/constraint sections are rejected. The supported subset and semantics are intentionally narrower than every solver's LP dialect.

## Validation and scaling limits

Tests cover exact agreement with `test1/analysis/model_io.py`, lossless export/reparse, renamed and reordered models with known mappings, tiny RHS perturbations, changed objectives, external coupling, sparse/parallel assignment incidence, non-bipartite rejection, residual constraints, corruption, query safety, default bounds, constants and parser errors.

Generate a larger *disjoint-copy* fixture without running the analysis:

```sh
lpir generate test1/mvp_ir --copies 10 --out /tmp/test1_10copies.lp
lpir generate test1/mvp_ir --target-mb 110 --out /tmp/test1_large.lp
```

Only small generator fixtures are exercised in this MVP. **No 100MB+ run has been performed.** Repeated disjoint blocks do not represent arbitrary dense, irregular, or highly coupled large models.

Parsing avoids loading the full source, and matrix arrays are memory mapped. However, variable/name dictionaries, row metadata, the largest logical expression, color-refinement neighborhoods, and the JSON analysis views still scale with model size and are held in memory. This is not yet a fully external-memory analyzer. Expensive general graph isomorphism, online AI orchestration, arbitrary parameter synthesis, model transformations, and solver benchmarking are outside this version.
