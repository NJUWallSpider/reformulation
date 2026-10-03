"""Exact sparse Ax and A-transpose-y with explicit coverage semantics."""

from fractions import Fraction
from mipframe.frame.schema import DType, SchemaError, ref_type
from mipframe.ops._runtime import operation


def compute(ctx, selection, values, value, missing, transpose):
    if missing not in ("error", "zero"):
        raise ValueError("missing must be 'error' or 'zero'")
    axis = "var" if transpose else "row"
    input_axis = "row" if transpose else "var"
    if getattr(selection, "_entity_axis", None) != axis:
        raise SchemaError("Wrong output axis")
    owner = selection._owner
    table, schema = ctx.stage_checked(
        values, (input_axis, value), unique=(input_axis,), nonnull=(input_axis,)
    )
    if not ref_type(owner, input_axis).compatible(schema[input_axis]):
        raise SchemaError("Input vector uses the wrong axis or snapshot")
    if schema[value].kind not in ("int", "number"):
        raise SchemaError("Vector values must be exact numbers")
    count = owner.shape[0 if transpose else 1]
    bad = f"SELECT c0,c1 FROM {table} WHERE c0<0 OR c0>=? OR c1 IS NULL OR c1 IN ('inf','-inf') ORDER BY ordinal"
    if ctx.execute(f"SELECT 1 FROM ({bad}) LIMIT 1", (count,)).fetchone():
        ctx.invalid(
            "Invalid vector ID, missing value, or nonfinite value",
            schema,
            ctx.rows(bad, schema, (count,)),
        )
    ctx.execute(f"CREATE UNIQUE INDEX {table}_input ON {table}(c0)")
    required = ctx.raw_table("id INTEGER PRIMARY KEY")
    m = owner.model
    block = max(1, ctx.memory // 128)
    axis_table, _ = ctx.stage_checked(selection, (axis,), nonnull=(axis,))

    def source_ids():
        return (
            r[0] for r in ctx.execute(f"SELECT c0 FROM {axis_table} ORDER BY ordinal")
        )

    if missing == "error":
        for source in source_ids():
            for targets, _ in m.sparse.blocks(axis, source, block):
                for target in targets:
                    ctx.execute(
                        f"INSERT OR IGNORE INTO {required} VALUES(?)", (int(target),)
                    )
        query = f"SELECT id FROM {required} r WHERE NOT EXISTS(SELECT 1 FROM {table} x WHERE x.c0=r.id) ORDER BY id"
        if ctx.execute(f"SELECT 1 FROM ({query}) LIMIT 1").fetchone():
            out = {input_axis: ref_type(owner, input_axis)}
            ctx.invalid(
                'Missing required vector values; use missing="zero" for a sparse vector',
                out,
                ctx.rows(query, out),
            )
    output = "value" if transpose else "activity"

    def records():
        for source in source_ids():
            total = Fraction(0)
            for targets, positions in m.sparse.blocks(axis, source, block):
                for target, pos in zip(targets, positions):
                    item = ctx.execute(
                        f"SELECT c1 FROM {table} WHERE c0=?", (int(target),)
                    ).fetchone()
                    if item is not None:
                        scalar = Fraction(item[0])
                        if scalar:
                            total += m.num(int(m.coeff_id[int(pos)])) * scalar
                            ctx.check_size(total)
            yield {axis: int(source), output: total}

    ctx.paths.add(
        "CSC exact multiply-accumulate"
        if transpose
        else "CSR exact multiply-accumulate"
    )
    return ctx.publish(
        {axis: ref_type(owner, axis), output: DType("number")},
        records(),
        key=(axis,),
        domains={axis: selection},
    )


@operation("matrix.activity")
def activity(ctx, selection, values, *, value="value", missing="error"):
    return compute(ctx, selection, values, value, missing, False)


@operation("matrix.transpose_activity")
def transpose_activity(ctx, selection, values, *, value="value", missing="error"):
    return compute(ctx, selection, values, value, missing, True)
