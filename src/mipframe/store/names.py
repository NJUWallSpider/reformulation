"""Read-only Python views of the exact numerical model."""

from __future__ import annotations
from collections.abc import Mapping
from fractions import Fraction
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mipframe.model import Model


class NameIndex(Mapping[str, int]):
    """
    Lazy read-only name -> original ID / full-matrix position mapping.

    MIPFRAME HELP BEGIN
    Explicit original-name lookup index; names do not replace typed Ref identity.

    Full contract: mf.help("api.NameIndex")
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
