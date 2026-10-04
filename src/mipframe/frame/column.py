"""Read-only column projections and exact reductions."""

from fractions import Fraction
from mipframe.frame.schema import Ref, SchemaError
from mipframe.frame.expr import col, Aggregate


class ColumnView:
    """
    MIPFRAME HELP BEGIN
    One requested field with exact reductions and aligned comparisons.

    Full contract: mf.help("api.ColumnView")
    MIPFRAME HELP END
    """
    def __init__(self, frame, name):
        if name not in frame.schema:
            raise SchemaError(f"Unknown column {name}")
        self._frame, self._name = frame, name

    @property
    def name(self):
        self._frame._guard()
        return self._name

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            raise AttributeError("Column views are read-only")
        object.__setattr__(self, name, value)

    def __iter__(self):
        for row in self._frame._iterate((self.name,)):
            value = row[self.name]
            typ = self._frame.schema[self.name]
            if typ.kind == "ref" and value is not None:
                yield Ref(typ.uid, typ.version, typ.axis, value).bind(
                    self._frame._owner_for(typ)
                )
            else:
                yield value

    def __len__(self):
        return len(self._frame)

    @property
    def ids(self):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: ids.

        Full contract: mf.help("api.ColumnView.ids")
        MIPFRAME HELP END
        """
        if self._frame.schema[self.name].kind != "ref":
            raise SchemaError("ids requires a Ref column")
        from mipframe.frame.base import PlanIDs

        return PlanIDs(self._frame, self.name)

    def head(self, n=5):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: head.

        Full contract: mf.help("api.ColumnView.head")
        MIPFRAME HELP END
        """
        return self._frame.head(n).column(self.name)

    def min(self):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: min.

        Full contract: mf.help("api.ColumnView.min")
        MIPFRAME HELP END
        """
        return self._extreme(False)

    def max(self):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: max.

        Full contract: mf.help("api.ColumnView.max")
        MIPFRAME HELP END
        """
        return self._extreme(True)

    def _extreme(self, maximum):
        iterator = (x for x in self if x is not None)
        return (max if maximum else min)(iterator)

    def sum(self):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: sum.

        Full contract: mf.help("api.ColumnView.sum")
        MIPFRAME HELP END
        """
        from mipframe.store.numbers import exact_binary
        total = Fraction(0)
        for value in self:
            if value is not None:
                total = exact_binary("add", total, value)
        return total

    def count(self):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: count.

        Full contract: mf.help("api.ColumnView.count")
        MIPFRAME HELP END
        """
        return sum(x is not None for x in self)

    def nunique(self):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: nunique.

        Full contract: mf.help("api.ColumnView.nunique")
        MIPFRAME HELP END
        """
        return len(
            self._frame.select(self.name).where(col(self.name).is_not_null()).distinct()
        )

    def value_counts(self):
        """
        MIPFRAME HELP BEGIN
        Read or reduce one exact column: value_counts.

        Full contract: mf.help("api.ColumnView.value_counts")
        MIPFRAME HELP END
        """
        return (
            self._frame.group_by(self.name)
            .agg(count=Aggregate("count"))
            .sort_by("count", descending=True)
        )

    def argmin(self):
        """
        MIPFRAME HELP BEGIN
        Return all tied extremizers in original order and Frame type.

        Full contract: mf.help("api.ColumnView.argmin")
        MIPFRAME HELP END
        """
        return self._arg(False)

    def argmax(self):
        """
        MIPFRAME HELP BEGIN
        Return all tied extremizers in original order and Frame type.

        Full contract: mf.help("api.ColumnView.argmax")
        MIPFRAME HELP END
        """
        return self._arg(True)

    def _arg(self, maximum):
        try:
            value = self._extreme(maximum)
        except ValueError:
            return self._frame.head(0)
        return self._frame.where(col(self.name) == value)

    def __eq__(self, v):
        return col(self.name) == v

    def __ne__(self, v):
        return col(self.name) != v

    def __lt__(self, v):
        return col(self.name) < v

    def __le__(self, v):
        return col(self.name) <= v

    def __gt__(self, v):
        return col(self.name) > v

    def __ge__(self, v):
        return col(self.name) >= v

    def __repr__(self):
        return repr(self._frame.select(self.name))
