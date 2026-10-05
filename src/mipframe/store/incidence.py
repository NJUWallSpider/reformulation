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
            record=fs.find('color',idx.fid-1)
            row=(record[4],record[2])
            if fs is global_store and idx.ordinals == range(row[1]):
                self.colors = fs.result._store.array(fs.result._config, kind, fs.depth)
                self.color = row[0]

    def mask(self, ids):
        self.frame._owner.model
        if self.colors is not None:
            return self.colors[ids] == self.color
        return np.fromiter(
            (int(i) in self.ids for i in ids), dtype=bool, count=len(ids)
        )
