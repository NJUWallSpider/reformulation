"""Lazy graph presentation and a non-authoritative linear-certificate bridge."""
from dataclasses import dataclass
from types import MappingProxyType
import numpy as np
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType,SchemaError
from mipframe.frame.array_source import frame_from_columns,read_columns
from mipframe.data.batch import Columns


class SharedArtifact:
    def __init__(self,work):self.work=work;self.references=0
    def acquire(self):self.references+=1;return ArtifactLease(self)


class ArtifactLease:
    def __init__(self,artifact):self.artifact=artifact;self.closed=False
    def guard(self):
        if self.closed:raise ValueError('Graph result is closed')
    def close(self):
        if not self.closed:
            self.closed=True;self.artifact.references-=1
            if self.artifact.references==0:self.artifact.work.close()
            if hasattr(self,'parent_lease'):self.parent_lease.close()
    def __del__(self):self.close()


def selected_frame(source,ids,models,lease,extras=None):
    extras=extras or {};schema=dict(source.schema)
    schema.update({n:typ for n,(typ,values) in extras.items()})
    def scan(fields,ctx):
        lease.guard();source._guard()
        original=tuple(n for n in fields if n not in extras)
        data=read_columns(source,original,ctx) if original else None
        for j,i in enumerate(ids):
            from mipframe.data.relations import scalar
            yield {n:scalar(extras[n][1][j] if n in extras else data.columns[n][i]) for n in fields}
    result=Frame(schema,source=scan,models=models,parents=(source,),known_count=len(ids),key=source.key,domains=source.domains)
    result._array_take=(source,ids,extras)
    result._query.resource=lease
    return result


@dataclass(frozen=True)
class LinearCertificate:
    """
    MIPFRAME HELP BEGIN
    Snapshot-bound typed input to the exact linear checker; never a proof.

    Full contract: mf.help("api.LinearCertificate")
    MIPFRAME HELP END
    """
    uid: str
    revision: str
    targets: object
    witnesses: object
    _resource: object = None


class PathWitnesses:
    def __init__(self,result,removed,offsets,edges):
        self.result=result;self.removed_edge_ids=removed;self.path_offsets=offsets;self.path_edge_ids=edges
    def __len__(self):self.result._guard();return len(self.removed_edge_ids)
    def _position(self,key):
        r=self.result;r._guard()
        if r.keys is None:
            if isinstance(key,bool) or not isinstance(key,(int,np.integer)):raise KeyError(key)
            ordinal=int(key)
        else:
            lo,hi=0,len(r.keys)
            while lo<hi:
                mid=(lo+hi)//2
                if r.keys[int(r._key_order[mid])]<key:lo=mid+1
                else:hi=mid
            if lo==len(r.keys) or r.keys[int(r._key_order[lo])]!=key:raise KeyError(key)
            ordinal=int(r._key_order[lo])
        lo,hi=0,len(self.removed_edge_ids)
        while lo<hi:
            mid=(lo+hi)//2
            if self.removed_edge_ids[int(r._removed_order[mid])]<ordinal:lo=mid+1
            else:hi=mid
        if lo==len(self.removed_edge_ids) or self.removed_edge_ids[int(r._removed_order[lo])]!=ordinal:raise KeyError(key)
        return int(r._removed_order[lo])
    def head(self,n=5):
        r=self.result;r._guard();n=max(0,min(n,len(self.removed_edge_ids)))
        ids=self.removed_edge_ids[:n]
        return frame_from_columns(dict(removed_edge=ids if r.keys is None else r.keys[ids],
            path_len=np.diff(self.path_offsets[:n+1])),dict(removed_edge=r.key_type,path_len='int'),models=r.models)
    def path(self,key):
        i=self._position(key);r=self.result;lo,hi=map(int,self.path_offsets[i:i+2]);ids=self.path_edge_ids[lo:hi]
        from mipframe.execution.algorithm import ArrayAlgorithmStore
        with ArrayAlgorithmStore(r.models) as ctx:
            data=read_columns(r.source,(r.tail,r.head),ctx).columns
            return frame_from_columns(dict(step=np.arange(len(ids)),edge_id=ids if r.keys is None else r.keys[ids],
                u=data[r.tail][ids],v=data[r.head][ids]),dict(step='int',edge_id=r.key_type,u=r.source.schema[r.tail],v=r.source.schema[r.head]),models=r.models)
    def as_linear_certificate(self,*,target='row',source='row',side='ub'):
        r=self.result;r._guard()
        if side not in ('lb','ub','eq'):raise ValueError('Invalid row side')
        types=[]
        for name in (target,source):
            if name not in r.source.schema:raise SchemaError('Missing edge-to-row mapping: '+name)
            t=r.source.schema[name]
            if t.kind!='ref' or t.axis!='row':raise SchemaError('Certificate mapping requires snapshot-qualified row references')
            types.append(t)
        if not types[0].compatible(types[1]):raise SchemaError('Certificate row mappings belong to different snapshots')
        from mipframe.execution.algorithm import ArrayAlgorithmStore
        from mipframe.check.kernel import Targets,CertificateBatch
        artifact=SharedArtifact(r.lease.artifact.work.fork());lease=artifact.acquire();work=artifact.work
        try:
            with ArrayAlgorithmStore(r.models,work=work) as ctx:
                columns=read_columns(r.source,tuple(dict.fromkeys((target,source))),ctx).columns
                dtype='<u8' if any(len(columns[n]) and np.max(columns[n])>=2**32 for n in (target,source)) else '<u4'
                target_ids=work.array(len(self.removed_edge_ids),dtype)
                block=max(1,work.memory//128)
                for start in range(0,len(target_ids),block):target_ids[start:start+block]=columns[target][self.removed_edge_ids[start:start+block]]
                source_ids=work.array(len(self.path_edge_ids),dtype)
                block=max(1,work.memory//128)
                for start in range(0,len(source_ids),block):source_ids[start:start+block]=columns[source][self.path_edge_ids[start:start+block]]
                target_ids=work.seal(target_ids);source_ids=work.seal(source_ids)
            # Retain the independently leased path offsets when the result closes.
            parent_lease=r.lease.artifact.acquire()
            lease.parent_lease=parent_lease
            return LinearCertificate(types[0].uid,types[0].version,Targets._rows(target_ids,side,work),
                CertificateBatch(self.path_offsets,source_ids,sides=side),lease)
        except BaseException:lease.close();raise


class TransitiveReductionResult:
    """
    MIPFRAME HELP BEGIN
    Complete retained and removed edge views, ragged retained-edge witnesses and execution statistics.

    Full contract: mf.help("api.TransitiveReductionResult")
    MIPFRAME HELP END
    """
    def __init__(self,source,tail,head,kept,removed,removed_ids,offsets,paths,keys,edge_column,stats,lease,models,key_order,removed_order):
        self.source,self.tail,self.head=source,tail,head
        self._key_order,self._removed_order=key_order,removed_order
        self.keys,self.edge_column,self.lease,self.models=keys,edge_column,lease,models
        self.key_type=source.schema[edge_column] if keys is not None else DType('int')
        extras={} if keys is not None else {edge_column:(DType('int'),kept)}
        self.kept=selected_frame(source,kept,models,lease,extras)
        extras={} if keys is not None else {edge_column:(DType('int'),removed)}
        self.removed=selected_frame(source,removed,models,lease,extras)
        self.witnesses=PathWitnesses(self,removed_ids,offsets,paths)
        self.stats=MappingProxyType(stats)
        self.relations=MappingProxyType(dict(kept=self.kept,removed=self.removed))
    def _guard(self):self.lease.guard();self.source._guard()
    def close(self):self.lease.close()
    def __enter__(self):self._guard();return self
    def __exit__(self,*args):self.close()
    def __repr__(self):return f'<TransitiveReductionResult kept={self.stats["kept"]:,} removed={self.stats["removed"]:,} witnesses=complete>'
    def _finalize_algorithm(self,name,params,resources,paths):
        self._algorithm_info=dict(status='complete',path=', '.join(sorted(paths)))
        for part,frame in self.relations.items():
            frame._query.spec=dict(kind='algorithm',algorithm=name,algorithm_version=1,arguments=params,resources=resources,result=part)
            frame._algorithm_info=self._algorithm_info
            frame._reduction_result=self
    def explain(self):self._guard();return dict(self._algorithm_info,stats=dict(self.stats))
    def to_spec(self):self._guard();return dict(kind='transitive_reduction_result',version=1,kept=self.kept.to_spec())
    @classmethod
    def from_spec(cls,spec,**bindings):
        if spec.get('kind')!='transitive_reduction_result' or spec.get('version')!=1:raise SchemaError('Invalid reduction recipe')
        from mipframe.frame import frame_from_spec
        return frame_from_spec(spec['kept'],**bindings)._reduction_result
