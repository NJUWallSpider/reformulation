"""Normalize public hypotheses once before entering the exact indexed checker."""
from dataclasses import dataclass
from itertools import groupby
from mipframe.frame import Frame, Record, SchemaError
from mipframe.frame.query import dependencies
from mipframe.ir.indexing.nodes import restore
from mipframe.ir.indexing.operators import _ref_id


@dataclass(frozen=True)
class OperatorCandidate:
    operator: dict
    rows: object
    kernel: object
    coordinates: object


class Inputs:
    def __init__(self, result):
        self.result = result
        self.owner = result._store.owner

    def frame(self, frame):
        frame._guard()
        for current in dependencies(frame):
            spec = current._query.spec
            if spec.get('kind') == 'indexed_directory' and spec['run'] != self.result.id:
                raise SchemaError('Candidate Frame belongs to another indexed run')
            if any((m.uid,m.version)!=(self.owner.uid,self.owner.version) for m in current._models):
                raise SchemaError('Candidate Frame belongs to another snapshot')
        return frame

    def node(self, value):
        return restore(self.frame(value)) if isinstance(value,Frame) else value

    def operator(self, value):
        if isinstance(value,Frame):
            value = self.frame(value).one()
        if isinstance(value,Record):
            value = value.to_record()
        metadata = dict(value)
        if 'indexed_key' in metadata:
            from mipframe.ir.indexing.views import _Directory
            if metadata['indexed_key'] != _Directory(self.result,'operators').identity(metadata['id']):
                raise SchemaError('Candidate directory identity differs from this run')
        if 'row_count' in metadata:
            metadata['rows'] = metadata.pop('row_count')
        required = {'id','tensor','fixed_axis','axis_invariant','shape','kernel_shape','ast','rows'}
        if not required <= metadata.keys():
            # Directory records carry scalars; complete machine data lives in the cache.
            metadata = dict(self.result._card('operators',metadata['id']), **metadata)
        metadata.pop('indexed_key', None)
        metadata['ast'] = self.node(metadata['ast'])
        return metadata

    def records(self, value, *, coordinates=False):
        if not isinstance(value,Frame):return value
        self.frame(value)
        def records():
            for record in value:
                row = record.to_record()
                if coordinates:
                    row['variable'] = _ref_id(self.result._store,row['var'],'var')
                yield row
        return records()

    def sets(self, value):
        if not isinstance(value,Frame):return value
        self.frame(value)
        if 'var' not in value.schema:
            value = value.related('members')
        if not {'row','var'} <= value.schema.keys():
            raise SchemaError('Partition hypotheses require row and var membership columns')
        def groups():
            for row,members in groupby(value.sort_by('row'),key=lambda r:r.to_record()['row']):
                yield dict(row_id=_ref_id(self.result._store,row,'row'),
                           variables=(_ref_id(self.result._store,r.to_record()['var'],'var') for r in members))
        return groups()


def normalize_spec(result, spec):
    inputs = Inputs(result)
    out = dict(spec)
    if 'ast' in out:out['ast'] = inputs.node(out['ast'])
    if 'candidate' in out:
        candidate = out['candidate']
        out['candidate'] = OperatorCandidate(
            inputs.operator(candidate['operator']), inputs.records(candidate['rows']),
            inputs.records(candidate['kernel']), inputs.records(candidate['coordinates'],coordinates=True))
    for name in ('kernel','rows','coordinates','mapping'):
        if name in out:out[name] = inputs.records(out[name],coordinates=name=='coordinates')
    if 'partitions' in out:out['partitions'] = [inputs.sets(part) for part in out['partitions']]
    if 'sets' in out:out['sets'] = inputs.sets(out['sets'])
    if isinstance(out.get('variables'),Frame):
        frame = inputs.frame(out['variables'])
        out['variables'] = (_ref_id(result._store,r.to_record()['var'],'var') for r in frame)
    return out
