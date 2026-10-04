"""Canonical, globally keyed nonzero terms and lightweight live handles."""

from math import inf
import numpy as np
from mipframe.frame.base import Frame
from mipframe.frame.base import Record
from mipframe.frame.base import merge_models
from mipframe.frame.schema import DType
from mipframe.frame.schema import ref_type
from mipframe.frame.schema import Ref
from mipframe.frame.schema import SchemaError
from mipframe.entity import Entity


class Term(Entity):
    """
    MIPFRAME HELP BEGIN
    Read-only original matrix term with lazy exact coefficient.

    Full contract: mf.help("api.Term")
    MIPFRAME HELP END
    """
    columns = ("row", "var", "coef")

    def __init__(self, owner, row, var, position=None):
        self._owner, self._row, self._var, self._position = (
            owner,
            int(row),
            int(var),
            position,
        )

    @property
    def id(self):
        self._owner.model
        return self._row, self._var

    @property
    def row(self):
        """
        MIPFRAME HELP BEGIN
        Read the original row field or entity relation.

        Full contract: mf.help("api.Term.row")
        MIPFRAME HELP END
        """
        return self._owner.rows.loc[self._row]

    @property
    def var(self):
        """
        MIPFRAME HELP BEGIN
        Read the original var field or entity relation.

        Full contract: mf.help("api.Term.var")
        MIPFRAME HELP END
        """
        return self._owner.vars.loc[self._var]

    @property
    def coef(self):
        """
        MIPFRAME HELP BEGIN
        The original exact Fraction coefficient, decoded only when requested.

        Full contract: mf.help("api.Term.coef")
        MIPFRAME HELP END
        """
        m = self._owner.model
        if self._position is None:
            self._position = m.sparse.position(self._row, self._var)
        return m.num(int(m.coeff_id[self._position]))

    def __repr__(self):
        return f"Term(row={self.row.name!r}, var={self.var.name!r}, coef={self.coef})"


class TermFrame(Frame):
    """
    MIPFRAME HELP BEGIN
    Original nonzero matrix terms keyed by row and var.

    Full contract: mf.help("api.TermFrame")
    MIPFRAME HELP END
    """
    _term_frame = True

    def __init__(self, owner, row_id=None, *, rows=None, vars=None):
        from mipframe.frame.entity import RowFrame
        from mipframe.frame.entity import VarFrame

        owner.model
        if row_id is not None:
            rows = RowFrame(owner, (int(row_id),))
        for value, cls in ((rows, RowFrame), (vars, VarFrame)):
            if value is not None and (
                not isinstance(value, cls)
                or (value._owner.uid, value._owner.version)
                != (owner.uid, owner.version)
            ):
                raise SchemaError(
                    "Term axes must belong to the same snapshot and have the correct type"
                )
        self._owner, self._rows, self._vars = owner, rows, vars
        schema = {
            "row": ref_type(owner, "row"),
            "var": ref_type(owner, "var"),
            "coef": DType("number"),
        }
        super().__init__(
            schema,
            key=("row", "var"),
            models=merge_models(
                owner.rows, *(v for v in (rows, vars) if v is not None)
            ),
            source=self._scan,
            domains={
                "row": owner.rows if rows is None else rows,
                "var": owner.vars if vars is None else vars,
            },
            known_count=owner.nnz if rows is None and vars is None else None,
        )
        self._query.spec = {
            "kind": "terms",
            "uid": owner.uid,
            "version": owner.version,
        }

    @classmethod
    def _from_frame(cls, frame):
        self = cls.__new__(cls)
        self.__dict__.update(frame.__dict__)
        self._owner = frame._models[0]
        return self

    def _scan(self, fields, ctx):
        m = self._owner.model
        for row, var, pos in self._positions(ctx):
            self._guard()
            yield {n: row if n == "row" else var if n == "var"
                   else m.num(int(m.coeff_id[pos])) for n in fields}

    def _positions(self, ctx):
        m = self._owner.model
        rows = range(len(m.rows)) if self._rows is None else self._rows.ids
        # Canonical term order is row ID then variable ID, irrespective of selection order.
        if not isinstance(rows, range) or rows.step != 1:
            table, _ = ctx.stage(self._rows, ("row",))
            rows = (record[0] for record in ctx.execute(f"SELECT c0 FROM {table} ORDER BY c0"))
        all_vars = (
            self._vars is None
            or isinstance(self._vars.ids, range)
            and self._vars.ids == range(len(m.variables))
        )
        if self._rows is None and not all_vars:
            from itertools import islice

            def columns(variables):
                for var in variables:
                    for offset in range(int(m.col_ptr[var]), int(m.col_ptr[var + 1])):
                        yield int(m.col_positions[offset]), var

            selected = [r["var"] for r in islice(self._vars._raw(("var",), ctx), 2)]
            if not selected:
                return
            positions = None
            if len(selected) == 1:
                positions = columns(selected)
            else:
                variables, _ = ctx.stage(self._vars, ("var",))
                def selected_vars():
                    return (v for v, in ctx.execute(f"SELECT c0 FROM {variables}"))
                degree = sum(int(m.col_ptr[v + 1]) - int(m.col_ptr[v]) for v in selected_vars())
                if degree < len(m.col_idx) // 2:
                    # Sort sparse column hits by CSR position under the query budget.
                    schema = {"position": DType("int"), "var": self.schema["var"]}
                    table = ctx.table(schema)
                    ctx.append(table, schema, (
                        {"position": pos, "var": var} for pos, var in columns(selected_vars())
                    ))
                    positions = ctx.execute(f"SELECT c0,c1 FROM {table} ORDER BY c0")
            if positions is not None:
                for pos, var in positions:
                    pos = int(pos)
                    yield int(np.searchsorted(m.row_ptr, pos, side="right") - 1), var, pos
                return
        for row in rows:
            for pos in range(int(m.row_ptr[row]), int(m.row_ptr[row + 1])):
                self._guard()
                var = int(m.col_idx[pos])
                if all_vars or ctx.contains(self._vars, var):
                    yield row, var, pos

    def __iter__(self):
        self._guard()
        return (
            Term(self._owner, r["row"], r["var"]) for r in self._iterate(("row", "var"))
        )

    @property
    def rows(self):
        return self.refs("row")

    @property
    def vars(self):
        return self.refs("var")
