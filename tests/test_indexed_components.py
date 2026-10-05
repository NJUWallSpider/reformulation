"""Semantic parity and storage scaling for disconnected indexed components."""
from collections import defaultdict, deque
from fractions import Fraction
import json
import sqlite3
import numpy as np
import pytest
import mipframe as mf
from mipframe.ir.indexing.discovery import candidate, evidence, partition, split_two
from mipframe.ir.indexing.store import Store, Table
from mipframe.control.cache import directory


# Frozen v1.2 discovery algorithm: compare complete identities, origins and
# memberships, including odd cycles and empty rows, not only tensor counts.
def legacy_split_two(s, run, g, rg, vg):
    members = s.members[g];rs,vs = members.columns['r'],members.columns['v']
    variables,counts = np.unique(vs,return_counts=True)
    if len(variables)!=s.size(vg) or np.any(counts!=2):return
    order = np.argsort(vs,kind='stable');ends = rs[order].reshape(-1,2)
    graph = defaultdict(list)
    for a,b in ends:
        graph[int(a)].append(int(b));graph[int(b)].append(int(a))
    colors = {}
    for raw in s.domains[rg]:
        root = int(raw)
        if root in colors or root not in graph:continue
        colors[root] = 0;queue = deque([root]);component = [];valid = True
        while queue:
            r = queue.popleft();component.append(r)
            for other in graph[r]:
                if other not in colors:
                    colors[other] = 1-colors[r];queue.append(other)
                elif colors[other]==colors[r]:
                    if valid:evidence(s,run,'non-bipartite candidate',candidate=g,edge=[r,other])
                    valid = False
        if not valid:continue
        uv = s.domain('var',variables[np.isin(ends[:,0],component)])
        for side in (0,1):
            rr = s.domain('row',(r for r in component if colors[r]==side))
            new = candidate(s,run,rr,uv,dict(kind='bipartite_component',parent=g,root=root,side=side))
            mask = np.isin(rs,s.domains[rr])
            s.members[new] = s.make_table(('r','v'),zip(rs[mask],vs[mask]))
            partition(s,run,new,rr,uv)


@pytest.mark.parametrize('memory',[128,0.015625])
def test_component_discovery_matches_original(tmp_path,memory):
    path=tmp_path/'model.lp'
    path.write_text('Minimize\n obj:x\nSubject To\n r:x=1\nEnd\n')
    # Disjoint pairs, a path, parallel edges, an even cycle and an odd cycle.
    ends=[(2*i,2*i+1) for i in range(32)]
    ends += [(64,65),(65,66),(67,68),(67,68),(69,70),(70,71),(71,72),(72,69),
             (73,74),(74,75),(75,73)]
    pairs=sorted((r,v) for v,rows in enumerate(ends) for r in rows)
    with mf.read(path) as m:
        snapshots=[]
        for implementation in (legacy_split_two,split_two):
            s=Store(m,memory)
            try:
                rg=s.domain('row',range(78));vg=s.domain('var',range(len(ends)))
                g=candidate(s,'run',rg,vg,dict(kind='test'))
                s.members[g]=s.make_table(('r','v'),pairs)
                implementation(s,'run',g,rg,vg)
                snapshots.append((dict(s.candidates),dict(s.cards),
                    {g:(s.domain_axes[g],a.dtype.str,a.tolist()) for g,a in s.domains.items()},
                    {g:list(t.records()) for g,t in s.members.items()}))
            finally:s.close()
        assert snapshots[0]==snapshots[1]
        def retained(cards,domains,members):
            return sorted((domains[c['rows_domain']][2],domains[c['vars_domain']][2],
                           members[c['g']],c['origins'])
                          for (run,kind,key),c in cards.items() if kind=='partitions' and c['sets']>=2)
        s=Store(m,memory);s.include_singletons=False
        try:
            rg=s.domain('row',range(78));vg=s.domain('var',range(len(ends)))
            g=candidate(s,'run',rg,vg,dict(kind='test'))
            s.members[g]=s.make_table(('r','v'),pairs)
            split_two(s,'run',g,rg,vg)
            domains={g:(s.domain_axes[g],a.dtype.str,a.tolist()) for g,a in s.domains.items()}
            assert retained(dict(s.cards),domains,{g:list(t.records()) for g,t in s.members.items()})==retained(*snapshots[0][1:])
            assert all(c['sets']>=2 for c in s.records('run','partitions'))
            assert any(c['reason']=='non-bipartite candidate' for c in s.records('run','evidence'))
        finally:s.close()


@pytest.mark.parametrize('memory',[128,0.015625])
def test_singleton_partitions_roundtrip_and_grouped_artifacts(tmp_path,memory):
    n=32
    source=tmp_path/'pairs.lp'
    source.write_text('Minimize\n obj:'+'+'.join(f'x{i}' for i in range(n))+
        '\nSubject To\n'+'\n'.join(f'r{2*i+j}:x{i}=1' for i in range(n) for j in (0,1))+'\nEnd\n')
    with mf.read(source) as m:
        result=m.indexed(memory_mb=memory,include_singletons=True)
        assert len(result.partitions)==2*n
        result.verify(result.partitions).require()
        s=result._store
        expected=list(s.records(result.id,'partitions'))
        members={c['id']:list(s.members[c['g']].records()) for c in expected}
        with sqlite3.connect(directory(m)/'indexing.sqlite') as db:
            manifest=dict(db.execute('select key,value from meta'))['manifest']
        state=json.loads((directory(m)/'derived'/manifest).read_text())
        assert sum(a['ids']['shape'][0] for a in state['singleton_domains'])==3*n
        assert sum(a['ids']['shape'][0] for a in state['singleton_members'])==2*n
        assert len(state['members'])==1
        # File count depends on column families, not component count.
        assert len(list((directory(m)/'derived').glob('index-*.npy')))<100
        saved=m.save(tmp_path/'saved')
    with mf.load(saved) as loaded:
        result=loaded.indexed(include_singletons=True)
        assert list(result._store.records(result.id,'partitions'))==expected
        assert {c['id']:list(result._store.members[c['g']].records()) for c in expected}==members
        result.verify(result.partitions).require()
        # Fork a scope after load; closing an old store cannot invalidate views.
        old=result._store
        scoped=loaded.indexed(rows=loaded.rows.head(8),include_singletons=True)
        old.close()
        scoped.verify(scoped.partitions).require()
        for a in scoped._store.domains.values():assert not a.flags.writeable


def test_table_exact_scalars_and_raw_replay():
    from mipframe.data.runtime import WorkContext
    with WorkContext(0.015625) as work:
        values=[Fraction(2,3),None,2**80,Fraction(-3,7),-1,0]
        t=Table(('i','x'),enumerate(values),numeric=('x',),work=work)
        assert list(t.records())==list(enumerate(values))
        assert all(v is None or isinstance(v,Fraction) for _,v in t.records())
        assert list(t.records(exact=False))==list(enumerate(values))


@pytest.mark.parametrize('full_first',[False,True])
def test_singleton_filter_is_early_cached_and_persistent(tmp_path,monkeypatch,full_first):
    source=tmp_path/'pairs.lp'
    source.write_text('Minimize\n obj:x+y\nSubject To\n a:x=1\n b:x=1\n c:y=1\n d:y=1\nEnd\n')
    with mf.read(source) as m:
        results={}
        for full in (full_first,not full_first):
            if not full:
                # An output-only filter would still call this per component.
                with monkeypatch.context() as patch:
                    patch.setattr(Store,'domain_sorted',lambda *a:pytest.fail('singleton allocated'))
                    results[full]=m.indexed(include_singletons=full)
            else:results[full]=m.indexed(include_singletons=full)
        assert len(results[False].partitions)==0
        assert len(results[True].partitions)==4
        assert results[True].id!=results[False].id
        assert m.indexed().id==results[False].id
        assert m.indexed(include_singletons=True).id==results[True].id
        with pytest.raises(TypeError,match='boolean'):m.indexed(include_singletons=1)
        saved=m.save(tmp_path/'filtered')
    with mf.load(saved) as m:
        from mipframe.ir.indexing import discovery
        monkeypatch.setattr(discovery,'discover',lambda *a:pytest.fail('cached run rebuilt'))
        assert len(m.indexed().partitions)==0
        assert len(m.indexed(include_singletons=True).partitions)==4
        m.indexed(include_singletons=True).verify(m.indexed(include_singletons=True).partitions).require()


def test_filter_preserves_tensors_and_exact_operators(tmp_path):
    from scripts.indexed_instances import model
    text,_=model([[5,2,0],[2,5,1],[0,1,5]],groups=2,scaled=True,permute=True)
    source=tmp_path/'tensor.lp';source.write_text(text)
    with mf.read(source) as m:
        snapshots=[]
        for full in (False,True):
            i=m.indexed(depth=3,include_singletons=full);i.verify().require();s=i._store
            tensors=sorted((c['shape'],list(s.coordinates[c['id']].records())) for c in s.records(i.id,'tensors'))
            operators=sorted((c['fixed_axis'],c['shape'],c['kernel_statistics'],list(s.bindings[c['id']].records()),
                list(s.kernels[c['id']].records())) for c in s.records(i.id,'operators'))
            snapshots.append((tensors,operators))
        assert snapshots[0]==snapshots[1]


def test_batched_columns_do_not_round_large_signed_integers():
    from mipframe.data.runtime import WorkContext
    with WorkContext() as work:
        values=[-1,2**63+1,0,2**64-1]
        t=Table(('i','x'),enumerate(values),numeric=('x',),work=work)
        assert list(t.records())==list(enumerate(values))
