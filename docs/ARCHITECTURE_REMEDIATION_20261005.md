# 控制层／数据层整改实施状态

日期：2026-10-05。目标版本：**1.2.0，未发布**；native 格式：**v7**。

**状态：已实施主要 SQL 数据路径迁移，但整体整改尚未完成，不能按计划验收。**
禁止把“数值探针零 SQL”“Data 包 AST 检查通过”或“数学回归通过”单独等同于资源契约完成。高级搜索、indexed discovery、证明 DAG 元数据和 edit 构造仍有规模相关的 Python 容器；性能也存在未解决回退。

**最新执行决定：用户已确认 test33 速度足够，随后要求停止全部性能测试。性能测试和调优已停止，未启动新一轮五组对比。** 以下 r8 数据保留为历史证据，不能代表目前工作树；最新阶段记录见下一节。停止测试不等同于通过原性能门槛或完成整体架构整改。

## 性能整改后续记录：按用户要求停止

- Reduction 保留已验证的 typed 输入列，certificate bridge 不再重新执行上游关系；完成结果通过 lease 保有这些列。
- Frame 增加共享的分块 scan/filter/projection 和唯一键 join 输出；保持 bigint/rational 精确比较、稳定顺序，以及完整 cardinality/coverage 验证 barrier。
- Refinement 的小度数聚合、签名 interning、排序预算和 partition 发布去掉重复暂存；import 复用名称索引，避免重复编码和名字查询。
- 集合操作使用分块 membership 和 typed 结果列；稀疏投影使用 CSR/CSC 分块统计全部匹配，完整保留失败诊断。
- 定向回归曾发现稀疏 CSC 选择退化和 partition 数组归属问题，均已修复并通过对应测试。

最后已有的 public 单次测量如下；它们不是新一轮五组中位数，最终工作树还包括这些测量之后的少量改动，未再次测量：

| 流程 | r8 五组中位数 | 后续最后单次测量 | 测量候选 |
|---|---:|---:|---|
| test32 public | 9.085 s | 4.101 s | r30 |
| test33 public | 82.929 s | 17.124 s | r25；用户确认速度足够 |

本阶段定向测试分别完成 110 项 Frame/ops/边界测试、20 项 refinement 测试、70 项投影/矩阵/边界测试、62 项集合/primitive/边界测试，以及 55 项 parser/MPS/边界测试（5 项外部数据测试跳过）。这些集合有重叠，不相加作为唯一通过总数。

最后全量回归因停止性能测试而中断：**333 passed，80.55 s，退出码 2**；该套件包含性能相关测试，因此没有继续运行。未执行部分不计为通过，不能声称最新完整工作树全量回归通过。帮助目录已重新生成。最新工作树未重跑独立 SQL/实际分配探针，下面的 r8 探针结果仍只归属于 r8。

逐项记录、源文件摘要和已有测量见 [后续记录](audit/performance_followup_v7.json)，中断日志见 [全量回归中断记录](audit/pytest_v7_performance_followup_interrupted.txt)。

## 基线和变更范围

以审查时的完整工作树为基线，保留了原有修改和未跟踪实现，没有回退到 HEAD。

- 基线归档：`/var/folders/dp/92zcv0g50zg13k6bj5p2pqmc0000gn/T/mipframe-pre-v7-_u6cl8n6/working-tree.tar.gz`，旁有文件摘要和 Git 状态。
- 对照源代码、输入及环境由 `scripts/benchmark_runtime_pairs.py` 记录，当前冻结候选为 `current-r8`。
- 相对于完整基线，本轮修改 68 个、增加 26 个、删除 14 个 Python 源文件。路径与摘要见 [变更清单](audit/remediation_changes_v7.json)。这里没有把原来已有的 dirty changes 算成新实现。
- 原始审查基线：629 passed、8 skipped。原始 F1–F9 及 SQL trace 保留在 [审查报告](ARCHITECTURE_AUDIT_20261005.md)，不改写历史测量。

## F1–F9 的实施与验收状态

| 项目 | 实际实施 | 当前结论 |
|---|---|---|
| F1：Frame 检查 SQL staging | unique/function/product、domain membership、project 和完整 violations 改为 typed relations、排序和分组 | 已关闭审查中的 SQL 路径；完整失败与低预算回归覆盖 |
| F2：SQLIndex 与逐行 terms | OrderedIndex 保留首次出现顺序；membership 独立索引；单 ID、连续范围借用 view；terms 用 CSR/CSC | 已删除 SQLIndex；顺序、关闭后访问和逐行 terms 探针覆盖 |
| F3：from_records 自动回退 | 输入统一流式 typed builder，精确数值／字符串 packed 存储，预算不足 mmap；删除 `_sqlite_from_records` | list/generator 和低预算不再改变后端；SQL 隔离已覆盖输入到诊断 |
| F4：两个关系执行器及无界中间结果 | 删除 `numpy_backend.py`；统一 streaming filter/select/head 与 typed blocking operators；完整 join 验证 barrier；batches 共享 ledger | 执行器已收敛；全库 Python 暂存和实际分配审计仍未完成，不能标记全部解决 |
| F5：certificate 编译和高级 checker | LinearBatch 从 add 起使用构造时 workspace；Targets/CertificateBatch、Frame compiler、status、依赖列和 round_many 改 typed；solution violations/point bundle 改 typed | 主批处理路径已迁移；assumptions、cases、enumerate、部分 claim 编译及 proof DAG 仍需继续整改 |
| F6：refinement／indexed IR 持久化 | 删除 row_data/var_data 和逐 color definitions；BytePool 保存完整签名，typed tokens/modes/ranges；indexed cards/candidates/runs 用 RecordMap typed artifacts，SQLite 仅引用 generation manifest | 旧 SQL 数值 payload 已去除；indexed discovery 内部、record 解码及整体 manifest 注册表仍有无界工作集 |
| F7：反向依赖 | 精确标量、异常、资源下沉到 core；Data 不再导入 Frame/check/alg/ops/edit/ir/control/store 或 SQLite；适配代码移到 execution | AST 边界通过；execution 内仍有混合适配与数学状态的模块，需要进一步拆分，不能只靠移动文件宣称完全分层 |
| F8：遗留基础设施、格式和发布 | 删除 SQL AlgorithmStore／ProofWorkspace／SQL evidence、SQL adapter、DSU SQL 分支、重复 ArrayGraph、旧 native readers；统一 native7；proof artifact bundle；只读源独立 cache；refinement/indexed 失败清理 | 主要遗留后端已删除；catalog 发布职责、proof DAG、edit payload 与全部输出预算还未完全收敛 |
| F9：探针与 explain | 全 SQLite 连接探针；表及 payload 字节统计；AST 禁止反向依赖；explain 展示输入／selection／operators／barriers／spill；执行记录 ledger 峰值 | 可观测性明显补齐；所有算法入口和实际缓冲的独立追踪仍不足，运行统计未统一控制访问计数 |

## 已实现的公共和存储变化

1. `Frame.from_records`、直接 `RecordBatch` 构造及 `iter_batches` 使用 typed builders；中间结果按共享 ledger 分配，可 spill，batch 保留独立 lease。
2. 显式 Python 物化使用 `materialize_mb`，不再与执行 `memory_mb` 混用。通用工作默认 128 MiB，临时 spill 磁盘默认不限。通用最低预算 16 KiB；LP/MPS 导入仍要求至少 32 MiB。
3. 精确 interning 以完整值比较解决哈希碰撞；选择去重保留第一次出现顺序，引用仍携带 snapshot、version 和 axis。
4. 普通过滤 `head` 可以停止扫描；join cardinality、coverage 和 project 的完整校验不能被 `head(0/1)` 跳过。
5. Native v7 同时覆盖 base 与 overlay；所有旧 native 格式明确拒绝并提示重新导入，不开发 v5/v6 迁移器。
6. `BatchProof.write` 与 `PointReport.write` 输出版本化目录式 artifact bundle；显式 `to_spec` 受物化上限约束。加载历史证明不授予编辑权限，仍须 replay/audit。
7. 只读 native 源的派生缓存放到独立工作目录；重新选择 refinement 预算会创建相应 workspace，而非只改变 block-size 参数。
8. 校验和扫描和 native 映射向量的完整校验采用有界块，避免整列布尔临时数组。
9. Refinement 先写带唯一名称的 artifacts，再提交目录引用；失败删除本轮文件。Indexed 发布失败恢复记录导出状态，并清理临时目录文件。

迁移细节见 [API_MIGRATION.md](API_MIGRATION.md)。

## r8 历史验证证据

本节记录实际运行结果；跳过不算通过。正在完成的结果不会提前记为通过。

- 定向语义回归：65 个 refinement/family 测试通过；40 个边界、scan 和 refinement 测试通过；46 个发布、indexed、refinement 和边界测试通过。
- 最新全量测试：**640 passed、8 skipped，253.11 s**。命令为 `PYTHONPATH=src /Users/bhz/miniforge3/bin/python3 -m pytest -q -rs`。8 项跳过是未设置外部数据根目录；指定实际数据根目录后，已单独补跑 **8 passed、24 deselected，121.68 s**。源码侧合计 648 项通过，24 项 deselected 是该补跑命令有意排除的既已测试用例，未计为新增通过。补跑见 [外部数据结果](audit/pytest_v7_external.txt)。完整运行日志见 [全量结果](audit/pytest_v7_full.txt)。
- 独立探针：[脚本](audit/control_data_probe_v7.py)、[JSON](audit/control_data_probe_v7.json)。对 q=128/512/2048，分别检查输入构造、选择、逐行 terms、成功/失败检查、图、certificate、冷／warm／load 后缓存；本次最终重跑已通过：五个数值阶段全部零 SQL；cold/warm/load 后 refinement 分别为 66/3/49 条控制语句，在三个规模上保持不变。
- 测试包含完整失败诊断、join + head(0)、稳定顺序、精确 rational/bigint、磁盘耗尽、batch 父对象关闭后的读取、只读存档不变、旧版本拒绝及证明 replay。
- 独立分配观测：[脚本](audit/allocation_probe_v7.py)、[JSON](audit/allocation_probe_v7.json)。在 16 KiB 参数、q=128/512/2048 时，已拦截 NumPy owned-array 峰值为 6/12/10 KiB；Python tracemalloc 峰值约 58/128/173 KiB。后者包含库对象和 mmap 元数据，不能直接等同于数值缓冲，但说明不能用 ledger 的 9–14 KiB 自报值宣称全库内存已经符合计划。该钩子没有覆盖所有 ndarray 运算或 C 扩展分配，完整实际分配审计仍未通过。

## r8 历史性能：当时未通过

冻结基线、交替独立进程、五组采样的驱动已经实现。第一轮 test32 fixed 完成五组，后续 public 发现 Ref ID 类型错误而停止；不能把未完成的 test33 或 public 组记为通过。

第一轮 test32 fixed 中位数：基线约 1.66 s、早期候选约 2.34 s。后续定位并修复了 primitive Ref 类型、literal membership 反复扫描、refinement 中逐 token 查表等问题。

调优中的 test32 public 单次测量从约 60.3 s 降到约 9.24 s，但相应冻结基线单次约 3.38 s。单次调优测量不是最终五组验收结果。**仍超出 `max(20 ms, 10%)` 门槛，必须继续处理，不能以禁 SQL 为理由接受回退。**

冻结候选 `current-r8` 的全部 40 个独立子进程已运行完毕，源码摘要前后相同。每组五次、交替 baseline/current；未清理 OS 文件缓存，不把这组数据称为磁盘冷启动。四组均未通过性能门槛：

| 流程 | 基线中位数 (s) | 当前中位数 (s) | 回退 | 当前 RSS 中位峰值 (MiB) | 当前临时磁盘抽样峰值 (MiB) | 当前 artifact (MiB) |
|---|---:|---:|---:|---:|---:|---:|
| 32-fixed | 1.635 | 2.204 | 34.7% | 99.9 | 17.7 | 40.2 |
| 32-public | 3.416 | 9.085 | 166.0% | 97.3 | 18.9 | 42.5 |
| 33-fixed | 5.170 | 10.306 | 99.3% | 301.5 | 56.6 | 134.9 |
| 33-public | 15.759 | 82.929 | 426.2% | 293.4 | 66.4 | 156.1 |

[各阶段汇总](audit/runtime_pairs_v7_summary.json)、[全部测量](audit/runtime_pairs_v7_measurements.json)、[环境、输入及源码摘要](audit/runtime_pairs_v7_environment.json)。临时磁盘每 0.5 秒抽样，可能漏掉短时峰值；RSS 包含解释器和 mmap 页，不等同于工作预算。

可比阶段的主要回退在导入、IR 冷构建、关系 staging/validation 和图输入适配。test33 首组的 certificate gather 从约 0.19 s 增至 14.31 s：bridge 在 reduction 之后重新执行了上游 Frame 关系和完整验证，应把已验证的 edge-to-row 列随结果 lease 保留。线性证书核本身在该流程约 0.02 s。`certificate_write` 是显式 reformulation JSON 导出，前后格式已变化，其时间和大小须结合格式差异解释；这不影响导入和关系计算已经超标的结论。

合成规模五组对比也已完成：1,000/2,000/4,000 个独立三角形，分别为 3q 行、3q 变量、6q 非零、q targets 和 2q witnesses。导入中位数分别从 0.165/0.218/0.323 s 增至 0.301/0.452/0.755 s；4,000 组时 scan 也超出门槛。数学检查和共享 base+delta 断言通过，性能不通过。[合成汇总](audit/scaling_pairs_v7_summary.json)、[全部测量](audit/scaling_pairs_v7_measurements.json)、[环境](audit/scaling_pairs_v7_environment.json)。该输入同时增长 n/m/nnz/W，不代替每一维独立变化及大量 distinct values 的资源验收。

## 未完成的具体工作

以下每项都属于原计划范围，不是建议性增强：

- `check/compiler.py` 的 claim/assumptions/intervals、`check/finite.py` 的 enumeration state、cases 的父证明和剩余 assumptions：仍存在完整 Python dict/list/set；绑定真实 WorkContext 尚不足以约束这些分配。
- `check/artifact.py` 的 proof DAG 节点／对象索引和 `compact.py` 的递归依赖遍历：需 typed adjacency、去重遍历、完整深链与共享父节点规模测试；目前并非所有路径都支持任意大证明。
- `execution/search.py`、`integer.py`、`isomorphism.py`：仍有 `np.intersect1d/union1d`、全量 candidates、Counter、搜索栈和映射等未登记工作缓冲。
- `ir/indexing/discovery.py`、`operators.py`、`validation.py`、部分 algebraic/compressed IR：仍需有界分组和 signatures；RecordMap 解码单条复杂记录仍可能完整物化；domains/table registry 和 generation manifest 会复制已有目录。
- `data/builder.py`：typed 名称／数值字典和 MPS row registry 已迁移；系数 run 文件、部分 NumPy 快路径、解析器缓存和输出暂存仍需独立实际分配／磁盘核查。
- edit 的新增实体、replacement 暂存、proof bindings 与 dependency closure：主要 base+delta 保留，但尚未全部改成 typed artifacts／共享预算。
- Native/proof 的大 DAG JSON 元数据、发布期间的所有输出文件、存量 artifact 的计费和清理规则还需统一；catalog API 尚未完全成为各控制事实唯一发布入口。
- 全入口 keyword-only resource 参数、显示物化上限、控制访问运行统计和最低预算说明尚须最后逐项核对；通用 null bitmap、独立 typed protocols，以及单条超大精确值在解码前的拒绝，也尚未按完整契约交付。
- 默认预算性能回退和所有规模维度 n/m/nnz/W 的实际缓冲追踪仍未验收。

## 剩余架构工作顺序

1. 保持现有数学回归和零 SQL 边界；性能测试及调优已按用户要求停止，不自动重启 test32/test33 benchmark。
2. 收敛 compiler/finite/proof DAG 的剩余容器，补深链、共享父证明、大 W、磁盘耗尽与失败清理测试。
3. 迁移搜索／isomorphism／integer，以及 indexed discovery/validation 的工作状态；不得通过移动包或仅增加参数掩盖未计费工作。
4. 完成 import/edit/发布工作集与 catalog 权威审计，再做全实际分配追踪和全部入口资源测试。
5. 完成剩余正确性和资源验证，逐条关闭本文件中的未完成项，最后才标记架构整改完成。性能基准只有用户另行要求时才重新启动。
