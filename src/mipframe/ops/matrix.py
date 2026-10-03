"""Logical sparse matrix selections, bounded inspection and explicit export."""

from __future__ import annotations

from collections.abc import Iterator
from fractions import Fraction
from heapq import nsmallest
from itertools import islice
from math import isfinite
from numbers import Integral
from typing import TYPE_CHECKING

from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from mipframe.frame.entity import _Frame
from mipframe.frame.entity import _text
from mipframe.store.numbers import MIPFrameError
from mipframe.options import options
from mipframe.frame.row import Formula

if TYPE_CHECKING:
    from mipframe.model import Model


def _axis(owner, frame, cls):
    """Snapshot arbitrary selections, but retain lazy unique family indices."""
    if frame is None:
        return cls(owner)
    if not isinstance(frame, cls):
        raise TypeError(f"Matrix selection requires a {cls.__name__} or None")
    owner.model
    frame._owner.model
    if (frame._owner.uid, frame._owner.version) != (owner.uid, owner.version):
        raise ValueError("Matrix selections require frames from the same model")
    index = frame.ids
    size = len(owner.model.rows if cls is RowFrame else owner.model.variables)
    if isinstance(index, range):
        if index and (min(index[0], index[-1]) < 0 or max(index[0], index[-1]) >= size):
            raise ValueError("Matrix selection contains an invalid model ID")
    elif not getattr(index, "unique", False):
        ids = {}
        for ident in index:
            if isinstance(ident, bool) or not isinstance(ident, Integral):
                raise TypeError("Matrix frame indices must be integer model IDs")
            if not 0 <= ident < size:
                raise ValueError("Matrix selection contains an invalid model ID")
            ids[int(ident)] = None
        index = tuple(ids)
    return cls(owner, index)


def _grid(lines):
    """Format an already bounded grid without applying global frame limits."""
    widths = [max(len(line[i]) for line in lines) for i in range(len(lines[0]))]
    return "\n".join(
        "  ".join(cell.rjust(width) for cell, width in zip(line, widths)).rstrip()
        for line in lines
    )


class MatrixAccessor:
    """
    MIPFRAME HELP BEGIN
    MatrixAccessor [api.MatrixAccessor]

    Model matrix operations accessed through m.matrix.

    MatrixAccessor [type]

    When to use: Model matrix operations accessed through m.matrix.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.MatrixAccessor.nnz
    MIPFRAME HELP END
    """
    def __init__(self, owner: Model):
        self._owner = owner

    def nnz(self, *, rows=None, vars=None, axis="row"):
        """
        MIPFRAME HELP BEGIN
        MatrixAccessor.nnz [api.MatrixAccessor.nnz]

        Access the original sparse matrix: nnz.

        MatrixAccessor.nnz(*, rows=None, vars=None, axis='row')

        When to use: Access the original sparse matrix: nnz.

        Inputs: Same-snapshot axes; activity input is unique Frame(var,value), transpose input Frame(row,value).

        Returns: Frame(row,nnz) or Frame(var,nnz), including zero-degree axis members.

        Establishes: Activity is exact and requires all participating inputs unless missing="zero".

        Does not establish: No model change or additional mathematical conclusion is implied.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        See also: concept.frame, concept.exact
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import sparse_counts

        return sparse_counts(self._owner, rows, vars, axis)

    def activity(
        self,
        values,
        *,
        rows=None,
        value="value",
        missing="error",
        memory_mb=128,
        max_output_rows=1_000_000,
        disk_mb=None,
    ):
        """
        MIPFRAME HELP BEGIN
        MatrixAccessor.activity [api.MatrixAccessor.activity]

        Access the original sparse matrix: activity.

        MatrixAccessor.activity(values, *, rows=None, value='value', missing='error', memory_mb=128, max_output_rows=1000000, disk_mb=None)

        When to use: Access the original sparse matrix: activity.

        Inputs: Same-snapshot axes; activity input is unique Frame(var,value), transpose input Frame(row,value).

        Returns: Frame(row,activity), including zero rows.

        Establishes: Activity is exact and requires all participating inputs unless missing="zero".

        Does not establish: No model change or additional mathematical conclusion is implied.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        See also: concept.frame, concept.exact
        MIPFRAME HELP END
        """
        from mipframe.ops._matrix import activity

        selected = self._owner.rows if rows is None else rows
        if not isinstance(selected, RowFrame) or (
            selected._owner.uid,
            selected._owner.version,
        ) != (self._owner.uid, self._owner.version):
            raise ValueError("rows must belong to the same snapshot")
        self._owner.model
        from mipframe.frame.base import merge_models

        models = merge_models(self._owner.rows, selected)
        selected = self._owner.rows._select(selected.ids)
        selected._models = models
        return activity(
            selected,
            values,
            value=value,
            missing=missing,
            memory_mb=memory_mb,
            max_output_rows=max_output_rows,
            disk_mb=disk_mb,
        )

    def transpose_activity(
        self,
        multipliers,
        *,
        vars=None,
        value="value",
        missing="error",
        memory_mb=128,
        max_output_rows=1_000_000,
        disk_mb=None,
    ):
        """
        MIPFRAME HELP BEGIN
        MatrixAccessor.transpose_activity [api.MatrixAccessor.transpose_activity]

        Access the original sparse matrix: transpose_activity.

        MatrixAccessor.transpose_activity(multipliers, *, vars=None, value='value', missing='error', memory_mb=128, max_output_rows=1000000, disk_mb=None)

        When to use: Access the original sparse matrix: transpose_activity.

        Inputs: Same-snapshot axes; activity input is unique Frame(var,value), transpose input Frame(row,value).

        Returns: Frame(var,value), including zero columns.

        Establishes: Activity is exact and requires all participating inputs unless missing="zero".

        Does not establish: No model change or additional mathematical conclusion is implied.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        See also: concept.frame, concept.exact
        MIPFRAME HELP END
        """
        from mipframe.ops._matrix import transpose_activity

        selected = self._owner.vars if vars is None else vars
        if not isinstance(selected, VarFrame) or (
            selected._owner.uid,
            selected._owner.version,
        ) != (self._owner.uid, self._owner.version):
            raise ValueError("vars must belong to the same snapshot")
        self._owner.model
        from mipframe.frame.base import merge_models

        models = merge_models(self._owner.vars, selected)
        selected = self._owner.vars._select(selected.ids)
        selected._models = models
        return transpose_activity(
            selected,
            multipliers,
            value=value,
            missing=missing,
            memory_mb=memory_mb,
            max_output_rows=max_output_rows,
            disk_mb=disk_mb,
        )

    def view(
        self, *, rows: RowFrame | None = None, vars: VarFrame | None = None
    ) -> MatrixView:
        """
        Select original constraint coefficients; None selects the full axis.

        MIPFRAME HELP BEGIN
        MatrixAccessor.view [api.MatrixAccessor.view]

        Access the original sparse matrix: view.

        MatrixAccessor.view(*, rows: 'RowFrame | None' = None, vars: 'VarFrame | None' = None) -> 'MatrixView'

        When to use: Access the original sparse matrix: view.

        Inputs: Same-snapshot axes; activity input is unique Frame(var,value), transpose input Frame(row,value).

        Returns: MatrixView retaining original selected axis order.

        Establishes: Activity is exact and requires all participating inputs unless missing="zero".

        Does not establish: No model change or additional mathematical conclusion is implied.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        See also: concept.frame, concept.exact
        MIPFRAME HELP END
        """
        return MatrixView(self._owner, rows=rows, vars=vars)


class MatrixView:
    """
    Read-only logical slice; coefficients stay in the model until requested.

    MIPFRAME HELP BEGIN
    MatrixView [api.MatrixView]

    Logical sparse slice with explicit preview and float64 export.

    MatrixView [type]

    When to use: Logical sparse slice with explicit preview and float64 export.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.MatrixView.preview
    MIPFRAME HELP END
    """

    def __init__(
        self,
        owner: Model,
        *,
        rows: RowFrame | None = None,
        vars: VarFrame | None = None,
    ):
        owner.model
        self._owner = owner
        self._rows = _axis(owner, rows, RowFrame)
        self._vars = _axis(owner, vars, VarFrame)
        self._all_rows = len(self._rows) == len(owner.model.rows)
        self._all_vars = len(self._vars) == len(owner.model.variables)
        self._var_members = None
        self._nnz = None
        self._row_counts = None
        self._var_counts = None

    @property
    def rows(self) -> RowFrame:
        """
        MIPFRAME HELP BEGIN
        MatrixView.rows [api.MatrixView.rows]

        Read selected matrix rows.

        MatrixView.rows [property]

        When to use: Read selected matrix rows.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        self._owner.model
        return self._rows

    @property
    def vars(self) -> VarFrame:
        """
        MIPFRAME HELP BEGIN
        MatrixView.vars [api.MatrixView.vars]

        Read selected matrix vars.

        MatrixView.vars [property]

        When to use: Read selected matrix vars.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        self._owner.model
        return self._vars

    @property
    def shape(self) -> tuple[int, int]:
        """
        MIPFRAME HELP BEGIN
        MatrixView.shape [api.MatrixView.shape]

        Read selected matrix shape.

        MatrixView.shape [property]

        When to use: Read selected matrix shape.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        return len(self.rows), len(self.vars)

    def _positions(self, rows=None):
        """Yield (original row ID, original var ID, CSR position), no values."""
        m = self._owner.model
        if not len(self._vars):
            return
        if not self._all_vars and self._var_members is None:
            ids = self._vars.ids
            self._var_members = ids if isinstance(ids, range) else frozenset(ids)
        for row in self._rows.ids if rows is None else rows:
            self._owner.model
            for pos in range(int(m.row_ptr[row]), int(m.row_ptr[row + 1])):
                self._owner.model
                var = int(m.col_idx[pos])
                if self._all_vars or var in self._var_members:
                    yield row, var, pos

    def _counts(self, axis):
        from mipframe.frame.adapters import sparse_counts
        from mipframe.frame.executor import Context
        from mipframe.frame.materialized import stored_frame

        ctx = Context((self._owner,))
        try:
            source = sparse_counts(self._owner, self._rows, self._vars, axis)
            table, schema = ctx.stage(source)
            return stored_frame(ctx, table, schema, key=(axis,))
        except BaseException:
            ctx.close()
            raise

    def _scan_counts(self):
        if self._row_counts is None:
            self._row_counts = self._counts("row")
        self._nnz = int(self._row_counts.column("nnz").sum())

    @property
    def nnz(self) -> int:
        """
        MIPFRAME HELP BEGIN
        MatrixView.nnz [api.MatrixView.nnz]

        Read selected matrix nnz.

        MatrixView.nnz [property]

        When to use: Read selected matrix nnz.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        m = self._owner.model
        if self._nnz is None:
            if not all(self.shape):
                self._nnz = 0
            elif self._all_rows and self._all_vars:
                self._nnz = len(m.col_idx)
            elif self._all_vars:
                self._nnz = sum(
                    int(m.row_ptr[r + 1] - m.row_ptr[r]) for r in self._rows.ids
                )
            elif self._all_rows:
                self._nnz = sum(
                    int(m.col_ptr[v + 1] - m.col_ptr[v]) for v in self._vars.ids
                )
            else:
                self._scan_counts()
        return self._nnz

    @property
    def density(self) -> float:
        """
        MIPFRAME HELP BEGIN
        MatrixView.density [api.MatrixView.density]

        Read selected matrix density.

        MatrixView.density [property]

        When to use: Read selected matrix density.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        rows, variables = self.shape
        return self.nnz / (rows * variables) if rows and variables else 0.0

    @property
    def row_nnz(self):
        """
        MIPFRAME HELP BEGIN
        MatrixView.row_nnz [api.MatrixView.row_nnz]

        Read selected matrix row_nnz.

        MatrixView.row_nnz [property]

        When to use: Read selected matrix row_nnz.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        self._owner.model
        if self._row_counts is None:
            self._row_counts = self._counts("row")
        return self._row_counts

    @property
    def var_nnz(self):
        """
        MIPFRAME HELP BEGIN
        MatrixView.var_nnz [api.MatrixView.var_nnz]

        Read selected matrix var_nnz.

        MatrixView.var_nnz [property]

        When to use: Read selected matrix var_nnz.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        self._owner.model
        if self._var_counts is None:
            self._var_counts = self._counts("var")
        return self._var_counts

    @property
    def entries(self):
        """
        MIPFRAME HELP BEGIN
        MatrixView.entries [api.MatrixView.entries]

        Original selected terms as a TermFrame; no dense conversion.

        MatrixView.entries [property]

        When to use: Original selected terms as a TermFrame; no dense conversion.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        return self._owner.terms(rows=self._rows, vars=self._vars)

    def preview(
        self, *, max_rows: int = 8, max_vars: int = 12, choose_vars: str = "most_active"
    ) -> Formula:
        """
        Render a bounded exact grid; rank columns over all selected rows.

        MIPFRAME HELP BEGIN
        MatrixView.preview [api.MatrixView.preview]

        Explicit small exact preview; default 8 rows by 12 variables.

        MatrixView.preview(*, max_rows: 'int' = 8, max_vars: 'int' = 12, choose_vars: 'str' = 'most_active') -> 'Formula'

        When to use: Explicit small exact preview; default 8 rows by 12 variables.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        m = self._owner.model
        _Frame._size(max_rows)
        _Frame._size(max_vars)
        if choose_vars not in ("most_active", "first", "largest_coef"):
            raise ValueError(
                "choose_vars must be 'most_active', 'first', or 'largest_coef'"
            )
        nrows, nvars = self.shape
        rows = list(islice(self._rows.ids, max_rows))
        # An empty grid needs neither a coefficient scan nor column ranking.
        if not rows or not max_vars or not nvars:
            shown_vars = min(max_vars, nvars)
            return Formula(
                f"{len(rows)} rows × {shown_vars} vars shown\n"
                f"selected from {nrows:,} × {nvars:,}; choose_vars={choose_vars}\n"
                "(empty preview)"
            )
        if choose_vars == "first":
            variables = list(islice(self._vars.ids, max_vars))
        else:
            if choose_vars == "most_active":
                values = self.var_nnz.column("nnz")
            else:
                from mipframe.frame.expr import col

                maximum = (
                    self.entries.select("var", score=abs(col("coef")))
                    .group_by("var")
                    .agg(score=col("score").max())
                )
                values = (
                    self._vars.select("var")
                    .join(maximum, on="var", how="left")
                    .column("score")
                )
            ranked = nsmallest(
                min(max_vars, nvars),
                (
                    (i, v, 0 if score is None else score)
                    for i, (v, score) in enumerate(zip(self._vars.ids, values))
                ),
                key=lambda item: (-item[2], item[0]),
            )
            variables = [var for _, var, _ in ranked]
        wanted = set(variables)
        cells = {}
        for row, var, pos in self._positions(rows):
            if var in wanted:
                cells[row, var] = _text(m.num(int(m.coeff_id[pos])))
        more_rows, more_vars = len(rows) < nrows, len(variables) < nvars
        lines = [["", *[_text(m.variables[v][1]) for v in variables]]]
        if more_vars:
            lines[0].append("...")
        for row in rows:
            line = [
                _text(m.rows[row][1]),
                *[cells.get((row, v), ".") for v in variables],
            ]
            if more_vars:
                line.append("...")
            lines.append(line)
        if more_rows:
            lines.append(["..."] * len(lines[0]))
        return Formula(
            f"{len(rows)} rows × {len(variables)} vars shown\n"
            f"selected from {nrows:,} × {nvars:,}; choose_vars={choose_vars}\n\n"
            + _grid(lines)
        )

    def to_scipy(self):
        """
        Copy this selection to float64 CSR, in rows.ids / vars.ids order.

                        Ordinary float rounding is allowed; overflow and nonzero underflow fail.
                        The returned matrix remains usable after the model closes.

        MIPFRAME HELP BEGIN
        MatrixView.to_scipy [api.MatrixView.to_scipy]

        Export independent float64 CSR with local axes matching rows.ids and vars.ids.

        MatrixView.to_scipy()

        When to use: Export independent float64 CSR with local axes matching rows.ids and vars.ids.

        Establishes: Preview most_active ranks over all selected rows; largest_coef uses exact maximum absolute values; ties use selected axis order.

        Does not establish: Automatic column selection need not be active in the displayed row prefix. SciPy conversion is inexact and explicit.

        Failures: SciPy requires mipframe[scipy]; nonfinite/overflow/nonzero underflow conversion fails.

        See also: concept.resources, concept.exact
        MIPFRAME HELP END
        """
        m = self._owner.model
        try:
            from scipy.sparse import csr_matrix
        except ImportError as exc:
            raise MIPFrameError("Matrix export requires mipframe[scipy]") from exc
        import numpy as np

        nrows, nvars = self.shape
        nnz = self.nnz
        data = np.empty(nnz, dtype=np.float64)
        indices = np.empty(nnz, dtype=np.int64)
        indptr = np.empty(nrows + 1, dtype=np.int64)
        var_ids = self._vars.ids
        natural_order = isinstance(var_ids, range) and var_ids == range(nvars)
        positions = None if natural_order else {v: i for i, v in enumerate(var_ids)}
        cursor = 0
        indptr[0] = 0
        for i, row in enumerate(self._rows.ids):
            for _, var, pos in self._positions((row,)):
                exact = m.num(int(m.coeff_id[pos]))
                try:
                    value = float(exact)
                except (OverflowError, ValueError) as exc:
                    raise MIPFrameError(
                        "Matrix coefficient cannot be represented in finite float64"
                    ) from exc
                if not isfinite(value):
                    raise MIPFrameError(
                        "Matrix coefficient cannot be represented in finite float64"
                    )
                if value == 0 and exact != 0:
                    raise MIPFrameError(
                        "Nonzero matrix coefficient underflows to zero in float64"
                    )
                data[cursor] = value
                indices[cursor] = var if natural_order else positions[var]
                cursor += 1
            indptr[i + 1] = cursor
        result = csr_matrix((data, indices, indptr), shape=(nrows, nvars))
        result.sort_indices()
        return result

    def __repr__(self) -> str:
        rows, variables = self.shape
        value = self.density
        density = f"{value:.2e}" if 0 < value < 0.001 else f"{value:.3g}"
        if "e" in density:
            mantissa, exponent = density.split("e")
            mantissa = mantissa.rstrip("0").rstrip(".")
            density = f"{mantissa}e{int(exponent):+d}"
        return (
            "MatrixView\n"
            f"  shape   : {rows:,} × {variables:,}\n"
            f"  nnz     : {self.nnz:,}\n"
            f"  density : {density}\n\n"
            "  display : not materialized"
        )

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))
