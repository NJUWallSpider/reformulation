"""Read-only CSR/CSC access without coefficient duplication or global row maps."""

import numpy as np
from mipframe.store.numbers import MIPFrameError


class SparseStore:
    def __init__(self, store):
        self._store = store

    def guard(self):
        if self._store.db is None:
            raise MIPFrameError("Model is closed")

    def blocks(self, axis, source, block_size):
        self.guard()
        m = self._store
        ptr = m.row_ptr if axis == "row" else m.col_ptr
        start, end = int(ptr[source]), int(ptr[source + 1])
        for begin in range(start, end, block_size):
            self.guard()
            stop = min(end, begin + block_size)
            positions = (
                np.arange(begin, stop, dtype=np.int64)
                if axis == "row"
                else m.col_positions[begin:stop]
            )
            targets = (
                m.col_idx[begin:stop]
                if axis == "row"
                else np.searchsorted(m.row_ptr, positions, side="right") - 1
            )
            yield targets, positions

    def position(self, row, var):
        self.guard()
        m = self._store
        left, end = int(m.row_ptr[row]), int(m.row_ptr[row + 1])
        right = end
        while left < right:
            mid = (left + right) // 2
            if int(m.col_idx[mid]) < var:
                left = mid + 1
            else:
                right = mid
        if left >= end or int(m.col_idx[left]) != var:
            raise KeyError((row, var))
        return left
