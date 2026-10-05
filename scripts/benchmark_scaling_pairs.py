"""Alternate frozen runtimes in independent synthetic scaling processes."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from benchmark_runtime_pairs import disk_bytes, tree_hash, file_hash


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','current','out'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--sizes',type=int,nargs='+',default=[1000,2000,4000])
    parser.add_argument('--repeat',type=int,default=5)
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    paths={'baseline':args.baseline.resolve(),'current':args.current.resolve()}
    frozen={arm:tree_hash(path/'src') for arm,path in paths.items()}
    environment=dict(sources={k:str(v) for k,v in paths.items()},source_hashes=frozen,
        sizes=args.sizes,repeat=args.repeat,python=sys.version,resources=dict(memory_mb=128,disk_mb=None),
        input='n independent three-edge triangles; 3n variables, 3n rows, 6n nonzeros, n targets, 2n witness entries',
        filesystem_cache='not evicted',temporary_disk_sampling_seconds=.05,
        harness_hashes={arm:file_hash(path/'scripts/benchmark_runtime_scaling.py') for arm,path in paths.items()})
    (args.out/'environment.json').write_text(json.dumps(environment,indent=2))
    records=[]
    for n in args.sizes:
        for repetition in range(args.repeat):
            arms=('baseline','current') if repetition%2==0 else ('current','baseline')
            for arm in arms:
                path=paths[arm];out=args.out/f'{n}-{repetition+1}-{arm}'
                with tempfile.TemporaryDirectory(prefix='mipframe-scale-measure-') as temporary:
                    env=dict(os.environ,PYTHONPATH=str(path/'src'),TMPDIR=temporary)
                    command=[sys.executable,str(path/'scripts/benchmark_runtime_scaling.py'),'--size',str(n),'--out',str(out.resolve())]
                    with (args.out/(out.name+'.log')).open('w') as log:
                        process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
                        peak=0
                        while process.poll() is None:
                            peak=max(peak,disk_bytes(temporary));time.sleep(.05)
                        if process.returncode:raise RuntimeError(f'Failed {out.name}')
                measurement=json.loads((out/'measurement.json').read_text())
                measurement.update(arm=arm,repetition=repetition+1,temporary_disk_peak_sampled=peak,
                    artifact_bytes=0,input_bytes=(out/'input.mps').stat().st_size,input_sha256=file_hash(out/'input.mps'))
                records.append(measurement)
                (args.out/'measurements.json').write_text(json.dumps(records,indent=2))
                print(out.name,measurement['timings'],flush=True)
    summary={}
    for n in args.sizes:
        result={}
        for arm in paths:
            selected=[r for r in records if r['n']==n and r['arm']==arm]
            result[arm]={name:statistics.median(r['timings'][name] for r in selected) for name in selected[0]['timings']}
            for name in ('maxrss','temporary_disk_peak_sampled','artifact_bytes'):result[arm][name]=statistics.median(r[name] for r in selected)
        result['regressions']=[name for name in records[0]['timings'] if result['current'][name]-result['baseline'][name]>max(.02,.1*result['baseline'][name])]
        summary[n]=result
    if {arm:tree_hash(path/'src') for arm,path in paths.items()}!=frozen:raise RuntimeError('Frozen sources changed')
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2))


if __name__=='__main__':main()
