"""Produce a reproducible phase report from completed paired process measurements."""
import argparse
import importlib.util
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    spec=importlib.util.spec_from_file_location('pairs',Path(__file__).with_name('benchmark_runtime_pairs.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    records=[r for directory in args.runs for r in json.loads((directory/'measurements.json').read_text())]
    environments=[json.loads((directory/'environment.json').read_text()) for directory in args.runs]
    for e in environments[1:]:
        for key in ('python','executable','platform','source_hashes','input_hashes','witness_hash','harness_hash','helper_hash','resources'):
            if e[key]!=environments[0][key]:raise ValueError('Incomparable runs: '+key)
    summary=module.summarize(records)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.with_suffix('.json').write_text(json.dumps(dict(runs=list(map(str,args.runs)),summary=summary),indent=2))
    lines=['# test32/test33 runtime v5 性能复测','',
        '本报告只描述冻结实现及确定性场景的结果；完整架构仍有未完成项，见 [实施状态](RUNTIME_V5_STATUS.md)。',
        '', '## 完整回放', '',
        '单位：秒、MiB。时间与 RSS 均为独立进程测量的中位数；括号为范围。', '',
        '| 场景 | 每个版本次数 | 基线时间 | 当前时间 | 加速 | 基线峰值 RSS | 当前峰值 RSS |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    def measure(d,key,scale=1):
        r=d[key];return f'{r["median"]/scale:.3f} ({r["minimum"]/scale:.3f}–{r["maximum"]/scale:.3f})'
    for name,entry in summary.items():
        b,c=entry['baseline'],entry['current']
        lines.append(f'| {name} | {len(c["complete"]["values"])} | {measure(b,"complete")} | {measure(c,"complete")} | {b["complete"]["median"]/c["complete"]["median"]:.2f}× | {measure(b,"maxrss",2**20)} | {measure(c,"maxrss",2**20)} |')
    lines+=['','## 公开操作阶段中位数','',
        '阶段计时存在嵌套，不能相加代替 complete。check、证书编译和 commit 是函数级计时；certificate_kernel 还包含编译和前置编辑。', '',
        '| 阶段 | test32 基线 | test32 当前 | test33 基线 | test33 当前 |','| --- | ---: | ---: | ---: | ---: |']
    for phase in summary['32-public']['baseline']:
        if phase in ('maxrss','temporary_disk_peak_sampled','artifact_bytes'):continue
        values=[summary[f'{case}-public'][arm].get(phase,{}).get('median') for case in (32,33) for arm in ('baseline','current')]
        lines.append('| '+phase+' | '+' | '.join('—' if v is None else f'{v:.6f}' for v in values)+' |')
    lines+=['','## 磁盘与 artifact','',
        '临时磁盘每 0.5 秒采样一次，短暂峰值可能漏采；该数值不是精确上界。', '',
        '| 场景 | 基线临时磁盘 MiB | 当前临时磁盘 MiB | 基线输出 MiB | 当前输出 MiB |',
        '| --- | ---: | ---: | ---: | ---: |']
    for name,entry in summary.items():
        b,c=entry['baseline'],entry['current'];lines.append('| '+name+' | '+' | '.join(measure(d,k,2**20) for k in ('temporary_disk_peak_sampled','artifact_bytes') for d in (b,c))+' |')
    lines+=['','## 阈值检查','']
    for name,entry in summary.items():
        lines.append(f'- {name}: 完整耗时改善={entry["whole_time_improved"]}，RSS 改善={entry["rss_improved"]}；阶段中位回退超过 max(10%, 20ms)：{", ".join(entry["stable_regressions"]) or "无"}。')
    lines+=['','## 测量与复现','',
        '输入、audit、320,000 contributions 的固定 witness、源码和 harness 均记录 SHA-256。每次启动独立 Python 进程，两个版本串行交替；不清空文件系统缓存。首次结构分析与缓存命中分别计时。没有运行 agent、test34 或求解器。', '',
        '性能基线为 baseline-synchronized：从实施开始前和用户完成并行 check/edit 修改后的两个快照重建，保留用户完成的优化，仅撤回可定位到本迁移的修改。它不是 git HEAD，也不是原 B1 分钟级历史值。具体恢复文件、方法和必要 guard 适配见 baseline-synchronized.json。该重建过程是比较结果的限制。', '',
        '运行目录：', '',*[f'- `{d}`：environment.json、measurements.json、summary.json、每个进程日志及实际输出。' for d in args.runs], '',
        '复现命令（仓库根目录，相同解释器）：', '', '```sh',
        'python scripts/benchmark_runtime_pairs.py --baseline benchmarks/runtime-v5/baseline-synchronized --current benchmarks/runtime-v5/candidate-final --out benchmarks/runtime-v5/reproduce --witness-33 benchmarks/runtime-v5/witness-33.json --repeat 5',
        '```','',
        '## 数学与 SQL 验证','',
        '- test32 独立 verifier：382 行、4,792 列、41,133 个矩阵非零元；另有 2 个目标系数。全部固定变量、one-hot、整数声明和删行证书通过。',
        '- test33 独立 verifier：90,000 个 targets，320,000 contributions（55,000 条长度 3，20,000 条长度 4，15,000 条长度 5）；70,633 行、6,250 列、191,240 个矩阵非零元，保留 DAG 无替代路径。',
        '- SQL trace 单独运行，不参与计时。两场景 Frame fallback 打开次数为零；query、graph、check、edit 无 SQL。读取和 IR 完成结果采用批量 catalog 写入。唯一带 ORDER BY 的 SELECT 是一次角色字典批量读取。',
        '- SQL trace 和独立日志分别保存在 sql-32-current、sql-33-current-r2 以及各目录的 independent 子目录；test32 独立日志在 r9-32-public/independent。', '',
        '未完成的统一资源预算、残留 Python 容器和测试迁移，不因这些场景通过而视为验收完成。','']
    args.output.write_text('\n'.join(lines))


if __name__=='__main__':main()
