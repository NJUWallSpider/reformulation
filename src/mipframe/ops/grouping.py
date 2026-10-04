"""Exact equality of grouped multisets; hashes only select comparison buckets."""

import hashlib
import json
from itertools import zip_longest
from mipframe.frame.schema import DType, SchemaError, encode
from mipframe.store.algorithm_store import KeyIndex
from mipframe.ops._runtime import operation


def fingerprint(rows):
    digest = hashlib.blake2b(digest_size=16)
    count = 0
    for row in rows:
        record = json.dumps(
            [encode(v) for v in row], ensure_ascii=False, separators=(",", ":")
        ).encode()
        digest.update(len(record).to_bytes(8, "big"))
        digest.update(record)
        count += 1
    return count, digest.digest()


def classify(ctx, index, stream):
    buckets = ctx.raw_table("n INTEGER,h BLOB,representative INTEGER")
    ctx.execute(f"CREATE INDEX {buckets}_lookup ON {buckets}(n,h)")
    classes = ctx.raw_table("id INTEGER PRIMARY KEY,class INTEGER")
    sentinel = object()
    for ident, _ in index.entries():
        ctx.guard()
        count, digest = fingerprint(stream(ident))
        representative = None
        for (candidate,) in ctx.execute(
            f"SELECT representative FROM {buckets} WHERE n=? AND h=? ORDER BY representative",
            (count, digest),
        ):
            if all(
                a == b
                for a, b in zip_longest(
                    stream(ident), stream(candidate), fillvalue=sentinel
                )
            ):
                representative = candidate
                break
        if representative is None:
            representative = ident
            ctx.execute(f"INSERT INTO {buckets} VALUES(?,?,?)", (count, digest, ident))
        ctx.execute(f"INSERT INTO {classes} VALUES(?,?)", (ident, representative))
    ctx.execute(f"CREATE INDEX {classes}_class ON {classes}(class,id)")
    ctx.paths.add("fingerprint buckets + exact sorted-stream comparison")
    return classes


def class_result(ctx, index, classes, name):
    ctx.enforce(index.n)
    if name == "class":
        raise SchemaError("Rename the group key to avoid the class output column")
    return ctx.publish(
        {name: index.typ, "class": index.typ},
        (
            {name: index.value(i), "class": index.value(c)}
            for i, c in ctx.execute(f"SELECT id,class FROM {classes} ORDER BY id")
        ),
        key=(name,),
        domains={name: index.as_frame(name), "class": index.as_frame(name)},
    )


@operation("equal_groups")
def equal_groups(ctx, F, *, by, rows, groups=None):
    """
    MIPFRAME HELP BEGIN
    Compare grouped payload multisets exactly.

    Full contract: mf.help("api.ops.equal_groups")
    MIPFRAME HELP END
    """
    rows = (rows,) if isinstance(rows, str) else tuple(rows)
    if len(set(rows)) != len(rows):
        raise SchemaError("rows columns must be distinct")
    fields = tuple(dict.fromkeys((by, *rows)))
    table, schema = ctx.stage_checked(F, fields, nonnull=(by,))
    index = KeyIndex.domain(
        ctx,
        schema[by],
        groups if groups is not None else F.domains.get(by),
        f"SELECT c0 AS value FROM {table}",
    )
    index.validate(f"SELECT c0 AS value FROM {table}", by)
    names = tuple(schema)
    ctx.execute(f"CREATE INDEX {table}_groups ON {table}(c0)")
    payload = {n: schema[n] for n in rows}
    cols = ",".join(f"c{names.index(n)}" for n in rows)

    def stream(ident):
        original = ctx.execute(
            f"SELECT c0 FROM {index.table} WHERE ordinal=?", (ident,)
        ).fetchone()[0]
        query = f"SELECT {cols or '1'} FROM {table} WHERE c0=?" + (
            f" ORDER BY {cols}" if cols else ""
        )
        for row in ctx.rows(query, payload, (original,)):
            yield tuple(row.values())

    classes = classify(ctx, index, stream)
    return class_result(ctx, index, classes, by)
