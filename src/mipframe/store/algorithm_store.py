"""One exact, bounded scratch context for an entire synchronous algorithm call."""

from dataclasses import replace
from contextlib import contextmanager
from math import isfinite
import sqlite3
import os
import stat
from pathlib import Path
from mipframe.frame.executor import Context, pack, unpack
from mipframe.frame.schema import DType, SchemaError
from mipframe.frame.materialized import stored_frame
from mipframe.check.algorithm import QueryTooLarge, DiskBudgetExceeded
from mipframe.check.report import CardinalityError, Report


class AlgorithmStore(Context):
    def __init__(
        self, models=(), *, memory_mb=128, max_output_rows=1_000_000, disk_mb=None
    ):
        if max_output_rows is not None and (
            type(max_output_rows) is not int or max_output_rows < 0
        ):
            raise ValueError("max_output_rows must be a nonnegative integer or None")
        if disk_mb is not None and (
            isinstance(disk_mb, bool)
            or not isinstance(disk_mb, (int, float))
            or not isfinite(disk_mb)
            or disk_mb <= 0
        ):
            raise ValueError("disk_mb must be finite and positive or None")
        self._fd_directory = next(
            (p for p in ("/proc/self/fd", "/dev/fd") if os.path.isdir(p)), None
        )
        if disk_mb is not None and self._fd_directory is None:
            raise NotImplementedError(
                "disk_mb requires file-descriptor accounting on this platform"
            )
        self._disk_baseline = set(self._open_files()) if disk_mb is not None else set()
        self._quota_error = None
        self._ticks = 0
        super().__init__(models, memory_mb)
        self.ensure_storage()
        self.limit = max_output_rows
        self.disk_limit = None if disk_mb is None else int(disk_mb * 1024**2)
        self.reserved = 0
        self.paths = set()
        if self.disk_limit is not None:
            page = self.db.execute("PRAGMA page_size").fetchone()[0]
            self.db.execute(f"PRAGMA max_page_count={max(1, self.disk_limit // page)}")
        self.operation = ""
        self.db.execute(f"PRAGMA cache_size=-{max(1, self.memory // 8192)}")
        self.db.execute(f"PRAGMA temp.cache_size=-{max(1, self.memory // 8192)}")

    @contextmanager
    def reader(self, store):
        # A materialized input keeps its own connection, but borrows this call's
        # cache allowance while scanning. Do not add its original 128 MiB budget.
        if store is self:
            yield
            return
        store.guard()
        previous = store.db.execute("PRAGMA cache_size").fetchone()[0]
        store.db.execute(f"PRAGMA cache_size=-{max(1, self.memory // 8192)}")
        try:
            yield
        finally:
            if store.db is not None:
                store.db.execute(f"PRAGMA cache_size={previous}")

    def execute(self, sql, args=()):
        try:
            self._ticks += 1
            if self._ticks % 256 == 0:
                self.disk_check()
            return super().execute(sql, args)
        except sqlite3.OperationalError as exc:
            if self._quota_error is not None:
                error, self._quota_error = self._quota_error, None
                raise error from exc
            if "full" in str(exc).lower() and self.disk_limit is not None:
                raise DiskBudgetExceeded(
                    f"Algorithm disk budget exceeded: {self.disk_limit} bytes"
                ) from exc
            raise

    def append(self, table, schema, rows):
        # All insertions pass through budget-aware execute, including upstream Frame stages.
        marks = ",".join("?" for _ in range(len(schema) + 1))
        for row in rows:
            self.check_size(row)
            self.execute(
                f"INSERT INTO {table} VALUES({marks})",
                (None, *(pack(row[n], t) for n, t in schema.items())),
            )

    def raw_table(self, definition):
        self.serial += 1
        name = f"a{self.serial}"
        self.execute(f"CREATE TABLE {name}({definition})")
        return name

    def reserve(self, amount):
        if amount + self.reserved > self.memory // 2:
            return False
        self.reserved += amount
        return True

    def enforce(self, count):
        if self.limit is not None and count > self.limit:
            raise QueryTooLarge(self.limit, count)

    def _open_files(self):
        result = {}
        if self._fd_directory is None:
            return result
        for name in os.listdir(self._fd_directory):
            try:
                info = os.fstat(int(name))
            except (ValueError, OSError):
                continue
            if stat.S_ISREG(info.st_mode):
                result[(info.st_dev, info.st_ino)] = info.st_size
        return result

    def _progress(self):
        self.guard()
        # SQLite's external sorter may hold already-unlinked temporary files;
        # their descriptors, not directory names, account for that scratch space.
        try:
            if hasattr(self, "disk_limit"):
                self.disk_check()
        except DiskBudgetExceeded as exc:
            self._quota_error = exc
            return 1
        return 0

    @contextmanager
    def output_budget(self, store):
        """Charge newly retained output, including growth in an existing store."""
        previous = getattr(store, "_output_check", None)
        path = Path(store.temp.name) / "query.sqlite"
        info = path.stat()
        existing = (info.st_dev, info.st_ino) in self._disk_baseline
        page = store.db.execute("PRAGMA page_size").fetchone()[0]
        initial = store.db.execute("PRAGMA page_count").fetchone()[0] * page

        def check():
            if self.disk_limit is not None:
                logical = store.db.execute("PRAGMA page_count").fetchone()[0] * page
                extra = max(0, logical - (initial if existing else path.stat().st_size))
                self.disk_check(extra_bytes=extra)

        store._output_check = check
        try:
            yield
            check()
        finally:
            store._output_check = previous

    def disk_check(self, *, extra_bytes=0):
        output_check = getattr(self, "_output_check", None)
        if output_check is not None:
            output_check()
        if self.disk_limit is None:
            return
        files = {
            key: n
            for key, n in self._open_files().items()
            if key not in self._disk_baseline
        }
        for root in (Path(self.temp.name), *(Path(p) for p in self.paths)):
            for path in root.rglob("*"):
                if path.is_file():
                    info = path.stat()
                    files[(info.st_dev, info.st_ino)] = info.st_size
        used = sum(files.values()) + extra_bytes
        if used > self.disk_limit:
            raise DiskBudgetExceeded(
                f"Algorithm disk budget exceeded: {used} > {self.disk_limit} bytes"
            )

    def publish(
        self, schema, rows, *, key=None, domains=None, relations=None, limited=True
    ):
        table = self.table(schema)
        count = 0

        def checked():
            nonlocal count
            for row in rows:
                count += 1
                if limited:
                    self.enforce(count)
                yield row

        self.append(table, schema, checked())
        self.disk_check()
        result = stored_frame(
            self, table, schema, key=key, domains=domains, relations=relations
        )
        result._known_count = count
        return result

    def invalid(self, message, schema, rows, *, relations=None, error=CardinalityError):
        violations = self.publish(schema, rows, relations=relations, limited=False)
        if not len(violations):
            return
        report = Report(
            self,
            False,
            {
                "operation": self.operation,
                "scope": "full declared inputs",
                "violations": len(violations),
            },
            violations,
        )
        if issubclass(error, CardinalityError):
            raise error(message, report)
        raise error(report)

    def nonnull(self, table, schema, names):
        columns = tuple(schema)
        where = " OR ".join(f"c{columns.index(n)} IS NULL" for n in names)
        query = (
            f"SELECT "
            + ",".join(f"c{i}" for i in range(len(schema)))
            + f" FROM {table} WHERE {where} ORDER BY ordinal"
        )
        if self.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1").fetchone():
            self.invalid(
                "Identity columns cannot contain missing values",
                schema,
                self.rows(query, schema),
            )

    def unique(self, table, schema, names):
        columns = tuple(schema)
        key = ",".join(f"c{columns.index(n)}" for n in names)
        query = f"SELECT {key},count(*) FROM {table} GROUP BY {key} HAVING count(*)>1"
        if self.execute(f"SELECT 1 FROM ({query}) LIMIT 1").fetchone():
            out = {n: schema[n] for n in names}
            label = "count"
            while label in out:
                label = "_" + label
            out[label] = DType("int")
            self.invalid(
                "Duplicate keys; use distinct() or an explicit aggregation",
                out,
                self.rows(query, out),
            )

    def stage_checked(self, frame, names, *, unique=(), nonnull=()):
        if len(set(names)) != len(names) or not set(names) <= set(frame.schema):
            raise SchemaError("Expected distinct, existing column names")
        table, schema = self.stage(frame, tuple(names))
        if nonnull:
            self.nonnull(table, schema, nonnull)
        if unique:
            self.unique(table, schema, unique)
        return table, schema


def key_column(frame):
    if getattr(frame, "_entity_axis", None):
        return frame._entity_axis
    if len(frame.schema) != 1:
        raise SchemaError("A domain must be an entity Frame or a single-column Frame")
    return next(iter(frame.schema))


class KeyIndex:
    """Typed original keys mapped to deterministic, one-based integer IDs."""

    def __init__(self, ctx, typ, query, *, frame=None):
        self.ctx, self.typ = ctx, replace(typ, nullable=False)
        self.table = ctx.table({"value": self.typ})
        ctx.execute(
            f"INSERT INTO {self.table}(c0) SELECT DISTINCT value FROM ({query}) ORDER BY value"
        )
        ctx.execute(f"CREATE UNIQUE INDEX {self.table}_value ON {self.table}(c0)")
        self.n = ctx.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0]
        self.frame = frame

    @classmethod
    def domain(cls, ctx, typ, frame, observed):
        if frame is None:
            return cls(ctx, typ, observed)
        name = key_column(frame)
        if not typ.compatible(frame.schema[name]):
            raise SchemaError("Domain key type or snapshot differs")
        table, schema = ctx.stage_checked(frame, (name,), nonnull=(name,))
        return cls(ctx, typ, f"SELECT c0 AS value FROM {table}", frame=frame)

    def validate(self, query, label="value"):
        schema = {label: self.typ}
        bad = f"SELECT DISTINCT x.value FROM ({query}) x WHERE NOT EXISTS(SELECT 1 FROM {self.table} d WHERE d.c0=x.value) ORDER BY x.value"
        if self.ctx.execute(f"SELECT 1 FROM ({bad}) LIMIT 1").fetchone():
            self.ctx.invalid(
                "Key outside the declared domain", schema, self.ctx.rows(bad, schema)
            )

    def value(self, ident):
        raw = self.ctx.execute(
            f"SELECT c0 FROM {self.table} WHERE ordinal=?", (ident,)
        ).fetchone()
        if raw is None:
            raise KeyError(ident)
        return unpack(raw[0], self.typ)

    def lookup(self, value):
        row = self.ctx.execute(
            f"SELECT ordinal FROM {self.table} WHERE c0=?", (pack(value, self.typ),)
        ).fetchone()
        if row is None:
            raise KeyError(value)
        return row[0]

    def entries(self):
        for ident, value in self.ctx.execute(
            f"SELECT ordinal,c0 FROM {self.table} ORDER BY ordinal"
        ):
            yield ident, unpack(value, self.typ)

    def selection(self, frame):
        if frame is None:
            return self.table
        name = key_column(frame)
        if not self.typ.compatible(frame.schema[name]):
            raise SchemaError("Selection key type or snapshot differs")
        table, schema = self.ctx.stage_checked(frame, (name,), nonnull=(name,))
        self.validate(f"SELECT c0 AS value FROM {table}", name)
        out = self.ctx.raw_table("ordinal INTEGER PRIMARY KEY")
        self.ctx.execute(
            f"INSERT INTO {out} SELECT DISTINCT d.ordinal FROM {self.table} d JOIN {table} x ON d.c0=x.c0"
        )
        return out

    def as_frame(self, name):
        result = stored_frame(self.ctx, self.table, {name: self.typ}, key=(name,))
        result._known_count = self.n
        return result


class Membership:
    def __init__(
        self, ctx, frame, set, member, *, sets=None, vertices=None, value=None
    ):
        fields = (set, member) if value is None else (set, member, value)
        table, schema = ctx.stage_checked(
            frame, fields, unique=(set, member), nonnull=(set, member)
        )
        self.ctx, self.source, self.set_name, self.member_name = ctx, frame, set, member
        self.sets = KeyIndex.domain(
            ctx,
            schema[set],
            sets if sets is not None else frame.domains.get(set),
            f"SELECT c0 AS value FROM {table}",
        )
        self.members = KeyIndex.domain(
            ctx,
            schema[member],
            vertices if vertices is not None else frame.domains.get(member),
            f"SELECT c1 AS value FROM {table}",
        )
        self.sets.validate(f"SELECT c0 AS value FROM {table}", set)
        self.members.validate(f"SELECT c1 AS value FROM {table}", member)
        self.value_type = None if value is None else schema[value]
        self.edges = ctx.raw_table("s INTEGER,m INTEGER,pos INTEGER,PRIMARY KEY(s,m)")
        ctx.execute(
            f"INSERT INTO {self.edges} SELECT s.ordinal,m.ordinal,t.ordinal FROM {table} t JOIN {self.sets.table} s ON s.c0=t.c0 JOIN {self.members.table} m ON m.c0=t.c1"
        )
        ctx.execute(f"CREATE INDEX {self.edges}_reverse ON {self.edges}(m,s)")
        self.raw = table
        self.sizes = ctx.raw_table("s INTEGER PRIMARY KEY,n INTEGER")
        ctx.execute(
            f"INSERT INTO {self.sizes} SELECT s.ordinal,count(e.m) FROM {self.sets.table} s LEFT JOIN {self.edges} e ON e.s=s.ordinal GROUP BY s.ordinal"
        )
        self.frequency = ctx.raw_table("m INTEGER PRIMARY KEY,n INTEGER")
        ctx.execute(
            f"INSERT INTO {self.frequency} SELECT m,count(*) FROM {self.edges} GROUP BY m"
        )

    def size(self, s):
        return self.ctx.execute(
            f"SELECT n FROM {self.sizes} WHERE s=?", (s,)
        ).fetchone()[0]

    def intersection(self, a, b):
        return self.ctx.execute(
            f"SELECT count(*) FROM {self.edges} a JOIN {self.edges} b ON a.m=b.m WHERE a.s=? AND b.s=?",
            (a, b),
        ).fetchone()[0]

    def stream(self, s, with_value=False):
        if with_value:
            for m, raw in self.ctx.execute(
                f"SELECT e.m,t.c2 FROM {self.edges} e JOIN {self.raw} t ON t.ordinal=e.pos WHERE e.s=? ORDER BY e.m",
                (s,),
            ):
                yield (m, unpack(raw, self.value_type))
        else:
            for (m,) in self.ctx.execute(
                f"SELECT m FROM {self.edges} WHERE s=? ORDER BY m", (s,)
            ):
                yield (m,)

    def pairs(self, frame):
        table, schema = self.ctx.stage_checked(
            frame,
            ("left", "right"),
            unique=("left", "right"),
            nonnull=("left", "right"),
        )
        for name in ("left", "right"):
            if not self.sets.typ.compatible(schema[name]):
                raise SchemaError("Pair endpoints must identify this set domain")
        self.sets.validate(f"SELECT c0 AS value FROM {table}", "left")
        self.sets.validate(f"SELECT c1 AS value FROM {table}", "right")
        out = self.ctx.raw_table("a INTEGER,b INTEGER,PRIMARY KEY(a,b)")
        self.ctx.execute(
            f"INSERT INTO {out} SELECT l.ordinal,r.ordinal FROM {table} p JOIN {self.sets.table} l ON l.c0=p.c0 JOIN {self.sets.table} r ON r.c0=p.c1"
        )
        return out
