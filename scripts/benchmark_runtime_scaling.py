"""Independent 1x/2x/4x fixed-short-path read/scan/check/delta scaling probe."""
import argparse
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time


def probe(n,out):
    import mipframe as mf
    import numpy as np
    from mipframe.check.kernel import facts
    out.mkdir(parents=True,exist_ok=False)
    source=out/'input.mps'
    with source.open('w') as s:
        s.write('NAME SCALE\nROWS\n N obj\n')
        for i in range(3*n):s.write(f' L r{i}\n')
        s.write('COLUMNS\n')
        for i in range(n):
            r=3*i
            s.write(f' x{r} r{r} 1 r{r+2} 1\n x{r+1} r{r} -1 r{r+1} 1\n x{r+2} r{r+1} -1 r{r+2} -1\n')
        s.write('RHS\nENDATA\n')
    timings={}
    def run(name,fn):
        t=time.perf_counter();value=fn();timings[name]=time.perf_counter()-t;return value
    with run('read',lambda:mf.read(source,memory_mb=128)) as m:
        assert run('scan',lambda:m.rows.where(nnz=2).count())==3*n
        targets=np.arange(2,3*n,3,dtype=np.uint32)
        cert=run('compile',lambda:mf.CertificateBatch.paths(([int(t)-2,int(t)-1] for t in targets)))
        proof=run('check',lambda:m.check.linear_rows(targets=targets,witnesses=cert).require())
        child=run('edit',lambda:m.edit().drop_rows(targets,because=proof).commit())
        with child:
            assert child.shape==(2*n,3*n) and child.nnz==4*n
            assert facts(child)._base.data is facts(m).data
    result=dict(n=n,timings=timings,maxrss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (out/'measurement.json').write_text(json.dumps(result,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--size',type=int)
    p.add_argument('--base',type=int,default=5000);p.add_argument('--repeat',type=int,default=3)
    args=p.parse_args()
    if args.size:return probe(args.size,args.out)
    args.out.mkdir(parents=True,exist_ok=False);records=[]
    for repetition in range(args.repeat):
        for scale in (1,2,4):
            out=args.out/f'{scale}x-{repetition+1}'
            subprocess.run([sys.executable,__file__,'--size',str(args.base*scale),'--out',str(out)],check=True)
            records.append(dict(scale=scale,**json.loads((out/'measurement.json').read_text())))
    medians={str(scale):{name:statistics.median(r['timings'][name] for r in records if r['scale']==scale)
        for name in records[0]['timings']} for scale in (1,2,4)}
    summary=dict(records=records,median=medians,growth_4_over_1={k:medians['4'][k]/medians['1'][k] for k in medians['1']},
                 filesystem_cache='not evicted',python=sys.version)
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary['growth_4_over_1']))


if __name__=='__main__':main()
