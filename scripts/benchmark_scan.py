"""Paired isolated-process numerical-load / scan benchmark (no profiler).

The baseline disables only the new inventory; parsing, native construction,
hashes and canonical-content validation are identical. Structural analysis is
measured separately and never included in read or its peak RSS.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time


def worker(path, mode):
    import resource
    import mipframe as mf
    from mipframe.model import Model
    from mipframe.store.model_store import NumericalStore
    if mode=='base':
        original=NumericalStore._open
        NumericalStore._open=lambda self,path,**kw:original(self,path,scan_memory_mb=None)
        Model._build_ir=lambda *args,**kwargs:None
    start=time.perf_counter()
    with mf.read(path) as m:
        elapsed=time.perf_counter()-start
        record=dict(mode=mode,read_seconds=elapsed,parse_seconds=m.model.manifest['timings']['parse'],
                    store_build_seconds=m.model.manifest['timings']['index'],
                    peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
                    rows=m.shape[0],variables=m.shape[1],A_nnz=m.nnz,version=m.version,source_sha256=m.model.manifest["source_sha256"])
        if mode!='base':
            data=m.ir.to_dict()
            record.update(objective_nnz=data['objective']['nonzeros'],scan_chars=len(str(m.ir)),
                          top_shape=data['local_row_shapes']['items'][0],domains=data['domains'],names=data['names'])
            assert not getattr(m,'_refinement_store',None)
            assert not getattr(m,'_indexed_results',{})
            start=time.perf_counter()
            for _ in range(5): str(m.ir)
            record['render_seconds_per_call']=(time.perf_counter()-start)/5
            if mode=='structure':
                start=time.perf_counter()
                record['depth1_row_groups']=m.families(depth=1).count()
                record['first_structure_seconds']=time.perf_counter()-start
        print(json.dumps(record,default=str))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--models',nargs='+',default=['test32.mps','test33.mps','test34.mps'])
    parser.add_argument('--worker',choices=['base','scan','structure'])
    parser.add_argument('--path',type=Path)
    args=parser.parse_args()
    if args.worker:
        worker(args.path,args.worker)
        return
    if args.data_root is None or args.output is None: parser.error('--data-root and --output are required')
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    root=Path(__file__).resolve().parents[1]
    source_digest=hashlib.sha256()
    for path in sorted((root/"src/mipframe").rglob("*.py")):
        source_digest.update(str(path.relative_to(root)).encode())
        source_digest.update(path.read_bytes())
    results=dict(python=sys.version,source_sha256=source_digest.hexdigest(),models={})
    def run(path,mode):
        result=subprocess.run([sys.executable,__file__,'--worker',mode,'--path',str(path)],env=env,text=True,capture_output=True,check=True)
        return json.loads(result.stdout)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for name in args.models:
        records=[]
        for i in range(args.repeats):
            # Alternate order to reduce warm filesystem-cache bias.
            for mode in (['base','scan'] if i%2==0 else ['scan','base']):
                item=run(args.data_root/name,mode)
                records.append(item)
                print(f'{name} {mode} {item["read_seconds"]:.3f}s',flush=True)
        assert len({r['version'] for r in records})==1
        medians={mode:statistics.median(r['read_seconds'] for r in records if r['mode']==mode) for mode in ('base','scan')}
        results['models'][name]=dict(runs=records,median_seconds=medians,
            scan_increment_seconds=medians['scan']-medians['base'],scan_increment_ratio=medians['scan']/medians['base']-1)
        args.output.write_text(json.dumps(results,indent=2))
        structural=run(args.data_root/name,'structure')
        results['models'][name]['first_structure']=structural
        args.output.write_text(json.dumps(results,indent=2))


if __name__=='__main__': main()
