"""Independent arithmetic, dependency attacks, overlays and actual-file audits."""
from fractions import Fraction as Q
import json
import random
import pytest
import mipframe as mf
from mipframe.check.kernel import facts


def model(tmp_path,text,name='input.lp'):
    p=tmp_path/name;p.write_text(text)
    return mf.read(p)


@pytest.fixture
def m(tmp_path):
    with model(tmp_path,'''Minimize
 obj: x
Subject To
 a: x - y <= 0
 b: y - z <= 0
 c: x - z <= 0
 d: x - y <= 1
Bounds
 0 <= x <= 2
 0 <= y <= 2
 0 <= z <= 2
Generals
 x y z
End
''') as m:yield m


def test_array_linear_lazy_and_cold_sql(m,monkeypatch):
    traces=[];m.model.db.set_trace_callback(traces.append)
    proof=m.check.linear_rows(targets=[2,3],witnesses=[[0,1],[0]])
    assert proof.ok
    assert not traces # numerical facts borrow the array runtime without SQL
    traces.clear()
    import mipframe.check.compact as compact
    original=compact.ProofRef
    monkeypatch.setattr(compact,'ProofRef',lambda *a:pytest.fail('Unexpected report materialization'))
    # Kernel-only check must not build a query store or materialize a report.
    again=m.check.linear_rows(targets=[2,3],witnesses=[[0,1],[0]])
    assert again.ok and not traces
    monkeypatch.setattr(compact,'ProofRef',original)
    e=m.edit().drop_rows([2,3],because=proof)
    summary=e.summary();n=e.commit()
    assert summary['semantic']['mip']=='SAME' and n._store._arrays is None and n._store._db is None
    assert n.diff(m).rows_dropped==(2,3)
    assert n._store._arrays is None and not traces
    n.close()


def test_witness_validation_and_immutability(m):
    for off in ([1,2],[0,3],[0,-1,2]):
        with pytest.raises(ValueError):mf.CertificateBatch(off,[0,1])
    for ids in ([True],[0.0],[-1]):
        with pytest.raises(ValueError):mf.Targets(ids)
    c=mf.CertificateBatch.paths([[0,1]])
    with pytest.raises(ValueError):c.sources.flags.writeable=True
    p=m.check.linear_rows(targets=[2],witnesses=c)
    with pytest.raises(AttributeError):p._status=b'\x01'
    with pytest.raises(TypeError):mf.BatchProof(None,m,None,b'\x01',rule='fake')
    assert not m.check.linear_rows(targets=[2],witnesses=[[0]]).ok
    with pytest.raises(ValueError):m.check.linear_rows(targets=[2],witnesses=[[99]])
    with pytest.raises(ValueError):m.check.linear_rows(targets=[2],witnesses=mf.CertificateBatch([0,1],[0],[-1]))


def test_circular_row_deletion_and_grounded_chain(m):
    p=m.check.linear_rows(targets=[0],witnesses=[[0]])
    e=m.edit().drop_rows([0],because=p)
    with pytest.raises(ValueError,match='grounded'):e.commit()
    with e.build() as n:assert n.diff(m).semantic['mip']=='UNKNOWN'
    p=m.check.linear_rows(targets=[2,3],witnesses=[[0,1],[0]])
    with m.edit().drop_rows([2,3],because=p).commit() as n:assert n.shape==(2,3)


def test_foreign_snapshot_and_partial_proof(m,tmp_path):
    path=m.write(tmp_path/'a.mps')
    with mf.read(tmp_path/'a.mps') as other:
        p=other.check.linear_rows(targets=[2],witnesses=[[0,1]])
        with pytest.raises(ValueError):m.edit().drop_rows([2],because=p)
    p=m.check.linear_rows(targets=[2,0],witnesses=[[0,1],[1]])
    assert not p.ok
    with pytest.raises(ValueError):m.edit().drop_rows([2],because=p)
    with m.edit().drop_rows([2],because=p.for_target(2)).commit() as n:assert n.shape==(3,3)


def test_scalar_batch_round_and_negative_floor(m):
    x=m.vars.by_name('x')
    p=m.check.linear(x<=Q(5,2),residual='bounds')
    r=m.check.round(x<=2,from_=p)
    assert r.ok
    b=m.check.bound(vars=[x.id],values=[Q(5,2)])
    assert b.round_many(ub=2).ok
    q=m.check.linear(x>=Q(-3,2),residual='bounds')
    assert m.check.round(x>=-1,from_=q).ok
    with m.edit().set_var_bounds(x,ub=2,because=r).commit() as n:assert n.diff(m).semantic['mip']=='SAME'


def test_exact_bigints_and_rationals(tmp_path):
    huge=2**90
    with model(tmp_path,f'''Minimize
 obj: x
Subject To
 a: {huge} x - {huge} y <= 0
 b: 0.5 y - 0.5 z <= 0
 c: {huge} x - {huge} z <= 0
Bounds
 x free
 y free
 z free
End
''') as n:
        p=n.check.linear_rows(targets=[2],witnesses=mf.CertificateBatch([0,2],[0,1],[1,2*huge]))
        assert p.ok
        wrong=n.check.linear_rows(targets=[2],witnesses=mf.CertificateBatch([0,2],[0,1],[1,2*huge-1]))
        assert not wrong.ok


def test_onehot_shared_rule_reference_and_integrality(tmp_path):
    with model(tmp_path,'''Minimize
 obj: z
Subject To
 choice: x + y = 1
 def: 2 x + 5 y - z = 0
Bounds
 0 <= x <= 1
 0 <= y <= 1
 0 <= z <= 3
Generals
 x y z
End
''') as n:
        x,y,z=(n.vars.by_name(name).id for name in ('x','y','z'))
        f=facts(n);shared=n.check.one_hot_affine(groups=[(0,[(1,z)])])
        fast=shared.bound_exclusions();expanded=shared.expand_exclusions()
        assert fast.ok and expanded.ok and list(fast.target_ids)==list(expanded.target_ids)==[y]
        p=n.check.affine_integrality(vars=[z],rows=[1]);assert p.ok
        with n.edit().set_var_bounds([y],ub=0,because=fast).commit() as middle:
            with middle.edit().substitute_fixed([y]).commit() as reduced:
                p=reduced.check.affine_integrality(vars=[z],rows=[1])
                with reduced.edit().set_domains([z],kind='C',because=p).commit() as final:
                    diff=final.diff(n)
                    assert diff.semantic['coordinates']=='PROJECTED'
                    assert diff.semantic['mip']=='EQUIVALENT_UNDER_EXTENSION'
                    assert diff.semantic['lp']=='SUBSET_UNDER_EXTENSION'
                    assert list(final.vars.ids)==sorted([x,z])
                    assert final.vars.loc[z].name=='z' and final.vars.iloc[1].id==x


def test_simultaneous_integrality_cycle_rejected(tmp_path):
    with model(tmp_path,'''Minimize
 obj: x
Subject To
 eq: x - y = 0
Bounds
 0 <= x <= 2
 0 <= y <= 2
Generals
 x y
End
''') as n:
        p=n.check.affine_integrality(vars=[0,1],rows=[0,0]);assert p.ok
        with pytest.raises(ValueError,match='grounded'):n.edit().set_domains([0,1],kind='C',because=p).commit()


def test_nonzero_elimination_parent_close_and_native(tmp_path):
    n=model(tmp_path,'''Minimize
 obj: 3 x + y + 4
Subject To
 c: 2 x + y <= 9
Bounds
 x = 2
 0 <= y <= 4
End
''')
    child=n.edit().substitute_fixed([0]).commit();n.close()
    assert child.rows.loc[0].ub==5 and child.objective.constant==10
    receipt=child.write(tmp_path/'out.mps');assert receipt['status']=='PROVED'
    saved=child.save(tmp_path/'native');child.close()
    with mf.load(saved) as restored:
        assert restored.vars.loc[1].name=='y' and restored.objective.constant==10
        assert restored._delta_semantic['mip']=='UNKNOWN'
        assert restored.write(tmp_path/'again.mps')['status']=='PROVED'


def test_saved_status_not_trusted(m):
    proof=m.check.linear_rows(targets=[2],witnesses=[[0,1]])
    spec=proof.to_spec();spec['status']='PROVED';spec['nodes'][spec['root']]['recipe']['tasks'][0][2][1][2]=0
    assert not mf.BatchProof.replay(spec,model=m).ok
    spec['snapshot']='other'
    with pytest.raises(ValueError):mf.BatchProof.replay(spec,model=m)


def test_readback_corruption_atomicity_and_source_change(m,tmp_path,monkeypatch):
    from mipframe.store import mps_output as io
    out=tmp_path/'out.mps'
    real=io._canonical
    def corrupt(f,path,**options):
        real(f,path,**options);path.write_text(path.read_text().replace('  c\n','  c_bad\n'))
    monkeypatch.setattr(io,'_canonical',corrupt)
    with pytest.raises(ValueError):m.write(out)
    assert not out.exists()
    monkeypatch.setattr(io,'_canonical',real)
    m.write(out)
    with pytest.raises(FileExistsError):m.write(out)


@pytest.mark.parametrize('seed',range(8))
def test_fraction_oracle(seed,m):
    rng=random.Random(seed)
    cs=[rng.randint(0,5) for _ in range(2)]
    rhs=Q(rng.randint(-4,4),3)
    coeff={0:cs[0],1:cs[1]-cs[0],2:-cs[1]}
    t=mf.Targets([0],'affine','ub',[rhs],[0,3],list(coeff),list(coeff.values()))
    cert=mf.CertificateBatch([0,2],[0,1],cs)
    p=m.check.linear_many(targets=t,witnesses=cert)
    assert p.ok==(rhs>=0)


def test_public_certificate_buffers_and_shared_structure_cannot_change(m,tmp_path):
    import numpy as np
    ids=np.array([2],dtype='<u4');ids.flags.writeable=False
    targets=mf.Targets(ids)
    ids.flags.writeable=True;ids[0]=0
    assert list(targets.ids)==[2]
    cert=mf.CertificateBatch.paths([[0,1]])
    proof=m.check.linear_many(targets=targets,witnesses=cert).require()
    for obj,attr in ((cert.multipliers,'values'),(cert.sides,'codes'),(targets.rhs,'values')):
        with pytest.raises(AttributeError):setattr(obj,attr,())
        with pytest.raises(AttributeError):delattr(obj,attr)
    assert proof.ok and mf.BatchProof.replay(proof.to_spec(),model=m).ok
    with model(tmp_path,'''Minimize
 obj: z
Subject To
 choice: x + y = 1
 def: 2 x + 5 y - z = 0
Bounds
 0 <= x <= 1
 0 <= y <= 1
 0 <= z <= 3
Generals
 x y
End
''') as n:
        shared=n.check.one_hot_affine(groups=[(0,[(1,n.vars.by_name('z').id)])])
        with pytest.raises(AttributeError):shared.groups=()
        with pytest.raises(AttributeError):del shared.data
        with pytest.raises(TypeError):shared.data[0][1][0][1][0]=99
        assert shared.bound_exclusions().ok


def test_enumeration_large_range_is_bounded_by_states(tmp_path):
    with model(tmp_path,'''Minimize
 obj: x
Subject To
 c: x >= 0
Bounds
 0 <= x <= 10000000000000000000000000000000000
Generals
 x
End
''') as n:
        x=n.vars.by_name('x')
        p=n.check.enumerate(x>=0,vars=[x],max_states=1,memory_mb=.015625)
        assert p.status=='INCONCLUSIVE' and p.summary['states']==1
        assert mf.Report.replay(p.to_spec(),models=[n],memory_mb=.015625).status=='INCONCLUSIVE'


def test_split_bound_evidence_and_multilevel_unknown(m):
    x=m.vars.by_name('x')
    lo=m.check.bound(vars=[x.id],side='lb',values=0)
    hi=m.check.bound(vars=[x.id],values=2)
    e=m.edit().set_var_bounds(x,lb=0,because=lo).set_var_bounds(x,ub=2,because=hi)
    with e.commit() as same:assert same.diff(m).semantic['mip']=='SAME'
    with m.edit().set_var_bounds(x,ub=1).build() as cut:
        with cut.edit().commit() as child:
            assert child.diff(m).semantic['lp']=='UNKNOWN'
            assert child.diff(m).semantic['mip']=='UNKNOWN'
