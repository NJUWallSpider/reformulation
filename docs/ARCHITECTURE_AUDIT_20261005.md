# mipframe Control Plane / Data Plane 架构审查

审查日期：2026-10-05（Asia/Shanghai）。

**结论：尚未清晰、完整地实现目标架构，不能按“规模相关计算不经过 SQLite”验收。** 底层共享数组、精确数值、稀疏矩阵、compact proof、主要 edit primitive 已经有实质实现；但 Frame 输入、关系检查、实体选择仍存在可达的 SQLite 数据路径。数组后端也还没有统一执行资源预算。整体属于有良好底座、但新旧执行模型混用的迁移状态。

有明确的越层依赖、重复实现、无生产调用的 SQL 遗留和失真的可观测性。用“全部是屎山”概括不准确；更准确的风险是：同一个公开功能随输入来源、大小、表达方式改变存储后端，调用者难以推断复杂度，维护者需要同时维护两套语义和资源管理。

## 1. 范围、标准和验证方法

- 审查当前工作树，包含已修改文件和未跟踪的新实现，不是只审查 HEAD。HEAD 为 `da1a940f113ad6f63177cbd11059977d44f40210`。
- 对 `src/mipframe` 的 154 个 Python 文件进行 SQL 入口、导入依赖、调用点扫描；沿 Model → Frame → algorithms/check/edit → persistence 的关键路径阅读实现。
- 源码快照 SHA-256：`f241260ba98a41262a2457c68407522376f9df8040f6b1722a029882a2892c22`。算法：按路径排序，逐个累加相对路径 `src/mipframe/…` 的 UTF-8 字节和文件字节。
- 实际运行完整测试：**629 passed、8 skipped，100.76 秒**。命令：`/Users/bhz/miniforge3/bin/python3 -m pytest -q`。
- 另外运行独立探针，拦截探针进程中所有新建 `sqlite3.connect` 连接，记录 SQL trace、INSERT 行数和 SQLite aggregate 的逐行回调；不是只拦截某个 Context 方法。
- 探针使用三种规模：行数 `m=q`、变量数 `n=q+1`、`nnz=2q`，`q=128/512/2048`。模型是链式约束 `x_i-x_(i+1)<=0`，变量界为 `[0,2]`。模型导入阶段不计入各运行阶段的 SQL。
- 低内存入口设置 `memory_mb=0.015625`，即 16 KiB，稳定触发容量边界；这不是大型真实模型耗时基准。
- 未修改生产代码、既有测试或既有文档；仅新增本报告、JSON 证据和复现脚本。

原始证据：[JSON](/Users/bhz/Code/reformulation/docs/ARCHITECTURE_AUDIT_20261005.json)。复现脚本：[control_data_probe_20261005.py](/Users/bhz/Code/reformulation/docs/audit/control_data_probe_20261005.py)。

本审查将 `n` 定义为变量数、`m` 为行数、`W` 为 certificate/witness 条目数。判定重点是**计算数据是否穿越 SQLite**，而非仅看 SQL 调用条数：一次返回 W 行的 SELECT，仍是 O(W) 数据路径；`executemany` 也不使逐实体持久化变成 O(1)。

允许 SQLite 保存名称、身份、版本、精确数值持久字典和 artifact manifest。首次导入时写这些目录不自动算违规；名字查询的明确 API 边界也不是数值 kernel。反之，将 ID selection、稀疏数据、证书记录放进临时表，再用于排序、聚合、验证或算法输入，就是 Data Plane 工作。

## 2. 分层符合度

| 路径 | 当前判断 | 依据 |
| --- | --- | --- |
| Snapshot / CSR / CSC | 基本符合 | 共享 mmap 数组，Facts 借用；两种稀疏方向均有数组 |
| exact-number 运行时读取 | 符合核心边界 | `ExactNumbers` 读数组及 bigint 文本，不逐项查询 SQLite |
| compact Targets / CertificateBatch → linear kernel | 符合禁 SQL 核心；完整预算仍有缺口 | 探针三个规模均零 SQL；内部部分暂存仍未入统一账本 |
| fixed substitution / masks / overrides / delta | 主要实现符合 | 共享 base，CSC incidence、typed delta、WorkContext；不隐式重建 SQL 数值库 |
| Frame 的普通实体／terms 数组执行 | 部分符合 | 已有 NumPy 后端，但 SQLIndex 输入能藏在后面，且全量中间结果未受统一预算控制 |
| Frame unique / product / function 检查 | 不符合 | 无条件 SQL staging 和 GROUP BY |
| Frame.from_records → 图／证书／聚合 | 不符合端到端边界 | 超预算自动改用 SQLite；下游重新扫描临时表 |
| 图算法内部 | 主要摆脱 SQL，但实现和资源契约不统一 | 通用 Graph 与新的 ArrayGraph 两条构图路线，输入也能来自 SQL |
| refinement | 数值迭代迁移了，持久化和工作集未收敛 | colors/tokens 已为数组，仍写逐行/逐变量冗余表，签名准备保留全量 Python 容器 |
| indexed IR | 大型关系列已迁移；目录和工作集仍需约束 | columns 为 typed artifacts，发布 state JSON 和本地 exact pools 仍整体构造 |
| native save/load、MPS patch/canonical/readback | 已审查的主要路径符合禁 SQL 方向 | 保存数组和 typed recipe；readback 名字索引为数组散列表，不是逐系数 SQL |
| 包依赖方向 | 不符合清晰分层 | `data` 反向导入 Frame、Report、check、ops、alg |

“基本符合”不表示已证明所有输入、故障和规模下均满足资源契约。

## 3. 可复现的问题，按优先级排列

### F1 · P1：Frame 关系检查无条件把计算数据写进 SQLite

入口例如 `m.rows.check('unique', 'row')`。即使 `m.rows` 本身是纯数组源，也会执行：

```text
Frame.check
  → check.frame.check
  → Context.stage(frame, selected)
  → temporary SQLite table + INSERT all records
  → SQL GROUP BY / DISTINCT / COUNT
  → SQL-backed violation report
```

证据：[check/frame.py:167](/Users/bhz/Code/reformulation/src/mipframe/check/frame.py:167)、[executor.py:212](/Users/bhz/Code/reformulation/src/mipframe/frame/executor.py:212)。`product` 还会遍历 SQL 记录，把合法记录逐条追加到另一张表，见 [check/frame.py:204](/Users/bhz/Code/reformulation/src/mipframe/check/frame.py:204)。

实测 2048 行唯一性检查写入 2048 行临时数据，执行 2060 条 SQL。检查全部成功，SQL 并非只存在于失败诊断分支。

**影响：** 行唯一性、函数依赖和笛卡尔覆盖检查仍以 SQLite 作为计算后端；代价随被检查的模型关系／证书关系规模增长。

**修复方向：** 使用 typed key 排序、分组计数、domain membership 数组和外部归并。完整 violations 也应为 typed artifacts；不能通过限制显示条数省略完整检查。

### F2 · P1：实体 ID 选择仍依赖 SQLIndex，普通 Row.terms 就能触发

`_IdentityFrame.__init__` 对非 range、未标记 unique 的 iterable 无条件调用 `stored_index`，创建 SQLite 临时库并插入 ID。

证据：[frame/entity.py:94](/Users/bhz/Code/reformulation/src/mipframe/frame/entity.py:94)、[query_store.py:148](/Users/bhz/Code/reformulation/src/mipframe/store/query_store.py:148)。

这不是只对私有构造器成立。公开路径：

```text
row.terms
  → TermFrame(owner, row_id)
  → RowFrame(owner, (row_id,))
  → stored_index → query.sqlite
```

对应 [row.py:231](/Users/bhz/Code/reformulation/src/mipframe/frame/row.py:231)、[terms.py:87](/Users/bhz/Code/reformulation/src/mipframe/frame/terms.py:87)。`Var.terms`、单实体邻域以及部分反序列化也复用这个选择入口。

**实测：** `sum(row.terms.count() for row in m.rows)` 在 2048 行模型上产生 2048 次 CREATE TABLE、2048 次 INSERT、4096 次 SELECT，连同 PRAGMA/BEGIN 共 **20,480 条 SQL**。这是随 m 增长的真实公开 API 路径。

此外，2048 个显式 ID 的 RowFrame 在 `explain()` 中仍报告 `numpy: typed column execution`；NumPy 的 `entity()` 随后通过 `np.fromiter(ids, …)` 把 SQLIndex 全部读回来。解释信息只看到最终算子，没看到选择存储。

**修复方向：** 使用有序 typed selection + 去重／membership 索引；单 ID 使用 range 或小型 typed view。注意当前 SQLIndex 保留首次出现的顺序，不能直接替换成排序后的 IDSet 而破坏 `.iloc` 语义。

### F3 · P1：数据越大，Frame.from_records 越容易切回 SQLite，并带入图算法和 proof 编译

证据：[serialization.py:99](/Users/bhz/Code/reformulation/src/mipframe/frame/serialization.py:99)。当前先积累 Python lists 和 key set，估算使用量超过预算一半后调用 `_sqlite_from_records`。回退对调用者透明，primitive int/ref/number 列也会触发。

后续存在三种实际问题：

1. **数学聚合重新由 SQLite 驱动。** SQL backend 为 int/number 使用 `TEXT COLLATE EXACT`；`exact_sum` 的 SQLite aggregate callback 每条记录调用精确运算。实测对 2048 条低内存 Frame 做总和，新增两批共 4096 行 staging，SQLite 调用 aggregate step **2048 次**。见 [executor.py:131](/Users/bhz/Code/reformulation/src/mipframe/frame/executor.py:131)、[executor.py:310](/Users/bhz/Code/reformulation/src/mipframe/frame/executor.py:310)、[executor.py:352](/Users/bhz/Code/reformulation/src/mipframe/frame/executor.py:352)。
2. **图 kernel 虽为数组，输入仍全量经过 SQL。** `read_columns` 对 SQL source 调用 `frame._raw`，把查询结果转换为数组。2048 条边的 path 求解有一次读取整张关系的 SELECT；算法结果只报告 `typed arrays`。见 [array_source.py:24](/Users/bhz/Code/reformulation/src/mipframe/frame/array_source.py:24)。
3. **Frame certificate 入口规模相关。** 2048 targets + 4096 affine terms + 2048 witnesses 的三个 Frame 输入，共 8192 行先进入 SQLite；`linear_many` 随后用三次 SELECT 读取这些记录。三条 SQL 不是 O(1) 数据工作。

相比之下，直接调用 compact `linear_rows` 的三个规模均为 **0 SQL**。因此是输入／编译边界破坏了端到端隔离，不能据此指控精确线性 kernel 仍用 SQL 做残差运算。

**修复方向：** 输入构造器以 bounded typed builder 写 primitive columns、packed string、exact value IDs；预算不足时写 mmap/artifact runs。图和 certificate adapter 在运行前明确校验 Data Plane capability。若保留通用 SQLite 兼容后端，应显式隔离，不能静默成为模型计算输入。

### F4 · P1：NumPy 后端避开 SQL，但还不是统一有界的 Data Plane

[numpy_backend.py:71](/Users/bhz/Code/reformulation/src/mipframe/frame/numpy_backend.py:71) 无条件展开实体 IDs；[numpy_backend.py:128](/Users/bhz/Code/reformulation/src/mipframe/frame/numpy_backend.py:128) 为选中 terms 分配整块 positions、重复 row IDs 和 gather 数组；[numpy_backend.py:219](/Users/bhz/Code/reformulation/src/mipframe/frame/numpy_backend.py:219) 递归求整批结果。join/group/distinct/sort 也有完整 dict/list/set 或 NumPy scratch。

`ctx` 被传入这些函数，但工作数组没有统一交给 WorkContext，普通 Frame Context 也没有同一套 ledger。`Context.check_size` 检查的是单个输出 record，而不是此前已构造的全量中间结果。

**实测：** 在 16 KiB Context 下执行 `rows.where(nnz == 2).select('row').head(1)`：

| 模型行数 | 输出行数 | 最大中间批行数 | 该批数组 payload | tracemalloc 峰值 |
| ---: | ---: | ---: | ---: | ---: |
| 128 | 1 | 128 | 2048 B | 24,652 B |
| 512 | 1 | 512 | 8192 B | 36,400 B |
| 2048 | 1 | 2048 | 32,768 B | 86,912 B |

这里最有力的证据是**新建中间数组本身的 32 KiB 已超过 16 KiB 预算**。tracemalloc 包含 Python 开销，仅作辅助观察；没有把 RSS 或 mmap page cache 当作工作集预算。

纯 filter 没有完整校验 barrier，本可分块找到首条结果；当前只有直接 source 的部分 limit 做了特殊处理。join/project 的完整 cardinality 检查仍必须保留，不能一概下推 head 来规避。

**修复方向：** 给 Frame execution 统一 WorkContext；算子按 block/selection 工作，blocking 算子使用 bounded runs；必要的完整输出通过资源 lease 保留，而不是全量 Python 容器隐式常驻。

### F5 · P2：certificate 暂存和部分高级 checker 的预算没有贯穿全流程

这类问题多数没有 SQL，但仍不符合“certificate size 相关工作位于受控 Data Plane”的完整要求。

- `LinearBatch` 的 keys、keyset、sides、parents、positions、typed buffers 在 `add()` 期间持续增长；资源 options 留到 `run()` 的执行阶段才传递。[compiler.py:116](/Users/bhz/Code/reformulation/src/mipframe/check/compiler.py:116)
- `linear_many` 从 Frame 迭代，不等于 bounded streaming。它仍累计全部 target keys、RHS、terms values、weights、sides、kinds，再生成目标和 witness arrays。部分 scatter 数组使用 WorkContext，但前后的 Python 容器不在同一预算下。[compiler.py:292](/Users/bhz/Code/reformulation/src/mipframe/check/compiler.py:292)
- `frozen_ints` 和普通 `ExactColumn` 构造会生成完整缓冲／副本。它们不是 SQL 违规，但大 certificate 的额外峰值内存不能忽略。[kernel.py:31](/Users/bhz/Code/reformulation/src/mipframe/check/kernel.py:31)、[columns.py:61](/Users/bhz/Code/reformulation/src/mipframe/data/columns.py:61)
- `round` 和 `cases` 对 `WorkContext(memory_mb,disk_mb)` 只做进入后立即退出，后面的计算不使用它。[compiler.py:408](/Users/bhz/Code/reformulation/src/mipframe/check/compiler.py:408)、[compiler.py:444](/Users/bhz/Code/reformulation/src/mipframe/check/compiler.py:444)
- solution checking 的输入已用 ExactSparse，但完整 violations 仍放 Python list，再经 `Frame.from_records` 展示。[finite.py:152](/Users/bhz/Code/reformulation/src/mipframe/check/finite.py:152)

**修复方向：** 让输入编译、字典编码、排序、校验、诊断发布共享一个资源上下文；将新增存储与借用的不可变输入区分计费。确实未支持的预算参数应明确其范围，不能只校验参数值后继续无约束执行。

### F6 · P2：refinement 仍写逐实体冗余 SQLite 表，且签名准备全量常驻

已迁移的部分应肯定：edge roles、colors、tokens、multiplicity ranges 的计算和访问已使用数组；`_intern` 是 Python 内存 interning，并非逐 token SQL 查找。不能把当前 refinement 描述成仍以 SQL 实现图细化主循环。

但 `_prepare` 仍构造全量 `row_records`、`var_records`，然后写 `row_data`、`var_data`。两张表在生产源码里没有读取方；`row_info()` 已改为从 scale array 和原模型数值计算。[refinement_store.py:407](/Users/bhz/Code/reformulation/src/mipframe/ir/refinement_store.py:407)、[refinement_store.py:454](/Users/bhz/Code/reformulation/src/mipframe/ir/refinement_store.py:454)、[refinement_store.py:332](/Users/bhz/Code/reformulation/src/mipframe/ir/refinement_store.py:332)

2048 行／2049 变量模型的第一次 depth=1 refinement 向这两张表写 **4097 行**；即使数值循环无 SQL，这仍是分析调用的 O(m+n) 冗余序列化和持久化。第二次同配置调用只出现 3 条控制 SQL，说明问题主要在冷分析／新配置，不应声称每次热读取都重写这些表。

其他边界：

- `definitions` 每种 color 一条，并带 header、digest、token count；color 数最坏可随 m+n 增长。它与 payload 的界线应明确，不能仅凭名为 definition 就认定是常数级 manifest。当前 `_flush_definitions` 在完整数值轮次后执行，应与内部数学循环区别对待。[refinement_store.py:236](/Users/bhz/Code/reformulation/src/mipframe/ir/refinement_store.py:236)
- `_template_partition` 的 `prepared/counts/modes/ranges`、`_interned` 和 token bytes 保留全轮数据；`_flush_definitions` 再拼接 chunks。memory_mb 没有约束这一完整工作集。[refinement_store.py:490](/Users/bhz/Code/reformulation/src/mipframe/ir/refinement_store.py:490)
- indexing 的大型关系列已为 typed arrays；但发布时仍整体构造 state JSON，含 cards、candidates、exact pools，并重写 catalog。需要测量这些 payload 的增长，不能只验证 SQL statement 数量为常数。[indexing/store.py:202](/Users/bhz/Code/reformulation/src/mipframe/ir/indexing/store.py:202)

**修复方向：** 先删除无读取方的逐实体写入；保留小型版本/参数/artifact 目录。规模相关签名目录、exact pools 和 witness payload 使用数组或 packed artifacts；签名 interning/partition 使用受预算约束的分块排序与归并。

### F7 · P2：Data Plane 反向依赖上层接口，底层尚未形成独立边界

静态 AST 统计发现，data 包中有 21 个导入语句指向 frame、11 个指向 check、3 个指向 alg、2 个指向 ops。计数是语法位置数，不是独立依赖包数。

关键实例：

- `ArrayAlgorithmStore` 继承包含 SQL 开库、staging、聚合功能的 `frame.executor.Context`，并负责生成 Frame 和 Report。[data/algorithm.py:1](/Users/bhz/Code/reformulation/src/mipframe/data/algorithm.py:1)
- `data.graph` 输入 Frame、调用 `read_columns`、构造 Frame、抛出带 Report 的异常。它混合了接口适配和 CSR kernel。[data/graph.py:1](/Users/bhz/Code/reformulation/src/mipframe/data/graph.py:1)
- `data.reduction` 反向导入 `ops.reduction` 的公开结果类型。[data/reduction.py:64](/Users/bhz/Code/reformulation/src/mipframe/data/reduction.py:64)
- `data.columns` 从 `check.kernel` 导入 exact conversion，而 `check.kernel` 又依赖 data columns。这是通过局部 import 缓解加载顺序的结构性环依赖，不是已证明存在运行时 import 异常。[data/columns.py:65](/Users/bhz/Code/reformulation/src/mipframe/data/columns.py:65)

**影响：** 从底层模块命名无法判断是否会触发 Frame 执行、SQL 或报告物化；复用 kernel 和统一资源管理都更困难。

**修复方向：** 将 exact scalar、schema-independent ID/column protocols、资源和基础异常放在下层；Frame→typed inputs 与 typed results→Frame/Report 由上层 adapters 负责。数值 kernel 只接数组、IDs、workspace 和必要的标量选项。

### F8 · P2：新旧基础设施并存，部分是死代码，部分仍有不同语义

生产源码调用扫描结果：

| 遗留部分 | 当前状态 | 处理建议 |
| --- | --- | --- |
| `store/algorithm_store.py` 的 SQL AlgorithmStore/KeyIndex/Membership | 未发现生产源码外部调用；测试仍直接使用 AlgorithmStore | 迁移必要测试后删除／隔离，避免误用 |
| `check/workspace.py`、`check/evidence.py` | 前者只找到测试使用；后者未找到生产调用 | 确认兼容承诺后清理；不能报成当前主 proof 热路径 |
| `frame.adapters.sql_frame`、`Target.prepared` | 未发现生产调用 | 删除孤立旧适配器 |
| DSU 的 SQL 分支 | 现有生产调用传入含 work 的 context，走数组分支 | 收窄接口，移除 SQL 数值状态实现 |
| refinement 旧 schema | `key_chunks/arrays/template_ranges` 等持久表及多个 temp scratch 表只建不使用 | 删除旧 schema 和失效说明 |
| `control/catalog.py` 的 artifacts/derived_caches/proofs/edits 表 | 创建但未发现写入方；真实发布由 sidecar/native manifest 承担 | 选择统一的 catalog 权威；不要以空表冒充已落实的控制层 |
| Graph 与 ArrayGraph | 两条均可达；transitive reduction 用新 ArrayGraph，其他 graph 操作用 Graph | 统一图构建和预算接口，不要作为“死代码”直接删 |

定位：[algorithm_store.py:17](/Users/bhz/Code/reformulation/src/mipframe/store/algorithm_store.py:17)、[workspace.py:7](/Users/bhz/Code/reformulation/src/mipframe/check/workspace.py:7)、[union_find.py:7](/Users/bhz/Code/reformulation/src/mipframe/ops/_kernels/union_find.py:7)、[refinement_store.py:107](/Users/bhz/Code/reformulation/src/mipframe/ir/refinement_store.py:107)、[control/catalog.py:6](/Users/bhz/Code/reformulation/src/mipframe/control/catalog.py:6)、[data/graph.py:149](/Users/bhz/Code/reformulation/src/mipframe/data/graph.py:149)。

`AlgorithmStore` 文件 425 行，workspace/evidence 另有 95 行；这些行数不是性能证据，只说明维护负担并非几个孤立 import。

另一组活跃重复是 `frame/executor.py` 与 `frame/numpy_backend.py`：两边都实现 group、join、distinct、sort 和相关校验，资源行为却不同。仅修一边会留下输入来源相关的行为差异。

### F9 · P2：禁 SQL 测试和 explain 没有覆盖真正的执行边界

[test_runtime_arrays.py:18](/Users/bhz/Code/reformulation/tests/test_runtime_arrays.py:18) 禁止 `Context.execute`，并 trace 主模型 db。这能检查一部分路径，但漏掉：

- `QueryStore` 的独立连接和 SQLIndex；
- refinement 直接使用的 `self.db.execute/executemany`；
- 在执行 kernel 之前已发生的 Frame input staging；
- 单个 SELECT 读取所有 certificate records 的规模成本。

[test_runtime_arrays.py:117](/Users/bhz/Code/reformulation/tests/test_runtime_arrays.py:117) 中的 IR 禁 SQL 检查同样只 patch Context，并不禁止 refinement 自己的 SQL。

`Frame.explain()` 的 backend 标签只依据 supported 判断，不能证明选中 ID 和上游 source 是数组；算法 provenance 的 `typed arrays` 也不能证明输入无 SQL。

**修复方向：** 对公开数值操作从输入构建开始做全连接／表级 allowlist 测试，记录 payload 行数与字节量；explain 分别显示 source storage、selection storage、operator backend、spill backend。普通失败诊断、低预算、冷 cache 和 warm cache 都应覆盖。

## 4. 动态证据汇总

下表是 SQL trace 条数，包含 PRAGMA/DDL/BEGIN；`executemany` 每行记一次。不是耗时，也不是全库性能评分。

| 探针 | q=128 | q=512 | q=2048 |
| --- | ---: | ---: | ---: |
| 数组 Frame 唯一性检查 | 140 | 524 | 2060 |
| 显式 ID 列表构造、长度与遍历 | 137 | 521 | 2057 |
| 逐行 `row.terms.count()` | 1280 | 5120 | 20480 |
| 低内存 from_records 输入 | 136 | 520 | 2056 |
| 已回退 Frame 的 sum（额外开销） | 267 | 1035 | 4107 |
| 三个 certificate Frame 构造 | 536 | 2072 | 8216 |
| compact linear_rows 检查 | 0 | 0 | 0 |
| 第一次 template refinement depth=1 | 341 | 1109 | 4181 |
| 相同配置第二次 refinement | 3 | 3 | 3 |

两个不能靠 SQL 条数判断的案例：

- path 从已回退的 Frame 读 q 条边：每个规模只有 1 次 SELECT，但传输量为 O(q)。
- linear_many 从三个已回退的 Frame 编译：每个规模只有 3 次 SELECT，但分别读 q、2q、q 条记录。

为证明算术确实进入 SQLite callback，还统计了 `exact_sum.step`：三个规模分别是 **128、512、2048 次**。

## 5. 已经做对、值得保留的部分

1. **统一数值事实和生命周期。** SnapshotData mmap dense/sparse/name arrays，Facts 通过 acquire 借用，子快照与 proof 不必复制完整矩阵。[snapshot.py:75](/Users/bhz/Code/reformulation/src/mipframe/data/snapshot.py:75)、[kernel.py:64](/Users/bhz/Code/reformulation/src/mipframe/check/kernel.py:64)
2. **精确字典与运算分开。** SQLite 是持久字典，运行时使用 int64/rational arrays 与 bigint 文本；精确运算没有逐系数数据库查询。[numbers.py:10](/Users/bhz/Code/reformulation/src/mipframe/data/numbers.py:10)
3. **compact proof 主核。** ragged offsets、source IDs、exact multipliers 和按需 ProofRef 有效减少逐 target 对象；linear residual 的超预算路径使用 ExactSparse 外部归并。[kernel.py:490](/Users/bhz/Code/reformulation/src/mipframe/check/kernel.py:490)、[exact_sparse.py:15](/Users/bhz/Code/reformulation/src/mipframe/data/exact_sparse.py:15)
4. **edit 的 base + delta 路线正确。** 固定变量替换编译 sparse shifts 和 masks；overlay 共享 base，无隐式数值 SQL materialization。[edit/substitution.py:81](/Users/bhz/Code/reformulation/src/mipframe/edit/substitution.py:81)、[store/overlay.py:39](/Users/bhz/Code/reformulation/src/mipframe/store/overlay.py:39)
5. **新的 primitive 资源框架可继续推广。** WorkContext fork 共享 ledger，typed arrays 支持 mmap，结果有 lease；新 DAG reduction 已使用这一框架。[runtime.py:29](/Users/bhz/Code/reformulation/src/mipframe/data/runtime.py:29)、[reduction.py:64](/Users/bhz/Code/reformulation/src/mipframe/data/reduction.py:64)
6. **native/export 的主方向正确。** typed proof/delta artifacts、不可变 base 文件复制、独立 readback、先验证再发布都值得保留。MPS readback 自建预算内名字索引，而不是按系数查询名称 catalog。[native5.py:161](/Users/bhz/Code/reformulation/src/mipframe/store/native5.py:161)、[readback.py:10](/Users/bhz/Code/reformulation/src/mipframe/store/readback.py:10)

## 6. 建议整改顺序与验收门槛

不建议推倒重写，也不建议继续只优化个别 benchmark 路径。优先收敛共享入口和资源边界。

**第一阶段：截断已证实的 SQL Data Plane。** 先替换 SQLIndex，再迁移 Frame.check，最后把 from_records 的 spill 改为 typed artifacts。三者覆盖面远大于孤立 kernel 微优化。保留 exact 数学语义、选择顺序、引用身份、完整 cardinality/coverage 校验。

**第二阶段：统一执行契约。** Frame、graph adapters、certificate compiler、IR preparation 共享 WorkContext；新的 ArrayGraph 能力推广到通用 graph 构图；把数据转换和输出发布也纳入预算，而不仅是最内层 kernel。

**第三阶段：清理架构债务。** 删除未使用 SQL tables/helpers，明确 catalog 权威；将 Frame/Report 适配移出 data；复用精确 scalar 与 typed input/output 协议。失效 docstring、命名和测试同步修改。

**第四阶段：建立能失败的边界测试。** 至少满足以下条件后再声称完成架构迁移：

| 验收项 | 必须满足的行为 |
| --- | --- |
| source 类型不改变数值后端 | list、generator、array、Frame、低预算 Frame 输入均进入 typed Data Plane |
| 禁数值 SQL | 执行公共 Frame/check/graph/edit 数值路径时不创建数值临时表，不通过 SQL 读取规模相关计算 payload |
| 允许控制 SQL | 仅对明确列出的身份、版本、名称、manifest 等操作开放；不允许借 JSON/BLOB 隐藏大型 witness 或图关系 |
| 冷热路径分别覆盖 | 冷构建、warm reuse、save/load 后首次操作都检查；初始目录持久化单独计量 |
| 尺度测试 | 增大 n/m/nnz/W；同时观察 SQL payload、数组及暂存分配、临时磁盘，不只数 SQL 或测试耗时 |
| 资源预算可兑现 | 对超预算工作 spill 或明确失败；不以 `with WorkContext(...): pass` 或单 record size check 代替总工作集约束 |
| explain 可信 | 报告输入/选择存储、物理算子和 spill 策略，能够揭示隐藏的 SQLIndex 和 SQL Frame |
| 诊断完整 | 高失败率和大证书的完整 violations/dependencies 保持 typed/bounded，不因显示限制丢掉检查范围 |
| 数学回归 | 保持现有 629 个通过测试的契约，并补边界测试；跳过项不能计为验证成功 |

## 7. 复现与限制

在仓库根目录运行：

```sh
PYTHONPATH=src /Users/bhz/miniforge3/bin/python3 \
  docs/audit/control_data_probe_20261005.py \
  /tmp/mipframe_architecture_probe.json
```

脚本只在临时目录创建模型和运行缓存。可将 Python 路径替换为安装了项目依赖的解释器。报告 JSON 中保存源文件摘要和复现脚本摘要，便于识别后续代码变化。

本次没有重新跑所有大型公开模型耗时 benchmark，也没有证明所有算法的渐进时间或极端故障行为。测试全绿仅说明当前测试覆盖内的行为正确；本报告的强结论来自可达调用链和动态 SQL/数组证据。静态未发现生产调用的项目应在删除前结合兼容承诺复核，不能把“未发现调用”扩大解释为任意外部用户都不可能导入。

最终判断：**数值存储底座已成立；严格的控制／数据隔离尚未成立；最需要修的是 Frame/selection/compiler 的公共边界，以及统一的资源与依赖方向。**


## 整改进度更新（2026-10-05）

本报告上文保留整改前工作树的原始结论与测量，源码链接的行号可能随整改变化。
当前实施、F1–F9 对照、验证与未完成项见
[ARCHITECTURE_REMEDIATION_20261005.md](ARCHITECTURE_REMEDIATION_20261005.md)。

本轮已经移除 Frame SQL 计算路径、SQLIndex、重复数值后端和旧 native readers，
推进 typed 输入、证明与 IR artifact 存储，并新增全连接 SQL 探针和依赖边界测试。
**整体整改尚未完成**：剩余工作集预算和性能回退仍需解决，不将阶段性结果标为通过全计划验收。
