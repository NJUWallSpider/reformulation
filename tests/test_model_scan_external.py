"""Optional original test32–34 regressions; source models are never bundled."""
import pytest
import mipframe as mf
from external_models import model_path


@pytest.mark.parametrize('name,shape,nnz',[
    ('test32.mps',(576,18380),109706),
    ('test33.mps',(160633,6250),371240),
    ('test34.mps',(106954,53593),266805),
])
def test_original_scan_navigation(name,shape,nnz,monkeypatch):
    path=model_path(name)
    from mipframe.ir.refinement_store import RefinementStore
    from mipframe.ir.compressed import CompressedIR
    def forbidden(*a,**kw): pytest.fail('Loading an original model started structural analysis')
    monkeypatch.setattr(RefinementStore,'compute',forbidden)
    monkeypatch.setattr(CompressedIR,'_topology',forbidden)
    monkeypatch.setattr(mf.Model,'indexed',forbidden)
    with mf.read(path) as m:
        assert m.shape==shape and m.ir.size['nonzeros']==nnz
        data=m.ir.to_dict()
        assert len(str(m.ir))<=6000
        if name=='test32.mps':
            assert data['domains']['bounds']['fixed']==7282
            assert data['domains']['unit_interval']==11036
            templates={e['template'] for e in data['names']['rows']['items']}
            assert {'start_{int}','completion_{int}','jobexec_{int}','precedence_{int}_{int}',
                    'rescap_Mechaniker_{int}','rescap_Techniker_{int}'}<=templates
        elif name=='test33.mps':
            assert data['objective']['nonzeros']==6212
            top=data['local_row_shapes']['items'][0]
            assert top['count']==160625 and top['exact'] and top['nnz']==2
            assert top['sense']=='<=' and top['ub']==0
            assert {(v['value'],v['count']) for v in top['coefficients']}=={(-1,1),(1,1)}
        else:
            assert data['domains']['variable_types']=={'B':53592,'I':0,'C':1}
            assert data['objective']['nonzeros']==53131
        assert not getattr(m,'_refinement_store',None)
        assert not (m.model.path/'indexing.sqlite').exists()
