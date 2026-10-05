"""Budgeted CSR graph kernels; results contain integer IDs only."""
from itertools import chain


def component_labels(g,work,undirected=False):
    n=g.n;seen=work.array(n,'?',zero=True);stack=work.array(n,'<i8');cursor=work.array(n,'<i8')
    finish=work.array(n,'<i8');used=0
    for root in range(n):
        if seen[root]:continue
        seen[root]=True;stack[0]=root;cursor[0]=g.ptr[root];depth=1
        while depth:
            v=int(stack[depth-1]);p=int(cursor[depth-1])
            if p==g.ptr[v+1]:finish[used]=v;used+=1;depth-=1;continue
            cursor[depth-1]+=1;t=int(g.v[p])
            if not seen[t]:seen[t]=True;stack[depth]=t;cursor[depth]=g.ptr[t];depth+=1
    result=work.array(n,'<i8');result[:]=-1
    for raw in reversed(finish):
        root=int(raw)
        if result[root]>=0:continue
        stack[0]=root;head=0;tail=1;minimum=root;result[root]=root
        while head<tail:
            v=int(stack[head]);head+=1;minimum=min(minimum,v)
            neighbors=chain(g.neighbors(v),g.neighbors(v,True)) if undirected else g.neighbors(v,True)
            for raw in neighbors:
                t=int(raw)
                if result[t]<0:result[t]=root;stack[tail]=t;tail+=1
        for i in range(tail):result[int(stack[i])]=minimum
    for a in (seen,stack,cursor,finish):work.release(a)
    return result
