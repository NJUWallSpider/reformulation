"""Storage facts shared by native opening, verification and export."""
from mipframe.store.artifacts import sha256
from mipframe.store.numbers import MIPFrameError


def dimensions_valid(store):
    return (
        len(store.row_ptr) == len(store.rows) + 1
        and len(store.col_ptr) == len(store.variables) + 1
        and int(store.row_ptr[-1]) == len(store.col_idx)
        and len(store.coeff_id) == len(store.col_idx)
        and int(store.col_ptr[-1]) == len(store.col_idx)
        and len(store.col_positions) == len(store.col_idx)
    )


def checks(store, *, hash_file=sha256):
    valid = True
    for name, digest in store.manifest['base_sha256'].items():
        path = store.path / name
        ok = path.exists() and hash_file(path) == digest
        valid &= ok
        yield dict(component='storage', item=name,
                   status='PROVED' if ok else 'REFUTED',
                   reason='' if ok else 'Base artifact checksum mismatch')
    if valid:
        ok = dimensions_valid(store)
        yield dict(component='storage', item='dimensions',
                   status='PROVED' if ok else 'REFUTED',
                   reason='' if ok else 'Invalid sparse model dimensions')


def require_integrity(store):
    for check in checks(store):
        if check['status'] != 'PROVED':
            raise MIPFrameError(f"{check['reason']}: {check['item']}")
