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
        from fractions import Fraction
        return Fraction(m.num(int(m.csc_value_ids[self._position[1]] if isinstance(self._position,tuple) else m.coeff_id[self._position])) if self._position is not None else m.coefficient(self._row,self._var))

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
            rows = RowFrame(owner, range(int(row_id),int(row_id)+1))
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
                   else m.coefficient(row,var) if pos is None else m.num(int(m.csc_value_ids[pos[1]] if isinstance(pos,tuple) else m.coeff_id[pos])) for n in fields}

    def _positions(self, ctx):
        m = self._owner.model
        if self._rows is None or getattr(self._rows,'_index',None)==range(m.capacity('row')):rows=m.entity_ids('row')
        else:
            table,_=ctx.stage(self._rows,('row',));order=table.order(('row',))
            rows=(int(table.columns['row'][i]) for i in order)
        from mipframe.data.ids import IDSet
        full_vars=self._vars is None or getattr(self._vars,'_index',None)==range(m.capacity('var'))
        allowed=None if full_vars else IDSet(self._vars.ids,work=ctx.work)
        if allowed is not None and not hasattr(m,'f'):
            from mipframe.data.relations import build
            selected_rows=None if self._rows is None else IDSet((r['row'] for r in self._rows._raw(('row',),ctx)),work=ctx.work)
            def entries():
                for var in allowed:
                    for pos in range(int(m.col_ptr[var]),int(m.col_ptr[var+1])):
                        row=int(m.csc_row_ids[pos])
                        if selected_rows is None or row in selected_rows:yield dict(row=row,var=var,pos=pos)
            table=build(ctx.work,{'row':'int','var':'int','pos':'int'},entries());order=table.order(('row','var'))
            for record in table.records(ids=order):
                self._guard();yield record['row'],record['var'],('csc',record['pos'])
            return
        for row in rows:
            self._guard()
            if hasattr(m,'f'):
                for var,_ in m.iter_terms(row):
                    if allowed is None or var in allowed:yield row,var,None
            else:
                for pos in range(int(m.row_ptr[row]),int(m.row_ptr[row+1])):
                    self._guard()
                    var=int(m.col_idx[pos])
                    if allowed is None or var in allowed:yield row,var,pos

    def __iter__(self):
        self._guard()
        if self._op!='source':
            return (Term(self._owner,r['row'],r['var']) for r in self._iterate(('row','var')))
        def stream():
            from mipframe.frame.executor import Context
            ctx=Context(self._models)
            try:
                for row,var,pos in self._positions(ctx):
                    self._guard();yield Term(self._owner,row,var,pos)
            finally:ctx.close()
        return stream()

    @property
    def rows(self):
        return self.refs("row")

    @property
    def vars(self):
        return self.refs("var")
