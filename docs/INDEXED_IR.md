# L1/L2 indexed IR (0.5.0)

数值 IR → 本地行公式 → incidence 坐标 → 精确 indexed operator。各层可重叠；数值重建仍以原始数值 IR 为准。本功能不执行 presolve、固定变量、添加 cuts 或 reformulation。

## 公开入口

```python
from lpir import IR
with IR('artifact') as ir:
    answer = ir.analyze('operators', budget={'seconds': 30, 'memory_mb': 128,
                                             'candidates': 1000000})
    while answer['completeness'] == 'partial':
        answer = ir.analyze(**answer['continuation'], budget={'seconds': 30})
    tensors = ir.inspect('index_tensors')['items']
    operators = ir.inspect('index_operators')['items']
    if operators:
        op = operators[0]
        evidence = ir.validate({'kind': 'indexed_operator', 'id': op['id']})
        parameters = ir.query(op['kernel'], limit=20)
        # parameters['handle'] 可作为其他查询的 op=result 输入。
```

`analyze('incidence')` 在集合覆盖检查后停止；`coordinates` 在坐标恢复后停止；`operators` 执行后续行束与算子核验。构建默认运行 operators，预算独立于解析及 L0 构建时间。没有新侧车的旧 IR 仍可读取；describe/render 不启动发现。

CLI 使用现有统一入口，例如 `lpir analyze artifact operators`；完整参数以 `lpir analyze --help` 为准。所有查询使用同一个受限 JSON AST，不执行查询中的 Python。

## 关系、范围和复用

可扫描的关系为：

| 关系 | 主要字段与含义 |
|---|---|
| `index_sets` | id、set、row_id；集合及原行，适用时有精确比例和规范化界 |
| `index_members` | id、set、row_id、variable；原始变量身份 |
| `index_degrees` | id、variable、degree；集合族覆盖次数 |
| `index_partitions` | 覆盖、重叠、空集合和明确的变量作用域 |
| `index_tensors` | shape、缺失单元、两侧分区、坐标与轴句柄 |
| `index_axes` | id、axis、index、row_id、partition |
| `index_coordinates` | id、variable、axis0、axis1 |
| `index_operators` | 精确 AST、公式、行域、核域、验证结果 |
| `index_rows` | id、row_id、p、k、anchor、scale、shift、lower、upper |
| `index_kernel` | id、p、k、j、value；broadcast 时 k=null，未存项为精确零 |
| `index_alignment` | id、p、j、basis；有证据的轴对齐 |
| `index_evidence` | 碰撞反例、核行列聚合等证据 |

`inspect('index_kernel', operator_id)` 或 `query(op['kernel'])` 均分页返回，不要求把核加载到上下文。句柄对应的结果可与原有 terms、variables、column_incidence、explanation_bindings 等关系连接，从而检查所有外部关联。视图未包含的原行继续完整可查。

作用域可指定 `rows`、`variables`、`role`、`members` 关系，以及 `variable_kind`。关系参数可为查询 AST 或 `{'op':'result','handle':...}`，必须先完整精确物化。rows 包含 row_id；variables 包含 variable；role 包含 row_id、variable；members 包含 set_family、row_id、variable（集合实例由原行标识）。成员必须实际出现于该原行。

非默认作用域写入独立 `indexed_scope_….sqlite/json`，不覆盖默认分析。返回的表句柄携带 analysis 标识。跨作用域连接时先分别物化，再连接结果句柄。inspect 可用 `key={'analysis': analysis_id, 'id': object_id}`；validate 接受 analysis 字段。

## 发现与证明的边界

自动集合候选包括同类型、同方向的原始 incidence 分组，以及统一系数行的规范化种子；不依赖变量名称、项顺序或固定尺寸。统一系数种子只是候选来源。分区、交集与坐标证明不使用系数大小。首版不枚举任意集合子族。

两个分区必须覆盖相同变量集，各自唯一覆盖。交集多于一个变量会产生反例；交集缺失产生稀疏坐标域，不会声称完整 Cartesian product。同族候选通过二次覆盖及二部性提出两个分区；二部图候选需要符合工作内存预算。

行束当前覆盖：一条原行的张量成员固定一个轴，并且恰有一个张量外变量，且该变量类型不同于张量成员。不同本地系数家族可以进入同一行束。一个外部项不能被静默删除；不满足条件的原行留在 L0。没有唯一 distinguished variable 的一般多输出网络算子不在首版自动搜索范围内。

核先按 anchor 系数精确规范化，再比较同一输出索引下的稀疏切片，包括所有隐含零。完整、完全相同的切片才发布 broadcast；不相同或缺少行位置时保留依赖 k 的 reduction 核。输入固定轴与输出 k 对齐就是 axis-identity gating，表示 A⊗I / I⊗A；只检查已有坐标下的复制，不搜索未知排列同构。

每个算子发布前逐行重建原始系数及上下界。核之外的 anchor 项、行比例和原行映射均保留。独立验证不会使用发现阶段的已检查行缓存。

输出轴 p 与输入轴 j 独立。仅当精确核每行有唯一最大值且这些位置形成双射时，才保存映射并检验对齐后的对称性/对角元；这不证明模型具有置换对称性。首版不做一般 Kronecker 分解、低秩或业务标签推断。

## 调用者提出假设

- `indexed_partition`：传 id，或传 sets（row_id、variables）及可选完整 variables 作用域。
- `cartesian`：传 id 及可选替代 coordinates；也可直接传两个 partitions（各为 row_id、variables 的列表）及 coordinates，不依赖自动发现。
- `row_bundle`：传算子 id 及可选替代 rows，检查坐标、anchor、原行支持及覆盖。
- `indexed_operator`：传 id，可传完整替代稀疏 kernel；也可传 candidate，包含 operator 定义、coordinates、rows、kernel，直接与原始数值行比较。
- `axis_alignment`：传算子 id 及可选 mapping，重新检查唯一极值和双射。

核中省略的项是零，空核不会回退到原参数。未知 AST 不执行、不假定成立。验证返回 valid、exactness、verification、completeness 和首个反例；预算耗尽返回 candidate/partial。

## 预算、持久化与摘要

数值矩阵逐项复制到侧车工作表，以便落盘关联；这会增加磁盘用量，报告单独列出侧车大小。原始文件和旧公式侧车不被改写。集合、坐标、核、绑定和中间结果均为 SQLite 分页关系，小清单只保留阶段、游标、模型和分析版本。

扫描可在单行中间恢复；覆盖计数、坐标、核行和展开核验保留进度。二部候选图和单个稀疏切片检查是有内存估算的原子操作，SQL 聚合也采用合作式预算，不承诺严格时间/RSS 硬上限。极低时间/内存预算可能需要增加后才能完成一个候选。公共候选预算计算扫描成员及结构检查工作，不等于 Python 指令数。

未完成的算子及其绑定/核不会作为公开已验证视图发布。已发布的局部事实可以在整个搜索仍 partial 时查询；查询结果标记来自哪个侧车阶段的快照，不能把该快照中缺少对象解释为完整搜索证明。

summary 来自 describe 的同一持久化文档；Markdown 是纯阅读视图，不嵌入 JSON 或 AST；完整机器字段保留在 describe / summary.json 中。默认 20KB，索引卡片、参数和原行使用查询句柄；遗漏卡片计数显式显示。render 不新增推理。

## 回放与性能

- `scripts/indexed_instances.py`：独立生成器，支持尺寸、稀疏核、重命名/置换、比例缩放及扰动。
- `scripts/evaluate_indexed.py INPUT --out DIR`：完整构建、公开 API 回放、视图核验、数值 LP round-trip。
- `scripts/benchmark_indexed.py BASE_IR --out DIR`：在只读数值副本上运行当前 L1/L2，单独记录新增分析性能与实际源代码哈希。

性能与发现质量分别报告。默认预算下的大模型 partial 不代表不存在结构；复制块、三个中型或三个大型实例都不能证明任意规模性能，也不能替代完整原文输入 AI 的 A/B 评测。

## 可读引用（0.5.1）

默认 `describe()` 和 Markdown 使用 `X1`、`operator1`、`formula1`、`partition1`、`model1` 等短名称。它们是**当前 artifact 内的引用**，不是跨模型的全局指纹，也不是变量原名。内部 SHA、数值校验、缓存键不变。

映射保存在独立的 `references.sqlite`，按首次出现分配，之后不会因分页、追加发现或重新打开而重新编号。首次生成可读输出会写入这个名称登记表，不会启动结构发现或修改数值/公式侧车；复制 artifact 时应一起复制它。不同 artifact 的 `X1` 不保证代表相同对象。

短引用与内部 ID 均可用于 `inspect`、JSON `query`、`validate` 及续跑句柄。只解析标识符位置，不替换原始变量名、系数或任意文本。示例：

```python
op = ir.describe()['indexed_views']['operators'][0]
ir.query(op['kernel'])
ir.validate({'kind': 'indexed_operator', 'id': op['id']})
readable_result = ir.readable(ir.query(op['kernel'], limit=20))
# readable_result['handle'] 也可用于 ir.query(handle=...)。
```

CLI 的 inspect/query/analyze/overview/validate/probe 默认返回可读引用，`--raw-ids` 可查看内部身份。Python 的现有数值查询返回格式保持兼容，可用 `ir.readable(result)` 转换；`describe(raw_ids=True)` / `render(raw_ids=True)` 返回内部 ID。Markdown 与 describe 使用同一套引用。新 Markdown 不支持反解析为机器数据；机器数据请使用 describe() 或 summary.json。
