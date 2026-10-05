"""Deterministic B1 witness replay; candidate discovery is outside the timer.

Run with the same interpreter for both arms. No solver or solver output is used.
"""
from pathlib import Path
import argparse
import json
import time
import resource
import mipframe as mf


def prepare(case,root,path_kind="nomf"):
    run=root/'runs'/f'test{case}-nomf-B1'
    audit=json.loads((run/'output/audit.json').read_text())
    model=mf.read(run/'input/model.mps')
    rows={r[1]:r[0] for r in model.model.rows}
    variables={v[1]:v[0] for v in model.model.variables}
    if case==33:
        witness=dict(targets=[rows[c['removed_row']] for c in audit['certificates']],
                     paths=[[rows[r] for r in c['retained_path_rows']] for c in audit['certificates']])
    else:
        groups=[(rows[g['onehot_row']],((rows[g['start_row']],variables[g['start_variable']]),
                                       (rows[g['completion_row']],variables[g['completion_variable']]))) for g in audit['choice_groups']]
        cap=audit['removed_capacity_rows']
        offsets=[0];sources=[];sides=[];multipliers=[];prec=[]
        group_by_job={g['job']:g for g in audit['choice_groups']}
        for name,path in audit['removed_precedence_rows'].items():
            prec.append(rows[name])
            for r in path['rows']:sources.append(rows[r]);sides.append('ub');multipliers.append(1)
            for j in path['jobs'][1:-1]:
                g=group_by_job[j]
                sources.extend((rows[g['completion_row']],rows[g['start_row']]))
                sides.extend(('eq','eq'));multipliers.extend((1,-1))
            offsets.append(len(sources))
        witness=dict(groups=groups,original_fixed=[variables[v] for v in audit['removed_original_fixed_zero']],
                     cap_targets=[rows[r] for r in cap],cap_paths=[[rows[r]] for r in cap.values()],
                     prec_targets=prec,prec_arrays=(offsets,sources,multipliers,sides),
                     integral_vars=[v for _,ds in groups for _,v in ds],
                     integral_rows=[r for _,ds in groups for r,_ in ds])
    if case==33 and path_kind=='bfs':
        from collections import defaultdict,deque
        # Same grouped retained-edge BFS as the historical mf B1 session.
        by_name={r[1]:r[0] for r in model.model.rows};dropped=set(witness['targets'])
        adj=defaultdict(list);requests=defaultdict(list)
        for r in range(model.shape[0]):
            terms=list(model.model.iter_terms(r))
            if len(terms)!=2 or sorted(c for v,c in terms)!=[-1,1]:continue
            u=next(v for v,c in terms if c==1);v=next(v for v,c in terms if c==-1)
            if r in dropped:requests[u].append((r,v))
            else:adj[u].append((v,r))
        paths={}
        for u,rs in requests.items():
            wanted={v for r,v in rs};parent={u:None};queue=deque([u])
            while queue and wanted:
                v=queue.popleft()
                for w,r in adj[v]:
                    if w in parent:continue
                    parent[w]=(v,r);wanted.discard(w);queue.append(w)
            assert not wanted
            for r,v in rs:
                path=[]
                while v!=u:v,edge=parent[v];path.append(edge)
                paths[r]=list(reversed(path))
        witness['paths']=[paths[r] for r in witness['targets']]
        names={r[0]:r[1] for r in model.model.rows}
        audit=dict(audit,certificates=[dict(c,retained_path_rows=[names[r] for r in paths[by_name[c['removed_row']]]]) for c in audit['certificates']])
    return model,witness,audit


def run_mf(case,model,w,out):
    start=time.perf_counter();proofs=[];timings={};snapshots=[]
    if case==33:
        if 'certificate' in w:
            proof=model.check.linear_many(w['certificate']).require()
        else:
            cert=mf.CertificateBatch.paths(w['paths'])
            proof=model.check.linear_rows(targets=w['targets'],witnesses=cert).require()
        timings['certificate_kernel']=time.perf_counter()-start;proofs.append(proof)
        e=model.edit().drop_rows(w['targets'],because=proof)
        final=e.commit()
    else:
        zero=model.check.one_hot_affine(groups=w['groups']).bound_exclusions().require()
        assert len(zero.target_ids)==6306
        proofs.append(zero)
        tightened=model.edit().set_var_bounds(zero.target_ids,ub=0,because=zero).commit();snapshots.append(tightened)
        removed=w['original_fixed']+list(map(int,zero.target_ids))
        reduced=tightened.edit().substitute_fixed(removed).commit();snapshots.append(reduced)
        cap=reduced.check.linear_rows(targets=w['cap_targets'],witnesses=w['cap_paths'],residual='bounds').require()
        offsets,sources,multipliers,sides=w['prec_arrays']
        prec=reduced.check.linear_rows(targets=w['prec_targets'],witnesses=mf.CertificateBatch(offsets,sources,multipliers,sides),residual='bounds').require()
        integral=reduced.check.affine_integrality(vars=w['integral_vars'],rows=w['integral_rows']).require()
        proofs.extend((cap,prec,integral));timings['certificate_kernel']=time.perf_counter()-start
        e=reduced.edit().drop_rows(w['cap_targets'],because=cap).drop_rows(w['prec_targets'],because=prec)
        final=e.set_domains(w['integral_vars'],kind='C',because=integral).commit()
    timings['edit']=time.perf_counter()-start-timings['certificate_kernel']
    diff=final.diff(model)
    t=time.perf_counter()
    out.mkdir(parents=True,exist_ok=True)
    try:
        from mipframe.control.proofs import proof_bundle
    except ImportError:
        from mipframe.store.native4 import proof_bundle
    (out/'reformulation.json').write_text(json.dumps(proof_bundle(final._exact_facts),separators=(',',':')))
    timings['certificate_write']=time.perf_counter()-t
    receipt=final.write(out/'model.mps');timings.update(receipt['timings'])
    timings['total']=time.perf_counter()-start
    expected=(382,4792,41133) if case==32 else (70633,6250,191240)
    assert (receipt['rows'],receipt['variables'],receipt['nonzeros'])==expected
    result=dict(case=case,arm='mf',timings=timings,artifact=receipt,semantic=diff.semantic,
                maxrss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    (out/'measurement.json').write_text(json.dumps(result,indent=2))
    final.close()
    for s in reversed(snapshots):s.close()
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/Users/bhz/Code/reform_test'))
    p.add_argument('--case',type=int,choices=(32,33),required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--arm',choices=('mf','nomf'),default='mf');p.add_argument('--paths',choices=('nomf','bfs'),default='nomf')
    p.add_argument('--independent',action='store_true')
    args=p.parse_args();start=time.perf_counter()
    m,w,a=prepare(args.case,args.root,args.paths)
    print(json.dumps(dict(read_and_candidates=time.perf_counter()-start)),flush=True)
    try:
        if args.arm=='mf':result=run_mf(args.case,m,w,args.out)
        else:
            from benchmark_nomf_reference import run
            m.close();result=run(args.case,args.root,args.out,a)
        print(json.dumps(result,indent=2),flush=True)
        if args.independent:independent(args.case,args.root,args.out,a)
    finally:m.close()


def independent(case,root,out,audit):
    import hashlib,subprocess,sys
    staged=out/'independent';(staged/'output').mkdir(parents=True)
    audit=dict(audit,original=str(root/'runs'/f'test{case}-nomf-B1/input/model.mps'),
               reformulated=str(out.absolute()/'model.mps'),
               reformulated_sha256=hashlib.sha256((out/'model.mps').read_bytes()).hexdigest())
    (staged/'output/audit.json').write_text(json.dumps(audit))
    original=root/'runs'/f'test{case}-nomf-B1/output/verify.py'
    (staged/'output/verify.py').write_bytes(original.read_bytes())
    result=subprocess.run([sys.executable,'output/verify.py'],cwd=staged,capture_output=True,text=True)
    (staged/'verification.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    print(result.stdout,flush=True)


if __name__=='__main__':main()
