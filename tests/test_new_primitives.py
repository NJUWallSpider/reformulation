from fractions import Fraction as Q
import json
import numpy as np
import pytest
import mipframe as mf
from mipframe.check.kernel import facts


def model(tmp_path, text, suffix='lp'):
    path=tmp_path/('input.'+suffix);path.write_text(text);return mf.read(path)


LP='''Minimize
 obj: 3 x + y + 4
Subject To
 a: 2 x + y <= 9
 b: x <= 2
Bounds
 x = 2
 0 <= y <= 4
End
'''


def test_substitution_compiled_lift_and_native(tmp_path,monkeypatch):
    m=model(tmp_path,LP);x=m.vars.by_name('x');y=m.vars.by_name('y')
    e=m.edit().substitute_fixed()
    summary=e.summary();compiled=e._compiled()
    monkeypatch.setattr(facts(m),'column',lambda *a:pytest.fail('repeated substitution'))
    child=e.commit()
    assert child.shape==(1,1) and child.objective.constant==10
    assert child.rows.iloc[0].ub==5 and child.nnz==1
    semantic=child.diff(m).semantic
    assert semantic['coordinates']=='PROJECTED'
    assert semantic['mip']==semantic['lp']=='EQUIVALENT_UNDER_EXTENSION'
    assert semantic['objective']['offset']==0 and semantic['objective']['constant_delta']==6
    lifted=child.lift({child.vars.by_name('y'):Q(1,2)})
    assert {r['var'].id:r['value'] for r in lifted}=={x.id:2,y.id:Q(1,2)}
    saved=child.save(tmp_path/'native');child.close()
    with mf.load(saved) as restored:
        assert restored.shape==(1,1) and restored.objective.constant==10
        assert restored.audit(source=m)['status']=='PROVED'
    m.close()


def test_zero_substitution_no_coefficient_decoding_and_atomicity(tmp_path,monkeypatch):
    m=model(tmp_path,LP.replace('x = 2','x = 0').replace('2 x + y','17 x + y'))
    x=m.vars.by_name('x');y=m.vars.by_name('y');e=m.edit()
    with pytest.raises(ValueError):e.substitute_fixed([x,y])
    assert not e._fixed
    e.substitute_fixed([x]).substitute_fixed([x])
    numbers=facts(m).numbers;original=type(numbers).__getitem__
    def number(self,i):
        result=original(self,i)
        if result==17:pytest.fail('zero substitution decoded matrix coefficient')
        return result
    monkeypatch.setattr(type(numbers),'__getitem__',number)
    child=e.commit();assert child.nnz==1 and child.rows.iloc[0].ub==9
    child.close();m.close()


def test_infeasible_and_source_relative(tmp_path):
    with model(tmp_path,LP.replace('b: x <= 2','b: x <= 1')) as m:
        e=m.edit().substitute_fixed()
        assert e.summary()['status']=='INFEASIBLE'
        with pytest.raises(mf.SubstitutionInfeasibleError):e.build()
        with e.build(allow_infeasible=True) as c:
            assert c.shape==(2,1) and c.rows.by_name('b').ub==-1
    with model(tmp_path,LP) as m:
        x=m.vars.by_name('x')
        e=m.edit();e.add_row(x<=5,name='new')
        with pytest.raises(ValueError,match='intermediate'):e.substitute_fixed([x])
        assert not e._fixed
        e=m.edit().substitute_fixed([x])
        with pytest.raises(ValueError,match='intermediate'):e.add_row(x<=5,name='new')


MPS="""NAME TEST
ROWS
 N OBJ
 L R1
 L R2
COLUMNS
 X OBJ 3 R1 2
 X R2 1
 Y OBJ 1 R1 1
RHS
 rhs R1 9 R2 2
BOUNDS
 FX bnd X 2
 UP bnd Y 4
ENDATA
"""


def test_patch_source_portable_and_failed_write_retry(tmp_path):
    m=model(tmp_path,MPS,'mps');e=m.edit().substitute_fixed()
    plan=e.patch_plan();assert plan.mode=='STREAM_PATCH'
    path=tmp_path/'out.mps';path.write_text('occupied')
    with pytest.raises(FileExistsError):e.write(path,strategy='patch')
    assert not e._sealed;path.unlink()
    receipt=e.write(path,strategy='patch');assert receipt['writer']=='stream_patch'
    with mf.read(path) as r:assert r.shape==(1,1) and r.objective.constant==6
    native=e._built.save(tmp_path/'native');e.close();m.close();(tmp_path/'input.mps').unlink()
    with mf.load(native) as r:
        assert r.patch_plan().mode=='STREAM_PATCH'
        r.write(tmp_path/'again.mps',strategy='patch')


@pytest.mark.parametrize('memory',[128,0.015625])
def test_graph_witnesses_parallel_ids_budget_and_bridge(tmp_path,memory):
    with model(tmp_path,'''Minimize
 obj: x
Subject To
 a: x-y <= 0
 b: y-z <= 0
 c: x-z <= 0
Bounds
 0 <= x <= 2
 0 <= y <= 2
 0 <= z <= 2
End
''') as m:
        x,y,z=[m.vars.by_name(n) for n in ('x','y','z')]
        E=mf.Frame.from_records([dict(edge_id=k,u=u,v=v,row=m.rows.by_name(r)) for k,u,v,r in
                                [('a',x,y,'a'),('b',y,z,'b'),('c',x,z,'c'),('d',x,y,'a')]],
              schema=dict(edge_id='str',u=m.vars.schema['var'],v=m.vars.schema['var'],row=m.rows.schema['row']),models=[m])
        R=mf.ops.graph.transitive_reduction(E,witnesses=True,memory_mb=memory)
        assert R.kept.count()==3 and R.removed.count()==1 and R.stats['path_edges']==2
        assert list(R.witnesses.path('c').column('edge_id'))==['a','b']
        assert len(R.witnesses.head())==1
        cert=R.witnesses.as_linear_certificate()
        R.close()
        with pytest.raises(ValueError):R.witnesses.path('c')
        proof=m.check.linear_many(cert).require()
        with m.edit().drop_rows([m.rows.by_name('c')],because=proof).commit() as child:assert child.shape==(2,3)
        with pytest.raises(mf.QueryTooLarge):mf.ops.graph.transitive_reduction(E,witnesses=True,max_witness_edges=1)


def test_shared_budget_transfer_and_immutable_buffers():
    from mipframe.data.runtime import WorkContext
    with WorkContext(0.015625,1) as work:
        a=work.array(2000,'i8');child=work.fork();b=child.array(2000,'i8')
        assert work.resident<=work.memory and work.disk>0
        work.transfer(a,child);work.close()
        a[:]=2;a=child.seal(a)
        with pytest.raises(ValueError):a.flags.writeable=True
        child.close();assert work.resident==work.disk==0


@pytest.mark.parametrize('memory',[128,0.015625])
def test_random_dag_witness_oracle(memory):
    import random
    rng=random.Random(713)
    for n in (0,1,8,40):
        records=[dict(u=u,v=v,edge_id=f'{u}-{v}',payload=u*100+v)
                 for u in range(n) for v in range(u+1,n) if rng.random()<.22]
        E=mf.Frame.from_records(records,schema=dict(u='int',v='int',edge_id='str',payload='int'))
        R=mf.ops.graph.transitive_reduction(E,witnesses=True,memory_mb=memory)
        kept={r['edge_id']:r for r in R.kept};removed=list(R.removed)
        assert list(kept)==[r['edge_id'] for r in records if r['edge_id'] in kept]
        def reachable(u,v,skip):
            seen={u};stack=[u]
            while stack:
                x=stack.pop()
                for r in records:
                    if r['edge_id']==skip or r['u']!=x:continue
                    if r['v']==v:return True
                    if r['v'] not in seen:seen.add(r['v']);stack.append(r['v'])
            return False
        for r in records:assert (r['edge_id'] not in kept)==reachable(r['u'],r['v'],r['edge_id'])
        for r in removed:
            path=list(R.witnesses.path(r['edge_id']));at=r['u']
            for edge in path:
                assert edge['edge_id'] in kept and edge['u']==at;at=edge['v']
            assert at==r['v']
        R.close()


def test_streaming_audits_and_corruption(tmp_path):
    from mipframe.store.readback import compare,compare_lp
    with model(tmp_path,LP) as m:
        p=tmp_path/'exact.mps';m.write(p,strategy='canonical',memory_mb=.015625)
        assert compare(facts(m),p,memory_mb=.015625)['status']=='PROVED'
        p.write_text(p.read_text().replace('RHS\n','RHS\n    rhs  a  100\n'))
        # A later RHS assignment legitimately overrides this insertion.
        compare(facts(m),p,memory_mb=.015625)
        text=p.read_text();p.write_text(text.replace('  a  2','  a  3',1))
        with pytest.raises(ValueError):compare(facts(m),p,memory_mb=.015625)
        lp=tmp_path/'exact.lp';m.write(lp,memory_mb=.015625)
        assert compare_lp(facts(m),lp,memory_mb=.015625)['status']=='PROVED'


def test_fixed_continuation_ranges_and_vector_selection(tmp_path):
    from mipframe.store.patch import pair_record,bound_record
    lines=[b'NAME TEST\nROWS\n N  OBJ\n L  A\n E  B\nCOLUMNS\n',
           pair_record('X',[('A','2'),('B','1')],'fixed'),
           pair_record('',[('OBJ','3')],'fixed'),
           pair_record('Y',[('A','1'),('B','1')],'fixed'),
           b'RHS\n',pair_record('R1',[('A','9'),('B','2')],'fixed'),
           pair_record('R2',[('A','19'),('B','12')],'fixed'),
           b'RANGES\n',pair_record('Q1',[('B','4')],'fixed'),
           b'BOUNDS\n',bound_record('FX','B1','X',2,'fixed'),
           bound_record('UP','B1','Y',10,'fixed'),b'ENDATA\n']
    p=tmp_path/'fixed.mps';p.write_bytes(b''.join(lines))
    with mf.read(p,mps_format='fixed',rhs='R1',ranges='Q1',bounds='B1') as m:
        child=m.edit().substitute_fixed().commit()
        assert child.patch_plan().output_format=='fixed'
        out=tmp_path/'fixed-out.mps';child.write(out,strategy='patch',memory_mb=.015625)
        assert b'R2' not in out.read_bytes()
        with mf.read(out,mps_format='fixed') as got:
            assert got.rows.by_name('A').ub==5
            assert got.rows.by_name('B').lb==0 and got.rows.by_name('B').ub==4
            assert got.objective.constant==6
        child.close()


def test_nonzero_exact_cancellation_bigints_and_composed_lift(tmp_path):
    with model(tmp_path,'''Minimize
 obj: 3 x + 2 y + z
Subject To
 a: 100000000000000000000 x - 200000000000000000000 y + z <= 7
Bounds
 x = 2
 y = 1
 0 <= z <= 8
End
''') as m:
        e=m.edit().substitute_fixed();compiled=e._compiled()
        assert not compiled.shifts
        with e.commit() as child:
            assert child.rows.by_name('a').ub==7 and child.objective.constant==8
        with m.edit().substitute_fixed([m.vars.by_name('x')]).commit() as a:
            with a.edit().substitute_fixed().commit() as b:
                values=b.lift({b.vars.by_name('z'):3},ancestor=m)
                assert sorted(r['value'] for r in values)==[1,2,3]


def test_mask_pages_are_shared_without_full_copy():
    from mipframe.data.mask import LiveMask
    a=LiveMask(10_000_000,removed=np.array([2],dtype='u8'))
    b=LiveMask(len(a),a,np.array([8_000_000],dtype='u8'))
    assert b.parent is a and a.data.nbytes==b.data.nbytes==LiveMask.PAGE
    assert b.count==9_999_998 and not b[2] and not b[8_000_000] and b[3]


def test_each_substitution_call_keeps_its_trivial_policy(tmp_path):
    text='Minimize\n obj: z\nSubject To\n a: x <= 0\n b: y <= 0\nBounds\n x = 0\n y = 0\n z >= 0\nEnd\n'
    with model(tmp_path,text) as m:
        e=m.edit().substitute_fixed([m.vars.by_name('x')],drop_trivial_rows=True)
        e.substitute_fixed([m.vars.by_name('y')],drop_trivial_rows=False)
        with e.commit() as child:
            assert child.rows.count()==1 and child.rows.by_name('b').ub==0
            saved=child.save(tmp_path/'policies')
        with mf.load(saved) as loaded:assert loaded.audit(source=m)['status']=='PROVED'


def test_patch_binary_bounds_and_audit_failure_are_atomic(tmp_path,monkeypatch):
    p=tmp_path/'binary.mps';p.write_text('NAME B\nROWS\n N obj\n L a\nCOLUMNS\n x a 1\nRHS\n rhs a 1\nBOUNDS\n BV b x\nENDATA\n')
    with mf.read(p) as m:
        x=m.vars.by_name('x');e=m.edit().set_var_bounds(x,ub=0)
        with e.build() as child:
            out=tmp_path/'changed.mps';child.write(out,strategy='patch')
            with mf.read(out) as actual:assert actual.vars.by_name('x').kind=='I' and actual.vars.by_name('x').ub==0
        e=m.edit();out=tmp_path/'failed.mps'
        import mipframe.store.readback as rb
        original=rb.compare
        def fail(*a,**kw):raise ValueError('injected audit failure')
        monkeypatch.setattr(rb,'compare',fail)
        with pytest.raises(ValueError,match='audit failure'):e.write(out)
        assert not e._sealed and not out.exists()
        monkeypatch.setattr(rb,'compare',original)
        e.write(out)


def test_graph_recipe_limits_and_tampered_certificate(tmp_path):
    with model(tmp_path,'Minimize\n obj: x\nSubject To\n a: x-y <= 0\n b: y-z <= 0\n c: x-z <= 0\nEnd\n') as m:
        E=mf.Frame.from_records([dict(u=0,v=1,row=m.rows.by_name('a')),dict(u=1,v=2,row=m.rows.by_name('b')),dict(u=0,v=2,row=m.rows.by_name('c'))],schema=dict(u='int',v='int',row=m.rows.schema['row']),models=[m])
        R=mf.ops.graph.transitive_reduction(E,witnesses=True)
        spec=R.to_spec()
        restored=mf.TransitiveReductionResult.from_spec(spec,models={m.uid:m},relations={E._query.spec["relation"]:E})
        assert restored.kept.count()==2 and restored.witnesses.path(2).count()==2
        cert=R.witnesses.as_linear_certificate()
        bad=mf.LinearCertificate(cert.uid,cert.revision,cert.targets,mf.CertificateBatch([0,2],[0,0]))
        assert not m.check.linear_many(bad).ok
        restored.close();R.close()
        with pytest.raises(mf.QueryTooLarge):mf.ops.graph.transitive_reduction(E,witnesses=True,max_output_rows=1)
    n=250
    from mipframe.frame.array_source import frame_from_columns
    E=frame_from_columns(dict(u=np.r_[np.arange(n-1),np.arange(n-2)],v=np.r_[np.arange(1,n),np.full(n-2,n-1)]),dict(u='int',v='int'))
    with pytest.raises(mf.QueryTooLarge):mf.ops.graph.transitive_reduction(E,witnesses=True,memory_mb=.015625,max_witness_edges=1000)
    with pytest.raises(mf.DiskBudgetExceeded):mf.ops.graph.transitive_reduction(E,witnesses=True,memory_mb=.015625,disk_mb=.001)


def test_old_native_rejected_and_unknown_feature(tmp_path):
    p=tmp_path/'model.lp';p.write_text(LP)
    with mf.read(p) as original:saved=original.save(tmp_path/'v7')
    manifest=saved/'manifest.json';data=json.loads(manifest.read_text());assert data['schema_version']==7
    for version in (5,6):
        manifest.write_text(json.dumps(dict(data,schema_version=version)))
        with pytest.raises(mf.MIPFrameError,match='re-import'):mf.load(saved)
    data['features'].append('unknown-future');manifest.write_text(json.dumps(data))
    with pytest.raises(mf.MIPFrameError,match='feature'):mf.load(saved)


@pytest.mark.parametrize('format',['mps','lp'])
def test_output_disk_quota_has_no_partial_publication(tmp_path,format):
    from mipframe.check.algorithm import DiskBudgetExceeded
    with model(tmp_path,MPS,'mps') as m:
        e=m.edit(memory_mb=16).substitute_fixed()
        target=tmp_path/('limited.'+format)
        with pytest.raises(DiskBudgetExceeded):e.write(target,memory_mb=.015625,disk_mb=.00001)
        assert not target.exists() and not e._sealed
        assert not any(p.name.startswith('tmp') for p in tmp_path.iterdir())
        receipt=e.write(target,memory_mb=16,disk_mb=16)
        assert receipt.verified and e._sealed


def test_diff_extension_map_borrows_fixed_columns(tmp_path):
    with model(tmp_path,LP) as m:
        with m.edit().substitute_fixed().commit() as child:
            mapping=child.diff(m).lifting
            assert not isinstance(mapping,dict)
            assert dict(mapping)=={m.vars.by_name('x').id:2}
            assert mapping.layers[0] is facts(child).delta.fixed
