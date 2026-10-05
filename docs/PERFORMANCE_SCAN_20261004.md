# 初始 model scan 实施与性能验收

日期：2026-10-04。默认 IR 已改为 schema 0.4 的 level-0 scan；native v3 不变。

## 结果

`read()`、`load()`、`Model(path)` 不再调用 refinement、连通分量或 indexed discovery。
旧的 structure/topology/indexed 属性和 objective role_support 在显式访问时计算。
默认 repr、Mapping 遍历及 JSON/dict 导出仅访问 scan，执行过分析也不会改变默认输出。
完整精确 local-shape/name-template 查询通过带成员关系的 Frame 提供。

| 输入 | 纯数值加载 s | 加载＋scan s | scan 增量 s | 相对增量 | scan 加载峰值 RSS MiB | 首次 depth-1 查询 s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| test32.mps | 1.605 | 1.641 | 0.036 | 2.26% | 53.7 | 0.727 |
| test33.mps | 6.162 | 6.560 | 0.398 | 6.46% | 96.7 | 7.613 |
| test34.mps | 6.599 | 7.189 | 0.590 | 8.94% | 109.9 | 6.483 |

三个模型均达到 scan 增量不超过纯数值加载 10% 的目标。此结果是本机测量，
不构成任意模型、环境或输入顺序下的固定耗时保证。test34 的新读取路径没有进入
此前阻塞加载的 indexed discovery。

## 测量口径与复现

- Python 3.13.13 | packaged by conda-forge | (main, Apr  8 2026, 02:29:07) [Clang 19.1.7 ]
- 每个输入分别运行三次 baseline 和三次 scan，使用独立进程，交替两者顺序。
- baseline 只禁用 scan observer 和 IR 包装；解析、term 归并、CSR/CSC、文件 hash
  与 canonical-content 校验完全相同。每组运行核对数学内容版本一致。
- 表中加载耗时取三次中位数；scan 增量为两个中位数之差，包含最终 scan 汇总。
  同一顺序遍历内的校验与统计没有使用 profiler 强行拆分计时。
- JSON 逐次记录 parse、store build、read、显示耗时、峰值 RSS、输入 SHA256、
  数学内容版本与运行源码摘要。首次结构查询单独运行，调用 `m.families(depth=1).count()`；
  它的耗时和内存不计入 read 指标。文件系统缓存不是强制冷缓存。
- 原始记录：`benchmarks/scan-20261004/accepted-timings.json`（本地产物，不随库分发）。

```sh
python scripts/benchmark_scan.py \
  --data-root /path/to/reform_test/data \
  --output benchmarks/scan-repeat/timings.json
MIPFRAME_MODEL_DATA_ROOT=/path/to/reform_test/data \
  python -m pytest -q tests/test_model_scan_external.py
```

## 数据核验

- test32：576 rows、18,380 vars、109,706 A-nnz；7,282 fixed vars、11,036 个精确
  `[0,1]` bounds。摘要包含 start、completion、jobexec、precedence 与两类 rescap 模板。
- test33：160,633 rows、6,250 vars、371,240 A-nnz；objective-nnz 独立为 6,212。
  160,625 条 `<= 0`、nnz=2、系数多重集 `{-1,+1}` 行保留精确计数。
- test34：106,954 rows、53,593 vars、266,805 A-nnz；53,592 个二元变量、1 个连续变量。
  三个实际输入均在禁止 refinement/topology/indexed 调用的测试下通过。

## 计算与精度边界

scan 复用必要的内容校验遍历，按数值 ID 累计系数频次；大计数向量使用临时 mmap。
行级统计借用 ndarray 视图，避免逐行创建新的 memmap 包装对象。基本计数、域、界、
行列度、系数符号与数值范围保持精确。top-K 采用有界 Space-Saving，超预算返回误差界；
宽行指纹明确标注 candidate，名字明确标注 lexical hypothesis。精确完整分组另行查询。

core store 仍使用现有外部排序归并 terms；本次没有把整个 read 宣称为严格线性。
工作预算不是进程 RSS 上限，也不包含 core store、degree workspace 和分离导出结果。
默认显示上限为 6,000 字符，不会因调大展示选项而重新扫描模型。

## 回归

新增测试覆盖默认路径禁止分析、独立缓存与失败重试、精确 Frame 的成员/序列化、
频次误差界、候选标记、词法遗漏、slot 范围、低预算 mmap 清理、空/自由/range 行、
整数域、系数抵消、输出限长及超长有理数的显示与精确 JSON 序列化。
README、用户指南、迁移说明、离线 help 和 agent 工作流已同步。

最终 IR 补丁在从 HEAD 创建、仅包含本次改动的独立工作树中验证：
**567 passed、8 skipped（135.45 秒）**。其中 test32–34 使用其原始文件目录另行
执行，**3 passed（20.60 秒）**；help 生成、一致性检查及 diff 空白检查均通过。

共享工作区同时出现另一组 overlay/delta 修改。共享目录最近一次全量测试为
565 passed、8 skipped、1 failed；失败为 `store/overlay.py:149` 引入 public facade，
违反已有依赖规则，随后单独复测仍失败。本次未修改该并行实现，因此独立补丁
通过不等于当前混合工作区全量通过。共享目录的 help 已按当前源码重新生成并检查。

本地验证日志：`benchmarks/scan-20261004/isolated-tests.txt`；
独立补丁：`benchmarks/scan-20261004/validated-ir.patch`。
