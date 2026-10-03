"""Shared spill storage for completed expressions, separate from working tables."""

import weakref
from fractions import Fraction

from mipframe.frame.executor import size
from mipframe.store.algorithm_store import AlgorithmStore


class ExpressionView:
    def __init__(self, archive, ident, models):
        self.archive, self.ident, self.models = archive, ident, models
        self.memory = archive.store.memory
        self._finalizer = weakref.finalize(self, archive.release, ident)

    @property
    def db(self):
        return self.archive.store.db

    def guard(self):
        for model in self.models:
            model.model
        self.archive.store.guard()

    def execute(self, query, args=()):
        self.guard()
        return self.archive.store.execute(query, args)


class ExpressionArchive:
    def __init__(self, owner, memory_mb):
        self.store = AlgorithmStore((owner,), memory_mb=memory_mb, max_output_rows=None)
        try:
            self.store.db.execute("PRAGMA journal_mode=DELETE")
            self.store.db.execute("PRAGMA synchronous=OFF")
            self.table = self.store.raw_table("expr INTEGER,v INTEGER,c TEXT,PRIMARY KEY(expr,v)")
        except BaseException:
            self.store.close()
            raise
        self.serial = 0
        self.copying = False
        self.pending = []

    def release(self, ident):
        self.pending.append(ident)
        if self.copying:
            return
        try:
            self.flush()
        except Exception:
            # Retry after the next copy; closing the owner always closes storage.
            pass

    def flush(self):
        if self.store.db is None:
            self.pending.clear()
            return
        while self.pending:
            ident = self.pending[-1]
            self.store.execute(f"DELETE FROM {self.table} WHERE expr=?", (ident,))
            self.pending.pop()
        self.store.db.commit()

    def retain(self, owner, ctx, table, constant):
        from mipframe.linear.expr import LinExpr

        self.flush()
        self.serial += 1
        ident = self.serial
        s = self.store
        s.memory = ctx.memory
        for pragma in ("cache_size", "temp.cache_size"):
            s.execute(f"PRAGMA {pragma}=-{max(1, s.memory // 8192)}")
        self.copying = True
        try:
            with ctx.output_budget(s):
                s.execute("SAVEPOINT expression_copy")
                try:
                    for v, c in ctx.execute(f"SELECT v,c FROM {table} ORDER BY v"):
                        s.execute(f"INSERT INTO {self.table} VALUES(?,?,?)", (ident, v, c))
                    s.disk_check()
                    s.execute("RELEASE expression_copy")
                except BaseException as exc:
                    try:
                        s.db.execute("ROLLBACK TO expression_copy")
                        s.db.execute("RELEASE expression_copy")
                    except Exception as cleanup:
                        exc.add_note(f"Expression rollback also failed: {cleanup}")
                    raise
            view = ExpressionView(self, ident, ctx.models)
            return LinExpr(owner, constant, store=view,
                           query=f"SELECT v,c FROM {self.table} WHERE expr={ident} ORDER BY v",
                           models=ctx.models)
        finally:
            self.copying = False
            self.flush()


def finish_expression(owner, ctx, table, constant):
    """Consume an exclusively owned expression workspace and return a compact value."""
    from mipframe.linear.expr import LinExpr

    ctx.check_size(constant)
    ctx.disk_check()
    data, used = [], 0
    for v, c in ctx.execute(f"SELECT v,c FROM {table} ORDER BY v"):
        c = Fraction(c)
        used += size(v) + size(c) + 72
        if used > min(65536, ctx.memory // 4):
            break
        data.append((v, c))
    else:
        values = tuple(data)
        result = LinExpr(owner, constant, items=lambda: iter(values), models=ctx.models)
        ctx.close()
        return result
    reference = getattr(owner, "_expression_archive", None)
    archive = reference() if reference is not None else None
    created = archive is None or archive.store.db is None
    if created:
        archive = ExpressionArchive(owner, ctx.memory / 1024**2)
        owner._expression_archive = weakref.ref(archive)
    try:
        result = archive.retain(owner, ctx, table, constant)
    except BaseException:
        if created:
            archive.store.close()
        raise
    ctx.close()
    return result
