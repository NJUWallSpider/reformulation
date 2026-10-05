"""Serial, alternating independent-process runtime comparisons.

Freeze both source trees before invoking this driver. Repetitions contain no
solver, LLM, or cache eviction. Sampling applies equally to both arms and is not
included in the child's stage timers. No SQL tracing is used for timed runs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time


def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()


def tree_hash(root):
    return {str(p.relative_to(root)):file_hash(p) for p in sorted(root.rglob('*.py')) if '__pycache__' not in p.parts}


def disk_bytes(root):
    total=0
    for directory,_,files in os.walk(root):
        for name in files:
            try:total+=(Path(directory)/name).stat().st_size
            except FileNotFoundError:pass
    return total


def summarize(records):
    result={}
    for case in (32,33):
        for mode in ('fixed','public'):
            entry={}
            for arm in ('baseline','current'):
                selected=[r for r in records if (r['case'],r['mode'],r['runtime_arm'])==(case,mode,arm)]
                if not selected:continue
                fields={**{name:[r['timings'][name] for r in selected] for name in selected[0]['timings']},
                    'maxrss':[r['maxrss'] for r in selected],
                    'temporary_disk_peak_sampled':[r['temporary_disk_peak_sampled'] for r in selected],
                    'artifact_bytes':[r['artifact_bytes'] for r in selected]}
                entry[arm]={name:dict(median=statistics.median(values),minimum=min(values),maximum=max(values),values=values) for name,values in fields.items()}
            if len(entry)==2:
                entry['stable_regressions']=[name for name,b in entry['baseline'].items()
                    if name not in ('maxrss','temporary_disk_peak_sampled','artifact_bytes') and
                    name in entry['current'] and entry['current'][name]['median']-b['median']>max(.020,.1*b['median'])]
                entry['whole_time_improved']=entry['current']['complete']['median']<entry['baseline']['complete']['median']
                entry['rss_improved']=entry['current']['maxrss']['median']<entry['baseline']['maxrss']['median']
            result[f'{case}-{mode}']=entry
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--current',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path('/Users/bhz/Code/reform_test'))
    p.add_argument('--witness-33',type=Path,required=True)
    p.add_argument('--repeat',type=int,default=5)
    p.add_argument('--cases',type=int,nargs='+',choices=(32,33),default=[32,33])
    p.add_argument('--modes',nargs='+',choices=('fixed','public'),default=['fixed','public'])
    a=p.parse_args()
    if a.repeat<1:raise ValueError('repeat must be positive')
    a.out.mkdir(parents=True,exist_ok=False)
    harness=Path(__file__).with_name('benchmark_runtime.py').resolve()
    helper=Path(__file__).with_name('benchmark_proof_carrying.py').resolve()
    environment=dict(python=sys.version,executable=sys.executable,platform=platform.platform(),
        baseline=str(a.baseline.resolve()),current=str(a.current.resolve()),
        source_hashes={arm:tree_hash(path/'src') for arm,path in [('baseline',a.baseline),('current',a.current)]},
        input_hashes={str(path):file_hash(path) for case in (32,33) for path in
            [a.root/f'runs/test{case}-nomf-B1/input/model.mps',a.root/f'runs/test{case}-nomf-B1/output/audit.json']},
        witness_hash=file_hash(a.witness_33),harness_hash=file_hash(harness),helper_hash=file_hash(helper),
        filesystem_cache='not evicted; first-use and derived-cache timings separate',
        resources=dict(memory_mb=128,disk_mb=None),temporary_disk_sampling_seconds=.5)
    (a.out/'environment.json').write_text(json.dumps(environment,indent=2))
    records=[]
    for case in a.cases:
        for mode in a.modes:
            for repetition in range(a.repeat):
                arms=('baseline','current') if repetition%2==0 else ('current','baseline')
                for arm in arms:
                    out=a.out/f'{case}-{mode}-{repetition+1}-{arm}'
                    source=a.baseline if arm=='baseline' else a.current
                    arm_harness=(source/'scripts/benchmark_runtime.py').resolve()
                    arm_helper=(source/'scripts/benchmark_proof_carrying.py').resolve()
                    cmd=[sys.executable,str(arm_harness),'--case',str(case),'--mode',mode,'--out',str(out.resolve()),
                        '--helper',str(arm_helper),'--root',str(a.root.resolve()),'--witness-33',str(a.witness_33.resolve()),'--overlay-roundtrip']
                    source=a.baseline if arm=='baseline' else a.current
                    env=dict(os.environ,PYTHONPATH=str((source/'src').resolve()))
                    with tempfile.TemporaryDirectory(prefix='mipframe-measure-') as scratch:
                        env['TMPDIR']=scratch
                        with (a.out/(out.name+'.log')).open('w') as log:
                            proc=subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
                            peak=0
                            while proc.poll() is None:
                                peak=max(peak,disk_bytes(scratch));time.sleep(.5)
                            if proc.returncode:raise RuntimeError(f'Failed {out.name}; see its log')
                    result=json.loads((out/'measurement.json').read_text())
                    result.update(runtime_arm=arm,repetition=repetition+1,temporary_disk_peak_sampled=peak,command=cmd)
                    records.append(result)
                    (a.out/'measurements.json').write_text(json.dumps(records,indent=2))
                    (a.out/'summary.json').write_text(json.dumps(summarize(records),indent=2))
                    print(f'{out.name}: {result["timings"]["complete"]:.3f}s, RSS {result["maxrss"]/1024**2:.1f} MiB',flush=True)
    final={arm:tree_hash(path/'src') for arm,path in [('baseline',a.baseline),('current',a.current)]}
    if final!=environment['source_hashes']:raise RuntimeError('Source trees changed during measurement')


if __name__=='__main__':main()
