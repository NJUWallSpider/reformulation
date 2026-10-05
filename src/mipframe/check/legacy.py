"""Bounded certificate v2 input adapter into the unified logical graph.

No intermediate proof objects are issued and no stored statuses are consulted.
"""
from mipframe.check.kernel import Targets,CertificateBatch,exact
from mipframe.check.graph import CheckBatch,GraphExecution,GraphProof
from mipframe.check.certificate import InvalidCertificateError


def replay_legacy(model,spec,**options):
    plan=CheckBatch(model.check,**options)
    records=spec['nodes'];outputs=[]
    def decode(value):
        if isinstance(value,dict):
            if set(value)=={'q'}:return exact(value['q'])
            return {k:decode(v) for k,v in value.items()}
        if isinstance(value,(tuple,list)):return tuple(decode(v) for v in value)
        return value
    def claim(value):
        terms,rhs,relation=value
        return plan.pool.terms(terms),exact(rhs),relation
    def stage(rule,target,sources=(),conditions=(),config=()):
        return plan._stage(rule,target,sources,conditions,config)._index
    def at(values,index):
        if type(index) is not int or not 0<=index<len(values):raise InvalidCertificateError('Invalid parent position')
        return values[index]
    try:
        for record in records:
            plan.budget.poll()
            if record.get('schema')!='mipframe-certificate-node-2' or (record.get('snapshot'),record.get('revision'))!=(plan.f.uid,plan.f.revision):
                raise InvalidCertificateError('Certificate version or snapshot mismatch')
            parent_ids=record.get('parents',())
            if any(type(i) is not int or not 0<=i<len(outputs) for i in parent_ids):
                raise ValueError('Cyclic or invalid certificate dependency')
            parents=[outputs[i] for i in parent_ids]
            rule=record['rule'];recipe=decode(record['recipe'])
            if rule=='check_graph':
                # Mixed legacy/new DAGs are flattened by remapping logical IDs.
                exprs=[]
                for i,(kind,data) in enumerate(recipe['expressions']):
                    if kind=='terms':node=plan.pool.terms(data)
                    elif kind=='sum':node=plan.pool.combine((at(exprs,j),exact(c)) for j,c in data)
                    else:raise InvalidCertificateError('Unknown expression rule')
                    exprs.append(node)
                def remap(value):
                    e,rhs,rel=value
                    return at(exprs,e),exact(rhs),rel
                task_ids=[]
                for r,target,sources,conditions,config in recipe['tasks']:
                    refs=[]
                    for source in sources:
                        k=source[0]
                        if k=='task':ref=('task',at(task_ids,source[1]),*source[2:])
                        elif k=='external':ref=('task',at(at(parents,source[1]),source[2]),*source[3:])
                        elif k=='assumption':ref=(k,remap(source[1]),*source[2:])
                        elif k=='model':ref=tuple(source)
                        else:raise InvalidCertificateError('Unknown source rule')
                        refs.append(ref)
                    task_ids.append(stage(r,remap(target),tuple(refs),tuple(remap(c) for c in conditions),tuple(config)))
                outputs.append(tuple(at(task_ids,i) for i in recipe['roots']))
                continue
            targets=Targets(**decode(record['targets']))
            conditions=tuple(claim(c) for c in recipe.get('assumptions',()))
            selected=[]
            if rule in ('linear','identity','enumerate'):
                certificate=CertificateBatch(**decode(record['certificate']))
                if len(certificate.offsets)!=len(targets.ids)+1:raise InvalidCertificateError('Certificate target count mismatch')
                parent_indices=recipe.get('parent_indices',())
                for i in range(len(targets.ids)):
                    target=claim(targets.claim(plan.f,i));sources=[]
                    lo,hi=map(int,certificate.offsets[i:i+2])
                    for k,v,side,w in zip(certificate.kinds[lo:hi],certificate.sources[lo:hi],certificate.sides[lo:hi],certificate.multipliers[lo:hi]):
                        v=int(v)
                        if k=='proof':ref=('task',at(at(parents,v),at(parent_indices,v)),w)
                        elif k=='assumption':ref=(k,at(conditions,v),w)
                        elif k in ('row','var'):ref=('model',k,v,side,w)
                        else:raise InvalidCertificateError('Unknown certificate source')
                        sources.append(ref)
                    if rule=='enumerate':
                        config=(tuple(recipe['vars']),recipe['max_states'],recipe.get('time_limit') or 30)
                    else:
                        config=(recipe['residual'],recipe.get('domain','auto'),(targets.kind,int(targets.ids[i]),targets.sides[i]))
                    selected.append(stage(rule,target,tuple(sources),conditions,config))
            elif rule in ('bound','affine_integrality','one_hot_affine','round'):
                for i,v in enumerate(targets.ids):
                    v=int(v)
                    if rule=='affine_integrality':
                        target=(plan.pool.terms(((v,1),)),0,'==');config=(v,int(recipe['rows'][i]));r=rule;sources=()
                    else:
                        target=claim(targets.claim(plan.f,i));sources=()
                        if rule=='bound':r=rule;config=(v,targets.sides[i])
                        elif rule=='one_hot_affine':r='one_hot_exclusion';config=(v,recipe['groups'])
                        else:
                            r=rule;config=(v,targets.sides[i]);position=int(recipe['positions'][i])
                            sources=(('task',at(at(parents,0),position)),)
                    selected.append(stage(r,target,sources,conditions,config))
            elif rule in ('round_expression','equality','cases'):
                if len(targets.ids)!=1:raise InvalidCertificateError('Expected one composition target')
                target=claim(targets.claim(plan.f,0))
                indices=recipe.get('parent_indices',(0,)*len(parents))
                if len(indices)!=len(parents):raise InvalidCertificateError('Parent position count mismatch')
                sources=tuple(('task',at(p,i)) for p,i in zip(parents,indices))
                r='round' if rule=='round_expression' else rule
                config=(recipe['var'],tuple(recipe['values'])) if rule=='cases' else ()
                selected.append(stage(r,target,sources,conditions,config))
            else:raise InvalidCertificateError('Unknown certificate rule')
            outputs.append(tuple(selected))
        roots=at(outputs,spec['root'])
        state=GraphExecution(model,plan.pool,plan.tasks,(),plan.budget)
        state.execute(roots)
        result=GraphProof(model,state,roots)
        keys=records[spec['root']].get('keys')
        if keys is not None:
            from mipframe.check.keys import Keys
            from mipframe.data.runtime import WorkContext
            if len(keys)!=len(roots):raise InvalidCertificateError('Invalid external keys')
            with WorkContext(options.get('memory_mb',128),options.get('disk_mb')) as work:
                checked=Keys(work)
                for key in keys:checked.append(key)
                object.__setattr__(result,'_external_keys',checked)
        return result
    finally:plan.close()
