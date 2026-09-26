# Agent 取证查询：接口与边界

本层执行调用者指定的关系运算。它不判断是否应固定、代入、合并变量，也不推断业务索引。
原数值 IR 和公式 AST 保持原义；所有新增结果存入可重建的分析缓存。

## 可复用关系

```python
from lpir import IR
ir = IR("ARTIFACT")

terms = ir.query({"op": "terms", "rows": [0, 1]}, limit=1)
source = {"op": "result", "handle": terms["handle"]}
support = ir.query({"op": "distinct", "input": source, "fields": ["variable"]}, limit=1)
count = ir.query({
    "op": "aggregate", "input": source, "group_by": [],
    "metrics": {"variables": {"op": "count_distinct", "field": "variable"}}
})
```

结果句柄引用整个已物化关系，和 `limit` 返回多少预览无关。`result` 可嵌入
`filter/project/aggregate/join/semi_join/anti_join/union/intersection/difference`。
其中 join/半连接使用 `left_on`、`right_on` 键列表；join 字段带 `left.`、`right.` 前缀，
半连接只返回左侧字段，不因右侧重复而复制左侧记录。

查询关系默认具有 bag 语义；需要集合时显式 `distinct`。`distinct(fields=[...])` 投影并去重；
不指定 fields 时按完整 JSON 记录去重。集合交并差也按完整 JSON 记录比较，应先投影到相同字段。
`count_distinct` 忽略 null，使用精确有理数键：`0.5` 与 `1/2` 计为同值。
`sum/min/max` 返回精确数值字符串，计数返回整数。分组/去重的临时数据允许 SQLite 落盘，
不把所有成员装进 Python set。

```python
joined = ir.query({
    "op": "semi_join",
    "left": {"op": "scan", "table": "variables"},
    "right": {"op": "result", "handle": support["handle"]},
    "left_on": ["variable"], "right_on": ["variable"]
}, limit=1)
```

对 `joined` 按 `kind` 分组聚合 `cost/lower/upper`，即可统计被选变量的目标与定义域。
将 `explanation_bindings` 与 variables 连接，可按 family/role 统计；再连接
`column_incidence` 与 `explanation_rows`，可统计跨家族的行数和变量数。
所有维度由调用者指定，IR 不自动解释这些统计的强化含义。

## 按需稀疏访问与行活动

```python
selected = {"op": "result", "handle": ROW_RELATION_HANDLE}
terms = ir.query({"op": "terms", "rows": selected, "field": "row_id"})
activities = ir.query({"op": "row_activity", "rows": selected})
columns = ir.query({"op": "column_incidence", "variables": {
    "op": "result", "handle": VARIABLE_RELATION_HANDLE
}, "field": "variable"})
```

`rows/variables` 也接受最多 1000 个整数 ID；省略时显式扫描全模型。
输入 scope 按 ID 去重。`terms` 只读取选中行的 CSR 区段，`column_incidence` 只读取
选中变量的 CSC 区段。单行 terms 句柄，以及嵌套查询中可识别的行 ID 等式过滤也会下推。
不需要预先构建全模型 `numerical_terms`。不受限的 `scan terms` 保留旧的全量落盘路径。

`row_activity` 在**现有变量界的笛卡尔积**上精确计算线性表达式的下确界和上确界。
其他约束与整数格点均不参与计算；它不能证明该活动端点在原模型中可达到。
`minimum=null` 表示负无穷，`maximum=null` 表示正无穷；若检测到不一致的变量界，
`empty_box=true` 且两者为 null。输出还包含系数正负计数/范围、定义域计数、
所有下界是否非负、无穷界数量、目标系数符号计数和原行界/来源。
这是区间与关联事实，不是 presolve 结论。

## 通用图查询

```python
graph = ir.analyze("relation_components", scope={
    "edges": {"op": "result", "handle": EDGE_RELATION_HANDLE},
    "source": "source", "target": "target", "evidence": "row_id",
    "vertices": {"op": "result", "handle": VARIABLE_RELATION_HANDLE},
    "vertex": "variable"
}, limit=1)
```

边关系必须包含两端点及证据字段。顶点键支持整数和字符串，图按无向连通性计算。
显式 vertices 可加入没有边的孤立顶点；不提供时只包含边端点。不会自动加入全部模型变量。
重复边和自环不改变分量，但证据表保留每条输入边。

- 主结果每条记录为 `{vertex, component}`。
- `summary.outputs.members` 指向成员关系。
- `summary.outputs.components` 指向 `{component, vertices}` 大小关系。
- `summary.outputs.edges` 指向 `{edge_ordinal, component, source, target, evidence}`。
- 大小分布仅预览最多 20 种，附截断标记；完整结果可继续聚合 components 关系。

调用者可以将公式的两种角色指定为边。工具只确认这些边的连通分量；
“这些边意味着相等”“分量成员可代换”等数学判断属于 Agent。
若要计算某变量集涉及的全部分量成员：先将该集与 members 半连接，投影去重 component，
再用这些 component 对完整 members 半连接。孤立的种子变量应显式加入 vertices，
或最后与原种子集取并集。

## 视图与精确回映

- `raw_rows`、`variables`、`terms`：原始数值视图。
- `normalized_rows`（兼容名 `rows`）、`roles`、`bindings`：规范化视图。
  `raw_expression = scale * (normalized_expression - shift)`；负 scale 会交换上下界。
  normalized expression 使用 bindings 中记录的局部补变量；不能将其当成原始变量表达式。
- `explanation_rows/families/bindings/parameters`：公式解释视图。
  `explanation_rows.scale` 与 AST 中的常量、参数一起回映到原行；此层不套用旧分类的 shift。

新建解释 sidecar 带 `(variable,row_id,role)` 索引。已有 sidecar 的校验值不被改变；
它仍能通过现有行索引和稀疏列访问使用新接口。标识符等式过滤会下推到 SQL 索引，
剩余数值条件仍按精确有理数检查。

## 完整性、预算和继续

结果统一提供模型/分析版本、作用范围、`total_known`、`checked_range`、
`evidence_handle` 和 `continuation`。查询输出计数不是源矩阵已检查行数；
若尚未完成输入扫描计数，`checked_range` 明确返回未知。`exceptions.assessed=false`
表示该操作未定义例外检查，不表示“没有例外”。

未完成结果的 `total=null`；可以用 `query(handle=..., offset=...)` 查看已物化的部分，
但不能以 `op=result` 将它当成完整集合参与连接/差集/闭包。先重复生产者 AST 或 graph scope
续跑。嵌套操作的前置步骤没完成时返回 dependency 信息及原请求，不能误报空集合。
已完成查询分页只读取物化结果，不重复发现。旧版或外部模型的不可用句柄会明确报错。

稀疏事实查询按选中实体和非零元保存游标，可在单行中途恢复；图使用 SQLite 中的并查集，
按输入顶点、边、输出成员和证据分阶段恢复，不在内存中维护全图成员数组。
一般 SQL 查询中断后可复用已物化输出，但聚合/排序的 SQL 计划可能重新执行；
这不是任意 SQL 算子的内部断点恢复。时间/内存预算为协作式检查及工作集估算，不是进程硬限制。

```python
page = ir.analyze("explanations", budget={"candidates": 10000, "seconds": 10})
while page["completeness"] != "complete":
    page = ir.analyze("explanations", handle=page["handle"],
                      budget={"candidates": 10000, "seconds": 10})
```

解释层编译现在可跨进程恢复行发现、公式族汇总、目标统计和逐行核验。
编译预算在行/族/变量/核验行边界检查，单个大行或族仍是原子工作单元，可能超出时间量子。
全部核验完成后才原子发布 manifest；半成品不能用于 `describe/render` 或解释查询。
当前数值 IR 与已验证公式独立保存；分析检查点支持续跑。

## CLI 与复现

所有 AST 与 scope 均可直接用于 CLI `query --spec`、`analyze relation_components --scope`。
JSON 可内联，也可用 `@file.json`。结果/图/活动数据都不需要 Agent 自己写解析或集合检查脚本。

```sh
lpir query ARTIFACT --spec '{"op":"terms","rows":[0]}' --limit 1
lpir analyze ARTIFACT explanations --candidates 10000
PYTHONPATH=src python scripts/evaluate_structure.py --out NEW_DIRECTORY --seconds 300
```

重建脚本从原始 LP 生成当前格式的 IR、摘要与精确验证报告。
