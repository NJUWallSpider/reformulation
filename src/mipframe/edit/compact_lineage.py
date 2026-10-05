"""Lazy presentation of the retained delta; no proof replay on access."""
from mipframe.check.kernel import facts
from mipframe.edit.draft import DraftRef,DraftVar


class Lineage:
    """
    MIPFRAME HELP BEGIN
    Read-only Lineage domain facade.

    Full contract: mf.help("api.Lineage")
    MIPFRAME HELP END
    """
    def __init__(self,owner):self._owner=owner
    @property
    def parent(self):
        """
        MIPFRAME HELP BEGIN
        Read Lineage parent.

        Full contract: mf.help("api.Lineage.parent")
        MIPFRAME HELP END
        """
        return self._owner.parent
    @property
    def semantic(self):
        """
        MIPFRAME HELP BEGIN
        Read declared semantic scope; saved lineage summaries are historical, not live proofs.

        Full contract: mf.help("api.Lineage.semantic")
        MIPFRAME HELP END
        """
        return dict(self._owner._delta_semantic)
    @property
    def validation(self):
        """
        MIPFRAME HELP BEGIN
        Read Lineage validation.

        Full contract: mf.help("api.Lineage.validation")
        MIPFRAME HELP END
        """
        return dict(status='LIVE' if facts(self._owner).parent is not None else 'HISTORICAL_UNVERIFIED')
    @property
    def findings(self):
        """
        MIPFRAME HELP BEGIN
        Read Lineage findings.

        Full contract: mf.help("api.Lineage.findings")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        return Frame.from_records([],schema=dict(reason='str'))
    @property
    def operations(self):
        """
        MIPFRAME HELP BEGIN
        Read Lineage operations.

        Full contract: mf.help("api.Lineage.operations")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        f=facts(self._owner)
        return Frame.from_records([dict(operation=i,kind=k,count=len(ids)) for i,(k,ids,p) in enumerate(f.delta.operations)] if f.parent else [],
                                  schema=dict(operation='int',kind='str',count='int'))
    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        Read Lineage evidence.

        Full contract: mf.help("api.Lineage.evidence")
        MIPFRAME HELP END
        """
        f=facts(self._owner)
        return tuple(p for k,ids,p in f.delta.operations if p is not None) if f.parent else ()
    @property
    def changes(self):
        """
        MIPFRAME HELP BEGIN
        Read Lineage changes.

        Full contract: mf.help("api.Lineage.changes")
        MIPFRAME HELP END
        """
        from mipframe.control.proofs import delta_spec
        f=facts(self._owner)
        return delta_spec(f.delta) if f.parent else getattr(f,'saved_bundle',{})
    def resolve(self,ref):
        """
        MIPFRAME HELP BEGIN
        Read Lineage resolve.

        Full contract: mf.help("api.Lineage.resolve")
        MIPFRAME HELP END
        """
        if isinstance(ref,DraftVar):ref=ref.ref
        f=facts(self._owner)
        if not isinstance(ref,DraftRef) or ref.edit_uid!=getattr(f,'edit_uid',None):raise ValueError('Foreign draft reference')
        return (self._owner.rows if ref.kind=='row' else self._owner.vars).loc[ref.id]
    def mapping(self,axis,*,source=None):
        """
        MIPFRAME HELP BEGIN
        Read explicit source/target entity correspondence, optionally binding the parent.

        Full contract: mf.help("api.Lineage.mapping")
        MIPFRAME HELP END
        """
        from mipframe.frame.base import Frame
        from mipframe.frame.schema import Ref,ref_type
        f=facts(self._owner)
        if source is None or facts(source) is not f.parent:raise ValueError('Bind the direct parent for an entity mapping')
        if axis not in ('row','var'):raise ValueError('Unknown entity axis')
        import numpy as np
        from mipframe.frame.array_source import frame_from_columns
        original=f.parent.row_ids() if axis=='row' else f.parent.var_ids()
        ids=f.row_ids() if axis=='row' else f.var_ids()
        common=np.intersect1d(np.fromiter(original,dtype='<u8'),np.fromiter(ids,dtype='<u8'),assume_unique=True)
        return frame_from_columns({f'source_{axis}':common,axis:common},
            schema={f'source_{axis}':ref_type(source,axis),axis:ref_type(self._owner,axis)},
            models=(source,self._owner))
    def proof(self,ident,*,models,**options):
        """
        MIPFRAME HELP BEGIN
        Replay stored lineage evidence with explicitly bound source snapshots.

        Full contract: mf.help("api.Lineage.proof")
        MIPFRAME HELP END
        """
        from mipframe.check.compact import BatchProof
        p=self.evidence[ident]
        b=p.batch if hasattr(p,'batch') else p
        return BatchProof.replay(b.to_spec(),model=next(m for m in models if m.uid==b._facts.uid))
