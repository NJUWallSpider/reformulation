"""Bounded numerical inventory. No graph, query engine, or structural analysis.

The observer can share the canonical content-digest traversal. Heavy-hitter
counts are Space-Saving upper estimates with explicit deterministic error bounds.
"""
from collections import Counter, OrderedDict
from array import array
from tempfile import TemporaryFile
from fractions import Fraction as Q
import heapq
import re
import sys
import numpy as np

NEAR_ZERO = Q(1, 10**9)
_DIGITS = re.compile(r'([0-9]+)')


def name_tokens(name):
    # Common solver names (c123, x123) need neither a token list nor a join.
    if len(name)>1 and name[0].isalpha() and name[1:].isascii() and name[1:].isdigit():
        return name[0]+'{int}', (int(name[1:]),)
    parts = _DIGITS.split(name)
    return ''.join('{int}' if i % 2 else s for i, s in enumerate(parts)), tuple(int(s) for s in parts[1::2])


def sense(lo, hi):
    if lo is None: return 'free' if hi is None else '<='
    if hi is None: return '>='
    return '=' if lo == hi else 'range'


class HeavyHitters:
    """Fixed-capacity Space-Saving; payloads have separately bounded sizes."""
    def __init__(self, capacity):
        self.capacity = max(1, capacity)
        self.entries, self.heap = {}, []
        self.serial = 0
        self.total = 0
        self.overflow = False

    def add(self, key, weight=1):
        self.total += weight
        entry = self.entries.get(key)
        if entry is not None:
            entry['count'] += weight
            return entry
        error = 0
        if len(self.entries) == self.capacity:
            if not self.heap:
                self.heap = [(e['count'],e['serial'],k) for k,e in self.entries.items()]
                heapq.heapify(self.heap)
            # Heap priorities are lower bounds. Refresh only when an eviction
            # actually needs the minimum, not on every common-value update.
            while self.heap:
                n, serial, old = heapq.heappop(self.heap)
                current = self.entries[old]
                if current['count'] != n:
                    heapq.heappush(self.heap,(current['count'],serial,old))
                    continue
                del self.entries[old]
                error = n
                break
            self.overflow = True
        self.serial += 1
        entry = self.entries[key] = dict(count=error+weight,error=error,samples=[],serial=self.serial)
        if self.overflow: heapq.heappush(self.heap,(entry['count'],self.serial,key))
        return entry

    def summary(self, convert=lambda key, entry: dict(value=key), *, limit=None):
        items = []
        # Only bounded retained candidates are ranked, never terms or all rows.
        ranked = heapq.nsmallest(limit,self.entries.items(),key=lambda p:(-p[1]['count'],p[1]['serial'])) if limit is not None else sorted(self.entries.items(),key=lambda p:(-p[1]['count'],p[1]['serial']))
        for key, e in ranked:
            items.append(dict(convert(key, e), count=e['count'], error=e['error'],
                              count_lower=e['count']-e['error'], exact=e['error']==0))
        omitted = min((e['count'] for e in self.entries.values()),default=0) if self.overflow else 0
        if len(items)<len(self.entries):
            omitted=max(omitted,items[-1]['count'] if items else max(e['count'] for e in self.entries.values()))
        return dict(items=items, total_count=self.total, total_groups=None if self.overflow else len(self.entries),
                    total_groups_lower_bound=self.capacity+1 if self.overflow else len(self.entries),
                    complete=not self.overflow and len(items)==len(self.entries), ranking_exact=not self.overflow,
                    convention='Space-Saving: count_lower <= true count <= count; omitted frequency <= omitted_count_upper',
                    omitted_count_upper=omitted)


class Numeric:
    def __init__(self, capacity):
        self.hist = HeavyHitters(capacity)
        self.minimum = self.maximum = None
        self.positive = self.negative = self.count = self.near_zero = 0

    def add(self, key, value, n=1):
        self.count += n
        self.positive += n if value > 0 else 0
        self.negative += n if value < 0 else 0
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.hist.add(key, n)

    def export(self, number, *, absolute=False):
        counts = Counter()
        if not self.hist.overflow:
            for key, entry in self.hist.entries.items():
                value = number(key)
                counts[abs(value) if absolute else value] += entry['count']
        profile = weighted_profile(sorted(counts.items())) if not self.hist.overflow else dict(
            kind='varying', count=self.count, median=None, p90=None, mode=None)
        if not absolute:
            profile.update(min=self.minimum, max=self.maximum)
        profile['quantiles_exact'] = not self.hist.overflow
        top = self.hist.summary(lambda key, e: dict(value=number(key)),limit=32)
        return profile, top


def weighted_profile(pairs):
    n = sum(c for _, c in pairs)
    if not n: return dict(kind='empty', count=0, min=None, median=None, p90=None, max=None, mode=None)
    def at(rank):
        seen = 0
        for v, count in pairs:
            seen += count
            if rank < seen: return v
    def quantile(q):
        p = (n-1)*q
        lo = p.numerator // p.denominator
        a = at(lo)
        return a + (p-lo)*(at(min(lo+1, n-1))-a)
    out = dict(kind='constant' if len(pairs)==1 else 'varying', count=n, min=pairs[0][0],
               max=pairs[-1][0], median=quantile(Q(1,2)), p90=quantile(Q(9,10)),
               mode=max(pairs, key=lambda p:p[1])[0])
    if len(pairs)==1: out['value']=pairs[0][0]
    return out


def degree_profile(ptr):
    # Counting sort over degrees, not a sort of matrix entries or entities.
    degree = np.diff(ptr)
    hist = np.bincount(degree) if len(degree) else np.empty(0, dtype=np.int64)
    pairs = [(int(i), int(hist[i])) for i in np.flatnonzero(hist)]
    result = weighted_profile(pairs)
    result['histogram'] = dict(items=[dict(value=v,count=c,exact=True,error=0) for v,c in
                                    heapq.nlargest(32,pairs,key=lambda p:(p[1],-p[0]))],
                              total_groups=len(pairs), total_count=len(degree), complete=len(pairs)<=32)
    result['singleton'] = int(hist[1]) if len(hist)>1 else 0
    result['isolated'] = int(hist[0]) if len(hist) else 0
    return result


class Scan:
    """A numerical observer with byte-conservative per-entry capacity limits."""
    def __init__(self, memory_mb=128):
        from mipframe.core.resources import memory_bytes
        self.budget = max(1, min(8*1024**2, memory_bytes(memory_mb)//8))
        # Fixed allocations: 3 numeric summaries, shapes, two lexical directories,
        # a bounded number cache, and current-row workspace. Entry sizes include
        # Python container overhead and capped sample/slot payloads.
        self.capacity = max(1, self.budget // (8*512))
        self.matrix = Numeric(self.capacity)
        self.rhs = Numeric(self.capacity)
        self.objective = Numeric(self.capacity)
        self.shapes = HeavyHitters(max(1, self.budget//(8*32768)))
        self.names = {k:HeavyHitters(max(1,self.budget//(8*32768))) for k in ('rows','vars')}
        self.types = Counter(B=0,I=0,C=0)
        self.senses = Counter({'<=':0,'=':0,'>=':0})
        self.bounds = Counter({k:0 for k in ('fixed','free','lower_only','upper_only','boxed')})
        self.unit_bounds = 0
        self.abs_min = self.abs_max = None
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.row_limit = max(1, min(128, self.budget//32768))
        self.skipped_names = Counter()
        self.opaque_shapes = 0
        self.coefficient_counts = None
        self._coefficient_file = None
        self.small_patterns = OrderedDict()
        self.domain_cache = {}

    def number(self, ident):
        if ident is None: return None
        ident = int(ident)
        if ident in self.cache: return self.cache[ident][0]
        n, d = self.read_number(ident)
        value = Q(int(n), int(d))
        cost = 256 + sys.getsizeof(value.numerator) + sys.getsizeof(value.denominator)
        if cost <= self.budget//8:
            while self.cache and self.cache_bytes+cost > self.budget//8:
                _, (_, old_cost) = self.cache.popitem(last=False)
                self.cache_bytes -= old_cost
            self.cache[ident] = (value,cost)
            self.cache_bytes += cost
        return value

    def value(self, ident):
        if ident is None: return None
        cached = self.cache.get(int(ident))
        return cached[0] if cached is not None else self.number(ident)

    def name(self, axis, ident, name):
        # Extremely long lexical identifiers are counted as unprofiled, never
        # retained or truncated into a false grouping key.
        if len(name)>512:
            self.skipped_names[axis]+=1
            return
        template, slots = name_tokens(name)
        entry = self.names[axis].add(template)
        if 'slots' not in entry:
            entry['slot_count']=len(slots)
            entry['slots']=[dict(min=v,max=v,values=set(),overflow=False) for v in slots[:8]]
        if len(entry['samples'])<3: entry['samples'].append(dict(id=ident,name=name))
        for stat,value in zip(entry['slots'],slots):
            if value<stat['min']: stat['min']=value
            if value>stat['max']: stat['max']=value
            if len(stat['values'])<32 or value in stat['values']: stat['values'].add(value)
            else: stat['overflow']=True

    def variable(self, v):
        ident,name,kind,lo,hi,cost,*_ = v
        domain_key=(kind,lo,hi)
        domain=self.domain_cache.get(domain_key)
        if domain is None:
            lower,upper=self.value(lo),self.value(hi)
            from mipframe.frame.sources import binary
            from math import inf
            typ = 'B' if binary(kind,-inf if lower is None else lower,inf if upper is None else upper) else kind
            bound = ('free' if upper is None else 'upper_only') if lower is None else (
                'lower_only' if upper is None else 'fixed' if lower==upper else 'boxed')
            domain=(typ,bound,lower==0 and upper==1)
            if len(self.domain_cache)<128: self.domain_cache[domain_key]=domain
        typ,bound,unit=domain
        self.types[typ]+=1
        self.bounds[bound]+=1
        self.unit_bounds+=unit
        value=self.value(cost)
        if value: self.objective.add(cost,value)
        self.name('vars',ident,name)

    def row(self, row, coefficients):
        ident,name,lo,hi,*_ = row
        lower,upper=self.value(lo),self.value(hi)
        relation=sense(lower,upper)
        self.senses[relation]+=1
        for cid in ((lo,hi) if relation=='range' else (lo,) if relation=='>=' else (hi,)):
            value=self.value(cid)
            if value: self.rhs.add(cid,value)
        self.name('rows',ident,name)
        small_key=tuple(coefficients.tolist()) if len(coefficients)<=8 else None
        cached=self.small_patterns.get(small_key) if small_key is not None else None
        if cached is None:
            counts = {}
            candidate = False
            if small_key is not None:
                for cid in small_key: counts[cid]=counts.get(cid,0)+1
                for cid,n in counts.items(): self.coefficient_counts[cid]+=n
                candidate=len(counts)>self.row_limit
                pattern=frozenset(counts.items()) if not candidate else ('candidate',sum(small_key),sum(cid*cid for cid in small_key))
                if candidate: counts=dict(list(counts.items())[:self.row_limit])
                if len(self.small_patterns)>=max(1,self.budget//32768): self.small_patterns.popitem(last=False)
                self.small_patterns[small_key]=(counts,pattern,candidate)
            else:
                fingerprint1 = fingerprint2 = 0
                for start in range(0,len(coefficients),256):
                    block=Counter(coefficients[start:start+256].tolist())
                    for cid,n in block.items():
                        self.coefficient_counts[cid]+=n
                        fingerprint1=(fingerprint1+cid*n) % (2**64)
                        fingerprint2=(fingerprint2+cid*cid*n) % (2**64)
                        if cid in counts: counts[cid]+=n
                        elif len(counts)<self.row_limit: counts[cid]=n
                        else: candidate=True
                pattern=frozenset(counts.items()) if not candidate else ('candidate',fingerprint1,fingerprint2)
        else:
            counts,pattern,candidate=cached
            # Candidate previews omit coefficient IDs: count the original small
            # row when the workspace cap is too small for its full pattern.
            if candidate:
                for cid in small_key: self.coefficient_counts[cid]+=1
            else:
                for cid,n in counts.items(): self.coefficient_counts[cid]+=n
        key=(relation,lo,hi,len(coefficients),pattern)
        entry=self.shapes.add(key)
        if 'coefficients' not in entry:
            entry['coefficients']=tuple(counts.items())
            entry['candidate']=candidate
        if len(entry['samples'])<3: entry['samples'].append(dict(id=ident,name=name[:120]))
        self.opaque_shapes+=candidate

    def prepare_numbers(self, count):
        # Interned IDs give a compact exact frequency vector. Large vectors use
        # scratch mmap rather than an unbounded Python histogram or SQL updates.
        if count*8 <= self.budget//4:
            self.coefficient_counts=array('Q',[0])*count
        else:
            self._coefficient_file=TemporaryFile()
            self._coefficient_file.truncate(count*8)
            self.coefficient_counts=np.memmap(self._coefficient_file,dtype='<u8',mode='r+',shape=(count,))

    def close(self):
        counts=getattr(self,'coefficient_counts',None)
        if isinstance(counts,np.memmap) and not counts._mmap.closed: counts._mmap.close()
        self.coefficient_counts=None
        file=getattr(self,'_coefficient_file',None)
        if file is not None:
            file.close()
            self._coefficient_file=None

    def __del__(self): self.close()

    def export(self, row_ptr, col_ptr):
        try: return self._export(row_ptr,col_ptr)
        finally: self.close()

    def _export(self, row_ptr, col_ptr):
        source = getattr(self,'number_rows',None)
        if source is None:
            values=((cid,self.value(cid),int(n)) for cid,n in enumerate(self.coefficient_counts) if n)
        else:
            values=((cid,Q(int(num),int(den)),int(self.coefficient_counts[cid]))
                    for cid,num,den in source() if self.coefficient_counts[cid])
        for cid,value,n in values:
            av=abs(value)
            self.matrix.add(cid,value,n)
            self.matrix.near_zero+=n if 0<av<=NEAR_ZERO else 0
            self.abs_min=av if self.abs_min is None else min(self.abs_min,av)
            self.abs_max=av if self.abs_max is None else max(self.abs_max,av)
        matrix, common=self.matrix.export(self.value,absolute=True)
        matrix.update(min=self.abs_min,max=self.abs_max)
        rhs,rhs_top=self.rhs.export(self.value)
        costs,cost_top=self.objective.export(self.value)
        row_degree,col_degree=degree_profile(row_ptr),degree_profile(col_ptr)
        def shape(key,e):
            relation,lo,hi,nnz,_=key
            return dict(sense=relation,nnz=nnz,lb=self.value(lo),ub=self.value(hi),
                coefficients=[dict(value=self.value(cid),count=n) for cid,n in e['coefficients']],
                coefficients_complete=not e['candidate'],candidate=e['candidate'],
                grouping='fingerprint candidate; not exact equality' if e['candidate'] else 'exact local multiset',
                examples=e['samples'])
        def lexical(key,e):
            return dict(template=key,slot_count=e['slot_count'],examples=e['samples'],
                slots=[dict(index=i,min=s['min'],max=s['max'],
                    distinct=None if s['overflow'] or e['error'] else len(s['values']),
                    distinct_lower_bound=len(s['values'])+int(s['overflow']),
                    complete=not e['error'],distinct_exact=not s['overflow'] and not e['error'])
                    for i,s in enumerate(e['slots'])],slots_complete=e['slot_count']<=8)
        names={k:tracker.summary(lexical) for k,tracker in self.names.items()}
        for k in names:
            names[k]['unprofiled']=self.skipped_names[k]
            if self.skipped_names[k]:
                names[k].update(complete=False,total_groups=None,ranking_exact=False)
        names['convention']='lexical hypothesis; not structural evidence'
        shapes=self.shapes.summary(shape)
        for item in shapes['items']:
            if item['candidate']:
                item.update(exact=False,count_lower=0,error=item['count'])
        if self.opaque_shapes:
            shapes.update(total_groups=None,complete=False,ranking_exact=False,
                          candidate_rows=self.opaque_shapes)
        nv,nr=len(col_ptr)-1,len(row_ptr)-1
        return dict(size=dict(variables=nv,rows=nr,nonzeros=int(row_ptr[-1])),
            matrix=dict(density=Q(int(row_ptr[-1]),nr*nv) if nr*nv else Q(0),row_nnz=row_degree,column_nnz=col_degree),
            domains=dict(variable_types=dict(self.types),row_senses=dict(self.senses),bounds=dict(self.bounds),unit_interval=self.unit_bounds),
            numerics=dict(absolute_coefficients=matrix,nonzero_rhs=rhs,common_coefficients=common,common_rhs=rhs_top,
                sign_counts=dict(positive=self.matrix.positive,negative=self.matrix.negative),bounds=dict(self.bounds),
                coefficient_dynamic_range=self.abs_max/self.abs_min if self.abs_min else None,
                near_zero_coefficients=dict(count=self.matrix.near_zero,threshold=NEAR_ZERO,comparison='0 < abs(coef) <= threshold'),
                rhs_convention='finite nonzero row bounds; equality once, ranges both endpoints, free rows none'),
            objective=dict(nonzeros=self.objective.count,variables=nv,coefficient_profile=costs,common_coefficients=cost_top,
                sign_counts=dict(positive=self.objective.positive,negative=self.objective.negative,zero=nv-self.objective.count),
                kind='constant' if not self.objective.count else 'uniform' if costs['min']==costs['max'] else 'varying'),
            local_row_shapes=shapes,names=names,
            presolve_cues=dict(fixed_vars=self.bounds['fixed'],singleton_rows=row_degree['singleton'],singleton_cols=col_degree['singleton'],
                isolated_rows=row_degree['isolated'],isolated_cols=col_degree['isolated']),
            scan=dict(budget_bytes=self.budget,level=0))
