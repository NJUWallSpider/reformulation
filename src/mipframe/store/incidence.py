"""Internal integer sparse traversal; public relations are TermFrames."""

import re
import numpy as np
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame

_FAMILY = re.compile(r"(F|V)(0|[1-9][0-9]*)(?:@([0-9]+))?\Z")


def default_depth(owner, kind):
    return 2


class Target:
    def __init__(self, owner, kind, selection):
        owner.model
        cls = RowFrame if kind == "row" else VarFrame
        if isinstance(selection, str):
            match = _FAMILY.fullmatch(selection)
            if not match or match[1] != ("F" if kind == "row" else "V"):
                raise ValueError(f"family must identify a {kind} family")
            depth = (
                int(selection.rsplit("@", 1)[1])
                if "@" in selection
                else default_depth(owner, kind)
            )
            family = owner.family(selection, depth=depth)
            selection = family.rows if kind == 'row' else family.vars
        if not isinstance(selection, (RowFrame, VarFrame)):
            raise TypeError('Expected an entity Frame or a family label')
        if (selection._owner.uid, selection._owner.version) != (
            owner.uid,
            owner.version,
        ):
            raise ValueError("Selection requires the same model")
        if not isinstance(selection, cls):
            raise TypeError("Selection uses the wrong axis")
        self.frame = selection
        self.ids = selection.ids
        self.colors = None
        # A full, globally computed family can be filtered by its integer color.
        idx = self.ids
        fs = getattr(idx, "store", None)
        if hasattr(fs, "result"):
            global_store = fs.result._family_stores.get((kind, fs.depth))
            row = fs.db.execute(
                "SELECT color,n FROM families WHERE fid=?", (idx.fid,)
            ).fetchone()
            if fs is global_store and idx.ordinals == range(row[1]):
                self.colors = fs.result._store.array(fs.result._config, kind, fs.depth)
                self.color = row[0]

    def prepared(self, store):
        if (
            self.colors is not None
            or isinstance(self.ids, range)
            or (
                hasattr(self.ids, "store")
                and getattr(self.ids, "unique", False)
                and getattr(self.ids, "positions", None) is None
            )
        ):
            return self
        result = object.__new__(Target)
        result.frame, result.colors = self.frame, None
        table = store.table("ordinal INTEGER PRIMARY KEY, id INTEGER UNIQUE")
        store.db.executemany(
            f"INSERT OR IGNORE INTO {table}(id) VALUES(?)",
            ((int(i),) for i in self.ids),
        )
        from mipframe.store.query_store import SQLIndex

        result.ids = SQLIndex(
            store, f"SELECT id FROM {table} ORDER BY ordinal", unique=True
        )
        return result

    def mask(self, ids):
        self.frame._owner.model
        if self.colors is not None:
            return self.colors[ids] == self.color
        return np.fromiter(
            (int(i) in self.ids for i in ids), dtype=bool, count=len(ids)
        )
