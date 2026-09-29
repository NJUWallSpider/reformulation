# LPIR

LPIR turns linear LP/MPS models into exact numerical data, shared variable
coordinates, parameterized constraint families and queryable structural evidence.
It retains original coefficients and row provenance. It does not rewrite the
model, infer application semantics or claim symmetry from matching fingerprints.

## Quick start

```sh
python -m pip install -e '.[test]'
lpir build data/test1.lp --out /tmp/test1-ir
lpir render /tmp/test1-ir
lpir verify /tmp/test1-ir
```

`build` writes `summary.md` and `summary.json` inside the output directory.
Give an LLM `summary.md` as its initial model context and access to LPIR's
`inspect`/`query`/`validate` tools for details. `summary.json` is the same
structured document used to render that Markdown, suitable for programmatic
consumers. Neither snapshot replaces the complete numerical and relational IR.
The default Markdown budget is 20 KB; pagination and incomplete analysis remain
explicit. After additional analysis, use `describe()`/`render()` for current
views; the files written by `build` are build-time snapshots.

```python
from lpir import IR

with IR("evaluation/test1/ir") as ir:
    print(ir.render())
    families = ir.inspect("structure_families", limit=20)
    result = ir.query({"table": "structure_objective"}, limit=10)
    checked = ir.verify()
```

The repository keeps one evaluated set in [`evaluation`](evaluation/report.md).
[`evaluation/SUMMARIES.md`](evaluation/SUMMARIES.md) links all eight current summaries.
`data` contains only the eight original `.lp` files. Each `evaluation/<case>`
contains `ir/`, `summary.md`, `summary.json` and `report.json`.
Public benchmark inputs remain in `benchmarks/public`; reference literature is
in `literature`.

## Current interfaces

- `IR.build(source, output, **options)` creates a new numerical IR and attempts
  bounded formula, indexed-coordinate and structural analysis.
- `overview()`, `describe()` and `render()` return model statistics, machine data
  and human Markdown. Markdown is a reading view, not a machine serialization.
- `inspect(kind, key=None, offset=0, limit=20)` reads rows, variables, formula
  families, indexed diagnostics and `structure_*` relations.
- `query(spec, ...)` executes a restricted JSON relational AST. `query(handle=...)`
  pages a materialized result. `neighbors` and `external` provide incidence queries.
- `analyze(kind, scope=None, budget=None, handle=None, ...)` performs resumable
  discovery. `validate(spec, budget=None)` checks a hypothesis; `verify()` checks
  numerical files and published structural views.
- `export(path, **options)` writes the exact numerical model as LP.
- `probe(seconds=30, memory_mb=256)` runs the optional continuous LP relaxation;
  its results are numerical, not exact proofs.

Only numerical schema 2 and structural sidecar 2.0 are supported. Old readers,
string-expression queries, `template`/`parameter` aliases and migration commands
have been removed. Rebuild obsolete artifacts from their source LP/MPS.

```sh
lpir query evaluation/test1/ir --spec '{"table":"structure_families"}' --limit 2
lpir analyze evaluation/test1/ir structure --seconds 30 --memory-mb 128
lpir inspect evaluation/test1/ir structure_objective --limit 5
```

Use `--raw-ids` to display internal identities; readable references are local to
an artifact. Scope, pagination and continuation retain the current query contract.

## Structural representation

The primary view contains:

- Base variable entities with arbitrary-rank coordinates and exact sparse domains.
- Coordinate views and explicit index mappings, keeping aliases separate from entities.
- Constraint families with algebraic skeletons, row domains, restricted sums,
  coefficient tensors and original-row mappings.
- Objective coefficient tiles, verified axis invariance, and axis subsets with
  explicit evidence. Equal objective profiles do not prove whole-model symmetry.

All published families expand exactly to numerical rows, including coefficients,
bounds and normalization scales. Missing cells are never silently filled with
zero. Uncovered rows retain their numerical representation. Long rationals may be
abbreviated with `≈` in Markdown; exact values remain queryable.

The local formula compiler and indexed-coordinate/operator modules remain active
building blocks for structural discovery. Their diagnostic relations are not old
API implementations.

See [structural representation and validation](docs/STRUCTURAL_IR.md),
[indexed coordinates](docs/INDEXED_IR.md) and
[relational query recipes](docs/EVIDENCE_QUERIES.md).

## Exact queries and validation

The query AST supports scans, filters, projections, joins, grouping, set
operations, exact arithmetic, selected terms and column incidence. Use rational
strings for exact decimal constants. Arbitrary Python/SQL expressions are rejected.

Validation includes formulas, structural entities/families/maps/subsets/objective
tiles, support relations, supplied coordinate mappings, cliques and decomposition
hypotheses. A failed check returns a counterexample; an exhausted budget returns
an incomplete result rather than a proof.

Budget fields include `seconds`, `memory_mb` and `candidates`. Most internal
budgets are cooperative working-set estimates; native solver/graph calls and an
atomic candidate can overshoot a time quantum. The evaluation runner enforces
an independent subprocess deadline per model. Partial discovery is not evidence
that a structure does not exist.

Build gives each resumable analysis stage its own budget. Use
`IR.build(..., memory_mb=64, analysis_budget={"seconds": 10, "candidates": 5000})`
or `lpir build model.lp --out ir --memory-mb 64 --analysis-seconds 10
--analysis-candidates 5000`. Defaults are 30 seconds and 1,000,000 candidates per
stage; this is not an overall parsing/build deadline. If formula compilation is
incomplete, the entry summary says so and the numerical artifact remains usable.
Resume `analyze("explanations")` before calling `describe()` or `render()`.
See [module boundaries and recovery contracts](docs/ARCHITECTURE.md).

## Input, storage and export

LP parsing streams bounded chunks and reports unsupported syntax with locations.
Supported models are linear, with continuous, integer and binary variables,
finite or infinite bounds, equalities, inequalities and ranged rows. Nonlinear,
SOS, indicator and semicontinuous features are rejected.
HiGHS LP output is accepted, including objective/left-hand-side wraps between
a coefficient and its variable and an empty `semi` section. A nonempty `semi`
section still raises an unsupported-domain error. Completed terms require
explicit `+` or `-`; a scalar right-hand side ends at the physical newline.

MPS supports fixed/free records, integer markers, RHS/RANGES/BOUNDS sets and
objective offsets. Multiple vector sets require explicit selection. INTORG
columns default to `[0,1]` unless overridden by BOUNDS. LP export requires names
compatible with its grammar and rejects completely free rows it cannot represent;
explicit rename maps are supported.

`metadata.sqlite` and CSR/CSC arrays form the immutable, checksummed numerical
store. Analysis, local formulas, indexed coordinates and structure sidecars are
separate. Description-length metrics measure canonical algebraic descriptions,
not SQLite disk size. Raw identity audit mappings are excluded; coordinate maps,
views, subsets and objective representations are counted.

## Tests and evaluation

```sh
python -m pip install -e '.[test,probe]'
pytest -q
PYTHONPATH=src python scripts/evaluate_structure.py \
  --out /tmp/lpir-evaluation --seconds 300 --jobs 2
PYTHONPATH=src python scripts/evaluate_public.py --out /tmp/lpir-public
```

The structural runner builds danoint, assign1-5-8 and test1–test6 independently,
including parsing, summaries and verification within each five-minute cap.
It never overwrites an existing output directory. The public benchmark runner
checks small MPS models and optional independent SCIP comparisons.

Tests cover exact parsing, corruption rejection, numerical round trips, random
renaming/permutation/scaling, masked domains, parameter perturbations, paging,
scopes and continuation. [The offline AI evaluation protocol](docs/AI_EVALUATION.md)
describes the limits of claims based on deterministic regression checks.

Optional dependencies: `.[probe]` supplies SciPy and `.[graph]` supplies igraph.
`register_analysis(name, version, discover)` supports trusted Python extensions;
CLI JSON cannot install or execute plugins.

### 在 LPIR 内核验跨行模式

行号是 **从 0 开始的 `row_id`**，与 LP 文件物理行号、约束名称不同。
以下查询核验 `52+2j` 与 `53+2j`，其中 `0 <= j < 10`（`stop` 不包含在范围内）：

```python
spec = {
    "op": "compare_rows",
    "start": 0, "stop": 10, "step": 1,
    "left": {"offset": 52, "stride": 2},
    "right": {"offset": 53, "stride": 2},
    "mode": "vector",
}
page = ir.query(spec, limit=3)
print(ir.preview(page))
proof = ir.validate({**spec, "kind": "row_pairs"})
```

`mode` 支持 `support`（变量支持集）、`vector`（完整精确系数向量）、
`constraint`（向量及上下界）。不自动允许缩放或变量重命名。
配对越界会报错，不会跳过缺失的行。`row_pairs` 使用同样的范围与映射，仅生成行对。
验证返回 `valid=true/false/null`；未完成且尚未发现反例时为 `null`，发现反例即可为
`false`。空作用域使用全称量词的空真语义，但明确标注 `empty_scope`，预览提示未检查任何行对。
预算耗尽后用原 spec 再次调用，即从已保存的行内位置续跑；读取句柄只读取已有证据。

`row_signatures` 对 `start/stop/step` 指定的原行生成稀疏签名（默认全模型），支持同样的
三种模式。签名可用于分组和候选筛选，**哈希相同不构成相等证明**；证明使用 `compare_rows`。
这些新算子可作为 `aggregate`、`filter` 等关系算子的输入。

嵌套字段使用 `{"path": ["first_difference", "variable"]}`；原有
`{"field": "left.row_id"}` 仍按完整顶层键读取，避免与 join 输出冲突。
聚合的 `field` 可以接受路径表达式；嵌套分组字段先通过 `project` 提取为命名字段。
显式 `sort` 示例：

```python
sorted_rows = ir.query({
    "op": "sort",
    "input": {"op": "scan", "table": "raw_rows"},
    "by": [{"expr": {"field": "row_id"}, "direction": "desc"}],
})
```

排序对数值及有理数字符串使用精确比较；升序时 null 在前，其次数值，最后文本。
同值用完整记录作为稳定次序；排序放在最外层可得到稳定分页。路径缺失会报错。
`inspect("raw_rows")`、`inspect("variables")` 等基础关系与 query 共用关系目录。
CLI 的 `query`、`inspect`、`validate` 可添加 `--preview` 显示简洁证据，默认 JSON 接口保持不变。

结构摘要首先列出分块、去重覆盖率及已接受公式中的跨块关联，再展示公式和证据入口。
搜索完成表示当前算法完成，不代表已穷尽模型结构；零发现状态也会保留。
未被结构族覆盖的行不能据此认定为“无结构”，跨块关联也不等于可独立分解证明。

仓库的 `data/test7.lp` 可直接使用已核验的范围：

```sh
lpir query /path/to/test7.ir --spec @examples/test7_row_pairs.json --preview
```

该范围包含 3497 对行，完整系数向量全部相等；不声称上下界也相等。
