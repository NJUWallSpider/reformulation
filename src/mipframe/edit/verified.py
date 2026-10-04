"""Process-local receipts for completed verification, never restored from disk.

Only compact coordinate maps survive: no Edit, proof graph or source Model is
retained. The numerical snapshots remain immutable and must both still be live.
"""

from copy import deepcopy
from dataclasses import dataclass
from weakref import WeakKeyDictionary, ref
from mipframe.store.algorithm_store import AlgorithmStore


_receipts = WeakKeyDictionary()


@dataclass
class _VerifiedEdit:
    source: object
    target: object
    identity: tuple
    semantic: dict
    s: AlgorithmStore
    rows: str
    vars: str
    replaces: str


def remember(target, source, plan, *, memory_mb=128, disk_mb=None):
    store = AlgorithmStore((target,), memory_mb=memory_mb,
                           disk_mb=disk_mb, max_output_rows=None)
    try:
        tables = {}
        for attr, old, columns, schema in (
            ("rows", plan.rows, "id,ns,old", "id INTEGER PRIMARY KEY,ns INTEGER,old INTEGER"),
            ("vars", plan.vars, "id,ns,old", "id INTEGER PRIMARY KEY,ns INTEGER,old INTEGER"),
            ("replaces", "replaces", "old,new", "old INTEGER,new INTEGER,PRIMARY KEY(old,new)"),
        ):
            new = tables[attr] = store.raw_table(schema)
            for row in plan.s.execute(f"SELECT {columns} FROM {old}"):
                store.check_size(row)
                store.execute(f"INSERT INTO {new} VALUES({','.join('?' for _ in row)})", row)
        store.db.commit()
        store.disk_check()
        receipt = _VerifiedEdit(ref(source.model), ref(target.model),
            (source.uid, source.version, target.uid, target.version),
            deepcopy(plan.semantic), store, **tables)
    except BaseException:
        store.close()
        raise
    target._verified_edit = receipt
    _receipts[target] = ref(receipt)
    return receipt


def find(target, source):
    source_store, target_store = source.model, target.model
    reference = _receipts.get(target)
    receipt = reference() if reference is not None else None
    if (receipt is not None and receipt.s.db is not None
            and receipt.source() is source_store and receipt.target() is target_store
            and receipt.identity == (source.uid, source.version, target.uid, target.version)):
        return receipt
    return None
