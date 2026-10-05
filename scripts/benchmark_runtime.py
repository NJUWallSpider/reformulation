"""Deterministic public-operation and fixed-witness test32/test33 replay.

No LLM, solver, historical timings, or filesystem cache eviction. Use paired
independent processes with identical interpreter and inputs for comparisons.
"""
import argparse
from collections import defaultdict,deque
from contextlib import contextmanager
import importlib.util
import json
from functools import wraps
from pathlib import Path
import resource
import time
import mipframe as mf


_PHASE=[]

@contextmanager
def measured(times,name):
    start=time.perf_counter();_PHASE.append(name)
    try:yield
    finally:
        times[name]=times.get(name,0)+time.perf_counter()-start
        _PHASE.pop()


def paths(edges,requests):
    """Grouped retained-edge BFS, with deterministic adjacency order."""
    adj=defaultdict(list);wanted=defaultdict(list)
    for record in edges:
        u,v,r=record['u'],record['v'],record['row']
        adj[getattr(u,'id',u)].append((getattr(v,'id',v),r.id))
    for r,u,v in requests:wanted[u].append((r,v))
    result={}
    for source,targets in sorted(wanted.items()):
        remaining={v for r,v in targets};parent={source:None};queue=deque([source])
        while queue and remaining:
            u=queue.popleft()
            for v,r in adj[u]:
                if v not in parent:
                    parent[v]=(u,r);remaining.discard(v);queue.append(v)
        if remaining:raise AssertionError('Missing retained path')
        for target,v in targets:
            path=[]
            while v!=source:v,edge=parent[v];path.append(edge)
            result[target]=list(reversed(path))
    return result


def public_operations(case,m,w,times):
    with measured(times,'inspect_query'):
        repr(m.ir);m.rows.head(8).to_records();m.vars.head(8).to_records()
        m.rows.group_by('nnz').agg(count=mf.count()).to_records()
    with measured(times,'ir_cold'):
        m.row_families.select('id','count').to_records()
        m.var_families.select('id','count').to_records()
    with measured(times,'ir_cached'):
        m.row_families.select('id','count').to_records()
        m.var_families.select('id','count').to_records()
    if case==33:
        with measured(times,'structural_query'):
            P=m.rows.where(nnz=2);assert P.count()==160625
            assert P.where((mf.col('ub')!=0)|(mf.col('lb')!=-float('inf'))).count()==0
            T=P.terms
            assert T.where((mf.col('coef')!=1)&(mf.col('coef')!=-1)).count()==0
            E=T.where(coef=1).select('row',u=mf.col('var')).join(
                T.where(coef=-1).select('row',v=mf.col('var')),on='row',cardinality='1:1',require='both')
            assert E.count()==160625
        with measured(times,'graph_set'):
            with measured(times,'reduction_and_witness'):
                reduction=mf.ops.graph.transitive_reduction(E,vertices=m.vars,witnesses=True)
            assert reduction.kept.count()==70625 and reduction.removed.count()==90000
            with measured(times,'certificate_gather'):
                certificate=reduction.witnesses.as_linear_certificate()
            assert set(map(int,certificate.targets.ids))==set(w['targets'])
            w=dict(w,certificate=certificate,targets=certificate.targets.ids)
            assert reduction.stats['path_edges']>=180000
        with measured(times,'structural_query'):
            R=m.rows-P;assert R.count()==8
            assert R.terms.where(mf.col('coef')<=0).count()==0
            R.terms.group_by('row').agg(nnz=mf.count(),coef_min=mf.col('coef').min(),coef_max=mf.col('coef').max(),box_max_activity=mf.col('coef').sum()).to_records()
    else:
        # Family labels are presentation order, so select fixed lexical scopes
        # and validate complete numerical contents through the public operations.
        with measured(times,'structural_query'):
            metadata=m.rows.select('row','name').to_records()
            ids=lambda prefix:[r['row'] for r in metadata if r['name'].startswith(prefix)]
            cap=m.rows.where(mf.col('row').isin(ids('rescap_')))
            jobs=m.rows.where(mf.col('row').isin(ids('jobexec_')))
            prec=m.rows.where(mf.col('row').isin(ids('precedence_')))
            active=m.vars.where(mf.col('lb')!=mf.col('ub'))
            T=m.terms(rows=cap,vars=active)
            T.group_by('coef').agg(n=mf.count()).to_records()
            assert jobs.count()==30
            jobs.vars.project(job=jobs).count()
            pt=prec.terms.join(m.vars.select('var','name'),on='var',cardinality='m:1',require='left').to_records()
            grouped=defaultdict(list)
            for rec in pt:grouped[rec['row']].append(rec)
            records=[]
            for row,terms in grouped.items():
                assert len(terms)==2
                plus=next(t for t in terms if t['coef']==1);minus=next(t for t in terms if t['coef']==-1)
                assert plus['name'].startswith('C_') and minus['name'].startswith('S_')
                records.append(dict(row=row,u=int(plus['name'][2:]),v=int(minus['name'][2:])))
            E=mf.Frame.from_records(records,schema={'row':m.rows.schema['row'],'u':'int','v':'int'},key=('row',),models=(m,))
        with measured(times,'graph_set'):
            pairs=mf.ops.sets.pairs(T.select(set=mf.col('row'),member=mf.col('var')),sets=cap,relation='subset',ordered=True)
            pairs.count()
            retained=mf.ops.graph.transitive_reduction(E);retained.to_records()
        with measured(times,'candidate_compile'):
            excluded=m.check.one_hot_affine(groups=w['groups']).bound_exclusions().require()
            assert len(excluded.target_ids)==6306
            remaining=active.where(~mf.col('var').isin([m.vars.loc[int(v)] for v in excluded.target_ids]))
        with measured(times,'graph_set'):
            pairs=mf.ops.sets.pairs(m.terms(rows=cap,vars=remaining).select(set=mf.col('row'),member=mf.col('var')),sets=cap,relation='subset',ordered=True)
            pairs.count()
            components=mf.ops.sets.components(m.terms(vars=remaining).select(set=mf.col('row'),member=mf.col('var')),sets=m.rows,vertices=remaining)
            components.select('component').distinct().count()
    return w


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--case',type=int,choices=(32,33),required=True)
    p.add_argument('--mode',choices=('fixed','public'),required=True)
    p.add_argument('--root',type=Path,default=Path('/Users/bhz/Code/reform_test'))
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--witness-33',type=Path)
    p.add_argument('--overlay-roundtrip',action='store_true')
    p.add_argument('--sql-trace',action='store_true')
    p.add_argument('--helper',type=Path,default=Path(__file__).with_name('benchmark_proof_carrying.py'))
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('replay_helper',a.helper);helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    times={};detail={};patches=[];sql={}
    def instrument(cls,name,label):
        original=getattr(cls,name)
        @wraps(original)
        def call(*args,**kwargs):
            with measured(detail,label):return original(*args,**kwargs)
        setattr(cls,name,call);patches.append((cls,name,original))
    from mipframe.check.compiler import Checker
    from mipframe.check.compact import OneHotAffine
    from mipframe.edit.delta import DeltaEdit
    from mipframe.check.kernel import CertificateBatch
    instrument(CertificateBatch,'__post_init__','certificate_compile')
    instrument(Checker,'linear_rows','check')
    instrument(Checker,'affine_integrality','check')
    instrument(OneHotAffine,'bound_exclusions','check')
    instrument(DeltaEdit,'commit','edit_all_commits')
    instrument(DeltaEdit,'substitute_fixed','substitution_selection')
    from mipframe.edit import substitution
    instrument(substitution,'compile_fixed','substitution_compilation')
    if a.sql_trace:
        import sqlite3,re
        original_connect=sqlite3.connect
        def trace(statement):
            op=statement.lstrip().split(None,1)[0].upper() if statement.strip() else 'EMPTY'
            category=('control_write' if op in ('INSERT','UPDATE','DELETE','REPLACE','CREATE','DROP','BEGIN','COMMIT','PRAGMA')
                      else 'relational_compute' if re.search(r'\b(JOIN|GROUP BY|ORDER BY|WITH)\b',statement,re.I)
                      else 'catalog_read' if op=='SELECT' else 'other')
            key=(_PHASE[-1] if _PHASE else 'other')+':'+category+':'+op;sql[key]=sql.get(key,0)+1
            if category=='relational_compute':sql.setdefault('relational_samples',{})[statement[:400]]=True
        def connect(*args,**kwargs):
            db=original_connect(*args,**kwargs);db.set_trace_callback(trace);return db
        sqlite3.connect=connect;patches.append((sqlite3,'connect',original_connect))
    if a.overlay_roundtrip:
        original_write=mf.Model.write
        @wraps(original_write)
        def write(owner,*args,**kwargs):
            receipt=original_write(owner,*args,**kwargs)
            with measured(detail,'save_overlay'):owner.save(a.out/'overlay-native')
            with measured(detail,'load_overlay'):
                with mf.load(a.out/'overlay-native') as loaded:
                    assert loaded.shape==owner.shape and loaded.nnz==owner.nnz
            return receipt
        mf.Model.write=write;patches.append((mf.Model,'write',original_write))
    start=time.perf_counter()
    read=mf.read
    def timed_read(*args,**kwargs):
        with measured(times,'read'):return read(*args,**kwargs)
    mf.read=timed_read
    try:
        with measured(times,'read_and_witness_preparation'):m,w,audit=helper.prepare(a.case,a.root)
    finally:mf.read=read
    try:
        if a.case==33 and a.witness_33 is not None:
            w=json.loads(a.witness_33.read_text())
            assert len(w['targets'])==90000 and sum(map(len,w['paths']))==320000
        if a.mode=='public':w=public_operations(a.case,m,w,times)
        result=helper.run_mf(a.case,m,w,a.out)
        times.update(result['timings'])
        with measured(times,'save'):
            m.save(a.out/'native')
        with measured(times,'load'):
            with mf.load(a.out/'native') as loaded:assert loaded.shape==m.shape and loaded.nnz==m.nnz
        times['complete']=time.perf_counter()-start
        times.update(detail)
        result.update(mode=a.mode,sql_counts=sql,sql_trace=a.sql_trace,overlay_roundtrip=a.overlay_roundtrip,timings=times,maxrss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      artifact_bytes=sum(p.stat().st_size for p in a.out.rglob('*') if p.is_file()),
                      filesystem_cache='not evicted; paired processes run in alternating order')
        (a.out/'measurement.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
    finally:
        m.close()
        for obj,name,original in reversed(patches):setattr(obj,name,original)


if __name__=='__main__':main()
