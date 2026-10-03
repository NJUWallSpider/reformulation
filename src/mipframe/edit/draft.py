"""Edit-local identities and sparse affine construction; never model facts."""

from dataclasses import dataclass
from fractions import Fraction
from weakref import WeakValueDictionary
from mipframe.frame.schema import DType, SchemaError
from mipframe.linear.scalar import q, NonlinearExpressionError
from mipframe.linear.expr import LinExpr, coerce, merge

EDITS = WeakValueDictionary()


@dataclass(frozen=True)
class DraftRef:
    """
    MIPFRAME HELP BEGIN
    DraftRef [api.DraftRef]

    Detached Edit-local draft identity, distinct from a source snapshot Ref.

    DraftRef [type]

    When to use: Detached Edit-local draft identity, distinct from a source snapshot Ref.

    Does not establish: A signature, name or draft identity does not establish a model equivalence.

    See also: concept.frame, concept.identity
    MIPFRAME HELP END
    """
    edit_uid: str
    kind: str
    id: int

    def __post_init__(self):
        if (
            not isinstance(self.edit_uid, str)
            or not self.edit_uid
            or self.kind not in ("row", "var")
            or type(self.id) is not int
            or self.id < 0
        ):
            raise SchemaError("Invalid draft identity")

    def bind(self):
        edit = EDITS.get(self.edit_uid)
        if edit is None:
            raise SchemaError("Draft Edit is no longer available")
        edit._guard()
        return DraftVar(edit, self.id) if self.kind == "var" else self


def draft_type(edit, axis="var"):
    return DType("draft_ref", uid=edit.uid, version="draft", axis=axis)


class DraftVar:
    def __init__(self, edit, ident):
        self._edit, self._id = edit, ident

    @property
    def ref(self):
        self._edit._guard()
        return DraftRef(self._edit.uid, "var", self._id)

    @property
    def name(self):
        self._edit._guard()
        return self._edit._store.execute(
            "SELECT name FROM new_vars WHERE id=?", (self._id,)
        ).fetchone()[0]

    @property
    def expr(self):
        return DraftExpr.create(self._edit, (((1, self._id), Fraction(1)),), 0)

    def __repr__(self):
        return f"DraftVar({self.name!r})"

    def __add__(self, x):
        return self.expr + x

    __radd__ = __add__

    def __sub__(self, x):
        return self.expr - x

    def __rsub__(self, x):
        return -self.expr + x

    def __neg__(self):
        return -self.expr

    def __mul__(self, x):
        return self.expr * x

    __rmul__ = __mul__

    def __le__(self, x):
        return self.expr <= x

    def __ge__(self, x):
        return self.expr >= x

    def eq(self, x):
        return self.expr.eq(x)

    def __eq__(self, x):
        return isinstance(x, DraftVar) and self.ref == x.ref

    def __hash__(self):
        return hash(self.ref)


class DraftExpr:
    def __setattr__(self, n, v):
        if not n.startswith("_"):
            raise AttributeError("Draft expressions are immutable")
        object.__setattr__(self, n, v)

    @classmethod
    def create(cls, edit, items, constant):
        edit._guard()
        s = edit._store
        t = s.raw_table("ns INTEGER,v INTEGER,c TEXT,PRIMARY KEY(ns,v)")
        for (ns, v), c in items:
            c = q(c)
            s.check_size(c)
            old = s.execute(f"SELECT c FROM {t} WHERE ns=? AND v=?", (ns, v)).fetchone()
            c += Fraction(old[0]) if old else 0
            if c:
                s.execute(f"INSERT OR REPLACE INTO {t} VALUES(?,?,?)", (ns, v, str(c)))
            else:
                s.execute(f"DELETE FROM {t} WHERE ns=? AND v=?", (ns, v))
        x = cls()
        x._edit = edit
        x._table = t
        x._constant = q(constant)
        return x

    def _items(self):
        self._edit._guard()
        for ns, v, c in self._edit._store.execute(
            f"SELECT ns,v,c FROM {self._table} ORDER BY ns,v"
        ):
            self._edit._guard()
            yield (ns, v), Fraction(c)

    @property
    def constant(self):
        self._edit._guard()
        return self._constant

    def _coerce(self, x):
        if isinstance(x, DraftVar):
            x = x.expr
        if isinstance(x, DraftExpr):
            if x._edit is not self._edit:
                raise SchemaError("Different Edit scopes")
            return x
        x = coerce(self._edit._owner, x)
        return DraftExpr.create(
            self._edit, (((0, v), c) for v, c in x._items()), x.constant
        )

    def __add__(self, x):
        x = self._coerce(x)
        return self.create(
            self._edit, merge(self._items(), x._items()), self.constant + x.constant
        )

    __radd__ = __add__

    def __neg__(self):
        return self * -1

    def __sub__(self, x):
        return self + -self._coerce(x)

    def __rsub__(self, x):
        return self._coerce(x) - self

    def __mul__(self, x):
        if isinstance(x, (DraftVar, DraftExpr, LinExpr)) or hasattr(x, "_owner"):
            raise NonlinearExpressionError("Only scalar multiplication is supported")
        x = q(x)
        return self.create(
            self._edit, ((v, c * x) for v, c in self._items()), self.constant * x
        )

    __rmul__ = __mul__

    def __le__(self, x):
        return DraftClaim(self - x, "<=")

    def __ge__(self, x):
        return DraftClaim(self - x, ">=")

    def eq(self, x):
        return DraftClaim(self - x, "==")

    def __bool__(self):
        raise TypeError("Draft expression has no truth value")

    def formula(self, *, max_terms=12):
        from mipframe.frame.row import Formula

        if type(max_terms) is not int or max_terms < 0:
            raise ValueError("Invalid max_terms")
        out = []
        for i, ((ns, v), c) in enumerate(self._items()):
            if i == max_terms:
                out.append("...")
                break
            name = (
                DraftVar(self._edit, v).name
                if ns
                else self._edit._owner.vars.by_id(v).name
            )
            out.append(f"{c}*{name}")
        if self.constant:
            out.append(str(self.constant))
        return Formula(" + ".join(out) or "0")

    def __repr__(self):
        return f"DraftExpr({self.formula()})"


@dataclass(frozen=True)
class DraftClaim:
    expr: DraftExpr
    relation: str

    def __bool__(self):
        raise TypeError("Draft Claim is not a proof")
