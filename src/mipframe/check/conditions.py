"""Component-local immutable condition sets, with exact branch discharge.

IDs belong to a bounded execution component, never the global model space.
Unions share the claim dictionary; no target copies its parents' claim tuples.
"""
from fractions import Fraction


class ConditionSet:
    __slots__=('pool','bits')

    def __init__(self,pool,bits):self.pool,self.bits=pool,bits
    def __bool__(self):return bool(self.bits)
    def __len__(self):return self.bits.bit_count()
    def __iter__(self):
        bits=self.bits
        while bits:
            low=bits & -bits
            yield self.pool.claims[low.bit_length()-1]
            bits-=low
    def __contains__(self,claim):
        index=self.pool.ids.get(claim)
        return index is not None and bool(self.bits & (1<<index))


class ConditionPool:
    def __init__(self,terms,budget):
        self.terms,self.budget=terms,budget
        self.claims=[];self.ids={};self.matches={};self.sets={}
        self.retained_bytes=0

    def intern(self,bits):
        if bits not in self.sets:
            cost=96+(bits.bit_length()+7)//8
            self.budget.charge('memory',cost);self.retained_bytes+=cost
            self.sets[bits]=ConditionSet(self,bits)
        return self.sets[bits]

    def union(self,*groups):
        bits=0
        for group in groups:
            if isinstance(group,ConditionSet) and group.pool is self:
                bits |= group.bits
                continue
            for claim in group:
                index=self.ids.get(claim)
                if index is None:
                    self.budget.charge('memory',160)
                    self.retained_bytes+=160
                    index=len(self.claims);self.ids[claim]=index;self.claims.append(claim)
                    expr,rhs,rel=claim
                    ts=self.terms(expr)
                    if rel=='==' and len(ts)==1:
                        var,coef=next(iter(ts.items()))
                        key=(var,Fraction(rhs,coef))
                        self.matches[key]=self.matches.get(key,0)|(1<<index)
                bits |= 1<<index
        self.budget.charge(count=len(groups))
        return self.intern(bits)

    def discharge(self,group,var,value):
        group=self.union(group)
        matching=group.bits & self.matches.get((var,value),0)
        if not matching:
            from mipframe.check.certificate import InvalidCertificateError
            raise InvalidCertificateError('Missing matching case assumption')
        return self.intern(group.bits & ~matching)

    def finish(self):
        # Published sets need only the shared claims/ID dictionary. Drop the
        # bound executor method and reverse cache edges for prompt destruction.
        self.terms=None
        self.sets.clear()
