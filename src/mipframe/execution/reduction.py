"""Component-wise DAG reduction with compact retained-edge witnesses."""
import numpy as np
from mipframe.execution.graph import Graph as ArrayGraph
from mipframe.data.sorting import lexorder
from mipframe.core.errors import NotDAGError,QueryTooLarge
from mipframe.execution.dag_diagnostics import fail_cycles


from mipframe.data.buffers import Buffer

def topology(g,error=NotDAGError):
    """Stable min-ID order with a typed binary heap."""
    w=g.ctx.work;degree=w.array(g.n,'<i8');np.subtract(g.rptr[1:],g.rptr[:-1],out=degree,casting='unsafe')
    heap=w.array(g.n,'<i8');order=w.array(g.n,'<i8');size=0;n=0
    def push(value):
        nonlocal size
        i=size;size+=1
        while i:
            p=(i-1)//2
            if heap[p]<=value:break
            heap[i]=heap[p];i=p
        heap[i]=value
    for v in range(g.n):
        if degree[v]==0:push(v)
    while size:
        v=int(heap[0]);size-=1;last=int(heap[size]);i=0
        while 2*i+1<size:
            j=2*i+1
            if j+1<size and heap[j+1]<heap[j]:j+=1
            if heap[j]>=last:break
            heap[i]=heap[j];i=j
        if size:heap[i]=last
        order[n]=v;n+=1
        for raw in g.neighbors(v):
            t=int(raw);degree[t]-=1
            if degree[t]==0:push(t)
    w.release(heap);w.release(degree)
    if n!=g.n:fail_cycles(g,error)
    return order


def reduction(ctx,E,*,tail='u',head='v',vertices=None,witnesses=False,edge_id=None,max_witness_edges=10_000_000):
    if type(witnesses) is not bool:raise TypeError('witnesses must be boolean')
    if max_witness_edges is not None and (type(max_witness_edges) is not int or max_witness_edges<0):raise ValueError('Invalid witness output limit')
    w=ctx.work
    from mipframe.ops.reduction import SharedArtifact,TransitiveReductionResult,selected_frame
    artifact=SharedArtifact(w.fork());lease=artifact.acquire();out=artifact.work
    try:
        # Execute and validate the input relation once. Retain its typed columns
        # with the result so witness gathering and kept/removed views do not
        # re-run a potentially expensive upstream join for another projection.
        from mipframe.frame.array_source import frame_from_columns,read_columns
        from mipframe.data.relations import build
        original=E
        if E._op=='source' and (hasattr(E,'_array_columns') or hasattr(E,'_typed_relation')):
            columns=read_columns(E,tuple(E.schema),ctx).columns
        else:
            from mipframe.frame.stream import batches
            from mipframe.data.relations import RelationBuilder
            stream=batches(E,tuple(E.schema),ctx)
            if stream is None:columns=build(out,E.schema,E._raw(tuple(E.schema),ctx)).columns
            else:
                builder=RelationBuilder(out,E.schema)
                for batch in stream:builder.extend(batch)
                columns=builder.finish().columns
        E=frame_from_columns(columns,E.schema,models=ctx.models,key=E.key,domains=E.domains,relations=E.relations)
        E._input_source=original
        E._query.resource=lease
        g=ArrayGraph(ctx,E,tail,head,vertices)
        order=topology(g);rank=w.array(g.n,'<i8');block=max(1,w.memory//128)
        for start in range(0,g.n,block):rank[order[start:start+block]]=np.arange(start,min(start+block,g.n))
        ranked=w.array(len(g.v),'<i8')
        for start in range(0,len(g.v),block):ranked[start:start+block]=rank[g.v[start:start+block]]
        outgoing=lexorder(w,(g.u,ranked));w.release(ranked)
        incoming=lexorder(w,(g.v,g.u)) if witnesses else None
        keep=w.array(len(g.u),'?',zero=True)
        # Weak components partition the potential quadratic closure storage.
        roots=w.array(g.n,'<i8')
        for start in range(0,g.n,block):roots[start:start+block]=np.arange(start,min(start+block,g.n))
        def root(v):
            while roots[v]!=v:
                roots[v]=roots[int(roots[v])];v=int(roots[v])
            return v
        for u,v in zip(g.u,g.v):
            a,b=root(int(u)),root(int(v))
            if a!=b:roots[max(a,b)]=min(a,b)
        for v in range(g.n):roots[v]=root(v)
        comp=lexorder(w,(roots,rank))
        local=w.array(g.n,'<i8')
        seen=w.array(g.n,'<i8',zero=True);queue=w.array(g.n,'<i8');pred=w.array(g.n,'<i8') if witnesses else None
        wanted=w.array(g.n,'<i8',zero=True) if witnesses else None
        depth=w.array(g.n,'<i8') if witnesses else None
        original_order=lexorder(w,(g.pair_for_original,)) if witnesses else None
        original_ptr=None
        if witnesses:
            original_ptr=w.array(len(g.u)+1,'<u8',zero=True)
            for start in range(0,len(g.pair_for_original),block):np.add.at(original_ptr,g.pair_for_original[start:start+block]+1,1)
            np.cumsum(original_ptr,out=original_ptr)
        path_ids=Buffer(out,'<u4' if len(g.original_u)<2**32 else '<u8')
        removed_ids=Buffer(out,'<u4' if len(g.original_u)<2**32 else '<u8')
        offsets=Buffer(out,'<u8');offsets.append(0)
        stats=dict(vertices=g.n,edges=len(g.original_u),unique_edges=len(g.u),bitset_components=0,
                   traversal_components=0,witness_groups=0,path_probes=0)
        tag=0
        def positions(u):return outgoing[int(g.ptr[u]):int(g.ptr[u+1])]
        def publish_path(pair,length,edge_at):
            # Parallel original records retain separate, complete witnesses.
            ctx.enforce(removed_ids.count+int(original_ptr[pair+1]-original_ptr[pair]))
            for original in original_order[int(original_ptr[pair]):int(original_ptr[pair+1])]:
                total=path_ids.count+length
                if max_witness_edges is not None and total>max_witness_edges:raise QueryTooLarge(max_witness_edges,total)
                start=path_ids.reserve(length)
                edge_at(start,length)
                removed_ids.append(int(original));offsets.append(total)
        begin=0
        while begin<g.n:
            end=begin+1;component=int(roots[int(comp[begin])])
            while end<g.n and roots[int(comp[end])]==component:end+=1
            vs=comp[begin:end];n=len(vs)
            for start in range(0,n,block):local[vs[start:start+block]]=np.arange(start,min(start+block,n))
            words=(n+63)//64;closure=None
            if n*words*8<=w.available//2:
                closure=w.array((n,words),'<u8',zero=True);stats['bitset_components']+=1
            else:stats['traversal_components']+=1
            for raw in reversed(vs):
                u=int(raw);lo,hi=map(int,g.ptr[u:u+2]);tag+=1
                if hi-lo<=1:
                    if hi>lo:
                        pos=lo;keep[pos]=True
                        if closure is not None:
                            v=int(g.v[pos]);j=int(local[v]);closure[int(local[u])]|=closure[j]
                            closure[int(local[u]),j//64]|=np.uint64(1)<<np.uint64(j%64)
                    continue
                for rawpos in positions(u):
                    pos=int(rawpos);v=int(g.v[pos])
                    if closure is not None:
                        j=int(local[v]);row=closure[int(local[u])];bit=np.uint64(1)<<np.uint64(j%64)
                        if row[j//64]&bit:continue
                        keep[pos]=True;np.bitwise_or(row,closure[j],out=row);row[j//64]|=bit
                    else:
                        if seen[v]==tag:continue
                        keep[pos]=True;seen[v]=tag;queue[0]=v;h=0;t=1
                        while h<t:
                            x=int(queue[h]);h+=1
                            for q in positions(x):
                                if not keep[q]:continue
                                z=int(g.v[q])
                                if seen[z]!=tag:seen[z]=tag;queue[t]=z;t+=1
            if witnesses:
                if closure is not None:
                    cache_size=max(1,min(65536,w.available//96))
                    cache=w.array((cache_size,3),'<i8');cache[:]=-1
                    def next_edge(u,target):
                        slot=(u*11400714819323198485+target)%cache_size
                        if cache[slot,0]==u and cache[slot,1]==target:return int(cache[slot,2])
                        j=int(local[target]);bit=np.uint64(1)<<np.uint64(j%64)
                        for rawpos in positions(u):
                            p=int(rawpos)
                            if not keep[p]:continue
                            v=int(g.v[p]);stats['path_probes']+=1
                            if v==target or closure[int(local[v]),j//64]&bit:
                                cache[slot]=(u,target,p);return p
                        raise RuntimeError('Incomplete retained reachability')
                    for raw in vs:
                        u=int(raw)
                        for rawpos in positions(u):
                            pos=int(rawpos)
                            if keep[pos]:continue
                            target=int(g.v[pos]);x=u;length=0
                            while x!=target:x=int(g.v[next_edge(x,target)]);length+=1
                            def fill(start,size,u=u,target=target):
                                x=u
                                for k in range(size):
                                    p=next_edge(x,target);path_ids[start+k]=int(g.representative[p]);x=int(g.v[p])
                            publish_path(pos,length,fill)
                    w.release(cache)
                else:
                    # Count endpoint groups without Python sets.
                    ns=nt=0;tag+=1
                    for raw in vs:
                        u=int(raw);has=False
                        for p in positions(u):
                            if not keep[p]:
                                has=True;v=int(g.v[p])
                                if wanted[v]!=tag:wanted[v]=tag;nt+=1
                        ns+=int(has)
                    reverse=nt<ns
                    for raw in vs:
                        origin=int(raw);tag+=1;remaining=0
                        candidates=incoming[int(g.rptr[origin]):int(g.rptr[origin+1])] if reverse else positions(origin)
                        for p in candidates:
                            if not keep[p]:
                                endpoint=int(g.u[p] if reverse else g.v[p]);wanted[endpoint]=tag;remaining+=1
                        if not remaining:continue
                        stats['witness_groups']+=1;seen[origin]=tag;depth[origin]=0;queue[0]=origin;h=0;t=1
                        while h<t and remaining:
                            x=int(queue[h]);h+=1
                            adjacency=incoming[int(g.rptr[x]):int(g.rptr[x+1])] if reverse else positions(x)
                            for rawpos in adjacency:
                                p=int(rawpos)
                                if not keep[p]:continue
                                z=int(g.u[p] if reverse else g.v[p])
                                if seen[z]==tag:continue
                                seen[z]=tag;pred[z]=p;depth[z]=depth[x]+1;queue[t]=z;t+=1
                                if wanted[z]==tag:remaining-=1
                        if remaining:raise RuntimeError('Missing retained witness')
                        for rawpos in candidates:
                            pos=int(rawpos)
                            if keep[pos]:continue
                            endpoint=int(g.u[pos] if reverse else g.v[pos]);length=int(depth[endpoint])
                            def fill(start,size,endpoint=endpoint,reverse=reverse):
                                x=endpoint
                                for k in range(size):
                                    p=int(pred[x]);path_ids[start+(k if reverse else size-k-1)]=int(g.representative[p])
                                    x=int(g.v[p] if reverse else g.u[p])
                            publish_path(pos,length,fill)
            if closure is not None:w.release(closure)
            begin=end
        original_keep=out.array(len(g.original_u),'?');original_keep[:]=keep[g.pair_for_original]
        nk=int(original_keep.sum());nr=len(original_keep)-nk;ctx.enforce(nk)
        if witnesses:ctx.enforce(nr)
        def selection(flag):
            result=out.array(nk if flag else nr,'<u8');pos=0
            block=max(1,ctx.memory//128)
            for start in range(0,len(original_keep),block):
                idx=np.flatnonzero(original_keep[start:start+block] if flag else ~original_keep[start:start+block])+start
                result[pos:pos+len(idx)]=idx;pos+=len(idx)
            return out.seal(result)
        retained=selection(True)
        if not witnesses:
            result=selected_frame(E,retained,ctx.models,lease)
            return result
        removed=selection(False)
        key_order=None
        edge_column=edge_id or ('edge_id' if 'edge_id' in E.schema else None)
        if edge_column is not None:
            if edge_column not in E.schema:raise ValueError('Unknown edge identity column')
            from mipframe.frame.array_source import read_columns
            from mipframe.data.relations import build
            column=read_columns(E,(edge_column,),ctx).columns[edge_column]
            table=build(out,{edge_column:E.schema[edge_column]},({edge_column:v} for v in column))
            keys=table.columns[edge_column];key_order=table.order((edge_column,))
            if any(v is None for v in keys) or any(keys[key_order[i]]==keys[key_order[i-1]] for i in range(1,len(keys))):raise ValueError('Edge identities must be unique and non-null')
        else:keys=None;edge_column='edge_id'
        stats.update(kept=nk,removed=nr,path_edges=path_ids.count,witnesses='complete',
                     peak_working_bytes=w.ledger.peak_resident,peak_disk_bytes=w.ledger.peak_disk)
        removed_ids_array=removed_ids.finish()
        removed_order=lexorder(w,(removed_ids_array,));w.transfer(removed_order,out);removed_order=out.seal(removed_order)
        if key_order is not None:key_order=out.seal(key_order)
        result=TransitiveReductionResult(E,tail,head,retained,removed,
            removed_ids_array,offsets.finish(),path_ids.finish(),keys,edge_column,stats,lease,ctx.models,key_order,removed_order)
        stats.update(peak_working_bytes=w.ledger.peak_resident,peak_disk_bytes=w.ledger.peak_disk)
        ctx.paths.add('component DAG reduction + compact retained-edge witnesses')
        return result
    except BaseException:lease.close();raise
