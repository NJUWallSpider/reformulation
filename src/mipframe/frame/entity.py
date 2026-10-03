"""Lazy numerical row/variable frames and bounded plain-text displays."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from collections.abc import Sequence
from fractions import Fraction
from math import inf
from math import isfinite
import random
from typing import TYPE_CHECKING

import numpy as np

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


class ValueCounts(dict):
    """Count snapshot; dictionary access plus a bounded table representation."""

    def __init__(self, column, counts):
        self.column = column
        super().__init__(
            sorted(counts.items(), key=lambda pair: (-pair[1], _text(pair[0])))
        )

    def __repr__(self):
        keys = tuple(self)
        return _table(
            f"<ValueCounts: {self.column}>",
            ("count",),
            keys,
            lambda i: {"count": self[keys[i]]},
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))

    def head(self, n=5):
        from itertools import islice

        return type(self)(self.column, dict(islice(self.items(), _Frame._size(n))))

    def tail(self, n=5):
        n = _Frame._size(n)
        return type(self)(
            self.column, dict(list(self.items())[max(0, len(self) - n) :])
        )


class Description(dict):
    """Exact finite numeric statistics, keyed by column then statistic."""

    def __repr__(self):
        stats = ("count", "mean", "min", "max")
        return _table(
            "<Description: finite numeric values>",
            tuple(self),
            stats,
            lambda i: {column: values[stats[i]] for column, values in self.items()},
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


class _Frame:
    """Read-only, lazy selection retaining original model identities."""

    columns: tuple[str, ...]
    _numeric: tuple[str, ...]
    _kind: str
    _unit: str

    def __init__(self, owner: Model, index: Sequence[int] | None = None):
        self._owner = owner
        self._index = range(len(self._metadata)) if index is None else index

    @property
    def _metadata(self):
        m = self._owner.model
        return m.rows if self._kind == "row" else m.variables

    @property
    def ids(self) -> Sequence[int]:
        self._owner.model
        return self._index

    @property
    def shape(self) -> tuple[int, int]:
        return len(self), len(self.columns)

    def __len__(self) -> int:
        self._owner.model
        return len(self._index)

    def __getitem__(self, position: int | slice):
        self._owner.model
        if isinstance(position, slice):
            return self._select(self._index[position])
        if type(position) is not int:
            raise TypeError("Use an integer position or slice")
        return self._record(self._metadata[self._index[position]])

    def _select(self, index):
        selection = type(self)(self._owner, index)
        if hasattr(self, "_selection_total"):
            selection._selection_total = self._selection_total
        return selection

    @property
    def _total(self):
        self._owner.model
        return (
            self._selection_total
            if hasattr(self, "_selection_total")
            else len(self._metadata)
        )

    def __iter__(self) -> Iterator[dict]:
        metadata = self._metadata
        ids = self._index
        if isinstance(ids, range) and ids.step == 1:
            table = "rows" if self._kind == "row" else "variables"
            cursor = self._owner.model.db.execute(
                f"SELECT * FROM {table} WHERE id >= ? AND id < ? ORDER BY id",
                (ids.start, ids.stop),
            )
            for record in cursor:
                self._owner.model
                yield self._record(record)
        else:
            for ident in ids:
                self._owner.model
                yield self._record(metadata[ident])

    @staticmethod
    def _size(n):
        if type(n) is not int:
            raise TypeError("n must be an integer")
        if n < 0:
            raise ValueError("n must be nonnegative")
        return n

    def head(self, n: int = 5):
        return self[: self._size(n)]

    def tail(self, n: int = 5):
        n = self._size(n)
        return self[max(0, len(self) - n) :]

    def sample(self, n: int = 5, *, random_state: int | None = None):
        n = self._size(n)
        positions = random.Random(random_state).sample(range(len(self)), n)
        return self._select(tuple(self._index[i] for i in positions))

    def to_records(self) -> list[dict]:
        """Materialize selected records as independent dictionaries."""
        return list(self)

    def describe(self) -> Description:
        """Stream count/mean/min/max, excluding absent and infinite values."""
        result = Description(
            {
                col: {"count": 0, "mean": None, "min": None, "max": None}
                for col in self._numeric
            }
        )
        totals = dict.fromkeys(self._numeric, Fraction(0))
        for record in self:
            for col in self._numeric:
                value = record[col]
                if not isinstance(value, (int, Fraction, float)) or (
                    isinstance(value, float) and not isfinite(value)
                ):
                    continue
                stats = result[col]
                stats["count"] += 1
                totals[col] += value
                stats["min"] = (
                    value if stats["min"] is None else min(stats["min"], value)
                )
                stats["max"] = (
                    value if stats["max"] is None else max(stats["max"], value)
                )
        for col, stats in result.items():
            if stats["count"]:
                stats["mean"] = totals[col] / stats["count"]
        return result

    def value_counts(self, column: str) -> ValueCounts:
        if column not in self.columns:
            raise KeyError(column)
        # Categorical summaries do not need coefficients or numeric conversion.
        if column in ("sense", "type") and self._kind in ("row", "variable"):
            counts = Counter()
            ids = self.ids
            m = self._owner.model
            if isinstance(ids, range) and ids.step == 1:
                expr = (
                    "kind"
                    if column == "type"
                    else (
                        "CASE WHEN lo IS NULL AND hi IS NULL THEN 'free' "
                        "WHEN lo IS NULL THEN '<=' WHEN hi IS NULL THEN '>=' "
                        "WHEN lo=hi THEN '=' ELSE 'range' END"
                    )
                )
                table = "variables" if column == "type" else "rows"
                counts.update(
                    dict(
                        m.db.execute(
                            f"SELECT {expr}, count(*) FROM {table} "
                            f"WHERE id >= ? AND id < ? GROUP BY {expr}",
                            (ids.start, ids.stop),
                        )
                    )
                )
            else:
                for ident in ids:
                    row = self._metadata[ident]
                    key = row[2] if column == "type" else _sense(row[2], row[3])
                    counts[key] += 1
            return ValueCounts(column, counts)
        return ValueCounts(column, Counter(record[column] for record in self))

    def __repr__(self) -> str:
        total = self._total
        shown = min(len(self), options.display.max_rows)
        return _table(
            f"<{type(self).__name__}: {shown:,} of {total:,} {self._unit}>",
            self.columns,
            self.ids,
            lambda i: self[i],
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))


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
            index = range(
                len(
                    owner.model.rows
                    if self._entity_axis == "row"
                    else owner.model.variables
                )
            )
        elif not isinstance(index, range) and not getattr(index, "unique", False):
            from numbers import Integral

            original = index
            maximum = len(
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
        maximum = len(
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
        self._source_spec = {
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

    _size = staticmethod(_Frame._size)

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
        RowFrame.ids [api.RowFrame.ids]

        Read original IDs.

        RowFrame.ids [property]

        When to use: Read original IDs.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: concept.frame
        MIPFRAME HELP END
        """
        self._guard()
        return self._index

    def family(self, label):
        """
        MIPFRAME HELP BEGIN
        RowFrame.family [api.RowFrame.family]

        Select a family from the bound structural signature configuration.

        RowFrame.family(label)

        When to use: Select a family from the bound structural signature configuration.

        Returns: Entity Frame; family labels are local to snapshot/configuration/depth.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.store.incidence import Target

        target = Target(self._owner, self._entity_axis, label)
        from mipframe.frame.expr import col

        if isinstance(self.ids, range) and self.ids == range(len(self._metadata)):
            return target.frame
        return self.where(col(self._entity_axis).isin(target.frame))

    @property
    def terms(self):
        """
        MIPFRAME HELP BEGIN
        RowFrame.terms [api.RowFrame.terms]

        Read original terms.

        RowFrame.terms [property]

        When to use: Read original terms.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.terms import TermFrame

        return TermFrame(
            self._owner,
            **({"rows": self} if self._entity_axis == "row" else {"vars": self}),
        )

    def family_membership(self, *, depth=None, signatures=None):
        """
        MIPFRAME HELP BEGIN
        RowFrame.family_membership [api.RowFrame.family_membership]

        Expose family membership as a normalized relation.

        RowFrame.family_membership(*, depth=None, signatures=None)

        When to use: Expose family membership as a normalized relation.

        Returns: Frame retaining explicit signature configuration and depth.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import membership

        return membership(self, depth, signatures)

    def value_counts(self, column):
        if column == "family":
            return self.family_membership().column("family").value_counts()
        return self.column(column).value_counts()

    def pattern_counts(self, column="family", *, depth=None, memory_mb=128):
        """
        MIPFRAME HELP BEGIN
        RowFrame.pattern_counts [api.RowFrame.pattern_counts]

        Count source entities by their family-neighborhood pattern.

        RowFrame.pattern_counts(column='family', *, depth=None, memory_mb=128)

        When to use: Count source entities by their family-neighborhood pattern.

        Returns: Ordinary Frame with normalized pattern items/members in relations.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        if column != "family":
            raise ValueError("pattern_counts currently supports family")
        from mipframe.frame.adapters import pattern_counts

        return pattern_counts(self, depth=depth, memory_mb=memory_mb)

    def project(self, *, exactly_one=True, memory_mb=128, **bindings):
        """
        MIPFRAME HELP BEGIN
        RowFrame.project [api.RowFrame.project]

        Map each source to exactly one neighbor in every named target domain.

        RowFrame.project(*, exactly_one=True, memory_mb=128, **bindings)

        When to use: Map each source to exactly one neighbor in every named target domain.

        Returns: Ordinary Frame keyed by row/var, with named Ref columns and declared target domains.

        Does not establish: Adjacency cardinality does not establish coefficient equality or product coverage.

        Failures: Projection zero or multiple matches raises CardinalityError with complete violations and matches.

        Illustrative example (requires caller-provided inputs):
        P = V.project(i=A, j=B)
        P.check("product", ("i", "j")).require()

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.check.frame import project

        return project(self, bindings, exactly_one=exactly_one, memory_mb=memory_mb)

    def where(self, *predicates, **equalities):
        if "family" in equalities:
            selected = self.family(equalities.pop("family"))
            return (
                Frame.where(selected, *predicates, **equalities)
                if predicates or equalities
                else selected
            )
        return Frame.where(self, *predicates, **equalities)

    select = Frame.select
    join = Frame.join
    group_by = Frame.group_by
    sort_by = Frame.sort_by
    distinct = Frame.distinct
    sample = Frame.sample
    column = Frame.column
    collect = Frame.collect
    to_records = Frame.to_records
    one = Frame.one
    __getitem__ = Frame.__getitem__
    __repr__ = Frame.__repr__

    @property
    def iloc(self):
        """Position-based access in this selection, independent of model IDs."""
        self._owner.model
        return _IdentityILoc(self)

    def by_id(self, ident):
        """Look up an original model ID within this selection."""
        self._owner.model
        if type(ident) is not int:
            raise TypeError("Model IDs must be integers")
        if not 0 <= ident < len(self._metadata) or ident not in self.ids:
            raise KeyError(ident)
        return self._entity(ident)

    def by_name(self, name):
        """
        Look up an original name, including names resembling family labels.

        MIPFRAME HELP BEGIN
        RowFrame.by_name [api.RowFrame.by_name]

        Find an entity by its original name explicitly.

        RowFrame.by_name(name)

        When to use: Find an entity by its original name explicitly.

        Returns: One live entity; missing names raise KeyError.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        m = self._owner.model
        if not isinstance(name, str):
            raise TypeError("Names must be strings")
        names = m.row_names if self._kind == "row" else m.var_names
        return self.by_id(names[name])

    def head(self, n=5):
        return Frame.head(self, n)

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

    def at(self, position):
        """
        MIPFRAME HELP BEGIN
        RowFrame.at [api.RowFrame.at]

        Find one entity by positional index.

        RowFrame.at(position)

        When to use: Find one entity by positional index.

        Returns: Row or Var; negative positions allowed, bool rejected.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        if type(position) is not int:
            raise TypeError("position must be an integer")
        return self._entity(self.ids[position])

    def neighbors(self):
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
            return self._frame.at(position)
        raise TypeError(".iloc accepts an integer position or slice")


class RowFrame(_IdentityFrame):
    """
    MIPFRAME HELP BEGIN
    RowFrame [api.RowFrame]

    Deduplicated row entities with sparse variable neighborhoods.

    RowFrame [type]

    When to use: Deduplicated row entities with sparse variable neighborhoods.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.frame, api.RowFrame.project
    MIPFRAME HELP END
    """
    _display_columns = ("row", "name", "lb", "ub", "nnz")
    _numeric = ("lhs", "rhs", "nnz", "coef_min", "coef_max")
    _kind, _unit = "row", "rows"

    @property
    def loc(self):
        """
        MIPFRAME HELP BEGIN
        RowFrame.loc [api.RowFrame.loc]

        Look up an original integer ID or same-snapshot Ref within this entity selection.

        RowFrame.loc [property]

        When to use: Look up an original integer ID or same-snapshot Ref within this entity selection.

        Inputs: Integer row ID or Ref[row] with matching snapshot uid/version.

        Returns: One live entity in this selection.

        Establishes: Ref axis and snapshot are checked before ID lookup. Selection membership is always enforced.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        Illustrative example (requires caller-provided inputs):
        entity = m.rows.loc[ref]

        See also: concept.identity, api.Ref.bind, error.navigation
        MIPFRAME HELP END
        """
        return _RowLoc(self)

    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        RowFrame.vars [api.RowFrame.vars]

        Read deduplicated neighboring entities.

        RowFrame.vars [property]

        When to use: Read deduplicated neighboring entities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return self.neighbors()

    def refinement(self, start=1, stop=2, *, signature="structural", **options):
        from mipframe.ir.signatures import signature_result
        from mipframe.ir.display import RefinementDelta
        from mipframe.ir.display import validate_transition

        validate_transition(start, stop, "row")
        if signature not in ("structural", "exact_structural", "exact_strctural"):
            raise ValueError("refinement requires a structural signature")
        result = signature_result(self._owner, signature, stop, options)
        return RefinementDelta(result, start, stop, frame=self)

    def families(self, signature=None, *, depth=None, **options):
        if "level" in options:
            raise TypeError("families() got an unexpected keyword argument 'level'")
        signature = "algebratic" if signature is None else signature
        if signature in ("structural", "exact_structural", "exact_strctural"):
            from mipframe.ir.signatures import structural_families
            from mipframe.ir.signatures import signature_result

            result = signature_result(self._owner, signature, depth, options)
            from mipframe.frame.adapters import family_directory

            return family_directory(structural_families(result, self, result.depth))
        if depth is not None or options:
            raise ValueError(
                "depth and refinement options require signature='structural'"
            )
        from mipframe.ir.families import families
        from mipframe.frame.adapters import family_directory

        return family_directory(families(self, signature))

    def signature(self, signature="algebratic", *, depth=None, **options):
        if signature in ("structural", "exact_structural", "exact_strctural"):
            from mipframe.ir.signatures import signature_result

            result = signature_result(self._owner, signature, depth, options)
            from mipframe.frame.expr import col

            return result.rows.where(col("row").isin(self))
        if depth is not None or options:
            raise ValueError(
                "depth and refinement options require signature='structural'"
            )
        from mipframe.frame.adapters import signature_frame

        return signature_frame(self, signature)

    def show(self, *, formula=False, max_terms=160, order="abs_coef"):
        from mipframe.frame.row import Formula

        self._owner.model
        if not formula:
            return Formula(repr(self))
        return self.formula(order=order, max_terms=max_terms)

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
                row = self.at(pos)
                lines.append(
                    f"{row.id}  {_text(row.name)}: {row.formula(order=order, max_terms=max_terms)}"
                )
        return Formula("\n".join(lines))

    def _record(self, row):
        return self._entity(row[0])


class VarFrame(_IdentityFrame):
    """
    MIPFRAME HELP BEGIN
    VarFrame [api.VarFrame]

    Deduplicated variable entities with sparse row neighborhoods.

    VarFrame [type]

    When to use: Deduplicated variable entities with sparse row neighborhoods.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.frame, api.VarFrame.project
    MIPFRAME HELP END
    """
    _display_columns = ("var", "name", "kind", "lb", "ub", "obj", "type", "nnz")
    _numeric = ("lb", "ub", "obj", "nnz")
    _kind, _unit = "variable", "vars"

    @property
    def loc(self):
        """
        MIPFRAME HELP BEGIN
        VarFrame.loc [api.VarFrame.loc]

        Look up an original integer ID or same-snapshot Ref within this entity selection.

        VarFrame.loc [property]

        When to use: Look up an original integer ID or same-snapshot Ref within this entity selection.

        Inputs: Integer var ID or Ref[var] with matching snapshot uid/version.

        Returns: One live entity in this selection.

        Establishes: Ref axis and snapshot are checked before ID lookup. Selection membership is always enforced.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        Illustrative example (requires caller-provided inputs):
        entity = m.vars.loc[ref]

        See also: concept.identity, api.Ref.bind, error.navigation
        MIPFRAME HELP END
        """
        return _VarLoc(self)

    @property
    def rows(self):
        """
        MIPFRAME HELP BEGIN
        VarFrame.rows [api.VarFrame.rows]

        Read deduplicated neighboring entities.

        VarFrame.rows [property]

        When to use: Read deduplicated neighboring entities.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No coefficient or mathematical equivalence is inferred from navigation.

        Failures: Invalid selections, unknown labels/names and closed owners raise explicit errors.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return self.neighbors()

    def _record(self, row):
        return self._entity(row[0])


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
        return self._frame.by_id(label)


class _VarLoc(_EntityLoc):
    pass


class _RowLoc(_EntityLoc):
    pass


from mipframe.frame.terms import TermFrame
