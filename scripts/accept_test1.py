"""Reproducible acceptance report, never runs the 100MB generator."""
import argparse
import importlib.util
import json
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from lpir import IR
from lpir.store import dump,sha256
from lpir.numbers import LPIRError

ROOT=Path(__file__).resolve().parents[1]

def require(condition,message):
    if not condition:raise LPIRError(message)

def normalized(m):
    return {
        'sense':m.manifest['sense'],
        'constant':m.num(m.manifest['objective_constant']),
        'variables':{v[1]:(v[2],m.num(v[3]),m.num(v[4]),m.num(v[5])) for v in m.variables},
        'rows':{r[1]:({m.variables[v][1]:c for v,c in m.row(r[0]).items()},m.num(r[2]),m.num(r[3])) for r in m.rows},
    }

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifact',default='test1/mvp_ir');args=parser.parse_args()
    source=ROOT/'test1/test1.lp';out=Path(args.artifact)
    ir=IR(out) if (out/'manifest.json').exists() else IR.build(source,out)
    require(ir.model.manifest['model_version']==sha256(source),'Artifact is not for current test1')
    spec=importlib.util.spec_from_file_location('test1_reference',ROOT/'test1/analysis/model_io.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    start=time.perf_counter();obj,rows,bounds,binary=module.read(source);m=ir.model
    require(len(m.rows)==len(rows) and len(m.variables)==len(bounds),'Reference dimensions differ')
    for v in m.variables:
        original=int(v[1][1:]);require((m.num(v[3]),m.num(v[4]))==bounds[original],f'Bounds: {v[1]}')
        require(m.num(v[5])==obj.get(original,Fraction(0)),f'Objective: {v[1]}')
        require((v[2]=='B')==(original in binary),f'Type: {v[1]}')
    for row in rows:
        r=m.row_names[row.name]
        require({int(m.variables[v][1][1:]):c for v,c in m.row(r).items()}==row.a,f'Coefficients: {row.name}')
        require(m.num(m.rows[r][2])==(row.rhs if row.sense!='<=' else None),f'Lower: {row.name}')
        require(m.num(m.rows[r][3])==(row.rhs if row.sense!='>=' else None),f'Upper: {row.name}')
    reference_seconds=time.perf_counter()-start
    start=time.perf_counter();query=ir.query('filter','temporal','L[source] + q > U[target]',limit=3);query_seconds=time.perf_counter()-start
    require(query['total']==3341,'Unexpected time-window query count')
    verification=ir.verify();summary=ir.render();require(len(summary.encode())<=20000,'Summary exceeds budget')
    ir.export(out/'reconstructed.lp')
    start=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='lpir-roundtrip-') as temp:
        reopened=IR.build(out/'reconstructed.lp',Path(temp)/'ir')
        require(normalized(m)==normalized(reopened.model),'Export/reparse differs exactly')
    roundtrip_seconds=time.perf_counter()-start
    (out/'summary.md').write_text(summary)
    report={'source_sha256':sha256(source),'independent_parser_comparison':'PASS','compressed_roundtrip':verification,'export_reparse_exact':'PASS','overview':ir.overview(),'summary_bytes':len(summary.encode()),'time_window_query':query,'acceptance_seconds':{'reference_comparison':reference_seconds,'query':query_seconds,'export_reparse':roundtrip_seconds},'large_model_run':False,'large_model_note':'Only test1 and small variants; no 100MB+ claim.','artifact_bytes_measured':{p.name:p.stat().st_size for p in out.iterdir() if p.is_file() and p.name!='acceptance.json'}}
    dump(out/'acceptance.json',report)
    print(json.dumps({'independent_parser':'PASS','reconstruction':'PASS','export_reparse':'PASS','infeasible_arc_query_count':query['total'],'summary_bytes':len(summary.encode()),'build_metrics':m.manifest['timings'],'build_peak_rss_bytes':m.manifest['peak_rss_bytes'],'report':str(out/'acceptance.json')},indent=2))

if __name__=='__main__':main()
