"""Model-aware shared ownership of completed, disk-backed Frame results."""

from mipframe.frame.base import Frame


def stored_frame(ctx, table, schema, *, key=None, domains=None, relations=None):
    names = tuple(schema)

    def source(fields, execution):
        ctx.guard()
        columns = ",".join(f"c{names.index(n)}" for n in fields) or "1"
        from contextlib import nullcontext

        borrow = (
            execution.reader(ctx) if hasattr(execution, "reader") else nullcontext()
        )
        with borrow:
            yield from ctx.rows(
                f"SELECT {columns} FROM {table} ORDER BY ordinal",
                {n: schema[n] for n in fields},
            )

    result = Frame(
        schema,
        key=key,
        models=ctx.models,
        source=source,
        domains=domains,
        relations=relations,
    )
    result._resource = ctx
    ctx.retained = True
    result._source_spec = {"kind": "relation", "relation": f"query:{id(ctx)}:{table}"}
    return result
