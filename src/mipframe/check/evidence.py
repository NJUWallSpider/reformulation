"""Lazy public evidence views over the canonical proof relations."""

from fractions import Fraction
from mipframe.frame.base import Frame
from mipframe.frame.schema import DType, ref_type


def normalized_evidence(ctx):
    integer, text, number = DType("int"), DType("str"), DType("number")
    row = ref_type(ctx.owner, "row").optional()
    var = ref_type(ctx.owner, "var")

    def view(schema, query, convert):
        def source(fields, execution):
            ctx.guard()
            for record in ctx.execute(query):
                values = convert(record)
                yield {name: values[name] for name in fields}
        frame = Frame(schema, models=ctx.models, source=source)
        frame._query.resource = ctx
        return frame

    def num(x):
        return float(x) if x in ("inf", "-inf") else Fraction(x)

    return {
        "sources": view(dict(source=integer, direction=text, claim=integer,
                             multiplier=number, parent=DType("int", True)),
            f"SELECT id,direction,claim,multiplier,parent FROM {ctx.sources} ORDER BY id",
            lambda r: dict(source=r[0], direction=r[1], claim=r[2], multiplier=Fraction(r[3]), parent=r[4])),
        "claims": view(dict(claim=integer, relation=text, constant=number,
                            row=row, var=var.optional(), side=DType("str", True)),
            f"SELECT * FROM {ctx.claims} ORDER BY id",
            lambda r: dict(claim=r[0], relation=r[1], constant=Fraction(r[2]),
                           row=r[4] if r[3] == "row" else None,
                           var=r[4] if r[3] == "var" else None, side=r[5])),
        "terms": view(dict(claim=integer, var=var, coef=number),
            f"SELECT * FROM {ctx.terms} ORDER BY claim,v",
            lambda r: dict(claim=r[0], var=r[1], coef=Fraction(r[2]))),
        "assumptions": view(dict(claim=integer),
            f"SELECT claim FROM {ctx.assumptions} ORDER BY claim", lambda r: dict(claim=r[0])),
        "bounds": view(dict(direction=text, var=var, coef=number, side=text,
                            bound=number, contribution=DType("number", True)),
            f"SELECT direction,v,c,side,bound,contribution FROM {ctx.bounds_evidence} ORDER BY id",
            lambda r: dict(direction=r[0], var=r[1], coef=Fraction(r[2]), side=r[3],
                           bound=num(r[4]), contribution=None if r[5] is None else Fraction(r[5]))),
        "dependencies": view(dict(kind=text, row=row, var=var.optional(),
                                  side=text, parent=DType("int", True)),
            f"SELECT kind,entity,side,parent FROM {ctx.deps} ORDER BY id",
            lambda r: dict(kind=r[0], row=r[1] if r[0] == "row" else None,
                           var=r[1] if r[0] in ("var", "integer") else None,
                           side=r[2], parent=r[3] if r[3] >= 0 else None)),
    }
