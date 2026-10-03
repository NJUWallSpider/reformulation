"""Logical scalar types and detached, snapshot-qualified identities."""

from dataclasses import dataclass
from dataclasses import replace
from fractions import Fraction
from decimal import Decimal
from types import MappingProxyType
from math import isfinite
from mipframe.store.numbers import MIPFrameError


class SchemaError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    SchemaError [api.SchemaError]

    Diagnostic exception; inspect original message and any attached complete Report.

    SchemaError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.frame
    MIPFRAME HELP END
    """
    pass


@dataclass(frozen=True)
class Ref:
    """
    MIPFRAME HELP BEGIN
    Ref [api.Ref]

    Detached snapshot-qualified entity identity; navigate with ref.bind(model) or matching rows.loc[ref]/vars.loc[ref].

    Ref [type]

    When to use: Detached snapshot-qualified entity identity; navigate with ref.bind(model) or matching rows.loc[ref]/vars.loc[ref].

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity
    MIPFRAME HELP END
    """
    uid: str
    version: str
    kind: str
    id: int

    def __post_init__(self):
        if (
            not isinstance(self.uid, str)
            or not self.uid
            or not isinstance(self.version, str)
            or not self.version
        ):
            raise SchemaError("References require snapshot uid and version")
        if self.kind not in ("row", "var") or type(self.id) is not int or self.id < 0:
            raise SchemaError(
                "References require a row/var axis and nonnegative integer ID"
            )

    def bind(self, model):
        """
        MIPFRAME HELP BEGIN
        Ref.bind [api.Ref.bind]

        Bind a detached identity to the corresponding live snapshot.

        Ref.bind(model)

        When to use: Bind a detached identity to the corresponding live snapshot.

        Inputs: Model with matching uid/version and correct axis/ID.

        Returns: Live Row or Var.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        if (model.uid, model.version) != (self.uid, self.version):
            raise SchemaError("Reference and model snapshot differ")
        return (model.rows if self.kind == "row" else model.vars).by_id(self.id)


@dataclass(frozen=True)
class DType:
    """
    MIPFRAME HELP BEGIN
    DType [api.DType]

    Scalar column type, including nullable and snapshot-qualified references.

    DType [type]

    When to use: Scalar column type, including nullable and snapshot-qualified references.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.frame
    MIPFRAME HELP END
    """
    kind: str
    nullable: bool = False
    uid: str | None = None
    version: str | None = None
    axis: str | None = None
    values: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.kind not in ("ref", "draft_ref", "int", "number", "bool", "str", "null", "enum"):
            raise SchemaError("Unsupported column type")
        if self.kind == "enum":
            if (
                not self.values
                or any(not isinstance(v, str) for v in self.values)
                or len(set(self.values)) != len(self.values)
            ):
                raise SchemaError("Enum requires distinct string values")
            object.__setattr__(self, "values", tuple(self.values))
        elif self.values is not None:
            raise SchemaError("values only applies to enums")
        if type(self.nullable) is not bool:
            raise SchemaError("nullable must be boolean")
        if self.kind in ("ref", "draft_ref"):
            Ref(self.uid, self.version, self.axis, 0)
        elif any(v is not None for v in (self.uid, self.version, self.axis)):
            raise SchemaError("Snapshot identity is only valid for Ref types")

    def optional(self):
        return replace(self, nullable=True)

    def compatible(self, other):
        return replace(self, nullable=False) == replace(other, nullable=False)

    def to_spec(self):
        from dataclasses import asdict

        return asdict(self)

    def __repr__(self):
        core = f"Ref[{self.axis}]" if self.kind == "ref" else self.kind
        return core + ("?" if self.nullable else "")


def ref_type(model, axis):
    return DType("ref", uid=model.uid, version=model.version, axis=axis)


def dtype(value):
    from mipframe.edit.draft import DraftRef, DraftVar
    if isinstance(value, DraftVar): value = value.ref
    if isinstance(value, DraftRef):
        return DType("draft_ref", uid=value.edit_uid, version="draft", axis=value.kind)
    if isinstance(value, Ref):
        return DType("ref", uid=value.uid, version=value.version, axis=value.kind)
    if value is None:
        return DType("null", True)
    if type(value) is bool:
        return DType("bool")
    if type(value) is int:
        return DType("int")
    if isinstance(value, (Fraction, Decimal)):
        return DType("number")
    if isinstance(value, float) and not isfinite(value) and value == value:
        return DType("number")
    if isinstance(value, str):
        return DType("str")
    raise SchemaError(
        "Use scalar exact values; finite floats and object/list columns require explicit conversion"
    )


def normalize(value, typ):
    if value is None:
        if typ.nullable or typ.kind == "null":
            return None
        raise SchemaError("Non-nullable column received None")
    if typ.kind == "draft_ref":
        from mipframe.edit.draft import DraftRef, DraftVar
        if isinstance(value, DraftVar): value = value.ref
        if isinstance(value, DraftRef) and value.edit_uid == typ.uid and value.kind == typ.axis:
            return value.id
        if type(value) is int and value >= 0: return value
        raise SchemaError("Expected a reference from the same Edit")
    if typ.kind == "ref":
        if hasattr(value, "_owner") and hasattr(value, "id"):
            axis = "row" if type(value).__name__ == "Row" else "var"
            value = Ref(value._owner.uid, value._owner.version, axis, value.id)
        if isinstance(value, Ref):
            if not typ.compatible(dtype(value)):
                raise SchemaError(
                    "Reference belongs to a different snapshot or entity axis"
                )
            return value.id
        if type(value) is int and value >= 0:
            return value
        raise SchemaError(
            "Ref columns require qualified references or nonnegative declared IDs"
        )
    if typ.kind == "enum":
        if not isinstance(value, str) or value not in typ.values:
            raise SchemaError("Value outside declared enum")
        return value
    actual = dtype(value)
    if typ.kind == "number" and actual.kind in ("int", "number"):
        return value if isinstance(value, float) else Fraction(value)
    if typ.kind != actual.kind:
        raise SchemaError(f"Expected {typ}, got {actual}")
    return value


def frozen_schema(schema):
    if any(not isinstance(n, str) or not n for n in schema):
        raise SchemaError("Column names must be nonempty strings")
    return MappingProxyType(
        {k: v if isinstance(v, DType) else DType(v) for k, v in schema.items()}
    )


def encode(value):
    from mipframe.edit.draft import DraftRef
    if isinstance(value, DraftRef):
        return {"draft_ref": dict(edit_uid=value.edit_uid, kind=value.kind, id=value.id)}
    if isinstance(value, Fraction):
        return {"rational": [str(value.numerator), str(value.denominator)]}
    if isinstance(value, Decimal):
        return encode(Fraction(value))
    if isinstance(value, Ref):
        return {
            "ref": dict(
                uid=value.uid, version=value.version, kind=value.kind, id=value.id
            )
        }
    if isinstance(value, float):
        if value not in (float("inf"), -float("inf")):
            raise SchemaError(
                "Only explicit exact values or infinity may be serialized"
            )
        return {"infinity": 1 if value > 0 else -1}
    if value is None or type(value) in (str, int, bool):
        return value
    raise SchemaError("Unsupported scalar")


def decode(value):
    if not isinstance(value, dict):
        return value
    if set(value) == {"rational"}:
        n, d = value["rational"]
        return Fraction(int(n), int(d))
    if set(value) == {"infinity"}:
        if type(value["infinity"]) is not int or value["infinity"] not in (-1, 1):
            raise SchemaError("Invalid infinity encoding")
        return float("inf") * value["infinity"]
    if set(value) == {"draft_ref"}:
        from mipframe.edit.draft import DraftRef
        return DraftRef(**value["draft_ref"])
    if set(value) == {"ref"}:
        return Ref(**value["ref"])
    raise SchemaError("Unknown scalar encoding")
