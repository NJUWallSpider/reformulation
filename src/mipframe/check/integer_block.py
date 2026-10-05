"""Exact rational tiles represented by overflow-checked integer numerators.

Tiles are bounded scratch, not the stored certificate representation. Each
expression has an exact common denominator. Range checks precede every NumPy
multiply/add; unsafe cases stay in the arbitrary-precision sparse kernel.
"""
import numpy as np
from math import inf, lcm
from fractions import Fraction

_LIMIT = 2**63-1


def integer(value):
    if isinstance(value,int):return value
    if isinstance(value,Fraction) and value.denominator==1:return value.numerator
    return None


class IntegerBlock:
    def __init__(self, tree, reachable):
        self.tree=tree;self.vectors={};self.norms={};self.denominators={};self.available=False
        self.scale_cache={};self.sparse={};self.extrema={}
        n=len(tree.variables)
        size=n*len(reachable)*8
        if not n or size>8*1024**2 or size+n*32>tree.budget.limits['memory']-tree.budget.counts['memory']:
            return
        bounds=[tree.f.var_bounds(v) for v in tree.variables]
        finite=[[x not in (-inf,inf) for x in side] for side in zip(*bounds)]
        values=[[integer(x) if present else 0 for x,present in zip(side,mask)] for side,mask in zip(zip(*bounds),finite)]
        if any(x is None or abs(x)>_LIMIT for side in values for x in side):return
        tree.budget.charge('memory',size+n*32)
        self.limits=np.asarray(values,dtype=np.int64)
        self.finite=np.asarray(finite,dtype=bool)
        self.bound_norm=max((abs(x) for side in values for x in side),default=0)
        for i in sorted(reachable):
            kind,data=tree.pool.nodes[i]
            vector=None if kind=='terms' and len(data)<=8 else np.zeros(n,dtype=np.int64)
            if kind=='terms':
                denom=lcm(*(c.denominator for _,c in data))
                norm=0
                for v,c in data:
                    numerator=c.numerator*(denom//c.denominator)
                    norm=max(norm,abs(numerator))
                    if abs(numerator)>_LIMIT:return
                    if vector is not None:vector[tree.coordinates[v]]=int(numerator)
                if vector is None:self.sparse[i]=tuple((tree.coordinates[v],c.numerator*(denom//c.denominator)) for v,c in data)
            else:
                weights=[self.scale(scale,self.denominators[child]) for child,scale in data]
                denom=lcm(*(w.denominator for w in weights))
                norm=0
                for (child,_),weight in zip(data,weights):
                    factor=weight.numerator*(denom//weight.denominator)
                    if norm+abs(factor)*self.norms[child]>_LIMIT:return
                    if child in self.sparse:
                        for position,value in self.sparse[child]:vector[position]+=value*factor
                    elif factor==1:vector+=self.vectors[child]
                    elif factor==-1:vector-=self.vectors[child]
                    elif factor:vector+=self.vectors[child]*factor
                    norm+=abs(factor)*self.norms[child]
            if vector is not None:
                vector.flags.writeable=False
                self.vectors[i]=vector
            if vector is None:
                vals=[v for _,v in self.sparse[i]]
                self.extrema[i]=(min(vals,default=0),max(vals,default=0))
            else:
                lo,hi=int(np.min(vector,initial=0)),int(np.max(vector,initial=0))
                self.extrema[i]=(lo,hi);norm=max(-lo,hi)
            self.norms[i]=norm
            self.denominators[i]=denom
            tree.budget.charge(count=len(data) if vector is None else n)
        self.available=True

    def scale(self,value,denominator):
        key=(value,denominator)
        if key not in self.scale_cache:self.scale_cache[key]=Fraction(value,denominator)
        return self.scale_cache[key]

    def prepare(self,pairs,intervals,bounds):
        if not self.available:return None
        combined={}
        for i,w in pairs:
            if i not in self.norms:return None
            combined[i]=combined.get(i,0)+w
        combined={i:self.scale(w,self.denominators[i]) for i,w in combined.items() if w and self.norms[i]}
        denom=lcm(*(w.denominator for w in combined.values()))
        factors=tuple((i,w.numerator*(denom//w.denominator)) for i,w in combined.items())
        lower=upper=0
        for i,w in factors:
            lo,hi=self.extrema[i]
            lower+=min(0,w*lo,w*hi);upper+=max(0,w*lo,w*hi)
        norm=max(-lower,upper)
        local=[];limit=self.bound_norm
        for v,(lo,hi) in intervals.items():
            if lo!=hi:return None
            value=integer(lo)
            if value is None:return None
            limit=max(limit,abs(value));local.append((self.tree.coordinates[v],value))
        if norm>_LIMIT or norm*limit>_LIMIT:return None
        dense=[];sparse={}
        for i,weight in factors:
            if i in self.sparse:
                for position,value in self.sparse[i]:sparse[position]=sparse.get(position,0)+weight*value
            else:dense.append((i,weight))
        return tuple(dense),denom,tuple(local),bounds,tuple(sparse.items())

    def residual_many(self,requests):
        """One exact matrix kernel for a bounded tile of logical residuals."""
        n=len(self.tree.variables);budget=self.tree.budget
        capacity=max(1,min(1024,int((budget.limits['memory']-budget.counts['memory'])//max(1,n*64))))
        output=[]
        for start in range(0,len(requests),capacity):
            chunk=requests[start:start+capacity]
            scratch=len(chunk)*n*64
            budget.charge('memory',scratch)
            try:
                matrix=np.zeros((len(chunk),n),dtype=np.int64)
                counts=max((len(r[0]) for r in chunk),default=0)
                for slot in range(counts):
                    selected=[(row,request[0][slot]) for row,request in enumerate(chunk) if slot<len(request[0])]
                    if not selected:continue
                    rows=np.asarray([row for row,_ in selected],dtype=np.intp)
                    weights=np.asarray([scale for _,(_,scale) in selected],dtype=np.int64)
                    sources=np.stack([self.vectors[i] for _,(i,_) in selected])
                    matrix[rows]+=sources*weights[:,None]
                for row,request in enumerate(chunk):
                    for position,value in request[4]:matrix[row,position]+=value
                totals=[0]*len(chunk)
                for row,(_,_,local,_,_) in enumerate(chunk):
                    for position,value in local:
                        totals[row]+=int(matrix[row,position])*value;matrix[row,position]=0
                zero=~np.any(matrix,axis=1)
                negative,positive=matrix<0,matrix>0
                infinite=np.any((negative & ~self.finite[0]) | (positive & ~self.finite[1]),axis=1)
                products=matrix*np.where(positive,self.limits[1],self.limits[0])
                maxima=np.maximum(np.max(products,axis=1,initial=0),-np.min(products,axis=1,initial=0))
                safe=maxima<=_LIMIT//max(1,n)
                values=[0]*len(chunk)
                rows=np.flatnonzero(safe)
                for row,value in zip(rows,np.sum(products[rows],axis=1,dtype=np.int64)):values[int(row)]=int(value)
                for row in np.flatnonzero(~safe):values[int(row)]=sum(map(int,products[row]))
                lower=np.packbits(negative,axis=1,bitorder='little')
                upper=np.packbits(positive,axis=1,bitorder='little')
                for row,(_,denom,_,bounds,_) in enumerate(chunk):
                    if not bounds:result=Fraction(totals[row],denom),bool(zero[row]),()
                    elif infinite[row]:result=inf,False,()
                    else:
                        lo=int.from_bytes(lower[row].tobytes(),'little');hi=int.from_bytes(upper[row].tobytes(),'little')
                        result=Fraction(totals[row]+int(values[row]),denom),bool(zero[row]),(('dense',lo,hi),) if lo or hi else ()
                    output.append(result)
                budget.charge(count=len(chunk)*n)
            finally:budget.counts['memory']-=scratch
        return output

    def residual(self,pairs,intervals,bounds):
        if not self.available:return None
        combined={}
        for i,w in pairs:
            if i not in self.vectors:return None
            combined[i]=combined.get(i,0)+w
        combined={i:self.scale(w,self.denominators[i]) for i,w in combined.items() if w and self.norms[i]}
        denom=lcm(*(w.denominator for w in combined.values()))
        factors={i:w.numerator*(denom//w.denominator) for i,w in combined.items()}
        norm=sum(abs(w)*self.norms[i] for i,w in factors.items())
        if norm>_LIMIT:return None
        local=[];limit=self.bound_norm
        for v,(lo,hi) in intervals.items():
            if lo!=hi:return None
            value=integer(lo)
            if value is None:return None
            limit=max(limit,abs(value));local.append((self.tree.coordinates[v],value))
        if norm*limit>_LIMIT:return None
        vector=np.zeros(len(self.tree.variables),dtype=np.int64)
        for i,w in factors.items():
            if w==1:vector+=self.vectors[i]
            elif w==-1:vector-=self.vectors[i]
            elif w:vector+=self.vectors[i]*w
        total=0
        for i,value in local:
            total+=int(vector[i])*value;vector[i]=0
        nonzero=bool(np.any(vector))
        if not bounds:return Fraction(total,denom),not nonzero,[]
        negative,positive=vector<0,vector>0
        if np.any(negative & ~self.finite[0]) or np.any(positive & ~self.finite[1]):
            return inf,False,[]
        dtype=np.int64 if norm*limit*len(vector)<=_LIMIT else object
        total+=int(np.sum(vector*np.where(positive,self.limits[1],self.limits[0]),dtype=dtype))
        lo=int.from_bytes(np.packbits(negative,bitorder='little').tobytes(),'little')
        hi=int.from_bytes(np.packbits(positive,bitorder='little').tobytes(),'little')
        self.tree.budget.charge(count=len(vector))
        return Fraction(total,denom),not nonzero,[('dense',lo,hi)] if lo or hi else []
