# 数据快照与 Frame

## 数据与身份

Native v3 保存三张规范逻辑表，以及目标方向、目标常数和来源元数据。
Rows 为 `(row, name, lb, ub)`，Vars 为 `(var, name, kind, lb, ub, obj)`，
Terms 为 `(row, var, coef)`。物理存储仍是分页元数据、精确数字字典、CSR 和
引用 CSR 位置的 CSC；不会为三张表创建常驻 Python 字典列表。

每次从 LP/MPS 导入分配新 `uid`。`version` 对有序规范数学内容取指纹，
不包含路径和分析缓存。保存和加载保留身份；同一 uid 不同 version 禁止混用。
`source` 记录来源路径、摘要、格式和数值来源；`stage` 默认为 imported。
Native v2 明确拒绝加载，应从原 LP/MPS 重新导入。精确访问不再需要原文件存在。

有限数值是 Fraction。边界无穷使用正负无穷，与 nullable 列的 None 不同。
原始行保留双边界及符号；lhs/rhs 只是 lb/ub 别名。整数域非空且包含于
`{0,1}` 时 is_binary 为真，包括固定在 0 或 1 的整数变量。规范 kind 只有 C/I。

Ref 的身份是快照 uid、版本、轴和原始 ID。整数 ID 不能冒充另一轴的 Ref。
名称用于展示和 by_name，不能作为隐含关联键。

## 表与导航

```python
r = m.rows.iloc[0]
x = m.vars.by_name("x")
r.vars, x.rows            # 去重实体集合
r.terms, x.terms          # 原始非零项
m.terms(rows=R, vars=V)   # 两轴交集，按原始行/变量 ID 顺序
r.terms.one().var         # 可继续导航的实体
```

RowFrame 和 VarFrame 每个实体最多一次。TermFrame 的 key 是 `(row,var)`。
普通 Frame 允许重复记录，也允许 key=None；没有隐藏 index。

- iloc 是位置；loc 是声明键；复合键用 tuple；无 key 的 loc 报错。
- by_name 明确查名称；family 明确绑定 IR 配置及深度。
- select 和 join 返回普通 Frame；筛选、排序、切片、head 和 sample 保留类型。
- 实体 `& | -` 及 refs 返回模型 ID 顺序；distinct 保留首次出现顺序。
- sort_by 稳定，缺失最后；sample 无放回、固定种子可复现，保留原相对顺序。
- 普通 Frame 的 one 最多读两条输出，但不能越过上游完整验证屏障。

## 精确查询

```python
from mipframe import col, count
from fractions import Fraction
T = m.terms().where((col("coef") < 0) & col("var").isin(V))
A = T.select("var", weight=-col("coef") / Fraction(3))
G = A.group_by("var").agg(n=count(), total=col("weight").sum())
```

核心只接受可序列化 QueryExpr，不接受 lambda。int、Fraction、Decimal 可以作
精确常量；有限 float 必须先明确转换。布尔表达式用 `& | ~`，不能用 Python
and/or。nullable 筛选只保留 True；缺失 join 键不匹配，分组和 distinct 将缺失
作为一个组。count() 统计记录；列 count/sum/min/max/nunique 忽略缺失。
空 sum 为零，空列极值报错；分组无有效值的极值为 None。
无穷边界的计算也遵守精确规则：有限数除以无穷得到 `Fraction(0)`；
`inf - inf`、`0 * inf`、`inf / inf` 和包含相反无穷值的求和抛
`ExactArithmeticError`，不会生成 NaN 或有限 float。

## 关联与证明

```python
P = V.project(i=F0, j=F2)
P.check("product", ("i", "j")).require()
P.check("function", key="i", value="j")
A = m.terms(rows=F2, vars=V).select("var", a=-col("coef"))
Q = P.join(A, on="var", cardinality="1:1", require="left")
```

project 严格要求每个源、每个目标域恰好一个邻接成员。完整零/多匹配报告在
CardinalityError.report.violations 中；其 relations["matches"] 提供全部匹配项。
声明目标域不会随 head 或 where 收缩。product 验证唯一性、缺失坐标和域外记录，
不构造笛卡尔积。function 只验证函数依赖，并报告声明域中未覆盖成员数。

join 支持 inner/left，显式 1:1、1:m、m:1、m:m。auto 只有已知至少一侧唯一时
接受；require 单独约束 left/right/both 完整匹配。重名非键列必须显式重命名。
两个验证都是全量屏障；head(1) 也不能隐藏后面的重复键或漏匹配。
成功检查产生的唯一性事实只属于被检查的计划，不会提升到祖先查询。

Terms 分组只包含出现的组。需要零度成员时用：

```python
C = m.matrix.nnz(rows=R, vars=V, axis="var")
rare = C.where(col("nnz") <= 2).refs("var")
```

## 执行、导出和生命周期

查询共享工作预算，默认 128 MiB，最低 16 KiB；解析仍最低 32 MiB。
阻塞算子使用临时 SQLite，精确数值编码和有理数比较不依赖 REAL。
执行上下文在首次需要落盘时才创建临时目录和数据库，纯流式读取无需额外 SQLite。
预算不代表整个进程的 RSS 上限。repr 读取有界前缀，不额外 count；未知总量显示
`total not computed`。排序、聚合或验证仍可能需要扫描完整输入。
`inspect(max_items=N)` 对普通 Frame 也限制预览条数。族视图支持参数与样本展示；
Indexed 视图支持 `max_items` 和 `verbose`。不适用的非默认展示选项明确报错。
相邻 `where` 合并后统一下推；参数表的 `basis` 筛选不因写成多个 `where`
而额外计算其他数值基准。

to_records() 显式物化，默认限 128 MiB，超限抛 MaterializationLimitError。
iter_batches 完整流式输出只读批次，write 原子输出精确 JSONL；Parquet 输出需要
`mipframe[parquet]`，有理数保存为分子/分母，Ref 类型身份写入 schema 元数据。
独立记录中的 Ref 不依赖打开模型；调用 ref.bind(m) 才恢复实体导航。
实时查询依赖任一模型关闭后都失效，包括缓存和已暂停迭代器。

```python
spec = Q.to_spec()
restored = mf.frame_from_spec(spec, models={m.uid: m}, relations={})
Q.write("analysis.jsonl")
copy = mf.read_frame("analysis.jsonl", models=(m,))
```

模型外分析关系需要显式 relations 绑定。to_spec 不保存用户可伪造的唯一性证明。
共享子查询在 to_spec 中只定义一次，后续位置保存引用；旧的树形规格仍可读取。
explain 不执行查询，可查看计划、schema、键与验证屏障，共享父节点显示为引用。
membership 子查询和算法输入也纳入依赖展示与搜索来源追踪。

## 迁移

| 原写法 | 新写法 |
|---|---|
| F[0] / F[:5] | F.iloc[0] / F.head(5) |
| m.rows["F0"] | m.family("F0@2").rows |
| frame.index | frame.ids（实体）或声明 key |
| x.rows 的边项 | x.terms 的 Term |
| V.rows.project(...) | V.project(...) |
| P.check_product(...) | P.check("product", ...) |
| counts 字典和布尔掩码 | Frame.where(col("nnz") ...) 和 refs |
| I.tensors.one() 张量对象 | I.tensor(I.tensors.one().id) |
| I.operators.one() 算子对象 | I.operator(I.operators.one().id) |

模式统计的成员和模式项分别位于 `.relations["members"]` 和
`.relations["items"]`，不在单元格中嵌入成员列表。
LinExpr 将消费有类型 Ref 和精确标量；Edit 将产生新快照和显式映射；Solution
将携带快照依赖。本次查询表达式不会自动构成有效不等式证明或模型等价证明。
