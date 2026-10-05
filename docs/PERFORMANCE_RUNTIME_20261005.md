# test32/test33 runtime v5 性能复测

本报告只描述冻结实现及确定性场景的结果；完整架构仍有未完成项，见 [实施状态](RUNTIME_V5_STATUS.md)。

## 完整回放

单位：秒、MiB。时间与 RSS 均为独立进程测量的中位数；括号为范围。

| 场景 | 每个版本次数 | 基线时间 | 当前时间 | 加速 | 基线峰值 RSS | 当前峰值 RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 32-fixed | 5 | 2.248 (2.235–2.508) | 1.079 (1.075–1.105) | 2.08× | 112.656 (110.375–114.062) | 82.406 (79.484–84.078) |
| 32-public | 10 | 28.187 (27.701–30.489) | 2.898 (2.841–3.238) | 9.73× | 123.258 (119.141–127.656) | 90.656 (86.594–92.422) |
| 33-fixed | 5 | 11.081 (10.918–11.212) | 4.724 (4.659–4.754) | 2.35× | 482.547 (479.391–491.750) | 252.781 (245.188–261.484) |
| 33-public | 5 | 69.925 (67.270–72.652) | 18.386 (17.936–18.748) | 3.80× | 493.391 (487.984–502.922) | 279.672 (275.000–288.672) |

## 公开操作阶段中位数

阶段计时存在嵌套，不能相加代替 complete。check、证书编译和 commit 是函数级计时；certificate_kernel 还包含编译和前置编辑。

| 阶段 | test32 基线 | test32 当前 | test33 基线 | test33 当前 |
| --- | ---: | ---: | ---: | ---: |
| read | 1.681586 | 0.666127 | 6.703257 | 2.882046 |
| read_and_witness_preparation | 1.694914 | 0.692892 | 6.838745 | 3.143035 |
| inspect_query | 0.011878 | 0.006531 | 0.938615 | 0.035271 |
| ir_cold | 1.399719 | 0.975355 | 11.865764 | 7.860134 |
| ir_cached | 0.000839 | 0.000552 | 0.007069 | 0.003697 |
| structural_query | 0.773599 | 0.108290 | 7.117204 | 1.400885 |
| graph_set | 23.529446 | 0.495331 | 38.464494 | 3.969883 |
| candidate_compile | 0.266099 | 0.208894 | — | — |
| certificate_kernel | 0.139579 | 0.118104 | 0.804131 | 0.170296 |
| edit | 0.020142 | 0.016091 | 0.424187 | 0.348946 |
| certificate_write | 0.041195 | 0.022182 | 0.433454 | 0.355516 |
| write | 0.042773 | 0.048793 | 0.325325 | 0.279827 |
| readback | 0.056270 | 0.068835 | 0.522934 | 0.335098 |
| total | 0.391934 | 0.368597 | 3.499788 | 1.694102 |
| save | 0.061991 | 0.023745 | 0.505358 | 0.053631 |
| load | 0.065670 | 0.024428 | 0.449956 | 0.065966 |
| complete | 28.186913 | 2.898381 | 69.924993 | 18.385927 |
| check | 0.058980 | 0.049416 | 0.686588 | 0.060007 |
| edit_all_commits | 0.044671 | 0.053059 | 0.379852 | 0.274950 |
| certificate_compile | 0.000430 | 0.000400 | 0.106953 | 0.099558 |
| save_overlay | 0.055982 | 0.032812 | 0.607258 | 0.097217 |
| load_overlay | 0.029733 | 0.043918 | 0.371880 | 0.092805 |

## 磁盘与 artifact

临时磁盘每 0.5 秒采样一次，短暂峰值可能漏采；该数值不是精确上界。

| 场景 | 基线临时磁盘 MiB | 当前临时磁盘 MiB | 基线输出 MiB | 当前输出 MiB |
| --- | ---: | ---: | ---: | ---: |
| 32-fixed | 3.646 (3.646–4.481) | 4.452 (4.452–4.452) | 5.440 (5.440–5.440) | 11.356 (11.356–11.356) |
| 32-public | 9.838 (9.838–9.838) | 7.523 (7.523–7.523) | 5.440 (5.440–5.440) | 15.353 (15.353–15.353) |
| 33-fixed | 23.504 (23.286–23.735) | 21.305 (21.305–21.305) | 45.083 (45.083–45.083) | 66.281 (66.281–66.281) |
| 33-public | 64.300 (64.300–64.300) | 48.297 (48.297–48.297) | 45.083 (45.083–45.083) | 100.926 (100.926–100.926) |

## 阈值检查

- 32-fixed: 完整耗时改善=True，RSS 改善=True；阶段中位回退超过 max(10%, 20ms)：无。
- 32-public: 完整耗时改善=True，RSS 改善=True；阶段中位回退超过 max(10%, 20ms)：无。
- 33-fixed: 完整耗时改善=True，RSS 改善=True；阶段中位回退超过 max(10%, 20ms)：无。
- 33-public: 完整耗时改善=True，RSS 改善=True；阶段中位回退超过 max(10%, 20ms)：无。

## 测量与复现

输入、audit、320,000 contributions 的固定 witness、源码和 harness 均记录 SHA-256。每次启动独立 Python 进程，两个版本串行交替；不清空文件系统缓存。首次结构分析与缓存命中分别计时。没有运行 agent、test34 或求解器。

性能基线为 baseline-synchronized：从实施开始前和用户完成并行 check/edit 修改后的两个快照重建，保留用户完成的优化，仅撤回可定位到本迁移的修改。它不是 git HEAD，也不是原 B1 分钟级历史值。具体恢复文件、方法和必要 guard 适配见 baseline-synchronized.json。该重建过程是比较结果的限制。

运行目录：

- `benchmarks/runtime-v5/paired-final`：environment.json、measurements.json、summary.json、每个进程日志及实际输出。
- `benchmarks/runtime-v5/paired-extra32`：environment.json、measurements.json、summary.json、每个进程日志及实际输出。

复现命令（仓库根目录，相同解释器）：

```sh
python scripts/benchmark_runtime_pairs.py --baseline benchmarks/runtime-v5/baseline-synchronized --current benchmarks/runtime-v5/candidate-final --out benchmarks/runtime-v5/reproduce --witness-33 benchmarks/runtime-v5/witness-33.json --repeat 5
```

## 数学与 SQL 验证

- test32 独立 verifier：382 行、4,792 列、41,133 个矩阵非零元；另有 2 个目标系数。全部固定变量、one-hot、整数声明和删行证书通过。
- test33 独立 verifier：90,000 个 targets，320,000 contributions（55,000 条长度 3，20,000 条长度 4，15,000 条长度 5）；70,633 行、6,250 列、191,240 个矩阵非零元，保留 DAG 无替代路径。
- SQL trace 单独运行，不参与计时。两场景 Frame fallback 打开次数为零；query、graph、check、edit 无 SQL。读取和 IR 完成结果采用批量 catalog 写入。唯一带 ORDER BY 的 SELECT 是一次角色字典批量读取。
- SQL trace 和独立日志分别保存在 sql-32-current、sql-33-current-r2 以及各目录的 independent 子目录；test32 独立日志在 r9-32-public/independent。

未完成的统一资源预算、残留 Python 容器和测试迁移，不因这些场景通过而视为验收完成。

## 额外验证与剩余瓶颈

1×、2×、4× 的独立短路径模型，各三次新进程测量（5,000、10,000、20,000 个 targets）：4×/1× 中位耗时比为 read 3.17、scan 2.59、compile 3.81、check 2.62、edit 3.93。该结果未见这些路径出现平方增长；不能外推为所有图算法或通用 Frame 算子的复杂度证明。原始记录：`benchmarks/runtime-v5/scaling-final/summary.json`。

公开 test33 仍以首次 IR（约 7.86 秒）和图/路径生成（约 3.97 秒）为主要耗时。Frame/IR 的全局资源预算迁移也仍有工作，见实施状态。

当前输出 artifact 大于基线：v5 保留可独立搬迁的完整基础矩阵、证明依赖和已完成分析缓存，overlay 不压缩为只剩 live entities 的单一模型。测试输出还同时保存 base 和 overlay 两个独立 native 包。这是持久化能力与磁盘占用的取舍，不能宣传为所有资源指标都下降。

原五次 test32-public 中 load_overlay 中位数约 50.9 ms，基线约 29.5 ms，超过阈值约 1.4 ms且波动较大。追加五次相同来源与 harness 的配对测量后，合并十次分别约 43.9 ms 与 29.7 ms，低于 20 ms 回退阈值。保留全部原始样本，不删除慢样本。

迁移后的 `examples/exact_certificates.py` 和 `examples/exact_edits.py` 已实际运行通过，包括紧凑证书 replay、native v6 加载与独立 LP audit。

## 最终验证记录

- 完整测试：535 passed、71 failed、8 skipped，86.89 秒。71 个失败用例名称均见于初始基线；仅按名称对照不能证明失败原因完全相同，也不等于全套通过。原始 XML/log 和对照 JSON 在 `benchmarks/runtime-v5/validation/`。按用户边界，没有把仍在迁移的定向测试失败全部加入本轮修复范围。
- 重点运行时、紧凑证明、Frame、图/集合、indexed 和 help 测试：126 passed。
- `python scripts/build_help.py --check`：404 topics、357 public bindings，校验通过。
- `git diff --check` 通过；工作区 src 文件与 measured candidate-final 的冻结哈希逐个一致。

以上性能和数学场景验收已完成；**整个实施计划尚未全部完成**，不能将本报告当作统一预算、全部算法和所有公开测试的最终验收签字。


## 三个 primitives / native v6：本次实施与验收

本节使用任务开始时冻结的当前仓库作为基线，**不是前面 v5 报告使用的更早基线**。
基线完整测试为 604 passed、13 skipped；最终完整测试为 **629 passed、8 skipped**，
95.37 秒。没有保留未处理的测试失败。Native 复制失败测试的故障注入已迁移至同时覆盖
clone 和流式复制的统一入口；未删除失败清理覆盖。`build_help.py --check` 通过
（414 topics、367 public bindings），`git diff --check` 通过。

功能已实现并有回归覆盖；**整个计划尚未验收完成**。时间门槛存在明确回退，不能将图阶段
的加速、规模测试成功或账本峰值当作全计划通过。

### 五轮交替独立进程

每个场景、每个版本各 5 次，合计 40 个新进程。串行运行，交替版本顺序；无求解器、
不清除系统页缓存、不并行运行测试。保存和重新加载 overlay 纳入完整流程。冻结代码、
输入、harness 哈希及全部样本保存在 `benchmarks/primitives-v6/paired/`，汇总也嵌入
本报告同名 JSON 的 `primitives_v6` 字段。阶段存在嵌套，不能求和替代完整时间。

| 场景 | 基线完整时间/s | 当前完整时间/s | 基线 RSS/MiB | 当前 RSS/MiB |
|---|---:|---:|---:|---:|
| 32-fixed | 1.016 | 1.578 | 82.6 | 98.8 |
| 32-public | 2.769 | 3.335 | 92.0 | 106.7 |
| 33-fixed | 4.672 | 5.084 | 252.9 | 279.1 |
| 33-public | 17.746 | 15.539 | 296.6 | 325.9 |

公开 test33 的“归约 + 分组路径 + 证书构造”由 3.918 秒降至 1.539 秒，约 2.55×；
其中新归约与完整 witness 为 1.350 秒，数组证书 gather 为 0.179 秒。真实行证书检查
由 0.162 秒降至 0.020 秒，仍检查真实系数、bounds 和整条路径的连接条件。

固定证书 test32 的消元选择为 0.111 秒，编译为 0.012 秒；完整流程从 1.016 秒增至
1.578 秒。旧版 auto 在这个场景触发“剩余变量不足 60%”的 canonical 启发式，新版
按本计划必须选择支持的 patch。该语义变化解释了部分 I/O 差异，但不取消回退事实。
新源字节/index 的捕获、typed 编辑验证、保存/加载成本也纳入了完整流程。

按中位数差值 `> max(10% × baseline, 20ms)` 判定，以下阶段超过门槛：

- `32-fixed`：`read`、`read_and_witness_preparation`、`certificate_kernel`、`edit`、`write`、`readback`、`total`、`complete`、`edit_all_commits`。
- `32-public`：`read`、`read_and_witness_preparation`、`certificate_kernel`、`edit`、`write`、`readback`、`total`、`complete`、`edit_all_commits`。
- `33-fixed`：`read`、`edit`、`save`、`edit_all_commits`、`load_overlay`。
- `33-public`：`certificate_write`、`save`、`edit_all_commits`。

`certificate_kernel` 在 test32 包含前置编辑和消元；`edit_all_commits` 是嵌套函数计时。
新增的 planning/serialization/selection/compilation 没有旧版同名阶段，不伪造一一对照。

### 规模与低内存测试

以下每行均在独立新进程中执行。矩阵输入为预构建 immutable CSR/CSC，准备时间独立计量，
不把此项当成千万 nnz 的解析器性能。图测试完整校验每条路径的端点、连接和 retained membership。
下表“工作峰值”是共享账本登记值；持久化输入、源文件、Python runtime 和 mmap 页面另计。

| 场景 | 主阶段/s | 工作峰值/MiB | 临时磁盘峰值/MiB | RSS/MiB |
|---|---:|---:|---:|---:|
| local-1m-16 | 0.002504 | 2.0042 | 0.000 | 47.8 |
| local-10m-16 | 0.001695 | 2.0042 | 0.000 | 131.3 |
| local-10m-128 | 0.001664 | 16.0042 | 0.000 | 130.8 |
| auto-10m-16 | 1.330206 | 2.0042 | 0.000 | 129.4 |
| zero-100k-10m-128 | 0.862549 | 38.9099 | 4.758 | 207.8 |
| nonzero-high-incidence | 0.013909 | 2.5312 | 0.153 | 116.0 |
| nonzero-spill-16k | 4.134089 | 0.0102 | 12.846 | 135.7 |
| million-edges-128 | 29.881511 | 110.9603 | 112.043 | 278.8 |
| million-edges-16 | 41.450841 | 13.0368 | 195.968 | 213.6 |
| long-paths-16k | 0.659286 | 0.0130 | 4.209 | 39.2 |

- 固定 4 个选中列时，1M 与 10M nnz 均访问 40 个 incidence、0 次系数解码。无关矩阵
  扩大十倍没有触发完整矩阵扫描；毫秒级单次结果不用于证明速度差异。自动选择额外扫描
  1,000,000 个变量的元数据，单独计入 auto 场景。
- 10M nnz、100,000 个零固定变量：处理 1M incidence，0 次系数解码。
- 高 incidence 非零场景：4 个变量、每列 1,000 incidence。16 KiB 非零场景：
  10,000 个变量、100,000 incidence，exact 排序溢写，无部分结果发布。
- 百万边为 333,334 个独立三角形，共 1,000,002 条原记录；保留 666,668 条，
  删除 333,334 条，完整 witness 总长 666,668。不是所有 DAG 的复杂度承诺。
- 长路径场景有 1,001 个顶点、1,999 条边，输出总长 500,499；16 KiB 下使用一个
  traversal 分量、一次共享 BFS。没有逐 removed edge BFS。其三列 witness 载荷约
  2,013,992 字节，不包括索引、Frame、文件头或 scratch。

### 持久化与 I/O 字节成本

下面取公开流程第一轮的单个 `overlay-native` 包。proof、delta 和绑定数字为所引用 typed
array 文件的字节数；manifest 单列。整个 benchmark 另保存 base native、输出 MPS 和显式
JSON proof，因此总 artifact 更大。源文件增长是可搬迁能力的成本，不是工作内存，也不是加速。

| 项目/bytes | test32 | test33 |
|---|---:|---:|
| 源 MPS | 9,500,175 | 20,506,497 |
| 源 record/context index | 4,451,422 | 16,531,022 |
| Delta arrays | 1,099,616 | 360,128 |
| Proof arrays | 34,493 | 2,664,024 |
| Lineage binding arrays | 81,232 | 360,128 |
| Native proof manifest | 9,131 | 2,800 |
| 整个 overlay native | 21,981,981 | 80,994,049 |
| 额外显式 proof JSON | 422,691 | 10,318,975 |
| 输出 MPS | 3,272,503 | 10,696,497 |
| 原字节复制 | 3,056,221 | 10,696,497 |
| I/O 工作账本峰值 | 4,830,441 | 69,497,057 |
| I/O 临时磁盘峰值 | 3,272,503 | 10,696,497 |

百万边场景的三个 witness 数组本体为 6,666,688 字节；表中的 scratch 峰值还包含排序、
邻接、拓扑、连通分量、选择和输出构造中间存储。Native v6 默认保留源文件/index，
v5 读取适配器和未知 feature 拒绝均有测试；加载的历史 proof 不因保存或读回就成为 live proof。

### 尚未完成的门槛

1. 上述 test32 及部分 test33 阶段回退需要继续优化，再重跑五轮交替基准；当前不能签字为
   “现有可比阶段无超过阈值回退”。完整 witness 的收益不能抵消其他阶段未通过。
2. 新路径已经共享主要 typed allocation、exact spill、sort、mask、override、结果和
   readback 账本，但仍需完整分配审计。例如拓扑 degree 的 NumPy 临时数组、部分 dense
   touched-index 临时数组和字符串 cycle diagnostics 的展示构造尚未全部逐项计账。
   因此低预算测试只证明这些输入可完成且**已登记**工作量未超过配额，不证明所有输入的
   全部工作存储严格小于配置值；也不将 RSS 与工作内存混为一谈。
3. 保存加载的历史 lineage、通用 Frame 输入及 generic exact fallback 仍有明确的 legacy
   容器路径。大规模已冻结输入和 fast path 的覆盖不能外推为这些路径的完整资源验收。

原始日志：`benchmarks/primitives-v6/verified-tests.log`、`verified-tests.xml`、
`paired/measurements.json`、`scale-final/summary.json`、`persistence-bytes.json`。
