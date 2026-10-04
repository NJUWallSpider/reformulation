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
from mipframe.frame.base import Frame
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
    Model matrix operations accessed through m.matrix.

    Full contract: mf.help("api.MatrixAccessor")
    MIPFRAME HELP END
    """
    def __init__(self, owner: Model):
        self._owner = owner

    def nnz(self, *, rows=None, vars=None, axis="row"):
        """
        MIPFRAME HELP BEGIN
        Access the original sparse matrix: nnz.

        Full contract: mf.help("api.MatrixAccessor.nnz")
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
        Access the original sparse matrix: activity.

        Full contract: mf.help("api.MatrixAccessor.activity")
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
        Access the original sparse matrix: transpose_activity.

        Full contract: mf.help("api.MatrixAccessor.transpose_activity")
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
        Access the original sparse matrix: view.

        Full contract: mf.help("api.MatrixAccessor.view")
        MIPFRAME HELP END
        """
        return MatrixView(self._owner, rows=rows, vars=vars)


class MatrixView:
    """
    Read-only logical slice; coefficients stay in the model until requested.

    MIPFRAME HELP BEGIN
    Logical sparse slice with explicit preview and float64 export.

    Full contract: mf.help("api.MatrixView")
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
        Read selected matrix rows.

        Full contract: mf.help("api.MatrixView.rows")
        MIPFRAME HELP END
        """
        self._owner.model
        return self._rows

    @property
    def vars(self) -> VarFrame:
        """
        MIPFRAME HELP BEGIN
        Read selected matrix vars.

        Full contract: mf.help("api.MatrixView.vars")
        MIPFRAME HELP END
        """
        self._owner.model
        return self._vars

    @property
    def shape(self) -> tuple[int, int]:
        """
        MIPFRAME HELP BEGIN
        Read selected matrix shape.

        Full contract: mf.help("api.MatrixView.shape")
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
        Read selected matrix nnz.

        Full contract: mf.help("api.MatrixView.nnz")
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
        Read selected matrix density.

        Full contract: mf.help("api.MatrixView.density")
        MIPFRAME HELP END
        """
        rows, variables = self.shape
        return self.nnz / (rows * variables) if rows and variables else 0.0

    @property
    def row_nnz(self):
        """
        MIPFRAME HELP BEGIN
        Read selected matrix row_nnz.

        Full contract: mf.help("api.MatrixView.row_nnz")
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
        Read selected matrix var_nnz.

        Full contract: mf.help("api.MatrixView.var_nnz")
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
        Original selected terms as a TermFrame; no dense conversion.

        Full contract: mf.help("api.MatrixView.entries")
        MIPFRAME HELP END
        """
        return self._owner.terms(rows=self._rows, vars=self._vars)

    def preview(
        self, *, max_rows: int = 8, max_vars: int = 12, choose_vars: str = "most_active"
    ) -> Formula:
        """
        Render a bounded exact grid; rank columns over all selected rows.

        MIPFRAME HELP BEGIN
        Explicit small exact preview; default 8 rows by 12 variables.

        Full contract: mf.help("api.MatrixView.preview")
        MIPFRAME HELP END
        """
        m = self._owner.model
        Frame._size(max_rows)
        Frame._size(max_vars)
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
        Export independent float64 CSR with local axes matching rows.ids and vars.ids.

        Full contract: mf.help("api.MatrixView.to_scipy")
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
