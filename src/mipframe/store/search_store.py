"""Array search workspaces, cancellation, and atomic verified object publication."""
from fractions import Fraction
from math import isfinite
from time import monotonic
from array import array
import hashlib
import numpy as np
from mipframe.execution.algorithm import ArrayAlgorithmStore
from mipframe.frame.schema import DType
from mipframe.check.algorithm import SearchBudgetExceeded


class SearchStopped(Exception):
    pass


def nonnegative(value,name,*,optional=False):
    if optional and value is None:return
    if type(value) is not int or value<0:raise ValueError(f'{name} must be a nonnegative integer')


class SearchStore(ArrayAlgorithmStore):
    def __init__(self,*args,**kwargs):
        self.started=monotonic();self.deadline=None;self.searching=False
        self.prepared=False;self.stopped=None;self.states=0;self.published_rows=0
        super().__init__(*args,**kwargs)
        self.disk_limit=self.work.disk_limit

    def configure(self, *, candidate, max_states, time_limit):
        nonnegative(max_states, "max_states", optional=True)
        if time_limit is not None and (
            isinstance(time_limit, bool)
            or not isinstance(time_limit, (int, float))
            or not isfinite(time_limit)
            or time_limit <= 0
        ):
            raise ValueError("time_limit must be finite and positive or None")
        self.candidate, self.max_states = candidate, max_states
        self.time_limit = time_limit
        self.deadline = None if time_limit is None else self.started + time_limit
        self.searching = True
        self.scope_info = "full declared domain"


    def stop(self, reason):
        self.stopped = reason
        if self.candidate and self.prepared:
            raise SearchStopped(reason)
        raise SearchBudgetExceeded(
            self.operation,
            reason,
            self.states,
            self.max_states,
            self.time_limit,
            self.scope_info,
        )


    def clock(self):
        if (
            self.searching
            and self.deadline is not None
            and monotonic() >= self.deadline
        ):
            self.stop("time_limit")


    def tick(self):
        self.guard()
        self.clock()
        if self.max_states is not None and self.states >= self.max_states:
            self.stop("max_states")
        self.states += 1


    def ready(self):
        self.clock()  # An incomplete input stage must never yield a candidate result.
        self.prepared = True


    def finish(self, *, found, retained, details=""):
        self.clock()
        if self.candidate:
            self.searching = False
        self.search_info = dict(
            algorithm=self.operation,
            algorithm_version=1,
            mode="candidate" if self.candidate else "exhaustive",
            stop_reason=self.stopped
            or ("heuristic_finished" if self.candidate else "exhausted"),
            states=self.states,
            found=found,
            retained=retained,
            scope=self.scope_info,
            details=details,
            memory_bytes=self.memory,
            disk_bytes=self.disk_limit,
            max_output_rows=self.limit,
            max_states=self.max_states,
            time_limit=None if self.time_limit is None else str(self.time_limit),
            ranking=(
                "best found; global optimum not established"
                if self.candidate
                else "complete within declared scope"
            ),
        )
        schema = {
            k: (
                DType("int", v is None)
                if k
                in (
                    "algorithm_version",
                    "states",
                    "found",
                    "retained",
                    "max_states",
                    "memory_bytes",
                    "disk_bytes",
                    "max_output_rows",
                )
                else DType("str", v is None)
            )
            for k, v in self.search_info.items()
        }
        return self.publish(schema, [self.search_info], limited=False)


    def publish(self,schema,rows,*,limited=True,**kwargs):
        def checked():
            for row in rows:
                self.clock()
                if limited:
                    self.published_rows+=1;self.enforce(self.published_rows)
                yield row
        result=super().publish(schema,checked(),limited=False,**kwargs)
        if hasattr(self,'search_info'):result._search_provenance=(dict(self.search_info),)
        return result


class Objects:
    """Ragged integer sequences; full contents decide identity and exact ranking."""
    def __init__(self,ctx,graph,kind,top=None):
        self.ctx,self.graph,self.kind,self.top=ctx,graph,kind,top
        from mipframe.data.intern import BytePool
        from mipframe.data.columns import ExactBuilder
        self.pool=BytePool(ctx.work);self.scores=ExactBuilder(ctx.work)
        self.found=0;self.output_rows=0

    def sequence(self,i):
        import struct
        for chunk in self.pool.chunks(int(i),4096):
            for (value,) in struct.iter_unpack('<Q',chunk):yield value

    def add(self,sequence):return self._add(sequence)

    def _add(self,sequence):
        import struct
        score=0;count=0;ctx=self.ctx
        def chunks():
            nonlocal score,count
            for raw in sequence:
                v=int(raw);ctx.clock();score+=self.graph.weight(v);count+=1
                yield struct.pack('<Q',v)
        ident=self.pool.intern(chunks())
        if ident<self.found:return
        ctx.clock()
        if self.top is None:ctx.enforce(self.output_rows+count+1)
        self.scores.append(score);self.found+=1;self.output_rows+=count+1

    def publish(self,details=''):
        ctx,g=self.ctx,self.graph
        if ctx.candidate:ctx.searching=False
        from mipframe.data.relations import stable_order
        from itertools import zip_longest
        def compare(a,b):
            x,y=self.scores[a],self.scores[b]
            if x!=y:return -1 if x>y else 1
            for x,y in zip_longest(self.sequence(a),self.sequence(b),fillvalue=-1):
                if x!=y:return -1 if x<y else 1
            return 0
        order=stable_order(ctx.work,self.found,compare)
        if self.top is not None:order=order[:self.top]
        status=ctx.finish(found=self.found,retained=len(order),details=details)
        kind=self.kind;size='size' if kind=='clique' else 'length'
        summary=ctx.publish({kind:DType('int'),size:DType('int'),'score':DType('number')},
            ({kind:rank,size:int(self.pool.lengths[int(i)])//8,'score':self.scores[i]} for rank,i in enumerate(order,1)),
            key=(kind,),relations={'search':status})
        schema={kind:DType('int')}
        if kind=='cycle':schema['position']=DType('int')
        schema['vertex']=g.typ
        rows=({kind:rank,**({'position':position} if kind=='cycle' else {}),'vertex':g.value(v)}
              for rank,i in enumerate(order,1) for position,v in enumerate(self.sequence(i)))
        return ctx.publish(schema,rows,key=(kind,'position' if kind=='cycle' else 'vertex'),
            domains={'vertex':g.domain()},relations={'summary':summary,'search':status})
