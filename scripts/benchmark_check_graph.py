"""Full test34 reformulation workload, with generic linear/cases certificates.

Run in three separate processes for acceptance. This script does not reuse saved
proofs or modify the supplied model/run directory. It is deliberately specific
to the benchmark; the library rules contain no model-specific recognition.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import mipframe as mf
from mipframe.check.kernel import facts


def run(path):
    timings={}
    def timed(name,fn):
        start=perf_counter()
        try:return fn()
        finally:
            timings[name]=perf_counter()-start
            print(json.dumps({'phase':name,'seconds':timings[name]}),flush=True)
    with timed('read',lambda:mf.read(path)) as m:
        f=facts(m)
        def structure():
            aids=tuple(map(int,m.family('F2').rows.ids))
            eids=tuple(map(int,m.family('F1').rows.ids))
            zeroids=tuple(map(int,m.family('F4').rows.ids))
            links=tuple(map(int,(m.family('F0').rows | m.family('F3').rows).ids))
            facility=m.family('F5').rows.one().id
            z=m.vars.by_name('z').id
            ys={v for v,c in f.terms(facility)}
            assert len(ys)==231 and f.row_bounds(facility)==(13,13)
            assert all(c==1 for _,c in f.terms(facility))
            ep={}
            for r in eids:
                terms=dict(f.terms(r));assert terms.pop(z)==-1 and len(terms)==1
                v,d=next(iter(terms.items()));assert d>0 and v not in ep
                assert f.row_bounds(r)==(-float('inf'),0)
                ep[v]=(r,d)
            groups={}
            xs=set()
            for aid in aids:
                terms=dict(f.terms(aid));assert len(terms)==231 and all(c==1 for c in terms.values())
                assert f.row_bounds(aid)==(1,1) and not xs.intersection(terms)
                xs.update(terms);groups[aid]=tuple(terms)
                assert sum(v not in ep for v in terms)==1
            xy={}
            for r in links:
                terms=dict(f.terms(r));assert len(terms)==2 and f.row_bounds(r)==(-float('inf'),0)
                xx=[v for v,c in terms.items() if v in xs and c==1]
                yy=[v for v,c in terms.items() if v in ys and c==-1]
                assert len(xx)==len(yy)==1 and xx[0] not in xy
                xy[xx[0]]=yy[0]
            assert set(xy)==xs and set(ep)<=xs
            assert all({xy[v] for v in group}==ys for group in groups.values())
            for v in xs|ys:assert f.var_kind(v)=='I' and f.var_bounds(v)==(0,1)
            for r in zeroids:assert dict(f.terms(r))=={z:-1} and f.row_bounds(r)==(-float('inf'),0)
            assert set(aids)|set(eids)|set(zeroids)|set(links)|{facility}==set(f.row_ids())
            assert xs|ys|{z}==set(f.var_ids())
            assert len(groups)==231 and len(ep)==53130
            return aids,eids,zeroids,z,ys,ep,groups,xy
        aids,eids,zeroids,z,ys,ep,groups,xy=timed('structure',structure)
        def forward():
            expressions={}
            rows=m.rows;variables=m.vars
            with m.check.batch() as b:
                for aid in aids:
                    equality=rows.loc[aid].eq_claim
                    positive=sorted(((v,*ep[v]) for v in groups[aid] if v in ep),key=lambda item:item[2])
                    _,r,C=positive[0];expr=rows.loc[r].expr
                    p=b.linear(expr.le(0),using=[rows.loc[r].ub_claim])
                    for j,(v,r,d) in enumerate(positive[1:],1):
                        var=variables.loc[v];expr=expr+d*var;target=expr.le(0)
                        a0=var.eq(0);a1=var.eq(1)
                        p0=b.linear(target,using=[p,(d,a0)],assume=[a0],residual='bounds')
                        p1=b.linear(target,using=[rows.loc[r].ub_claim,(C,equality),(-C,a1)],assume=[a1],residual='bounds')
                        p=b.cases(target,var=var,cases={0:p0,1:p1},result_key=str(aid) if j==len(positive)-1 else None)
                        C=d
                    expressions[aid]=expr
                results=b.run().require()
            return expressions,results
        expressions,results=timed('forward',forward)
        def add():
            edit=m.edit()
            for aid in aids:edit.add_row(expressions[aid].le(0),name=f'aggregate_epigraph_{aid}',because=results[str(aid)])
            return edit.commit()
        with timed('add',add) as middle:
            def reverse():
                targets=[];witnesses=[]
                for aid in aids:
                    aggregate=middle.rows.by_name(f'aggregate_epigraph_{aid}').id
                    for v in groups[aid]:
                        if v in ep:targets.append(ep[v][0]);witnesses.append([aggregate])
                proof=middle.check.linear_rows(targets=targets,witnesses=witnesses,residual='bounds').require()
                zeros=middle.check.linear_rows(targets=zeroids,witnesses=mf.CertificateBatch(
                    offsets=range(len(zeroids)+1),sources=[z]*len(zeroids),sides='lb',kinds='var')).require()
                return proof,zeros
            backward,zeros=timed('reverse',reverse)
            def drop():
                edit=middle.edit();edit.drop_rows(eids,because=backward);edit.drop_rows(zeroids,because=zeros)
                return edit.commit()
            with timed('drop',drop) as final:
                assert final.shape==(53824,53593) and final.nnz==213675
                selected={m.vars.by_name(f'y[{k}]').id for k in range(1,14)}
                dmax=max(d for _,d in ep.values())
                def check_relaxation(point):
                    assert set(point)==set(f.var_ids())
                    for v,value in point.items():
                        lo,hi=f.var_bounds(v);assert lo<=value<=hi
                    for row in f.row_ids():
                        value=sum(c*point[v] for v,c in f.terms(row))
                        lo,hi=f.row_bounds(row);assert lo<=value<=hi
                def uniform():
                    point={v:(dmax/231 if v==z else Fraction(13,231) if v in ys else Fraction(1,231)) for v in f.var_ids()}
                    check_relaxation(point)
                    assert all(sum(ep[v][1] for v in group if v in ep)>dmax for group in groups.values())
                timed('uniform_witness',uniform)
                def relaxed():
                    value=max(ep.get(v,(None,0))[1] for v,y in xy.items() if y in selected)/13
                    point={v:(value if v==z else int(v in selected) if v in ys else Fraction(1,13) if xy[v] in selected else 0) for v in f.var_ids()}
                    check_relaxation(point)
                    assert any(min(ep.get(v,(None,0))[1] for v in group if xy[v] in selected)>value for group in groups.values())
                timed('relaxed_witness',relaxed)
                def integer_points():
                    chosen={min((v for v in group if xy[v] in selected),key=lambda v:ep.get(v,(None,0))[1]) for group in groups.values()}
                    value=max(ep.get(v,(None,0))[1] for v in chosen)
                    point={v:(value if v==z else int(v in selected) if v in ys else int(v in chosen)) for v in f.var_ids()}
                    for model in (m,final):
                        with model.check.batch() as b:
                            b.solution(point,result_key='point')
                            report=b.run().require()['point']
                        report.close()
                timed('integer_points',integer_points)
                with TemporaryDirectory(prefix='mipframe-check-benchmark-') as directory:
                    artifact=Path(directory)/'model.mps'
                    receipt=timed('write_readback',lambda:final.write(artifact,audit='readback'))
                    assert receipt.verified
                    audit=timed('audit',lambda:final.audit(source=m,artifact=artifact))
                    assert audit['status']=='PROVED'
                    semantic=audit['semantic']
                    assert semantic['lp']=='SUBSET' and semantic['mip']=='SAME' and semantic['objective']['kind']=='SAME'
    core=sum(timings[k] for k in ('forward','relaxed_witness','write_readback','audit'))
    total=sum(timings.values())
    return dict(timings=timings,core_seconds=core,total_seconds=total,
                accepted=core<=30 and total<=60,statistics=dict(results.statistics))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('model',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=run(args.model)
    print(json.dumps(result,indent=2))
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
