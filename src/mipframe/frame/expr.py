"""Serializable, typed column expressions. Never execute Python callbacks."""

from mipframe._help.diagnostics import hint as _help_hint

from dataclasses import dataclass
from fractions import Fraction
import operator
from mipframe.frame.schema import DType
from mipframe.frame.schema import SchemaError
from mipframe.frame.schema import dtype
from mipframe.frame.schema import encode
from mipframe.frame.schema import decode
from mipframe.frame.schema import Ref


@dataclass(frozen=True, eq=False)
class QueryExpr:
    """
    MIPFRAME HELP BEGIN
    QueryExpr [api.QueryExpr]

    Serializable table expression, not a model-variable affine function.

    QueryExpr [type]

    When to use: Serializable table expression, not a model-variable affine function.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: error.expressions
    MIPFRAME HELP END
    """
    op: str
    args: tuple

    def __bool__(self):
        raise _help_hint(TypeError("QueryExpr has no truth value; combine predicates with & and |"), 'error.expressions')

    def _binary(self, op, other):
        return QueryExpr(op, (self, literal(other)))

    def __eq__(self, x):
        return self._binary("eq", x)

    def __ne__(self, x):
        return self._binary("ne", x)

    def __lt__(self, x):
        return self._binary("lt", x)

    def __le__(self, x):
        return self._binary("le", x)

    def __gt__(self, x):
        return self._binary("gt", x)

    def __ge__(self, x):
        return self._binary("ge", x)

    def __add__(self, x):
        return self._binary("add", x)

    def __sub__(self, x):
        return self._binary("sub", x)

    def __mul__(self, x):
        return self._binary("mul", x)

    def __truediv__(self, x):
        return self._binary("div", x)

    def __radd__(self, x):
        return literal(x)._binary("add", self)

    def __rsub__(self, x):
        return literal(x)._binary("sub", self)

    def __rmul__(self, x):
        return literal(x)._binary("mul", self)

    def __rtruediv__(self, x):
        return literal(x)._binary("div", self)

    def __and__(self, x):
        return self._binary("and", x)

    def __or__(self, x):
        return self._binary("or", x)

    def __invert__(self):
        return QueryExpr("not", (self,))

    def __neg__(self):
        return QueryExpr("neg", (self,))

    def __abs__(self):
        return QueryExpr("abs", (self,))

    def is_null(self):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.is_null [api.QueryExpr.is_null]

        Construct the is_null table expression.

        QueryExpr.is_null()

        When to use: Construct the is_null table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        return QueryExpr("is_null", (self,))

    def is_not_null(self):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.is_not_null [api.QueryExpr.is_not_null]

        Construct the is_not_null table expression.

        QueryExpr.is_not_null()

        When to use: Construct the is_not_null table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        return ~self.is_null()

    def isin(self, values):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.isin [api.QueryExpr.isin]

        Construct the isin table expression.

        QueryExpr.isin(values)

        When to use: Construct the isin table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        if hasattr(values, "schema"):
            return QueryExpr("isin_frame", (self, values))
        return QueryExpr("isin", (self, tuple(literal(v) for v in values)))

    def count(self):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.count [api.QueryExpr.count]

        Construct the count table expression.

        QueryExpr.count()

        When to use: Construct the count table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        return Aggregate("count", self)

    def sum(self):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.sum [api.QueryExpr.sum]

        Construct the sum table expression.

        QueryExpr.sum()

        When to use: Construct the sum table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        return Aggregate("sum", self)

    def min(self):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.min [api.QueryExpr.min]

        Construct the min table expression.

        QueryExpr.min()

        When to use: Construct the min table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        return Aggregate("min", self)

    def max(self):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.max [api.QueryExpr.max]

        Construct the max table expression.

        QueryExpr.max()

        When to use: Construct the max table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        return Aggregate("max", self)

    def nunique(self):
        """
        MIPFRAME HELP BEGIN
        QueryExpr.nunique [api.QueryExpr.nunique]

        Construct the nunique table expression.

        QueryExpr.nunique()

        When to use: Construct the nunique table expression.

        Returns: Serializable predicate or aggregation expression.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: error.expressions
        MIPFRAME HELP END
        """
        return Aggregate("nunique", self)

    @property
    def dependencies(self):
        if self.op == "col":
            return {self.args[0]}
        result = set()
        for arg in self.args:
            if isinstance(arg, QueryExpr):
                result |= arg.dependencies
        return result

    def type(self, schema):
        op, a = self.op, self.args
        if op == "col":
            if a[0] not in schema:
                raise SchemaError(f"Unknown column {a[0]!r}")
            return schema[a[0]]
        if op == "literal":
            return dtype(a[0])
        left = a[0].type(schema)
        if op == "isin_frame":
            f = a[1]
            if len(f.key or ()) != 1:
                raise SchemaError("Membership requires a single-key frame")
            right = f.schema[f.key[0]]
            if not left.compatible(right):
                raise SchemaError("Membership key types or snapshots differ")
            return DType("bool", left.nullable)
        if op == "isin":
            for v in a[1]:
                _comparable(left, v.type(schema))
            return DType("bool", left.nullable)
        if op == "is_null":
            return DType("bool")
        if op == "not":
            if left.kind != "bool":
                raise SchemaError("Boolean operation requires bool")
            return left
        if op in ("neg", "abs"):
            if left.kind not in ("int", "number"):
                raise SchemaError("Arithmetic requires numeric columns")
            return left
        right = a[1].type(schema)
        if op in ("eq", "ne", "lt", "le", "gt", "ge"):
            _comparable(left, right)
            return DType("bool", left.nullable or right.nullable)
        if op in ("and", "or"):
            if left.kind != "bool" or right.kind != "bool":
                raise SchemaError("Boolean operation requires bool")
            return DType("bool", left.nullable or right.nullable)
        if left.kind not in ("int", "number") or right.kind not in ("int", "number"):
            raise SchemaError(
                "Arithmetic requires numeric columns; references are not integers"
            )
        return DType(
            "number" if op == "div" or "number" in (left.kind, right.kind) else "int",
            left.nullable or right.nullable,
        )

    def evaluate(self, row, context):
        op, args = self.op, self.args
        if op == "col":
            return row[args[0]]
        if op == "literal":
            from mipframe.edit.draft import DraftRef
            return args[0].id if isinstance(args[0], (Ref, DraftRef)) else args[0]
        a = args[0].evaluate(row, context)
        if op == "is_null":
            return a is None
        if op == "isin_frame":
            return None if a is None else context.contains(args[1], a)
        if op == "isin":
            if a is None:
                return None
            values = [x.evaluate(row, context) for x in args[1]]
            return True if a in values else None if None in values else False
        if op in ("not", "neg", "abs"):
            return (
                None
                if a is None
                else {"not": operator.not_, "neg": operator.neg, "abs": abs}[op](a)
            )
        b = args[1].evaluate(row, context)
        if op == "and":
            return (
                False
                if a is False or b is False
                else None
                if a is None or b is None
                else True
            )
        if op == "or":
            return (
                True
                if a is True or b is True
                else None
                if a is None or b is None
                else False
            )
        if a is None or b is None:
            return None
        if (
            op == "div"
            and isinstance(a, (int, Fraction))
            and isinstance(b, (int, Fraction))
        ):
            return Fraction(a) / Fraction(b)
        return {
            "eq": operator.eq,
            "ne": operator.ne,
            "lt": operator.lt,
            "le": operator.le,
            "gt": operator.gt,
            "ge": operator.ge,
            "add": operator.add,
            "sub": operator.sub,
            "mul": operator.mul,
            "div": operator.truediv,
        }[op](a, b)

    def to_spec(self):
        if self.op == "literal":
            return {"op": "literal", "value": encode(self.args[0])}
        if self.op == "col":
            return {"op": "col", "name": self.args[0]}
        if self.op == "isin_frame":
            return {
                "op": self.op,
                "input": self.args[0].to_spec(),
                "frame": self.args[1].to_spec(),
            }
        if self.op == "isin":
            return {
                "op": self.op,
                "input": self.args[0].to_spec(),
                "values": [v.to_spec() for v in self.args[1]],
            }
        return {"op": self.op, "args": [a.to_spec() for a in self.args]}


def _comparable(a, b):
    if a.kind == "null" or b.kind == "null":
        return
    if (a.kind, b.kind) in (("enum", "str"), ("str", "enum")):
        return
    if a.kind in ("int", "number") and b.kind in ("int", "number"):
        return
    if not a.compatible(b):
        raise SchemaError("Comparison types or reference snapshots differ")


def literal(value):
    from mipframe.edit.draft import DraftVar
    if isinstance(value, DraftVar): value = value.ref
    if isinstance(value, QueryExpr):
        return value
    if hasattr(value, "_owner") and hasattr(value, "id"):
        value = Ref(
            value._owner.uid,
            value._owner.version,
            "row" if type(value).__name__ == "Row" else "var",
            value.id,
        )
    dtype(value)
    return QueryExpr(
        "literal", (Fraction(value) if type(value).__name__ == "Decimal" else value,)
    )


def col(name):
    """
    MIPFRAME HELP BEGIN
    col [api.col]

    Reference a named Frame column in a query expression.

    col(name)

    When to use: Reference a named Frame column in a query expression.

    Inputs: Existing column name when bound to a Frame.

    Returns: QueryExpr.

    Does not establish: No model change or additional mathematical conclusion is implied.

    Illustrative example (requires caller-provided inputs):
    mf.col("coef") < 0

    See also: error.expressions
    MIPFRAME HELP END
    """
    if not isinstance(name, str):
        raise TypeError("Column name must be a string")
    return QueryExpr("col", (name,))


@dataclass(frozen=True)
class Aggregate:
    op: str
    expr: QueryExpr | None = None


def count():
    """
    MIPFRAME HELP BEGIN
    count [api.count]

    Construct a group aggregation counting records.

    count()

    When to use: Construct a group aggregation counting records.

    Returns: Aggregate for Grouped.agg; unlike column count it counts rows.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.Grouped.agg
    MIPFRAME HELP END
    """
    return Aggregate("count")
