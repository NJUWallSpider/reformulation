"""Reusable tables for sequential proof kernels; results are archived before reset."""

from collections import defaultdict
from mipframe.store.search_store import SearchStore


class ProofWorkspace(SearchStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scratch_tables = defaultdict(list)
        self._scratch_used = None

    def begin(self):
        if self._scratch_used is not None:
            raise RuntimeError("Proof workspace is already in use")
        self._scratch_used = defaultdict(int)

    def _reuse(self, key, create):
        if self._scratch_used is None:
            return create()
        index = self._scratch_used[key]
        tables = self._scratch_tables[key]
        if index == len(tables):
            tables.append(create())
        self._scratch_used[key] += 1
        return tables[index]

    def raw_table(self, definition):
        return self._reuse(("raw", definition), lambda: super(ProofWorkspace, self).raw_table(definition))

    def table(self, schema):
        key = ("frame", tuple(t.kind for t in schema.values()))
        return self._reuse(key, lambda: super(ProofWorkspace, self).table(schema))

    def reset(self):
        if self._scratch_used is not None:
            # Cached plans point into tables that are about to be emptied/reused.
            self.clear_caches()
            for key, count in self._scratch_used.items():
                for table in self._scratch_tables[key][:count]:
                    self.execute(f"DELETE FROM {table}")
            self._scratch_used = None
