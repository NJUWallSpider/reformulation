"""Shared bounded execution context, exact SQLite spill and sparse reads."""

from fractions import Fraction
from tempfile import TemporaryDirectory
import sqlite3
import sys
import weakref
from mipframe.store.lifecycle import close_store, close_connection, finalize_store
from mipframe.store.query_store import memory_bytes
from mipframe.store.numbers import MIPFrameError
from mipframe.store.numbers import compare_numeric_text
from mipframe.store.numbers import exact_binary


class QueryMemoryError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.QueryMemoryError")
    MIPFRAME HELP END
    """
    pass


class MaterializationLimitError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.MaterializationLimitError")
    MIPFRAME HELP END
    """
    pass


def size(value):
    n = sys.getsizeof(value)
    if isinstance(value, dict):
        return n + sum(size(k) + size(v) for k, v in value.items())
    if isinstance(value, (tuple, list)):
        return n + sum(map(size, value))
    if isinstance(value, Fraction):
        return n + size(value.numerator) + size(value.denominator)
    return n


def numeric(value):
    if value is None:
        return None
    if value in ("inf", "-inf"):
        return float(value)
    return Fraction(value)


def pack(value, typ):
    if value is None:
        return None
    if typ.kind in ("number", "int"):
        return str(value)
    if typ.kind == "bool":
        return int(value)
    return value


def unpack(value, typ):
    if value is None:
        return None
    if typ.kind == "number":
        return numeric(value)
    if typ.kind == "int":
        return int(value)
    if typ.kind == "bool":
        return bool(value)
    return value


class Context:
    @property
    def query_context(self):
        """The execution context used by Frame operators."""
        return self

    def __init__(self, models=(), memory_mb=None):
        from mipframe.options import options

        self.memory = memory_bytes(
            options.query.memory_mb if memory_mb is None else memory_mb
        )
        self.models = tuple(models)
        self.db = self.temp = None
        self._closed = False
        self.serial = 0
        self.staged = {}
        self.members = {}
        self.retained = False
        self._aggregate_error = None
        from mipframe.store.lifecycle import register
        for model in self.models:
            register(model, self)

    def ensure_storage(self):
        self.guard()
        if self.db is None:
            try:
                self._open()
            except BaseException:
                self.close()
                raise

    def _open(self):
        self.temp = TemporaryDirectory(prefix="mipframe-frame-")
        self.db = sqlite3.connect(self.temp.name + "/query.sqlite")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA mmap_size=0")
        self.db.execute(f"PRAGMA cache_size=-{max(1, self.memory // 4096)}")
        self.db.execute(f"PRAGMA temp.cache_size=-{max(1, self.memory // 4096)}")
        reference = weakref.ref(self)

        def progress():
            context = reference()
            return 1 if context is None else context._progress()

        self.db.set_progress_handler(progress, 10000)
        self.db.create_collation(
            "EXACT", compare_numeric_text
        )
        context = weakref.proxy(self)

        class Sum:
            def __init__(self):
                self.total = Fraction(0)

            def step(self, v):
                if v is not None:
                    try:
                        self.total = exact_binary("add", self.total, numeric(v))
                        context.check_size(self.total)
                    except MIPFrameError as exc:
                        context._aggregate_error = exc
                        raise

            def finalize(self):
                return str(self.total)

        self.db.create_aggregate("exact_sum", 1, Sum)

    def _progress(self):
        self.guard()
        return 0

    def guard(self):
        for model in self.models:
            model.model
        if self._closed:
            raise MIPFrameError("Query storage is closed")

    def check_size(self, row):
        if size(row) > self.memory // 2:
            raise QueryMemoryError(
                "A query value or record exceeds the working-memory budget"
            )

    def execute(self, sql, args=()):
        self.ensure_storage()
        return self.db.execute(sql, args)

    def table(self, schema):
        self.serial += 1
        name = f"f{self.serial}"
        types = {
            "ref": "INTEGER",
            "draft_ref": "INTEGER",
            "bool": "INTEGER",
            "int": "TEXT COLLATE EXACT",
            "number": "TEXT COLLATE EXACT",
            "str": "TEXT",
            "enum": "TEXT",
            "null": "TEXT",
        }
        fields = ",".join(
            f"c{i} {types[t.kind]}" for i, t in enumerate(schema.values())
        )
        self.execute(
            f"CREATE TABLE {name}(ordinal INTEGER PRIMARY KEY{',' if fields else ''}{fields})"
        )
        return name

    def index(self, table, columns, *, unique=False):
        """Reuse an index for a staged table, including across workspace resets."""
        columns = tuple(columns)
        name = f"{table}_{'unique' if unique else 'index'}_{'_'.join(columns)}"
        self.execute(
            f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {name} "
            f"ON {table}({','.join(columns)})"
        )

    def append(self, table, schema, rows):
        self.ensure_storage()
        names = tuple(schema)
        marks = ",".join("?" for _ in range(len(names) + 1))

        def encoded():
            for row in rows:
                self.guard()
                self.check_size(row)
                yield (None, *(pack(row[n], schema[n]) for n in names))

        self.db.executemany(f"INSERT INTO {table} VALUES({marks})", encoded())

    def stage(self, frame, fields=None):
        # Query plans must stay alive while id-based staging and membership caches refer to them.
        if not hasattr(self, "_staged_frames"):
            self._staged_frames = {}
        self._staged_frames[id(frame)] = frame
        fields = tuple(frame.schema if fields is None else fields)
        key = (id(frame), fields)
        if key not in self.staged:
            schema = {n: frame.schema[n] for n in fields}
            name = self.table(schema)
            self.append(name, schema, frame._raw(fields, self))
            self.staged[key] = (name, schema)
        return self.staged[key]

    def rows(self, query, schema, args=()):
        try:
            for values in self.execute(query, args):
                self.guard()
                yield {
                    name: unpack(value, typ)
                    for (name, typ), value in zip(schema.items(), values)
                }
        except sqlite3.OperationalError:
            # SQLite hides exceptions raised in aggregate callbacks. Preserve
            # the exact-arithmetic/resource diagnostic for public Frame reads.
            if self._aggregate_error is not None:
                error, self._aggregate_error = self._aggregate_error, None
                raise error from None
            raise

    def contains(self, frame, ident):
        if id(frame) not in self.members:
            key = frame.key[0]
            table, schema = self.stage(frame, (key,))
            self.index(table, ("c0",))
            self.members[id(frame)] = (table, schema[key])
        table, typ = self.members[id(frame)]
        return (
            self.execute(
                f"SELECT 1 FROM {table} WHERE c0=? LIMIT 1", (pack(ident, typ),)
            ).fetchone()
            is not None
        )

    def clear_caches(self):
        self.staged.clear()
        self.members.clear()
        self.__dict__.pop("_staged_frames", None)

    def close(self):
        self._closed = True
        close_store(self)
        if hasattr(self, "staged"):
            self.clear_caches()

    def _close_connection(self):
        self._closed = True
        close_connection(self)

    def __del__(self):
        finalize_store(self)


def blocking(frame, fields, context):
    """All non-streaming relational operations spill through this same context."""
    op, p = frame._op, frame._params
    parent = frame._parents[0]
    if op == "join":
        yield from join(frame, fields, context)
        return
    if op == "distinct":
        needed = tuple(parent.schema)
    elif op == "sort":
        needed = tuple(dict.fromkeys((*fields, *p["by"])))
    elif op in ("slice", "sample"):
        needed = fields
    else:
        needed = tuple(
            dict.fromkeys(
                (
                    *p["keys"],
                    *(
                        d
                        for a in p["aggregates"].values()
                        if a.expr is not None
                        for d in a.expr.dependencies
                    ),
                )
            )
        )
    table, schema = context.stage(parent, needed)
    names = tuple(schema)
    c = lambda n: f"c{names.index(n)}"
    outputs = (",".join(c(n) for n in fields) or "1") if op != "group" else ""
    if op == "sort":
        keys = p["by"]
        order = ",".join(
            f"{c(n)} IS NULL, {c(n)} {'DESC' if d else 'ASC'}"
            for n, d in zip(keys, p["descending"])
        )
        query = f"SELECT {outputs} FROM {table} ORDER BY {order},ordinal"
    elif op == "distinct":
        group = ",".join(c(n) for n in names)
        query = (f"SELECT {outputs} FROM {table} GROUP BY {group} ORDER BY min(ordinal)"
                 if group else f"SELECT 1 FROM {table} LIMIT 1")
    elif op == "slice":
        n = context.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        selected = range(n)[p["slice"]]
        for pos in selected:
            yield from context.rows(
                f"SELECT {outputs} FROM {table} WHERE ordinal=?",
                {n: frame.schema[n] for n in fields},
                (pos + 1,),
            )
        return
    elif op == "sample":
        import random

        n = context.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        amount = p["n"]
        if amount > n:
            raise ValueError("Sample larger than population")
        # External random ranking keeps Python state independent of sample size.
        rng = random.Random(p["seed"])
        from mipframe.frame.schema import DType
        rank_schema = {"position": DType("int"), "rank": DType("int")}
        ranks = context.table(rank_schema)
        context.append(
            ranks, rank_schema,
            ({"position": i, "rank": rng.getrandbits(128)} for i in range(1, n + 1)),
        )
        query = f"SELECT {outputs} FROM {table} WHERE ordinal IN (SELECT CAST(c0 AS INTEGER) FROM {ranks} ORDER BY c1,ordinal LIMIT {amount}) ORDER BY ordinal"
    elif op == "group":
        # Evaluate aggregate expressions once into scalar spill columns.
        from mipframe.frame.schema import DType

        expanded = {n: schema[n] for n in p["keys"]}
        for n, a in p["aggregates"].items():
            expanded[n] = DType("int") if a.expr is None else a.expr.type(parent.schema)
        stage = context.table(expanded)

        def values():
            for row in context.rows(
                f"SELECT {','.join(c(n) for n in names) or '1'} FROM {table} ORDER BY ordinal",
                schema,
            ):
                yield {
                    **{k: row[k] for k in p["keys"]},
                    **{
                        k: 1 if a.expr is None else a.expr.evaluate(row, context)
                        for k, a in p["aggregates"].items()
                    },
                }

        context.append(stage, expanded, values())
        enames = tuple(expanded)
        expressions = {n: f"c{enames.index(n)}" for n in p["keys"]}
        for n, a in p["aggregates"].items():
            col = f"c{enames.index(n)}"
            expressions[n] = (
                f"count(DISTINCT {col})"
                if a.op == "nunique"
                else f"COALESCE(exact_sum({col}),'0')"
                if a.op == "sum"
                else f"{a.op}({col})"
            )
        group = ",".join(expressions[k] for k in p["keys"])
        order = ",".join(
            f"{expressions[k]} IS NULL,{expressions[k]}" for k in p["keys"]
        )
        # An ungrouped aggregate still emits one row when its outputs are pruned.
        outputs = ",".join(expressions[n] for n in fields) or ("1" if group else "count(*)")
        query = f"SELECT {outputs} FROM {stage}" + (
            f" GROUP BY {group} ORDER BY {order}" if group else ""
        )
    else:
        raise NotImplementedError(op)
    yield from context.rows(query, {n: frame.schema[n] for n in fields})


def join(frame, fields, ctx):
    left, right = frame._parents
    pairs = frame._params["on"]
    lfields = tuple(dict.fromkeys((*pairs, *(n for n in fields if n in left.schema))))
    rfields = tuple(
        dict.fromkeys(
            (
                *pairs.values(),
                *(n for n in fields if n in right.schema and n not in left.schema),
            )
        )
    )
    lt, ls = ctx.stage(left, lfields)
    rt, rs = ctx.stage(right, rfields)
    ln, rn = tuple(ls), tuple(rs)
    keys_l = [f"c{ln.index(k)}" for k in pairs]
    keys_r = [f"c{rn.index(k)}" for k in pairs.values()]
    for table, keys in ((lt, keys_l), (rt, keys_r)):
        ctx.index(table, keys)
    match = " AND ".join(f"l.{a}=r.{b}" for a, b in zip(keys_l, keys_r))
    violations = []
    card = frame._params["cardinality"]
    for side, table, keys, unique in [
        ("left", lt, keys_l, card in ("1:1", "1:m")),
        ("right", rt, keys_r, card in ("1:1", "m:1")),
    ]:
        if unique:
            group = ",".join(keys)
            # Null key tuples are also subject to declared uniqueness.
            query = f"SELECT {group},count(*) FROM {table} GROUP BY {group} HAVING count(*)>1"
            for row in ctx.execute(query):
                violations.append((side, "duplicate", str(row[:-1]), row[-1]))
                if len(violations) >= 16:
                    break
    req = frame._params["require"]
    missing = {}
    for side, table, alias, opposite, oa in [
        ("left", lt, "l", rt, "r"),
        ("right", rt, "r", lt, "l"),
    ]:
        if req in (side, "both"):
            missing[side] = (
                f"SELECT {alias}.ordinal FROM {table} {alias} WHERE NOT EXISTS(SELECT 1 FROM {opposite} {oa} WHERE {match})"
            )
            if ctx.execute(missing[side] + " LIMIT 1").fetchone():
                violations.append((side, "unmatched", "", 1))
    if violations:
        from mipframe.check.frame import join_failure

        raise join_failure(frame, ctx, lt, ls, rt, rs, pairs, card, req, match)
    mapped = {n: f"l.c{ln.index(n)}" for n in ln}
    mapped.update({n: f"r.c{rn.index(n)}" for n in rn if n not in pairs.values()})
    outputs = ",".join(mapped[n] for n in fields) or "1"
    query = f"SELECT {outputs} FROM {lt} l {'LEFT JOIN' if frame._params['how'] == 'left' else 'JOIN'} {rt} r ON {match} ORDER BY l.ordinal,r.ordinal"
    yield from ctx.rows(query, {n: frame.schema[n] for n in fields})
