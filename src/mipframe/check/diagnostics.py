"""Lazy diagnostic presentation artifacts, separate from numerical kernels."""
import json
import struct
import weakref
from pathlib import Path
from tempfile import TemporaryDirectory
from mipframe.check.report import Report
from mipframe.frame import Frame, DType
from mipframe.frame.schema import encode
from mipframe.store.numbers import MIPFrameError


class DiagnosticStore:
    def __init__(self,owner):
        self.owner=owner;self.temp=TemporaryDirectory(prefix='mipframe-diagnostics-')
        self.paths={};self.counts={};self.serial=0;self.closed=False
        from mipframe.store.lifecycle import register
        register(owner,self)
    def guard(self):
        self.owner.model
        if self.closed:raise MIPFrameError('Diagnostic result is closed')
    def create(self):
        self.guard();self.serial+=1;ident=self.serial
        path=Path(self.temp.name)/str(ident);self.paths[ident]=path
        return ident,path.open('wb')
    @property
    def record_count(self):return sum(self.counts.values())
    def release(self,ident):
        path=self.paths.pop(ident,None);self.counts.pop(ident,None)
        if path is not None:path.unlink(missing_ok=True)
    def close(self):
        if self.closed:return
        self.closed=True;self.paths.clear();self.counts.clear();self.temp.cleanup()


class _Lease:
    def __init__(self,store,ident):
        self.store,self.ident=store,ident;self.owner=store.owner
        self._finalizer=weakref.finalize(self,store.release,ident)
    def guard(self):
        if not self._finalizer.alive:raise MIPFrameError('Diagnostic result is closed')
        self.store.guard()
    def close(self):self._finalizer()


def diagnostic(owner,checks,*,kind,summary=None,recipe=None):
    owner.model
    store=getattr(owner,'_diagnostic_store',None)
    if store is None:store=owner._diagnostic_store=DiagnosticStore(owner)
    ident,stream=store.create()
    counts = dict(checked=0, failures=0, skipped=0, inconclusive=0)
    count = checks_count = violations_count = 0
    def append(record, *, is_check, is_violation):
        nonlocal count, violations_count
        count += 1
        violations_count += is_violation
        payload=json.dumps(record,default=encode,separators=(',',':')).encode('utf8')
        stream.write(struct.pack('<QB',len(payload),int(is_check)|2*int(is_violation)))
        stream.write(payload)
    try:
        for check in checks:
            status = check['status']
            if status not in ('PROVED', 'REFUTED', 'INCONCLUSIVE', 'SKIPPED'):
                raise ValueError('Unknown diagnostic status')
            checks_count += 1
            counts['checked'] += status != 'SKIPPED'
            counts['failures'] += status == 'REFUTED'
            counts['skipped'] += status == 'SKIPPED'
            counts['inconclusive'] += status == 'INCONCLUSIVE'
            record = dict(component=check.get('component',kind), item=check.get('item',''),
                          status=status, reason=check.get('reason',''), detail=check.get('detail',''))
            details = iter(check.get('diagnostics', ()))
            first = next(details, None)
            unresolved = status in ('REFUTED', 'INCONCLUSIVE')
            append(record, is_check=True, is_violation=unresolved and first is None)
            if first is not None:
                from itertools import chain
                for detail in chain((first,), details):
                    append(dict(record, reason=detail.get('reason',''), detail=detail.get('detail','')),
                           is_check=False, is_violation=unresolved)
        stream.close()
        store.counts[ident]=count
    except BaseException:
        stream.close();store.release(ident)
        raise
    lease = _Lease(store, ident)
    schema = {n:DType('str') for n in ('component','item','status','reason','detail')}
    def frame(role):
        def scan(fields, ctx):
            lease.guard()
            mask=2 if role=='violations' else 1 if role=='checks' else 3
            with store.paths[ident].open('rb') as data:
                while header:=data.read(9):
                    lease.guard()
                    length,flags=struct.unpack('<QB',header)
                    if role!='all' and not flags&mask:data.seek(length,1);continue
                    row=json.loads(data.read(length))
                    yield {n:row[n] for n in fields}
        size = violations_count if role == 'violations' else checks_count if role == 'checks' else count
        f = Frame(schema, models=(owner,), source=scan, known_count=size)
        f._array_scan = scan
        f._query.resource = lease
        return f
    status = ('REFUTED' if counts['failures'] else
              'INCONCLUSIVE' if counts['skipped'] or counts['inconclusive'] else 'PROVED')
    return Report(lease, status == 'PROVED',
        dict({'operation':kind, **(summary or {}), **counts}, diagnostics=violations_count),
        frame('violations'), status=status,
        conclusion=dict(kind=kind), scope=dict(kind=kind, arithmetic='exact', domain=None,
        complete=not counts['skipped'], snapshot=dict(uid=owner.uid,version=owner.version)),
        evidence={'checks':frame('checks'), 'details':frame('all')}, recipe=recipe)


def report_check(component, item, report):
    """One checked proposition, with any number of subordinate diagnostics."""
    return dict(component=component, item=item, status=report.status,
                detail=json.dumps(dict(report.summary), default=encode),
                diagnostics=(row.to_record() for row in report.violations))
