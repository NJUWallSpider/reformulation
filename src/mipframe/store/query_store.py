"""Bounded, model-owned scratch storage for public sparse queries."""
from collections.abc import Sequence
from math import isfinite
from tempfile import TemporaryDirectory
import sqlite3
import weakref
from mipframe.store.lifecycle import close_store, close_connection, finalize_store

from mipframe.store.numbers import MIPFrameError


def memory_bytes(memory_mb):
    if isinstance(memory_mb, bool) or not isinstance(memory_mb, (int, float)) or not isfinite(memory_mb) or memory_mb * 1024**2 < 16384:
        raise ValueError('memory_mb must be finite and provide at least 16 KiB')
    return int(memory_mb * 1024**2)


class _Cursor:
    """Check lifecycle before advancing SQLite, including suspended iterators."""
    def __init__(self, store, cursor):
        self.store, self.cursor = store, cursor

    def __iter__(self):
        self.store.guard()
        return self

    def __next__(self):
        self.store.guard()
        return next(self.cursor)

    def fetchone(self):
        self.store.guard()
        return self.cursor.fetchone()


class QueryStore:
    def __init__(self, owner, memory_mb=128):
        owner.model
        self.owner, self.memory = owner, memory_bytes(memory_mb)
        self.db = self.temp = None
        try:
            self._open()
        except BaseException:
            self.close()
            raise

    def _open(self):
        self.temp = TemporaryDirectory(prefix='mipframe-query-')
        self.db = sqlite3.connect(self.temp.name + '/query.sqlite')
        self.db.execute('PRAGMA journal_mode=OFF')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute(f'PRAGMA cache_size=-{max(1, self.memory // 2048)}')
        self.db.execute(f'PRAGMA temp.cache_size=-{max(1, self.memory // 4096)}')
        self.db.execute('PRAGMA mmap_size=0')
        self._serial = 0
        from mipframe.store.lifecycle import register
        register(self.owner, self)

    def guard(self):
        self.owner.model
        if self.db is None:
            raise MIPFrameError('Query storage is closed')

    def execute(self, sql, parameters=()):
        self.guard()
        return _Cursor(self, self.db.execute(sql, parameters))

    def table(self, definition):
        self._serial += 1
        name = f'q{self._serial}'
        self.execute(f'CREATE TABLE {name} ({definition})')
        return name

    def close(self):
        close_store(self)

    def _close_connection(self):
        close_connection(self)

    def __del__(self):
        finalize_store(self)


class SQLIndex(Sequence):
    def __init__(self, store, query, parameters=(), positions=None, *, unique=False):
        self.store, self.query, self.parameters = store, query, parameters
        self.positions = positions
        self.unique = unique

    def __len__(self):
        self.store.guard()
        if self.positions is not None:
            return len(self.positions)
        return self.store.execute(f'SELECT count(*) FROM ({self.query})', self.parameters).fetchone()[0]

    def __iter__(self):
        self.store.guard()
        if self.positions is None or self.positions.step == 1:
            query, args = self.query, self.parameters
            if self.positions is not None:
                query = f'SELECT * FROM ({query}) LIMIT ? OFFSET ?'
                args = (*args, len(self.positions), self.positions.start)
            for (value,) in self.store.execute(query, args):
                self.store.guard()
                yield value
        else:
            # A strided/reversed result must remain one streaming SQL scan,
            # rather than issuing an OFFSET query for every source ID.
            positions = self.positions
            if not positions:
                return
            lower, upper = min(positions[0], positions[-1]), max(positions[0], positions[-1]) + 1
            direction = 'ASC' if positions.step > 0 else 'DESC'
            query = (f'SELECT id FROM (SELECT id,row_number() OVER ()-1 AS sequence_position '
                     f'FROM ({self.query})) WHERE sequence_position>=? AND sequence_position<? '
                     f'AND (sequence_position-?) % ? = 0 ORDER BY sequence_position {direction}')
            for (value,) in self.store.execute(query, (*self.parameters, lower, upper, positions.start, abs(positions.step))):
                yield value

    def _at(self, i):
        value = self.store.execute(f'SELECT * FROM ({self.query}) LIMIT 1 OFFSET ?', (*self.parameters, i)).fetchone()
        if value is None:
            raise IndexError(i)
        return value[0]

    def __getitem__(self, key):
        positions = self.positions if self.positions is not None else range(len(self))
        if isinstance(key, slice):
            return SQLIndex(self.store, self.query, self.parameters, positions[key], unique=self.unique)
        if type(key) is not int:
            raise TypeError('Use an integer position or slice')
        return self._at(positions[key])

    def __eq__(self, other):
        self.store.guard()
        if not isinstance(other, Sequence):
            return False
        return len(self) == len(other) and all(a == b for a, b in zip(self, other))

    __hash__ = None

    def __contains__(self, value):
        if self.positions is not None:
            return any(i == value for i in self)
        return self.store.execute(f'SELECT 1 FROM ({self.query}) WHERE id=? LIMIT 1', (*self.parameters, value)).fetchone() is not None


def stored_index(owner, values, memory_mb=128, *, unique=False):
    store = QueryStore(owner, memory_mb)
    try:
        table = store.table('ordinal INTEGER PRIMARY KEY, id INTEGER' + (' UNIQUE' if unique else ''))
        store.db.executemany(f'INSERT OR IGNORE INTO {table}(id) VALUES(?)', ((int(i),) for i in values))
        return SQLIndex(store, f'SELECT id FROM {table} ORDER BY ordinal', unique=unique)
    except BaseException:
        store.close()
        raise
