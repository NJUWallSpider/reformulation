"""Complete cycle diagnostics with typed DFS/BFS workspaces."""
import numpy as np
from mipframe.frame.schema import DType
from mipframe.execution.graph import scalar


def fail_cycles(g,error):
    w=g.ctx.work;n=g.n
    from mipframe.data.graph_kernels import component_labels
    labels=component_labels(g,w);sizes=w.array(n,'<u8',zero=True)
    for c in labels:sizes[int(c)]+=1
    cyclic=w.array(n,'?',zero=True)
    np.greater(sizes,1,out=cyclic)
    for u,v in zip(g.u,g.v):
        if u==v:cyclic[labels[u]]=True
    seen=w.array(n,'?',zero=True);stack=w.array(n,'<i8');queue=w.array(n,'<i8')
    pred=w.array(n,'<i8');seen[:]=False
    def witnesses():
        for root in range(n):
            if not cyclic[root]:continue
            target=next(int(v) for v in g.neighbors(root) if labels[v]==root)
            yield dict(component=scalar(g.values[root]),step=0,u=scalar(g.values[root]),v=scalar(g.values[target]))
            if target==root:continue
            head=0;tail=1;queue[0]=target;seen[target]=True;found=False
            while head<tail and not found:
                v=int(queue[head]);head+=1
                for raw in g.neighbors(v):
                    t=int(raw)
                    if labels[t]!=root or seen[t]:continue
                    seen[t]=True;pred[t]=v;queue[tail]=t;tail+=1
                    if t==root:found=True;break
            length=0;t=root
            while t!=target:stack[length]=t;length+=1;t=int(pred[t])
            at=target
            for i in range(length):
                t=int(stack[length-i-1])
                yield dict(component=scalar(g.values[root]),step=i+1,u=scalar(g.values[at]),v=scalar(g.values[t]));at=t
    wf=g.ctx.publish({'component':g.typ,'step':DType('int'),'u':g.typ,'v':g.typ},witnesses(),key=('component','step'),limited=False)
    bad=g.ctx.publish({'vertex':g.typ,'component':g.typ},
        (dict(vertex=scalar(g.values[v]),component=scalar(g.values[labels[v]])) for v in range(n) if cyclic[labels[v]]),
        key=('vertex',),relations={'witness':wf},limited=False)
    from mipframe.check.report import Report
    raise error(Report(g.ctx,False,dict(operation=g.ctx.operation,violations=len(bad)),bad))
