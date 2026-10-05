"""Lazy numerical row/variable frames and bounded plain-text displays."""

from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING


from mipframe.store.numbers import decimal
from mipframe.store.numbers import MIPFrameError
from mipframe.options import options

if TYPE_CHECKING:
    from mipframe.model import Model


def _positions(size: int, limit: int) -> list[int | None]:
    if size <= limit:
        return list(range(size))
    first = (limit + 1) // 2
    last = limit // 2
    return [*range(first), None, *range(size - last, size)]


def _text(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, Fraction):
        try:
            return decimal(value)
        except MIPFrameError:
            return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    # Names can contain tabs or newlines; keep each record on a single line.
    return str(value).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _table(title, columns, index, record_at) -> str:
    """Fetch only visible records and format them using current global limits."""
    col_positions = _positions(len(columns), options.display.max_cols)
    visible_columns = [columns[i] if i is not None else "..." for i in col_positions]
    lines = [["", *visible_columns]]
    for pos in _positions(len(index), options.display.max_rows):
        if pos is None:
            lines.append(["..."] * (len(visible_columns) + 1))
        else:
            record = record_at(pos)
            lines.append(
                [
                    _text(index[pos]),
                    *[
                        _text(record[col]) if col != "..." else "..."
                        for col in visible_columns
                    ],
                ]
            )
    widths = [max(len(line[i]) for line in lines) for i in range(len(lines[0]))]
    body = "\n".join(
        "  ".join(cell.rjust(width) for cell, width in zip(line, widths)).rstrip()
        for line in lines
    )
    return title + "\n" + body


def _sense(lower, upper) -> str:
    if lower is None:
        return "free" if upper is None else "<="
    if upper is None:
        return ">="
    return "=" if lower == upper else "range"


from mipframe.frame.base import Frame
from mipframe.frame.base import PlanIDs


class _IdentityFrame(Frame):
    def __init__(self, owner, index=None):
        from mipframe.frame.sources import entity_schema
        from mipframe.frame.sources import entity_scan
        from mipframe.store.query_store import stored_index

        self._owner = owner
        self._entity_axis = "row" if self._kind == "row" else "var"
        if index is None:
            index = owner.model.entity_ids(self._entity_axis) if hasattr(owner.model, 'entity_ids') else range(
                len(
                    owner.model.rows
                    if self._entity_axis == "row"
                    else owner.model.variables
                )
            )
        elif not isinstance(index, range) and not getattr(index, "unique", False):
            from numbers import Integral

            original = index
            maximum = owner.model.capacity(self._entity_axis) if hasattr(owner.model, 'capacity') else len(
                owner.model.rows
                if self._entity_axis == "row"
                else owner.model.variables
            )

            def validated():
                for ident in original:
                    if isinstance(ident, bool) or not isinstance(ident, Integral):
                        raise TypeError("Entity IDs must be integers")
                    if not 0 <= ident < maximum:
                        raise ValueError(
                            "Entity selection contains an invalid model ID"
                        )
                    yield int(ident)

            index = stored_index(owner, validated(), unique=True)
        maximum = owner.model.capacity(self._entity_axis) if hasattr(owner.model, 'capacity') else len(
            owner.model.rows if self._entity_axis == "row" else owner.model.variables
        )
        if (
            isinstance(index, range)
            and len(index)
            and (min(index[0], index[-1]) < 0 or max(index[0], index[-1]) >= maximum)
        ):
            raise ValueError("Entity selection contains an invalid model ID")
        self._index = index
        Frame.__init__(
            self,
            entity_schema(owner, self._entity_axis),
            key=(self._entity_axis,),
            models=(owner,),
            source=lambda fields, ctx: entity_scan(self, fields, ctx),
            known_count=len(index) if isinstance(index, range) else None,
        )
        if isinstance(index, PlanIDs):
            from mipframe.frame.base import merge_models

            self._models = merge_models(self, index.frame)
        self._query.spec = {
            "kind": self._entity_axis,
            "uid": owner.uid,
            "version": owner.version,
        }

    @property
    def _metadata(self):
        return (
            self._owner.model.rows
            if self._entity_axis == "row"
            else self._owner.model.variables
        )

    @property
    def _total(self):
        self._guard()
        return len(self._metadata)

    def _select(self, ids):
        return type(self)(self._owner, ids)


    def __len__(self):
        self._guard()
        return len(self._index)

    @property
    def shape(self):
        return len(self), len(self.schema)

    @property
    def ids(self):
        """
        MIPFRAME HELP BEGIN
        Read original IDs.

        Full contract: mf.help("api.RowFrame.ids")
        MIPFRAME HELP END
        """
        self._guard()
        return self._index


    @property
    def terms(self):
        """
        MIPFRAME HELP BEGIN
        Read original terms.

        Full contract: mf.help("api.RowFrame.terms")
        MIPFRAME HELP END
        """
        from mipframe.frame.terms import TermFrame

        return TermFrame(
            self._owner,
            **({"rows": self} if self._entity_axis == "row" else {"vars": self}),
        )

    def families(self, *, depth=2, **options):
        """
        Template families restricted to these members, with global identities.

        MIPFRAME HELP BEGIN
        Group selected members by full-model template families.

        Full contract: mf.help("api.RowFrame.families")
        MIPFRAME HELP END
        """
        from mipframe.analysis import families
        return families(self,method='template',depth=depth,**options)

    def family_membership(self, *, depth=2, **options):
        """
        MIPFRAME HELP BEGIN
        Expose family membership as a normalized relation.

        Full contract: mf.help("api.RowFrame.family_membership")
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import membership

        from mipframe.ir.signatures import refinement
        result = refinement(self._owner,depth=depth,mode='template',**options)
        return membership(self,depth,result)

    def value_counts(self, column):
        if column == "family":
            return self.family_membership().column("family").value_counts()
        return self.column(column).value_counts()

    def pattern_counts(self, *, depth=None, memory_mb=128):
        """
        MIPFRAME HELP BEGIN
        Count source entities by their family-neighborhood pattern.

        Full contract: mf.help("api.RowFrame.pattern_counts")
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import pattern_counts

        return pattern_counts(self, depth=depth, memory_mb=memory_mb)

    def project(self, *, memory_mb=128, disk_mb=None, **bindings):
        """
        MIPFRAME HELP BEGIN
        Map each source to exactly one neighbor in every named target domain.

        Full contract: mf.help("api.RowFrame.project")
        MIPFRAME HELP END
        """
        from mipframe.check.frame import project

        return project(self, bindings, memory_mb=memory_mb, disk_mb=disk_mb)

    @property
    def iloc(self):
        """Position-based access in this selection, independent of model IDs."""
        self._owner.model
        return _IdentityILoc(self)

    def _by_id(self, ident):
        """Look up an original model ID within this selection."""
        self._owner.model
        if type(ident) is not int:
            raise TypeError("Model IDs must be integers")
        if ident not in self.ids:
            raise KeyError(ident)
        return self._entity(ident)

    def by_name(self, name):
        """
        Look up an original name, including names resembling family labels.

        MIPFRAME HELP BEGIN
        Find an entity by its original name explicitly.

        Full contract: mf.help("api.RowFrame.by_name")
        MIPFRAME HELP END
        """
        m = self._owner.model
        if not isinstance(name, str):
            raise TypeError("Names must be strings")
        names = m.row_names if self._kind == "row" else m.var_names
        return self.loc[names[name]]

    def tail(self, n=5):
        n = self._size(n)
        return self.iloc[max(0, len(self) - n) :]

    def _entity(self, ident):
        from mipframe.frame.row import Row
        from mipframe.frame.var import Var

        return (Row if self._kind == "row" else Var)(self._owner, int(ident))

    def __iter__(self):
        from mipframe.entity import guarded_iter

        self._guard()
        return (self._entity(ident) for ident in guarded_iter(self._owner, self.ids))

    def _at(self, position):
        """Resolve a position within this selection."""
        if type(position) is not int:
            raise TypeError("position must be an integer")
        return self._entity(self.ids[position])

    def _neighbors(self):
        from mipframe.ops.support import neighbors

        return neighbors(self)

    def incidence_counts(self):
        from mipframe.ops.support import incidence_counts

        return incidence_counts(self)

    def __and__(self, other):
        from mipframe.ops.support import set_operation

        return set_operation(self, other, "intersection")

    def __or__(self, other):
        from mipframe.ops.support import set_operation

        return set_operation(self, other, "union")

    def __sub__(self, other):
        from mipframe.ops.support import set_operation

        return set_operation(self, other, "difference")

    def jaccard(self, other):
        from mipframe.ops.support import jaccard

        return jaccard(self, other)


class _IdentityILoc:
    def __init__(self, frame):
        self._frame = frame

    def __getitem__(self, position):
        self._frame._owner.model
        if isinstance(position, slice):
            from mipframe.frame.base import Locator

            return Locator(self._frame, True)[position]
        if type(position) is int:
            return self._frame._at(position)
        raise TypeError(".iloc accepts an integer position or slice")


class RowFrame(_IdentityFrame):
    """
    MIPFRAME HELP BEGIN
    Deduplicated row entities with sparse variable neighborhoods.

    Full contract: mf.help("api.RowFrame")
    MIPFRAME HELP END
    """
    _display_columns = ("row", "name", "lb", "ub", "nnz")
    _kind = "row"

    @property
    def loc(self):
        """
        MIPFRAME HELP BEGIN
        Look up an original integer ID or same-snapshot Ref within this entity selection.

        Full contract: mf.help("api.RowFrame.loc")
        MIPFRAME HELP END
        """
        return _EntityLoc(self)

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        Read deduplicated neighboring entities.

        Full contract: mf.help("api.RowFrame.vars")
        MIPFRAME HELP END
        """
        return self._neighbors()





    def formula(self, *, order="abs_coef", max_terms=16):
        """Preview selected original formulas in frame order.

        Display at most options.display.max_rows rows and max_terms terms per
        row (the same default as Row.formula). Only visible rows are scanned.
        """
        from mipframe.frame.row import Formula

        self._owner.model
        max_terms = self._size(max_terms)
        if order not in ("abs_coef", "index", "name"):
            raise ValueError("order must be 'abs_coef', 'index', or 'name'")
        lines = [
            f"<RowFrame: {min(len(self), options.display.max_rows):,} of {self._total:,} rows>"
        ]
        for pos in _positions(len(self), options.display.max_rows):
            if pos is None:
                lines.append("...")
            else:
                row = self.iloc[pos]
                lines.append(
                    f"{row.id}  {_text(row.name)}: {row.formula(order=order, max_terms=max_terms)}"
                )
        return Formula("\n".join(lines))


class VarFrame(_IdentityFrame):
    """
    MIPFRAME HELP BEGIN
    Deduplicated variable entities with sparse row neighborhoods.

    Full contract: mf.help("api.VarFrame")
    MIPFRAME HELP END
    """
    _display_columns = ("var", "name", "kind", "lb", "ub", "obj", "type", "nnz")
    _kind = "variable"

    @property
    def loc(self):
        """
        MIPFRAME HELP BEGIN
        Look up an original integer ID or same-snapshot Ref within this entity selection.

        Full contract: mf.help("api.VarFrame.loc")
        MIPFRAME HELP END
        """
        return _EntityLoc(self)

    @property
    def rows(self):
        """
        MIPFRAME HELP BEGIN
        Read deduplicated neighboring entities.

        Full contract: mf.help("api.VarFrame.rows")
        MIPFRAME HELP END
        """
        return self._neighbors()


class _EntityLoc:
    def __init__(self, frame):
        self._frame = frame

    def __getitem__(self, label):
        from mipframe.frame.schema import Ref, SchemaError
        from mipframe._help.diagnostics import hint

        owner = self._frame._owner
        owner.model
        axis = self._frame._entity_axis
        if isinstance(label, Ref):
            if label.kind != axis:
                raise hint(SchemaError(
                    f"Expected Ref[{axis}], received Ref[{label.kind}]. "
                    "Use the matching rows.loc or vars.loc, or ref.bind(model)."
                ), "error.navigation")
            if (label.uid, label.version) != (owner.uid, owner.version):
                raise hint(SchemaError(
                    "Reference and selection snapshot differ (uid/version must match). "
                    "Use ref.bind(model) with the matching snapshot."
                ), "concept.identity")
            label = label.id
        if type(label) is not int:
            raise hint(TypeError(
                f"Expected integer {axis} ID or same-snapshot Ref[{axis}], "
                f"received {type(label).__name__}. Use loc[ref] or ref.bind(model); "
                "use by_name() for names."
            ), "error.navigation")
        return self._frame._by_id(label)



from mipframe.frame.terms import TermFrame
