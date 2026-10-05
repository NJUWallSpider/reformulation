"""Direct exact reference arm, transcribed from the B1 nomf certificates.

This module deliberately does not import mipframe. Candidate paths/groups are
inputs. Parsing precedes the timer; verification, serialization, writing and one
full numerical readback are timed. Independent historical audit is separate.
"""
from pathlib import Path
from fractions import Fraction as Q
from collections import defaultdict
import ast
import hashlib
import json
import time
import resource


def parser(case,root):
    source=(root/'runs'/f'test{case}-nomf-B1/output/verify.py').read_text()
    tree=ast.parse(source)
    tree.body=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef)) and not isinstance(n,ast.FunctionDef) or isinstance(n,ast.FunctionDef) and n.name in ('decode','read_model','digest','combine')]
    env={};exec(compile(tree,'historical_nomf_parser','exec'),env)
    return env['decode' if case==32 else 'read_model']


def run(case,root,out,audit,paths=None):
    parse=parser(case,root);source=root/'runs'/f'test{case}-nomf-B1/input/model.mps'
    t=time.perf_counter();original=parse(source);read=time.perf_counter()-t
    start=time.perf_counter();out.mkdir(parents=True,exist_ok=True);timings={}
    if case==33:
        mat=original['matrix'];rows=original['rows'];rhs=original['rhs']
        records=audit['certificates'] if paths is None else paths
        removed={c['removed_row'] for c in records};assert len(removed)==90000
        certificates=[]
        for record in records:
            row=record['removed_row'];path=list(record['retained_path_rows'])
            combined=defaultdict(Q)
            for r in path:
                assert r not in removed and rows[r]=='L'
                for v,a in mat[r].items():combined[v]+=a
            assert {v:a for v,a in combined.items() if a}==mat[row]
            assert sum(rhs.get(r,0) for r in path)<=rhs.get(row,0)
            certificates.append(dict(record,retained_path_rows=path))
        timings['certificate_kernel']=time.perf_counter()-start
        t=time.perf_counter();section=None
        with source.open(encoding='latin-1',newline='') as src,(out/'model.mps').open('w',encoding='latin-1',newline='') as dst:
            for line in src:
                fields=line.split()
                if not fields or line.startswith('*'):dst.write(line);continue
                if not line[0].isspace():section=fields[0];dst.write(line);continue
                if section in ('ROWS','COLUMNS') and fields[1] in removed:continue
                dst.write(line)
        timings['write']=time.perf_counter()-t
        t=time.perf_counter();new=parse(out/'model.mps')
        assert new['rows']=={r:k for r,k in rows.items() if r not in removed}
        for field in ('rhs','bounds','integers','markers','rhs_sets','bound_sets'):
            assert new[field]==original[field]
        assert new['columns']=={v:{r:c for r,c in ts.items() if r not in removed} for v,ts in original['columns'].items()}
        timings['readback']=time.perf_counter()-t
        result_audit=dict(audit,certificates=certificates)
    else:
        raw,rows,cols,rhs,bounds,ints,mat=original
        fixed=set(audit['removed_original_fixed_zero']);excluded=set();groups=audit['choice_groups']
        assert fixed=={v for v,b in bounds.items() if b==[0,0]}
        seen=set();relaxed=set()
        for group in groups:
            onehot,sr,cr=(group[k] for k in ('onehot_row','start_row','completion_row'))
            sv,cv=(group[k] for k in ('start_variable','completion_variable'))
            options=set(mat[onehot]);assert not options&seen;seen|=options
            assert rows[onehot]=='E' and rhs.get(onehot,0)==1
            assert all(a==1 and v in ints and bounds[v][0]==0 and bounds[v][1] in (0,1) for v,a in mat[onehot].items())
            for r,y in ((sr,sv),(cr,cv)):
                assert rows[r]=='E' and mat[r][y]==-1 and rhs.get(r,0)==0 and set(mat[r])<=options|{y}
                assert all(a.denominator==1 for a in mat[r].values())
            for v in options-fixed:
                if any(not bounds[y][0]<=mat[r].get(v,0)<=bounds[y][1] for r,y in ((sr,sv),(cr,cv))):excluded.add(v)
            relaxed.update((sv,cv))
        assert excluded==set(audit['removed_onehot_bound_excluded']) and len(excluded)==6306
        removed=fixed|excluded;assert len(removed)==13588
        reduced={r:{v:a for v,a in ts.items() if v not in removed} for r,ts in mat.items()}
        for weak,strong in audit['removed_capacity_rows'].items():
            assert strong not in audit['removed_capacity_rows']
            assert rows[weak]==rows[strong]=='L' and rhs.get(weak,0)==rhs.get(strong,0)==0
            diff=defaultdict(Q,reduced[strong])
            for v,a in reduced[weak].items():diff[v]-=a
            assert all(a>=0 and bounds[v][0]>=0 for v,a in diff.items() if a)
        jobs={g['job']:g for g in groups}
        for row,witness in audit['removed_precedence_rows'].items():
            total=defaultdict(Q)
            for r in witness['rows']:
                assert r not in audit['removed_precedence_rows'] and rows[r]=='L' and rhs.get(r,0)==0
                for v,a in reduced[r].items():total[v]+=a
            for v,a in reduced[row].items():total[v]-=a
            expected=defaultdict(Q)
            for job in witness['jobs'][1:-1]:
                g=jobs[job];expected[g['completion_variable']]+=1;expected[g['start_variable']]-=1
            assert {v:a for v,a in total.items() if a}=={v:a for v,a in expected.items() if a}
        integer_inputs=ints-relaxed
        for g in groups:
            sv,cv=g['start_variable'],g['completion_variable'];sr,cr=g['start_row'],g['completion_row']
            assert all(reduced[cr].get(v,0)-reduced[sr].get(v,0)>0 for v in set(mat[g['onehot_row']])-removed)
            for r,y in ((sr,sv),(cr,cv)):
                assert reduced[r][y]==-1 and all(v==y or v in integer_inputs and a.denominator==1 for v,a in reduced[r].items())
        timings['certificate_kernel']=time.perf_counter()-start
        t=time.perf_counter();dropped=set(audit['removed_capacity_rows'])|set(audit['removed_precedence_rows'])
        intended_rows={r:k for r,k in rows.items() if r not in dropped}
        intended_cols={v:{r:a for r,a in co.items() if r not in dropped} for v,co in cols.items() if v not in removed}
        intended_bounds={v:b for v,b in bounds.items() if v not in removed}
        intended_ints=ints-removed-relaxed
        lines=['NAME          nomf_reference','ROWS']+[f' {k}  {r}' for r,k in intended_rows.items()]+['COLUMNS',"    MARK0000  'MARKER'  'INTORG'"]
        for integer in (True,False):
            if not integer:lines.append("    MARK0001  'MARKER'  'INTEND'")
            for v,co in intended_cols.items():
                if (v in intended_ints)==integer:
                    for r,a in co.items():assert a.denominator==1;lines.append(f'    {v}  {r}  {int(a)}')
        lines.append('RHS')
        lines.extend(f'    rhs  {r}  {int(a)}' for r,a in rhs.items() if r not in dropped)
        lines.append('BOUNDS')
        for v,(lo,hi) in intended_bounds.items():
            if v in intended_ints and lo==0 and hi==1:lines.append(f' BV bnd  {v}')
            else:lines.extend((f' LO bnd  {v}  {int(lo)}',f' UP bnd  {v}  {int(hi)}'))
        lines.append('ENDATA');(out/'model.mps').write_text('\n'.join(lines)+'\n')
        timings['edit_write']=time.perf_counter()-t
        t=time.perf_counter();new=parse(out/'model.mps')
        assert new[1:6]==(intended_rows,intended_cols,{r:a for r,a in rhs.items() if r not in dropped},intended_bounds,intended_ints)
        timings['readback']=time.perf_counter()-t;result_audit=dict(audit)
    t=time.perf_counter()
    result_audit['original']=str(source);result_audit['reformulated']=str(out/'model.mps')
    result_audit['reformulated_sha256']=hashlib.sha256((out/'model.mps').read_bytes()).hexdigest()
    (out/'audit.json').write_text(json.dumps(result_audit,separators=(',',':')))
    timings['certificate_write']=time.perf_counter()-t;timings['total']=time.perf_counter()-start
    result=dict(case=case,arm='nomf',timings=timings,read=read,maxrss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (out/'measurement.json').write_text(json.dumps(result,indent=2));return result
