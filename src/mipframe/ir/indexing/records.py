"""Compact component metadata, expanded only when queried.

The backing RecordMap remains a bounded typed tree. Repeated field names and
single-component origin dictionaries need not be encoded for every partition.
Old dictionary records and copy-on-write parent generations remain readable.
"""
from mipframe.data.records import RecordMap


class IndexRecords(RecordMap):
    def __setitem__(self,key,value):
        origins=value.get('origins',()) if isinstance(value,dict) else ()
        if (len(origins)==1 and set(origins[0])=={'kind','parent','root','side'}
            and origins[0]['kind']=='bipartite_component'):
            origin=origins[0];tail=(origin['parent'],origin['root'],origin['side'])
            if set(value)=={'run','rg','vg','origins'}:
                value=('component_candidate',value['run'],value['rg'],value['vg'],*tail)
            elif (set(value)=={'id','g','rows_domain','vars_domain','sets','variables','origins','verification'}
                  and value['sets']==value['variables']==1 and value['verification']=='verified'
                  and isinstance(key,tuple) and len(key)==3 and key[2]==value['id']):
                value=('component_partition',value['g'],value['rows_domain'],value['vars_domain'],*tail)
        super().__setitem__(key,value)

    def __getitem__(self,key):
        value=super().__getitem__(key)
        if not isinstance(value,tuple):return value
        kind,a,rg,vg,parent,root,side=value
        origins=[dict(kind='bipartite_component',parent=parent,root=root,side=side)]
        if kind=='component_candidate':return dict(run=a,rg=rg,vg=vg,origins=origins)
        if kind=='component_partition':
            return dict(id=key[2],g=a,rows_domain=rg,vars_domain=vg,sets=1,variables=1,
                        origins=origins,verification='verified')
        raise ValueError('Invalid compact indexed record')
