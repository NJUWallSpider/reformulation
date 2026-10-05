"""Exact family observations over sparse arrays, without relational workspaces."""
from contextlib import contextmanager
from fractions import Fraction
from math import inf


class Observations:
    def __init__(self):
        from mipframe.data.runtime import WorkContext
        from mipframe.data.relations import RelationBuilder
        self.work=WorkContext();self.dual=False;self.population=0
        self.term_builder=RelationBuilder(self.work,dict(o='int',member='int',sign='int',neighbor='int',value='number'))
        self.bound_builder=RelationBuilder(self.work,dict(o='int',member='int',lb='number',ub='number',objective='number'))
        self.source_builder=RelationBuilder(self.work,dict(o='int',member='int',row='int',factor='number',scale='number'))
    def finish(self):
        self.terms=self.term_builder.finish();self.bounds=self.bound_builder.finish();self.sources=self.source_builder.finish()
        self.populations=(0,1) if self.dual else (0,)
    def source_records(self):
        order=self.sources.order(('o','member','row'))
        try:
            for i in order:
                r=self.sources.row(int(i));yield (r['o'],r['member'],r['row']),(r['factor'],r['scale'])
        finally:self.work.release(order)
    def distributions(self,orientation,kind,filters):
        from mipframe.data.relations import build,Relation,ColumnView
        def include(name,value):return name not in filters or value in filters[name]
        def histogram(work,values,missing=0):
            table=build(work,{'value':'number'},({'value':v} for v in values))
            if missing:yield 0,missing
            for (value,),group in table.groups(('value',)):yield value,len(group)
        for label in ('lb','ub','objective'):
            if not (include('quantity','value') and include('role',label) and include('coefficient',None)):continue
            with self.work.fork() as work:
                values=(r[label] for r in self.bounds.records() if r['o']==orientation and r[label] is not None)
                yield label,'value',None,histogram(work,values)
        for (o,sign,neighbor),ids in self.terms.groups(('o','sign','neighbor')):
            if o!=orientation:continue
            role=f'{"+" if sign>0 else "-"}{"V" if kind=="row" else "Rstate"}{neighbor}'
            if not include('role',role):continue
            with self.work.fork() as work:
                terms=Relation({k:ColumnView(v,ids) for k,v in self.terms.columns.items()},len(ids),work)
                if include('quantity','coefficient') and include('coefficient',None):
                    yield role,'coefficient',None,histogram(work,terms.columns['value'])
                def multiplicities(table):
                    counts=build(work,{'n':'int'},({'n':len(group)} for _,group in table.groups(('member',))))
                    return histogram(work,counts.columns['n'],self.population-len(counts))
                if include('quantity','multiplicity') and include('coefficient',None):
                    yield role,'multiplicity',None,multiplicities(terms)
                if include('quantity','coefficient_multiplicity'):
                    for (value,),group in terms.groups(('value',)):
                        if include('coefficient',value):
                            sub=Relation({'member':ColumnView(terms.columns['member'],group)},len(group),work)
                            yield role,'coefficient_multiplicity',value,multiplicities(sub)


@contextmanager
def observations(result,kind,depth,members,basis):
    data=Observations()
    try:
        _build(data,result,kind,depth,members,basis);data.finish();yield data
    finally:data.work.close()


def _build(data,result,kind,depth,members,basis):
    if basis not in ('raw','canonical','scale_normalized'):raise ValueError('Unknown numeric basis')
    store=result._store;store.guard();m=store.owner.model;config=result._config
    neighbor_depth=max(0,depth-1)
    vc=store.array(config,'var',neighbor_depth)
    pos,neg=(store.array(config,k,depth) for k in ('pos','neg'))
    rp,rn=(store.array(config,k,neighbor_depth) for k in ('pos','neg'))
    direction_cache=data.work.array(m.capacity('row'),'<i1',zero=True)

    def row_info(row):
        scale,lo,hi,dual=store.row_info(config,row)
        directions=((0,1),)
        if dual:
            if not direction_cache[row]:
                first=1 if pos[row]<=neg[row] else -1
                if pos[row]==neg[row]:
                    from mipframe.data.relations import build
                    with data.work.fork() as work:
                        tables=[build(work,{'neighbor':'int','value':'number'},
                                      (dict(neighbor=int(vc[v]),value=factor*c) for v,c in m.iter_terms(row))) for factor in (1,-1)]
                        def contents(table):
                            for key,group in table.groups(('neighbor','value')):yield (*key,len(group))
                        for a,b in zip(contents(tables[0]),contents(tables[1])):
                            if a!=b:first=1 if a<b else -1;break
                direction_cache[row]=first
            first=direction_cache[row];directions=((0,first),(1,-first))
        return scale,lo,hi,directions

    def observe(member,row,terms,info):
        scale,lo,hi,directions=info
        canonical=1 if scale>0 else -1
        # A variable observation has one incident edge; row observations stream twice for dual orientation.
        for orientation,reverse in directions:
            factor=Fraction(1) if basis=='raw' else Fraction(reverse)/scale if basis=='scale_normalized' else Fraction(reverse*canonical)
            data.source_builder.append(dict(o=orientation,member=member,row=row,factor=factor,scale=scale))
            data.dual=data.dual or orientation==1
            for variable,value in terms():
                sign=1 if value*canonical*reverse>0 else -1
                neighbor=int(vc[variable]) if kind=='row' else int(rp[row] if reverse==1 else rn[row])
                data.term_builder.append(dict(o=orientation,member=member,sign=sign,neighbor=neighbor,value=value*factor))
            if kind=='row':
                if basis=='raw':
                    _,_,lower,upper,_=m.rows[row];lower,upper=m.num(lower),m.num(upper)
                else:
                    multiplier=abs(scale) if basis=='canonical' else 1
                    lower=None if lo is None else lo*multiplier
                    upper=None if hi is None else hi*multiplier
                data.bound_builder.append(dict(o=orientation,member=member,lb=-inf if lower is None else lower,ub=inf if upper is None else upper,objective=None))

    for member in members.ids:
        data.population+=1
        if kind=='row':
            observe(member,member,lambda:m.iter_terms(member),row_info(member))
        else:
            _,_,_,lo,hi,obj,_=m.variables[member]
            lower,upper,objective=m.num(lo),m.num(hi),m.num(obj)
            data.bound_builder.append(dict(o=0,member=member,lb=-inf if lower is None else lower,ub=inf if upper is None else upper,objective=objective))
            for row,c in m.iter_column(member):observe(member,row,lambda:((member,c),),row_info(row))
    if data.dual and kind=='var':
        for member in members.ids:
            _,_,_,lo,hi,obj,_=m.variables[member]
            lower,upper,objective=m.num(lo),m.num(hi),m.num(obj)
            data.bound_builder.append(dict(o=1,member=member,lb=-inf if lower is None else lower,ub=inf if upper is None else upper,objective=objective))
