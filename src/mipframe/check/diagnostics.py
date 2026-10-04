"""Shared, pageable diagnostic results with deterministic per-result release."""
import json
import weakref
from mipframe.check.report import Report
from mipframe.frame import Frame, DType
from mipframe.frame.schema import encode
from mipframe.store.query_store import QueryStore


class _Lease:
    def __init__(self, store, ident):
        self.store, self.ident = store, ident
        self.owner = store.owner
        self._finalizer = weakref.finalize(self, _release, weakref.ref(store), ident)

    def guard(self):
        if not self._finalizer.alive:
            from mipframe.store.numbers import MIPFrameError
            raise MIPFrameError("Diagnostic result is closed")
        self.store.guard()

    def close(self):
        self._finalizer()


def _release(reference, ident):
    store = reference()
    if store is not None and store.db is not None:
        store.db.execute('DELETE FROM diagnostic_records WHERE result=?', (ident,))
        store.db.commit()


def diagnostic(owner, checks, *, kind, summary=None, recipe=None):
    """Consume checks incrementally; publication is atomic and owns no new connection."""
    owner.model
    store = getattr(owner, '_diagnostic_store', None)
    if store is None:
        from mipframe.options import options
        store = QueryStore(owner, options.query.memory_mb)
        try:
            store.db.execute('CREATE TABLE diagnostic_records(result INTEGER, ordinal INTEGER, payload TEXT, status TEXT, is_check INTEGER, is_violation INTEGER, PRIMARY KEY(result,ordinal))')
            store.db.commit()
        except BaseException:
            store.close()
            raise
        owner._diagnostic_store = store
        store._result_serial = 0
    store.guard()
    store._result_serial += 1
    ident = store._result_serial
    counts = dict(checked=0, failures=0, skipped=0, inconclusive=0)
    count = checks_count = violations_count = 0
    def append(record, *, is_check, is_violation):
        nonlocal count, violations_count
        count += 1
        violations_count += is_violation
        store.db.execute('INSERT INTO diagnostic_records VALUES(?,?,?,?,?,?)',
            (ident, count, json.dumps(record, default=encode), record['status'],
             int(is_check), int(is_violation)))
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
        store.db.commit()
    except BaseException:
        store.db.execute('DELETE FROM diagnostic_records WHERE result=?', (ident,))
        store.db.commit()
        raise
    lease = _Lease(store, ident)
    schema = {n:DType('str') for n in ('component','item','status','reason','detail')}
    def frame(role):
        def scan(fields, ctx):
            lease.guard()
            query = 'SELECT payload FROM diagnostic_records WHERE result=?'
            if role == 'violations': query += ' AND is_violation=1'
            if role == 'checks': query += ' AND is_check=1'
            for (payload,) in store.execute(query+' ORDER BY ordinal',(ident,)):
                lease.guard()
                row = json.loads(payload)
                yield {n:row[n] for n in fields}
        size = violations_count if role == 'violations' else checks_count if role == 'checks' else count
        f = Frame(schema, models=(owner,), source=scan, known_count=size)
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
