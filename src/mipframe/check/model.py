"""Integrity reports for immutable numerical artifacts and prepared indexed runs."""
from mipframe.store.artifacts import sha256
from mipframe.check.diagnostics import diagnostic, report_check
from mipframe.store.integrity import checks as storage_checks


def verify(owner, *, runs=None):
    m=owner.model
    runs=tuple(getattr(owner,'_indexed_results',{})) if runs is None else tuple(runs)
    summary=dict(model_version=owner.version,rows_verified=0,nonzeros_verified=0,indexed_runs=len(runs))
    def checks():
        valid=True
        for check in storage_checks(m, hash_file=sha256):
            valid &= check['status'] == 'PROVED'
            yield check
        if valid:
            summary.update(rows_verified=len(m.rows),nonzeros_verified=len(m.col_idx))
        for run in runs:
            if not valid:
                yield dict(component='indexed',item=run,status='SKIPPED',reason='Base storage integrity failed')
                continue
            indexed=getattr(owner,'_indexed_results',{}).get(run)
            if indexed is None:
                from mipframe.frame import SchemaError
                raise SchemaError('Prepared indexed run is unavailable')
            yield report_check('indexed', run, indexed.verify())
    return diagnostic(owner,checks(),kind='model_integrity',
        summary=summary,
        recipe=dict(kind='model_integrity',runs=list(runs)))
