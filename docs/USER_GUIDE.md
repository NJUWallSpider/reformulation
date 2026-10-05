# MIPFrame 用户指南

MIPFrame 保留模型的精确系数和原始身份。基础表是 Rows、Vars、Terms；模型快照
不可变，Frame 查询也不可变。完整接口契约和旧写法迁移见 [FRAME.md](FRAME.md)，
代码目录职责见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 读取与快照

```python
import mipframe as mf
from mipframe import col

m = mf.read("model.mps")
m.uid, m.version, m.source, m.stage
m.shape, m.nnz
```

read/load 只准备廉价 scan；`m.indexed()`、family 和 topology 首次显式访问时计算。
自定义选区或签名配置在显式请求时计算。展示不运行新的结构搜索，不隐式生成稠密矩阵。
独立 read 分配不同 uid；save/load 保留 uid/version。Native v6 保存共享基础数组、增量与证明依赖，
不依赖原 LP/MPS 文件仍存在。v3/v4 需要从 LP/MPS 重新导入。

```python
saved = m.save("model.native")
m.close()
m = mf.load(saved)
```

只读有限数值为 Fraction。行 lb/ub 保持原始双边界，lhs/rhs 是别名。
变量 kind 为 C/I；is_binary 和展示 type 根据有效整数域推导。目标系数在 Vars.obj，
方向与常数在模型元数据中。重复项精确求和，抵消后的零项不进入 Terms。

## 实体与集合

```python
r = m.rows.iloc[0]                # 按位置
x = m.vars.by_name("x")          # 明确名称查找
m.rows.loc[r.id]                  # 按原始键
r.vars                           # 变量集合
x.rows                           # 行集合
r.terms                          # 原始非零项，包含系数
term = r.terms.head(1).one()
term.row, term.var, term.coef
r.formula(max_terms=8)
```

实体集合自动去重。筛选、排序、切片保留类型；select/join 返回普通 Frame。
`.ids` 是原始实体 ID；名称不作为关联身份。Frame 不接受混合语义的 `[]`，
使用 iloc、loc、where、family、column 明确表达意图。

```python
R = m.rows.where(col("nnz") > 10)
V = R.vars
small = R.head(8)
sample = R.sample(8, random_state=42)
intersection = R & m.rows.head(100)
```

sample 无放回并保留原相对顺序，固定 seed 可复现。集合操作和 refs 返回模型顺序。
one 要求恰好一个成员，零个或多个成员均抛 CardinalityError。

## Compressed IR：模型地图

```python
m.ir.show()
summary = m.ir.to_dict()
json_text = m.ir.to_json()
F0 = m.family("F0@2").rows
V2 = m.family("V2@1").vars
family = m.family("F0", depth=2)
family.inspect()
family.related("parameters").where(basis="canonical")
family.related("histogram").where(basis="canonical")
```

IR schema 0.4 默认只导出 scan：尺寸、域和界、行列度、数值、local shapes、名字模板与目标。
`m.ir.structure`、`m.ir.topology`、`m.ir.objective["role_support"]` 和 `m.ir.indexed`
保持旧访问方式，但首次访问才计算。`to_dict/to_json(include_analysis=True)` 显式计算并导出分析。
`m.ir.row_shapes()` 返回完整精确行型 Frame；`.where(...).rows.terms` 导航原始系数。
`m.ir.name_templates("row"|"var")` 返回精确模板目录，`.related("slots")` 返回整数槽统计。
scan 的高基数频次带误差界，宽行指纹标为 candidate，名字仅为 lexical hypothesis。
默认 top 8 和 6,000 字符上限不改变缓存；默认展示/导出不会隐式启动分析。

`m.family()` 返回单行 Frame；
`.rows` 或 `.vars` 返回该族的原始成员。族筛选后关联成员和统计也随之收缩。
默认行族和变量族都为深度 2；IR 中解释行模板的变量角色仍为深度 1，
因为深度 d 的行使用深度 d−1 的变量角色。

`family_key` 绑定模型版本、算法配置和深度；F/V 标签仅用于当前配置中的展示。
子集 `families()` 不重新编号，`count` 为选中成员数，`total_count` 为全模型成员数。
数值表用 basis 区分 canonical、raw、scale_normalized，orientation 保留等式双方向。

```python
F = m.family("F0")
F.rows
F.related("pattern")
F.related("parameters").where(basis="canonical")
F.related("sources").where(basis="scale_normalized")
F.inspect(verbose=True)
mf.analysis.families(m.rows, method="exact_structural", depth=3)
mf.analysis.signatures(m.rows, method="typed")
m.refinement(1, 2)
```

高级算法集中在 `mf.analysis`；Structure、Signature、Family 等公共对象已删除。
参见 [接口迁移表](FAMILIES.md)。

## 全量坐标验证与系数关联

以下标签对应 test10 示例；其他模型的标签应从 IR 中选择。

```python
F0 = m.family("F0@2").rows
F1 = m.family("F1@2").rows
F2 = m.family("F2@2").rows
V2 = m.family("V2@1").vars
P = V2.project(i=F0, k=F1, j=F2)
P.check("product", ("i", "j")).require()
P.check("function", key="i", value="k").require()
P.loc[V2.iloc[0].id].i.formula(max_terms=8)

A = m.terms(rows=F0, vars=V2).select("var", a=col("coef"))
Q = P.join(A, on="var", cardinality="1:1", require="left")
S = Q.group_by("i").agg(
    distinct=col("a").nunique(), amin=col("a").min(), amax=col("a").max())
S.where(col("distinct") != 1)
```

投影检查全部源成员。异常 report.violations 包含完整零/多匹配记录，其
relations["matches"] 提供匹配 Terms。product 使用声明目标域，包括零度成员；
head 不缩小该域。function 检查函数依赖并报告未覆盖成员，不能视作双射。

select("var", ...) 不证明 var 唯一。join 的 cardinality 和 require 分别验证
唯一性和完整匹配；head(1) 也必须先完成验证。浮点不参与精确比较和分组。
Fraction/Decimal 可以作常量，有限 float 必须先显式转换。

## 计数、模式和极值

```python
C = m.matrix.nnz(rows=F0, vars=V2, axis="var")
rare = C.where(col("nnz") <= 2).refs("var")
hub = C.where(col("nnz") >= 20).refs("var")
patterns = V2.pattern_counts()
patterns.relations["members"]
patterns.relations["items"]
rhs = F1.column("ub")
rhs.max(), rhs.argmax(), rhs.value_counts()
```

matrix.nnz 包含所选轴的零度成员。普通 Terms 分组只包含实际出现的组。
模式计数、模式成员和模式项分别是表，不将成员列表塞进单元格。
argmin/argmax 返回全部并列记录并保持 Frame 类型与顺序。

## 稀疏矩阵与 Python 科学计算

```python
S = m.matrix.view(rows=F0, vars=V2)
S                                # shape、nnz、density，无 dense preview
S.preview()                      # 默认最多 8 × 12
S.preview(choose_vars="first")
S.preview(choose_vars="largest_coef")
S.entries.head(20)               # TermFrame(row, var, coef)
S.row_nnz, S.var_nnz              # Frame，保留零计数成员
A = S.to_scipy()
row_ids, var_ids = S.rows.ids, S.vars.ids
```

most_active 按整个选区中的列非零次数排序，largest_coef 按精确最大绝对系数排序；
并列保持变量轴顺序。自动选列不保证展示的前 8 行中一定出现这些变量。
Terms 按原始行/变量 ID 排序，SciPy 的行列位置则严格对应 S.rows.ids/S.vars.ids。
SciPy 延迟导入，需要 `mipframe[scipy]`。导出 float64，检查溢出、非有限值和
非零下溢，结果与模型不共享可写数组。完整模型使用 `m.matrix.view().to_scipy()` 显式导出。

```python
binary = A.copy()
binary.data[:] = 1
row_overlap = binary @ binary.T
```

## Indexed

```python
I = m.indexed()
I.tensors, I.operators, I.partitions, I.evidence
T = I.tensor(I.tensors.head(1).one().id)
T.vars, T.axes, T.coordinates
T.coordinates.check("product", ("i", "j")).require()
O = I.operator(I.operators.head(1).one().id)
O.kernel, O.bindings
O.verify().require()
```

目录、坐标、kernel、bindings、证据使用普通 Frame；tensor/operator 保留领域方法。
默认 IR 不展示内部缓存哈希，机器摘要保留 ID、作用域和签名配置；
详细对象通过匹配配置的 IndexedResult 类型接口访问。

## 资源和导出

repr 只读有界前缀，未知总数显示 total not computed；count/len 显式求精确总量。
查询默认工作预算 128 MiB，最低 16 KiB；解析最低 32 MiB。模型操作选择数组后端；通用关系可显式 fallback 到 SQLite。
数组与精确 sparse 工作区支持 mmap/外部归并。完整预算覆盖尚未完成，详见
[RUNTIME_V5_STATUS.md](RUNTIME_V5_STATUS.md)。预算不代表整个 Python 进程的内存上限。

```python
records = Q.to_records()             # 默认最多 128 MiB，超限报错
for batch in Q.iter_batches(4096):
    process(batch.to_records())
Q.write("coefficients.jsonl")     # 完整、精确、原子发布
spec = Q.to_spec()
restored = mf.frame_from_spec(spec, models={m.uid: m})
```

独立记录使用 Ref 身份值，模型关闭后仍可读；ref.bind(m) 才恢复实体导航。
实时 Frame、实体、列和迭代器在任一依赖模型关闭后失效。保存后的数值快照、refinement 层和完整 indexed 结果
随 native 加载恢复；编辑生成的 lineage 也会保留。

## 通用集合、图与稀疏计算

`mf.ops.sets` 提供集合配对、成员展开、等价类、分量与精确聚合；
`mf.ops.graph` 提供分量、SCC、拓扑序、可达关系、DAG 传递约简与路径。
所有结果都是完整的 Frame，默认超过 100 万条整体报错。
`m.matrix.activity()` 和 `transpose_activity()` 使用精确数值，缺失输入默认报错。
完整接口、资源语义和组合案例见 [核心算法指南](OPS.md)。

## Expressions and explicit verification

Use `row.expr`, variable arithmetic, or `m.linexpr(...)` to build exact affine
expressions. `<=`, `>=` and `.eq()` create unproved Claims. The six `m.check`
primitives verify explicit evidence and return compact `BatchProof` / `ProofRef`
objects. Frame and indexed diagnostics still use `Report`; point checks use
`PointReport`. See [API migration](API_MIGRATION.md) for serialization changes.

Use `.ok`, `.summary`, `.require()` and `.status`; proof objects have no implicit
truth value. Var handles are
now hashable by snapshot identity for substitution mappings, while `x == y` still
compares entity identity. Mathematical equality uses `x.eq(y)`.

## Modify a snapshot explicitly

Use `candidate = m.edit().build()` to create an independent descendant; declare
changes on the Edit before building it. The default `prepare="logical"` validates
the edit but defers structural IR/indexed analysis until requested; use
`prepare="indexed"` for explicit eager analysis. `read()` and `load()` prepare only the scan.
Source entities and existing proofs are
never mutated. Bound Frames align by typed keys, not positions. The detailed
[Edit guide](EDIT.md) covers bulk rows, auxiliary variables, strict `because`
contracts, batch linear certificates, direct-parent diffs, persistence and LP
auditing. Run `examples/exact_edits.py` for a complete proved add/drop roundtrip.


## Find the current contract

Use `mf.help("support containment")` to find a pattern, then
`mf.help("recipe.support", detail="full")` for its input schema and boundaries.
For a specific call use `mf.help(mf.Frame.join)`; for a caught error use
`mf.help(error)`. Text results are displayable `Formula` objects, not printed side
effects. `format="json"` returns detached metadata, including real signatures.
The [help guide](HELP.md) describes the CLI, recipes and offline checks.
