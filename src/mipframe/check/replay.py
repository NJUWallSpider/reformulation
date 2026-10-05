"""Replay diagnostic recipes; numerical certificates use the compact batch format."""
from collections import deque
import json
from pathlib import Path
from mipframe.frame.schema import SchemaError
from mipframe.linear.expr import bind_model
from mipframe.check.certificate import InvalidCertificateError
from mipframe.core.resources import memory_bytes


def proof_events(spec):
    if isinstance(spec,(str,Path)):
        with open(spec,encoding='utf8') as stream:
            for line in stream:
                if line.strip():yield json.loads(line)
    else:
        if spec.get('format')!='mipframe-proof' or spec.get('version')!=1:
            raise SchemaError('Unsupported proof specification')
        yield from spec['events']


def replay(spec_or_path, *, models, relations=None, memory_mb=128, disk_mb=None):
    from mipframe.data.runtime import WorkContext
    from mipframe.frame.executor import size,QueryMemoryError
    from mipframe.check.algorithm import DiskBudgetExceeded
    from mipframe.frame.serialization import frame_from_spec
    model_list=tuple(models.values()) if isinstance(models,dict) else (models,) if hasattr(models,'uid') else tuple(models)
    model_map={m.uid:m for m in model_list}
    with WorkContext(memory_mb,disk_mb) as work:
        header=None;nodes={};used=0
        for event in proof_events(spec_or_path):
            used+=size(event)
            if used>work.memory:raise QueryMemoryError('Diagnostic recipe graph exceeds the working-memory budget')
            if event.get('type')=='header':
                if header is not None or event.get('format')!='mipframe-proof' or event.get('version')!=1:
                    raise SchemaError('Invalid proof header')
                header=event
            elif event.get('type')=='node':
                ident=event.get('node')
                if type(ident) is not int or ident<0 or ident in nodes:raise SchemaError('Invalid or duplicate proof node')
                nodes[ident]=event
            else:
                raise SchemaError('Legacy numerical report format is unsupported; regenerate a CertificateBatch and replay it')
        if header is None or type(header.get('root')) is not int or header['root'] not in nodes:
            raise InvalidCertificateError('Missing root report')
        dependents={i:[] for i in nodes};pending={}
        for i,node in nodes.items():
            parents=node.get('parents')
            if not isinstance(parents,list) or any(type(p) is not int or p not in nodes for p in parents):
                raise InvalidCertificateError('Missing report dependency')
            pending[i]=len(set(parents))
            for parent in set(parents):dependents[parent].append(i)
        queue=deque(sorted(i for i,n in pending.items() if not n));order=[]
        while queue:
            i=queue.popleft();order.append(i)
            for child in dependents[i]:
                pending[child]-=1
                if not pending[child]:queue.append(child)
        if len(order)!=len(nodes):raise InvalidCertificateError('Cyclic report dependency graph')
        def frame(spec):return frame_from_spec(spec,models=model_map,relations=relations)
        reports={}
        try:
            for i in order:
                node=nodes[i];recipe=node['recipe'];kind=recipe['kind']
                if kind=='frame':
                    result=frame(recipe['frame']).check(recipe['check'],recipe.get('columns'),
                        key=recipe.get('key'),value=recipe.get('value'),memory_mb=memory_mb)
                elif kind in ('execution','algorithm_contract'):
                    from mipframe.ops._runtime import restore
                    try:
                        if kind=='execution':len(frame(recipe['frame']))
                        else:
                            args=dict(recipe,algorithm_version=1)
                            args['resources']=dict(args['resources'],memory_mb=memory_mb,disk_mb=disk_mb)
                            restore(args,lambda plan:frame(dict(version=1,plan=plan)))
                    except Exception as error:
                        if getattr(error,'report',None) is None:raise
                        result=error.report
                    else:
                        from mipframe.execution.algorithm import ArrayAlgorithmStore
                        from mipframe.check.report import Report
                        ctx=ArrayAlgorithmStore(model_list,memory_mb=memory_mb,disk_mb=disk_mb,max_output_rows=None)
                        result=Report(ctx,True,dict(operation=kind,scope='full declared inputs'),
                            ctx.publish({'reason':__import__('mipframe').DType('str')},[]),recipe=recipe)
                else:
                    owner=bind_model(node['snapshot'],model_list)
                    if kind=='model_integrity':
                        from mipframe.check.model import verify
                        result=verify(owner,runs=recipe['runs'])
                    elif kind=='indexed':
                        from mipframe.ir.indexing.validation import restore_spec
                        indexed=getattr(owner,'_indexed_results',{}).get(recipe['run'])
                        if indexed is None:raise SchemaError('Completed indexed run is not bound; load its saved snapshot')
                        result=(indexed.validate(restore_spec(recipe['spec'],owner)) if 'spec' in recipe else
                                indexed.verify(frame(recipe['selection'])) if 'selection' in recipe else indexed.verify())
                    else:raise SchemaError('Legacy numerical report format is unsupported; regenerate a CertificateBatch')
                reports[i]=result
            return reports[header['root']]
        except BaseException:
            from mipframe.store.lifecycle import release_all
            release_all(r._store.close for r in reports.values())
            raise
