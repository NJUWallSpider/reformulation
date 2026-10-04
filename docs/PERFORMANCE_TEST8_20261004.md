# test8 proof/edit 性能调查与优化建议

日期：2026-10-04。调查对象：当前工作树的 mipframe；输入为 `reform_test/runs/test8-20261003-213957/input/model.lp`。

**结论：先修复语义闭包缺索引、同进程 diff 重放，以及 proof/report 的重复构造，再考虑 delta snapshot。** 当前主要问题确实是执行结构，而不是 Python、SQLite 或精确有理数本身。但原来的“177 秒 build”定位不准确；直接据此启动 overlay 大重构，会错过更便宜、收益更大的改动。

## 实施与验收（2026-10-04 更新）

本轮已实现主要 proof/edit 优化，达到批准的 **60–100 秒完整 workload** 目标。
同一 Python 3.14.7、同一输入及证明集合，实施前隔离源码基线为 **475.46 秒**；
实施后独立进程顺序运行 **65.26 / 64.20 / 63.87 秒**，中位数 **64.20 秒**，
相对该基线约 **7.4×**。基线是一次运行，优化后为三次运行；不把它解释为跨版本普遍加速保证。
计时包含初始 read/indexing、数值提取、全部证明、两次编辑、diff、save 和 eager load；
不包含 LLM 推理，也不包含另行执行的强制 replay 审计。

| 阶段 | 实施前基线 s | 实施后三次中位数 s |
|---|---:|---:|
| read + setup | 10.59 | 8.695 |
| 5,670 resource/deletion proofs | 96.99 | 9.506 |
| 8,946 branch proofs | 80.95 | 8.558 |
| 4,473 case discharge | 31.95 | 7.509 |
| 两次 staging | 7.66 | 5.410 |
| 两次 summary/语义验证 | 12.37 | 10.886 |
| middle build | 25.87 | 4.186 |
| final build | 9.06 | 1.333 |
| 两个同进程 diff | 166.38 | 0.000 |
| 4,473 reverse proofs | 31.75 | 3.846 |
| save + eager load | 1.89 | 4.214 |

各阶段中位数之和不等于总时间中位数。最终模型仍为 12,450 rows / 4,914 vars /
125,055 nnz，数学内容指纹仍为
`dad1643fda059c357f4a9892f42446483130c1350398eb0d0e31545e07c905fd`。
中间 LP SUBSET/MIP SAME，最终 LP SAME/MIP SAME，objective SAME，optimal value PRESERVED。

已交付：

1. **logical build 默认行为**：完整数值、证明及 LP/MIP 关系验证后发布独立 native
   snapshot；`.ir` 延迟初始化且成功后只计算一次，失败可重试。`prepare="indexed"`
   保留 eager 行为；read/load 仍 eager。资源注册与结构分析解耦。
2. **进程内验证凭证与 lazy diff**：成功 build 或完整 replay+target 核对后，保存
   实际 source/target store 与身份绑定、语义结果及分页坐标映射；不保留整个 Edit、
   proof graph 或 source Model。`verify="auto"` 仅对匹配的活对象复用；磁盘加载
   不能授予凭证，`verify="replay"` 强制完整审计。明细 Frames/degree stats 按需生成。
3. **规范化 batch**：add 直接保存经过校验的 inputs，不创建临时 INCONCLUSIVE
   Report；run 从已规范化输入执行共同 exact kernel，不重复摄入证书。临时表跨 target
   复用，结果归档一次；scalar / linear_many / replay 共用精确验证逻辑。
4. **证明证据视图**：sources、claims、terms、assumptions、bounds、dependencies
   直接读取规范化证明关系，避免先 materialize 成 Frame 再逐项复制为 JSON。
   `for_target` 仍按需构造 Report，已有序列化格式保持兼容。
5. **精确 sparse accumulation**：小 residual 用有保守字节预算的字典，减少逐项
   SELECT/UPDATE；±1 系数快捷路径；超预算或大 rational 仍走 exact SQLite spill。
6. **case runtime 复用**：共享 scratch workspace，直接读取已验证依赖表，避免为
   每个分支依赖 Frame 创建执行数据库；保留一般有限整数区间、assumption union、
   scope/domain、覆盖与目标一致性验证。
7. **存储与语义索引**：保留已有 deps/roots 索引，为 outputs/factclaims 增加覆盖列；
   content digest 使用 4 MiB 保守预算的数值字典预读/LRU，canonical 字节流不变。
   本轮基线已经包含此前补入的基本语义索引，不能把旧调查的全部索引收益重复计算。

全量测试首次执行为 509 passed / 5 skipped / 2 failed；两项失败仅因最后一次源码修改
使生成的 help 校验和过期，重新生成后两项均通过，合计 **511 项测试通过、5 项跳过**。
新增回归覆盖 logical 数值/save 不启动分析、lazy 失败重试、Edit 关闭后的 diff、
auto/replay 明细一致、磁盘 artifact 无运行时信任、恒定 scratch table 数量，以及
16 KiB 下宽 residual 精确落盘。既有篡改 lineage、case scope、失败原子性、取消、
磁盘限额及 5,000 结果低 FD 压力测试均纳入全量回归。

独立诊断运行已完成两个完整 lineage 的强制 replay，并重新核对目标全部数值事实：
中间与最终语义都与 auto diff 一致。带 SQL tracing 的额外审计分别耗时 71.15 / 30.13 秒，
这些审计时间不计入正常 workload。这也说明新加载 artifact 的首次独立 diff 仍会付出
完整验证成本，性能收益主要针对 Agent 同进程刚生成的编辑结果。

实际 SQL tracing：resource batch **1,470,800 条 SQL、29 张 CREATE TABLE**；
branch batch **1,219,430 条 SQL、29 张表**；reverse batch **481,836 条 SQL、29 张表**；
4,473 cases 总计 **21 张表**。每个线性 batch 的表数已满足 <=100 目标。
这里统计的是 SQLite 实际执行语句，包括 PRAGMA/事务，不能与旧 cProfile 的
Connection.execute 方法调用数当作完全相同口径。

三次峰值 RSS 为 699.0 / 887.9 / 802.2 MiB，中位数 802.2 MiB；基线为 1369.1 MiB。
该数值包含 workload 保留的全部报告和模型，工作内存参数不等于进程 RSS 上限。

**尚未达到的单项目标及后续边界：** case discharge 中位数约 7.5 秒，高于 3 秒分项预算；
resource SQL 数量约 147 万，未达到 50 万目标。
本轮没有实施完整的跨证书 source/bounds 编译缓存、稳定 proof-node DAG 去重和
unconditional frontier 缓存，也未全面替换通用递归 memory accounting。总体目标已达到，
后续应以新的 profile 决定是否继续这些工作。没有引入 overlay、Merkle、GMP/Rust、
新数据库、CSC row index 或 dtype 格式迁移；当前 build 已降至数秒，暂不需要这些大改。
这依然是精确 sparse kernel 按 target 执行的规范化批处理，不是浮点 WA-T 矩阵验证。

可复现材料：`benchmarks/test8-optimization-20261004/`（本地忽略目录），包含基线源码副本、
source-before/source-final SHA256、阶段 timings、三个验收日志、完整测试日志和独立审计。
重跑正常工作负载：

```bash
PYTHONPATH=src /Users/bhz/miniforge3/envs/mipframe/bin/python scripts/profile_test8.py \
  --output /tmp/mipframe-test8-new-run
```

`--sql-counts` 单独统计实际 SQL；`--audit-replay` 在计时工作负载后额外完整重放两个
lineage。使用这些诊断选项的耗时不混入正常三次验收中位数。

以下保留原始调查记录；其中“建议”“尚未实现”和 Python 3.13 的数值均对应实施前。

## 实测范围与可比性

- 从原始 agent session 提取 Python cells，重放 5,670 resource/deletion proofs、8,946 branch proofs、4,473 case discharge、4,473 reverse proofs，以及两个 edit、两个直接父子 diff。
- 当前 API 已移除 `check.batch('linear', ...)`，重放仅将这个调用改成 `check.linear_batch(...)`。候选集合用原始数值事实重建，不依赖已变更的 family API。资源覆盖结果仍为 882 个 U=6、756 个 U=9、378 个 U=12。
- 所有批量证明均 `require()` 通过；中间模型为 16,923 × 4,914、176,274 nnz，最终模型为 12,450 × 4,914、125,055 nnz。两个 diff 分别得到 LP SUBSET/MIP SAME 和 LP SAME/MIP SAME。
- 最终数学内容指纹与原始 run **完全相同**：`dad1643fda059c357f4a9892f42446483130c1350398eb0d0e31545e07c905fd`。save/load 后也一致。
- **初次调查阶段没有修改 `src/mipframe`，没有修改原始 run；随后获准实施，结果见文首实施记录。** 工作树原本已有大量未提交修改；保存了 HEAD、状态及源码 SHA256 清单，调查后再次核对源码未改变。增加的索引仅存在于实验进程的临时语义表中。
- 原 run 使用 Python 3.14.7 环境；本次使用 Python 3.13.13、macOS arm64、当前工作树。因此不能把本次与历史秒数直接解释为版本回归。性能归因、同进程索引对照、相同算法的 storage 对照才是主要证据。
- wall-time 主重放没有启用 cProfile，保留少量函数计时与指定 SQL 的计时/EXPLAIN；另对完整的 5,670 个 resource proofs 使用 cProfile。**cProfile 的耗时有明显额外开销，只用于调用数量和热点归因，不能与无 profiler 的 wall time 混加。**
- wall time 是单次顺序重放，未做整套多轮统计；storage 微实验各做 3 次并取中位数。索引对照在 baseline summary 后运行，存在暖缓存影响，但查询次数相同且执行计划明确由 SCAN 改为索引 SEARCH。

## 1. 首先纠正 177 秒的测量边界

原日志中 176.627 秒的 cell 实际执行：

```python
edit = m.edit()
...                       # 4,473 add_row + 756 drop_rows
display(edit.summary())   # 第一次 prepare，包含完整语义验证
middle = edit.build()     # 复用 prepared 结果
diff_add = middle.diff(m) # 从 lineage 重放证明，再次 prepare
display(diff_add)
```

所以它不是单独一次 `build()` 的耗时。原始所有 Python cells 总计 **409.750 秒**，其中 **30.005 秒**是 resource batch 超时、随后重新执行的失败尝试。四个主要成功 cell 合计 355.017 秒。

本次分阶段 wall time：

| 阶段 | 秒 |
|---|---:|
| LP read，含初始 IR/index | 8.91 |
| 数值提取及候选重建 | 2.43 |
| 5,670 resource/deletion proofs | 53.74 |
| 8,946 branch proofs，含 target 构造 | 68.17 |
| 4,473 cases | 23.02 |
| middle staging | 3.73 |
| middle summary/prepare | 36.22 |
| middle build，已有 prepared plan | 27.32 |
| middle diff | **153.59** |
| 4,473 reverse proofs，含实体查找 | 33.60 |
| final staging | 2.74 |
| final summary/prepare | 6.85 |
| final build，已有 prepared plan | 8.11 |
| final diff | **38.57** |
| native save | 0.075 |
| native load，含已有分析缓存恢复 | 2.21 |

不计额外的索引实验，本次上述工作共 **469.29 秒**；其中 proof/edit 主流程为 **455.66 秒**。这不是原 run 的完整重演：没有重复原先的探索/help、30 秒失败尝试、最终解见证和 LP roundtrip audit。

**两个 diff 合计 192.17 秒；两个 build 合计 35.43 秒。** 如果不先拆开调用边界，很容易把重复验证的时间归咎于 snapshot 物化。

## 2. 已通过对照验证：语义依赖表缺索引

位置：`src/mipframe/edit/semantics.py:177`。

中间 summary 的内部时间：metadata 0.157 秒、结构检查 0.760 秒、语义验证 **35.298 秒**。关键查询对每个 proof 查它的依赖，但 `deps.proof` 没有索引：

```sql
SELECT 1 FROM deps d
LEFT JOIN facts f ON f.id=d.fact
LEFT JOIN nodes n ON n.id=d.parent
WHERE d.proof=? AND coalesce(f.mip,n.mip,0)=0
LIMIT 1;
```

| 查询 | 次数 | 原计划/耗时 | 补索引后计划/耗时 |
|---|---:|---|---|
| MIP dependency closure | 14,175 | SCAN d / 17.252 s | SEARCH d(proof=?) / 0.031 s |
| LP dependency closure | 5,229 | SCAN d / 7.168 s | SEARCH d(proof=?) / 0.016 s |
| 按 conclusion digest 找 provider | 5,229 | SCAN outputs / 0.934 s | digest → outputs.claim / 0.009 s |
| root 与 operation proof binding 匹配 | 5,229 | SCAN roots / 0.161 s | roots.proof / 0.006 s |

实验在 `Prepared.table()` 创建的表上给适用的 `proof/claim/fact/binding/op` 列加索引，没有替换算法、删除检查或改变输入。再次运行同一个 prepared edit 的 semantic，返回结果完全相同，耗时 **9.084 秒**，约为原来的 **1/3.9**。

正式补丁应精确设置索引，而不是照搬实验的通用建索引 hook。优先：

```text
deps(proof)
outputs(claim, proof)
roots(proof, binding)
factclaims(fact, claim)
```

其中前 3 类的查询计划已直接验证。`factclaims` 用于未闭合 fact 的查找，当前算例的收益不宜夸大。`claims(digest)` 已存在，不需要重复建。

middle diff 中又发生同样的扫描：语义验证 **39.87 秒**，仅 LP/MIP dependency 查询就 **26.89 秒**。因此这不是一次性 startup 成本。

后续才考虑用 worklist、反向依赖邻接表替代全体未完成节点的反复 fixed-point 扫描。LP/MIP 的可用节点不同，必须保留 domain 过滤、最终 retained-fact grounding 和循环拒绝；不能简单按“报告 PROVED”直接关闭义务。

## 3. 最大单项：同进程 diff 重放已验证证明

路径：`edit/diff.py:28` → `Lineage._restore()` → `Lineage.proof()` → `Report.replay()` → `_prepare()` → `_verify_target()`。

即使 target 刚由同一个进程的可信 Edit 成功生成，diff 仍重新加载操作、逐 root 导出临时 JSONL、重放 proof、重建语义依赖和最终事实。middle diff 的 153.59 秒中，39.87 秒是再次执行 semantic；其余还包括 lineage 恢复/证明重放、完整 target 核对和 diff Frames 构造。现有计时没有将这 113.72 秒进一步全部归因给某一个函数，不能声称它们都是纯 checker 时间。

建议引入**仅限内部、不可由持久化字段授予的已验证 Edit 结果**：

1. build 成功后保留被冻结的操作映射、验证结论及其 source/target 身份绑定。
2. 对这对仍然有效的同进程 source/target，diff 复用这些结果；变化 Frames 按需生成。
3. 从磁盘加载的模型继续走独立 replay/核对路径；需要外部审计时可以显式要求 fresh verification。
4. 不把 manifest 中的 `PROVED` 或 `semantic` 字段直接当信任凭证，不允许先加载 JSON 再“升级”为已验证运行时对象。

这是当前比 overlay 更值得做的架构改动。它还可以减少临时文件、证明副本和内存。已经生成的证明 DAG 最好有稳定的 archive/node 身份；不要仅靠 Python wrapper 的 `id(report)` 去识别同一证明。

## 4. build 内部：lazy analysis 有价值；复制 CSR 不是大头

中间 build 27.32 秒的分解如下。内层项目嵌套于外层，不能直接全部相加。

| 项目 | 秒 | 说明 |
|---|---:|---|
| IR + indexed | **20.785** | 全新的结构分析 |
| lineage 写入 | 3.877 | 独立 proof event streams |
| 首次 content_digest | 0.969 | 写完后生成数学内容版本 |
| NumericalStore reopen | 1.051 | 其中再次 content_digest 0.954 s |
| CSR/CSC arrays | **0.129** | 不含前面的 entries 流输出 |
| 剩余构建工作 | 约 0.51 | 包含 metadata、entries、文件 hash 等 |

prepared 阶段还会完整遍历矩阵并逐 nnz 查变量映射、更新计数，但本例 `_structure()` 只有 0.76 秒。不能把 0.129 秒解释为“所有物化成本”，也不能把完整矩阵扫描当成这次的主瓶颈。

补充的独立 cold-analysis 重放进一步把 20.79 秒拆开：refinement compute **1.45 秒**，CompressedIR **2.14 秒**（包含 refinement），indexed prepare **0.68 秒**，indexed discover **11.50 秒**，indexed publish **6.46 秒**，operator recover 在本例几乎没有工作。`publish()` 包含清理、逐记录 content_hash、commit 与数据库 backup，尚未继续分解，不能把 6.46 秒全部说成 hash 或 IO。后续若优化“确实需要分析”的场景，应先检查 discovery 产物体量、重复 domain/card 表示以及 publish 路径；不是先优化 refinement arithmetic。中间 native 的已有缓存 warm load 为 **3.46 秒**；最终 native 为 **2.21 秒**。

中间 snapshot 的 matrix arrays 约 4.2 MiB、metadata 约 0.82 MiB；indexing.sqlite 却约 **165 MiB**，lineage.sqlite 约 **77.8 MiB**。本例的额外体量主要来自分析与证明产物。

建议先支持 `build(prepare="logical")` 一类显式模式：执行数学验证，生成数值快照，但不自动分析。第一次实际请求 `ir/indexed/families` 时才计算；已有缓存可直接恢复。这样中间 build 可免去实测的约 20.8 秒工作，剩余路径约 6.5 秒；这是时间归因预算，尚不是实现后的 benchmark。

当前文档和 `tests/test_indexed_eager.py` 明确约定 eager，不能只删 `_build_ir()` 而不修复访问器、资源释放、错误时机和文档。先提供 opt-in，再决定默认值迁移。

## 5. batch：持久存储已共享，执行仍重复构造 scalar 世界

路径：`check/batch.py:443` 和 `check/batch.py:500`。

目前 `add()` 建 ProofContext、拷贝 claim、生成 INCONCLUSIVE Report、capture、drop transient tables；`run()` 再 materialize request、建新的 ProofContext、调用 scalar kernel、生成另一套 Report/evidence、capture，再 drop tables。

5,670 个 resource proofs 的 cProfile 数据：

| 项目 | 调用次数 | 累计秒数（含 profiler 开销） |
|---|---:|---:|
| SQLite Connection.execute | 4,613,978 | 30.81 |
| Batch.capture | 11,340 | 29.54 |
| finish_report | 11,340 | 21.02 |
| 递归 size() | 23,176,251，含递归调用 | 21.87 |
| drop_transient | 11,340 | 11.56 |
| Fraction 构造 | 3,619,035 | 9.02 |
| accumulate | 5,670 | **5.95** |
| math.gcd | 4,036,032 | **0.30** |
| NumericalStore.num | 999,936 | **0.29** |

整个 profiled cell 为 101.39 秒，无 cProfile 的相同 cell 为 53.74 秒。各项有嵌套，不能相加。创建 table/raw_table 共约 **170,117 次**，对应约 30 张临时表/目标；平均约 814 次 SQLite execute/目标。问题远不止 Python 的 `for` 本身。

优先改造顺序：

1. **直接 stage normalized requests**，不先制造带完整 evidence 的 INCONCLUSIVE Report。
2. batch 生命周期内复用固定 scratch schema 或有预算的内存 accumulator，避免逐目标 DDL/DROP 和反复扫描 sqlite_master。
3. status、scope、claim、sources、dependencies 只保存一份；evidence Frames 直接查询 normalized relations，按需展示，减少 `finish_report → capture → JSON → for_target` 复制链。
4. 共享 source row、bounds、canonical claim 和 number 编码，使用 compact certificate program。按工作集分块，超预算才 spill。
5. 然后才优化 exact sparse accumulate：整数/±1 快路径、小 support merge、共享 multiplier × coefficient 结果；不需要第一步就实现通用 WA sparse matmul。

本次 staging/run 时间为 resource 18.14/34.95 秒、branch 24.93/37.47 秒、reverse 14.22/18.82 秒。只加速 run 会留下明显的 staging 成本；benchmark 必须同时覆盖 `add()` 和 `run()`。

**新增优先项：内存计量。** `frame/executor.py:size()` 递归遍历了大量本来就有已知 schema 的记录。对 canonical scalar 保存一次大小，对同质块按类型/字节数计量，避免同一 term 在各层反复递归测量；仍保留单值上限、低内存 spill 和 disk budget。不能为了快直接关闭这些检查。

## 6. cases：23 秒确实应优化，但不能省略语义边界

4,473 次 cases 单独花了 **23.02 秒**，平均约 5.15 ms。

当前 `Checker.cases` 仍经过通用 `checked()` 路径：临时数据库和 ProofContext、目标/分支结论/assumptions 拷贝、等价核对、前置依赖展开、evidence 生成、archive capture 和 scratch 回收。最终 archive 已共享，问题是每次构造和复制过程。

建议建立轻量 case node，引用已有 canonical target、branch node 与 domain facts；缓存每个验证节点的 unconditional prerequisite frontier。在 shared archive 中一次发布，必要时增加 cases batch。

不能只检查两个 status 为 PROVED。现有 API 支持一般有限整数区间、继承/额外 assumptions 及其 union，且必须防止 conditional fact 泄漏、domain bounds 被删后继续使用、跨 snapshot 引用和循环闭包。目标应是减少重复表示，而不是缩窄或削弱 contract。`<1–2 秒` 可以作为后续验收目标，目前没有实现结果支持该承诺。

## 7. 对其余建议的逐项评估

| 建议 | 评估与优先级 |
|---|---|
| Immutable base + delta overlay | 长期合理，当前不是第一项。数值物化非主耗时；先消除重复验证和自动分析。 |
| IR/refinement/indexing lazy | 高优先级，有直接的 20.8 s middle build 证据。注意 eager API 迁移。 |
| numbers pool 常驻 | 原始仅 8 个数、最终仅 12 个。运行时 LRU 已几乎完全命中，不是 proof bottleneck。更应修 content_digest 的独立 N+1 查询。 |
| Fraction → GMP/int64 rational | 暂缓。gcd 仅 0.30 s/profile；大量 Fraction 构造来自 TEXT/JSON 往返。先减少构造，再按小整数、真正分数、超大整数分别测。未安装或实测 GMP，不声称其收益。 |
| Columnar batch execution | 高优先级，但先去 staging/report/DDL 重复，再判断是否值得上 native kernel。 |
| Columnar/lazy Reports | 方向正确，已有 normalized shared archive 和 lazy for_target；应优化中间物化与 evidence 重复，而非假设仍是 list[独立数据库]。 |
| cases 仅为 metadata operation | 可做更轻，但必须处理 assumption union、完整 domain 覆盖和依赖有效性。不能机械套用只支持 [0,1] 的简化检查。 |
| CSC row_idx | 小规模收益低。当前是 NumPy 向量化 searchsorted，不是逐 nnz Python 二分。大规模或频繁列扫描时再考虑。 |
| 自动 uint32/uint64 | 适合后期大模型 IO 优化；本例数组只有数 MiB。需要更新固定 `<i8`/`<qq`、文件校验、format compatibility，并分别判断 pointer、var id、number id 的上限。 |
| bulk SQLite / 消除 N+1 | 高价值。缺索引已实测，proof 中大量逐 term/record SQL 也明确；不等于必须更换数据库。 |
| SQLite PRAGMA 优化 | 部分已做：scratch journal OFF、archive synchronous OFF；Edit 使用 journal DELETE 保护每次 staging 的 rollback。不要全局统一关 journal，否则破坏事务语义。 |
| trusted runtime fingerprint | 可避免新建后立即 reopen 的重复校验；但本例单次约 1 s，优先级低于 trusted diff reuse。 |
| Merkle / chunk hash | 延后到 overlay/identity 一起设计。当前 version 是 ordered canonical content digest，换成 edit-history hash 会改变身份语义；不能静默替换。 |
| DuckDB/Polars/Rust | 暂不迁移。当前的百万次细粒度 SQL、DDL、重复 proof materialization 会随语言/数据库迁移保留下来。 |

## 8. storage 小实验：区别相对倍数和绝对收益

在最终 native（125,055 nnz、12 个 numbers）上，各做 3 次：

| 实验 | baseline 中位数 | 实验中位数 | 解释 |
|---|---:|---:|---|
| content_digest：逐 qid SELECT → 一次加载原始 numerator/denominator 字符串 | 0.695 s | 0.205 s | **3.39×，且 digest 完全相同**；很适合小补丁 |
| 遍历全部 coeff_id：现有 LRU → dict lookup | 6.72 ms | 1.35 ms | 相对很快，绝对只省约 5 ms/scan |
| 扫描全部 CSC columns：searchsorted → 已算好的 row index | 8.92 ms | 1.39 ms | 只省约 7.5 ms/scan；新增 1,000,440 bytes int64 index |

row-index 实验不计预计算/持久化成本，实际端到端收益只会更小。number pool 实验不包含复杂任意精度分布，也不能推广成“100k 个不同 rational 一定只占几 MB”；应按字节预算选择全量或分块池。

content_digest 的低风险改法仍保留原始流的顺序、JSON 编码和每个数的规范字符串；无需改变 version schema，也无需 Merkle。

## 9. Overlay 的设计边界

overlay 可作为后续 Agent 多代 snapshot 的架构目标，但需要先解决以下真实接口约束：

- `Ref(uid, version, axis, id)`、现有行/列密集 ID、删除后的坐标映射、lineage map 必须保持一致；“加个 deleted bitmap”不能独自解决所有查询和 identity。
- 大量现有代码直接依赖 `model.db`、单组 CSR/CSC ndarray 和连续 row_ptr。需要明确的 store/view 抽象，逐模块适配，而不是只改 Edit.build。
- 子 snapshot 当前保证独立可用，parent.close 不应把子模型的 mmap/proof backing 一起关掉。结构共享需要独立 lease/refcount。
- 数值 delta 的构造可以接近 O(|delta|)，但 proof validation 仍至少要处理新增证明和受影响的依赖；不能把整个正确性验证也许诺为严格 O(|delta|)。
- 明确链长阈值、compaction 条件、CSC overlay 路径、save 独立持久化、失败清理和低内存策略。
- 数学 content identity 与 storage identity 应分别定义；Merkle 方案需要规范分块和版本迁移，不能让等价内容因为编辑历史不同产生语义混淆。

建议先做“省分析但仍独立物化”的 logical build，保留当前 native format。等 profile 显示数值物化在剩余时间中成为大头，再实施 overlay，风险和验证范围都更可控。

## 10. 推荐实施顺序与验收

| 顺序 | 交付项 | 收益依据 | 风险/投入 |
|---|---|---|---|
| P0 | semantic 缺失索引 + 对应查询计划回归 | 35.3 → 9.1 s 已在同输入对照实现 | 小；不改数学算法 |
| P0 | 分阶段 timings / agent tool timeout 配置 | 原 run 浪费 30 s retry；避免再把 diff 算到 build | 小 |
| P1 | 同进程 verified Edit result + diff reuse | 两次 diff 共 192.2 s | 中；需清晰可信边界 |
| P1 | logical build、lazy structural analysis | 两个 build 的自动分析共 26.14 s | 中；涉及 eager contract |
| P1 | batch 直接请求表、固定 scratch、lazy evidence | 17 万次 DDL、461 万次 SQL、重复 Report | 中；最高持续工作量收益 |
| P1 | typed/block memory accounting | profiler 中递归 size 累计 21.87 s | 中；必须守住预算与 spill |
| P2 | case nodes、proof DAG 共享与 lineage 去重 | cases 23.0 s，中间 lineage 77.8 MiB | 中；需兼容 replay/serialization |
| P2 | digest number prefetch + trusted internal reopen | 指纹 3.39× 已测，绝对约半秒/最终 hash | 小至中 |
| P3 | worklist closure、bounded exact batch kernel | 消除剩余反复图遍历和 SQL arithmetic | 中至大 |
| P3 | delta snapshot / compaction | 多代、大模型收益需另测 | 大；store/identity/lifetime 改造 |
| P4 | GMP、dtype、CSC row index、native extension | 本例尚不足以支持优先投入 | 以新 profile 为准 |

**不要把这些收益相乘。** 消除 diff 后，diff 内的索引收益已经包含在被省掉的时间里；lazy analysis 后，优化同一 analysis kernel 也不能再算一次。

本次 proof stages（resource、branches、cases、reverse）本身就有 **178.53 秒**。所以只改 snapshot/build，即使将所有 edit/diff 时间变为零，也达不到整次 60–100 秒。

可采用三层验收预算，均是待验证目标而不是已经实现的提速：

1. **小补丁阶段：**索引后的 middle semantic ≤12 s；指纹保持逐位一致；解释计划不再按 proof 全扫 deps。已有实验足以支持这一级。
2. **执行路径阶段：**加上可信 diff reuse 和 lazy analysis，整套 proof/edit 可把约 **210–240 s** 作为初期预算。约 210 s 来自当前耗时减去两次 diff、自动分析和一次 middle semantic 的实测可消除工作；还要为新的 diff fast path 留时间，不能当成保证。
3. **proof engine 阶段：**batch/report/cases 一起改造后再争取 **60–100 s**。**20–60 s 属于下一阶段挑战目标**，需要真正的批量执行、共享 DAG、减少序列化后重新实测。现阶段没有依据承诺整体 10–30×。

验收应同时记录 wall/CPU、DDL/SQL/解码次数、峰值 RSS、FD、临时数据库大小和 lineage 大小。当前主重放进程峰值 RSS 约 1.36 GiB，包含三代模型、保留的 reports/diffs 和额外索引实验；这是现场观测，不能据此判断泄漏，也不能将每次调用的 memory_mb 当成整个进程 RSS 上限。

正确性回归至少覆盖：两阶段 exact 指纹/语义相同；range/equality/directional certificates；额外和继承 assumptions；conditional leakage；删除 domain bounds；自证明/循环/失去 retained premises；跨 snapshot；篡改保存状态后必须 replay；parent.close 与子 snapshot 生命周期；低内存 spill；事务失败无部分发布；lazy analysis 失败可恢复。现有 `test_edit.py`、`test_case_discharge.py`、`test_proof_resources.py`、`test_indexed_eager.py` 可作为主要基线。

最终建议：**保留 Python + SQLite + mmap，把“每个 proof 都重新建表、重新写报告、重新解码、随后再重放一遍”改掉。** overlay 值得做，但这次证据不支持把它放在第一位。

## 复现与证据

运行目录：`/Users/bhz/Code/reformulation/benchmarks/test8-profile-20261004/`（仓库原本忽略 benchmarks 下的本地产物）。

- `source-state.json`：工作树来源与源码 hash。
- `original_cells.json`：原始 Python cells、duration/status/output；提取自原 agent session。
- `baseline/timings.json`、`baseline.log`：完整分阶段重放；索引实验单独计时，不算入 baseline 总计。
- `baseline/sql.json`：查询次数、耗时、EXPLAIN QUERY PLAN。
- `profile/resource_batch.prof`、`profile/resource_batch.txt`：完整 5,670 proofs 的 cProfile。之后的 branch profile 被主动中断，已命名为 `branch_batch.interrupted.prof`，不用于结论。
- `storage.json`：三轮 storage 对照和等价断言。
- `ir/timings.json`：中间 native 的 warm load / cold analysis 分解（单独后续实验）。
- `original.native`、`middle.native`、`baseline/final.native`：本次生成的快照副本；原始 run 未改动。

复现命令（从仓库根目录运行，输出目录需新建）：

```sh
PYTHONPATH=src python scripts/profile_test8.py --output benchmarks/test8-repeat --index-ablation --sql-profile
PYTHONPATH=src python scripts/profile_test8.py --output benchmarks/test8-cprofile --profile
PYTHONPATH=src python scripts/profile_test8_storage.py benchmarks/test8-profile-20261004/baseline/final.native --output benchmarks/test8-storage-repeat.json
PYTHONPATH=src python scripts/profile_test8_ir.py benchmarks/test8-profile-20261004/middle.native --output benchmarks/test8-ir-repeat
```

这里的 `--indexes`（可选）对整次执行加实验索引；`--index-ablation` 则先测 baseline，再只对 middle semantic 做索引对照。profiling 脚本还提供 lineage restore/target verification 的细分 hook，便于下一次迭代进一步拆解 diff。完整 `--profile` 会显著增加运行时间；本次仅使用已完成的 read/resource profile 作为详细归因证据。
