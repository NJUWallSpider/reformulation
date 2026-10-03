"""Read-only Python views of the exact numerical model."""

from __future__ import annotations
from collections.abc import Mapping
from collections.abc import Sequence
from fractions import Fraction
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import TypeVar
from typing import Generic
from typing import overload

if TYPE_CHECKING:
    from mipframe.model import Model


class NameIndex(Mapping[str, int]):
    """
    Lazy read-only name -> original ID / full-matrix position mapping.

    MIPFRAME HELP BEGIN
    NameIndex [api.NameIndex]

    Explicit original-name lookup index; names do not replace typed Ref identity.

    NameIndex [type]

    When to use: Explicit original-name lookup index; names do not replace typed Ref identity.

    Does not establish: A signature, name or draft identity does not establish a model equivalence.

    See also: concept.frame, concept.identity
    MIPFRAME HELP END
    """

    def __init__(self, owner: Model, kind: str):
        self._owner, self._kind = owner, kind

    @property
    def _names(self):
        m = self._owner.model
        return m.row_names if self._kind == "row" else m.var_names

    def __len__(self):
        return len(self._names)

    def __getitem__(self, name: str) -> int:
        names = self._names
        if not isinstance(name, str):
            raise TypeError("Index lookup requires an original name")
        return names[name]

    def __iter__(self):
        self._owner.model
        return self._iter_names()

    def _iter_names(self):
        names = iter(self._names)
        while True:
            self._owner.model
            try:
                name = next(names)
            except StopIteration:
                return
            yield name

    def to_dict(self) -> dict[str, int]:
        """Explicit snapshot usable after the model closes."""
        return dict(self)

    def __repr__(self):
        from mipframe.frame.entity import _table

        m = self._owner.model
        metadata = m.rows if self._kind == "row" else m.variables
        return _table(f"<NameIndex: {len(self):,} {self._kind}s; name -> position>",
                      ("name",), range(len(self)), lambda i: {"name": metadata[i][1]})

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


@dataclass(frozen=True)
class Variable:
    id: int
    name: str
    kind: str
    lower: Fraction | None
    upper: Fraction | None
    cost: Fraction
    source_line: int


@dataclass(frozen=True)
class Constraint:
    id: int
    name: str
    lower: Fraction | None
    upper: Fraction | None
    source_line: int
    _owner: Model

    @property
    def coefficients(self) -> dict[int, Fraction]:
        return self._owner.model.row(self.id)


@dataclass(frozen=True)
class Objective:
    _owner: Model

    @property
    def sense(self) -> str:
        return self._owner.model.manifest["sense"]

    @property
    def constant(self) -> Fraction:
        m = self._owner.model
        return m.num(m.manifest["objective_constant"])

    @property
    def coefficients(self) -> dict[int, Fraction]:
        m = self._owner.model
        return {v[0]: c for v in m.variables if (c := m.num(v[5]))}


T = TypeVar("T", Variable, Constraint)


class Records(Sequence[T], Generic[T]):
    """Lazy sequence; indexing is positional, names use by_name()."""

    def __init__(self, owner: Model, kind: str):
        self._owner, self._kind = owner, kind

    @property
    def _table(self):
        m = self._owner.model
        return m.variables if self._kind == "variable" else m.rows

    def __len__(self) -> int:
        return len(self._table)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> Variable | Constraint | list:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if type(index) is not int:
            raise TypeError("Use an integer position, slice, or by_name(name)")
        if not -len(self) <= index < len(self):
            raise IndexError(index)
        m = self._owner.model
        row = self._table[index]
        if self._kind == "variable":
            i, name, kind, lo, hi, obj, line = row
            return Variable(i, name, kind, m.num(lo), m.num(hi), m.num(obj), line)
        i, name, lo, hi, line = row
        return Constraint(i, name, m.num(lo), m.num(hi), line, self._owner)

    def by_name(self, name: str) -> T:
        m = self._owner.model
        names = m.var_names if self._kind == "variable" else m.row_names
        return self[names[name]]
