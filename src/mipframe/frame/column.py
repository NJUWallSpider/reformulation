"""Read-only column projections and exact reductions."""

from fractions import Fraction
from mipframe.frame.schema import Ref, SchemaError
from mipframe.frame.expr import col, Aggregate


class ColumnView:
    """
    MIPFRAME HELP BEGIN
    ColumnView [api.ColumnView]

    One requested field with exact reductions and aligned comparisons.

    ColumnView [type]

    When to use: One requested field with exact reductions and aligned comparisons.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.ColumnView.argmax, concept.exact
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
        ColumnView.ids [api.ColumnView.ids]

        Read or reduce one exact column: ids.

        ColumnView.ids [property]

        When to use: Read or reduce one exact column: ids.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        if self._frame.schema[self.name].kind != "ref":
            raise SchemaError("ids requires a Ref column")
        from mipframe.frame.base import PlanIDs

        return PlanIDs(self._frame, self.name)

    def head(self, n=5):
        """
        MIPFRAME HELP BEGIN
        ColumnView.head [api.ColumnView.head]

        Read or reduce one exact column: head.

        ColumnView.head(n=5)

        When to use: Read or reduce one exact column: head.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        return self._frame.head(n).column(self.name)

    def min(self):
        """
        MIPFRAME HELP BEGIN
        ColumnView.min [api.ColumnView.min]

        Read or reduce one exact column: min.

        ColumnView.min()

        When to use: Read or reduce one exact column: min.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        return self._extreme(False)

    def max(self):
        """
        MIPFRAME HELP BEGIN
        ColumnView.max [api.ColumnView.max]

        Read or reduce one exact column: max.

        ColumnView.max()

        When to use: Read or reduce one exact column: max.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        return self._extreme(True)

    def _extreme(self, maximum):
        iterator = (x for x in self if x is not None)
        return (max if maximum else min)(iterator)

    def sum(self):
        """
        MIPFRAME HELP BEGIN
        ColumnView.sum [api.ColumnView.sum]

        Read or reduce one exact column: sum.

        ColumnView.sum()

        When to use: Read or reduce one exact column: sum.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        return sum((x for x in self if x is not None), Fraction(0))

    def count(self):
        """
        MIPFRAME HELP BEGIN
        ColumnView.count [api.ColumnView.count]

        Read or reduce one exact column: count.

        ColumnView.count()

        When to use: Read or reduce one exact column: count.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        return sum(x is not None for x in self)

    def nunique(self):
        """
        MIPFRAME HELP BEGIN
        ColumnView.nunique [api.ColumnView.nunique]

        Read or reduce one exact column: nunique.

        ColumnView.nunique()

        When to use: Read or reduce one exact column: nunique.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        return len(
            self._frame.select(self.name).where(col(self.name).is_not_null()).distinct()
        )

    def value_counts(self):
        """
        MIPFRAME HELP BEGIN
        ColumnView.value_counts [api.ColumnView.value_counts]

        Read or reduce one exact column: value_counts.

        ColumnView.value_counts()

        When to use: Read or reduce one exact column: value_counts.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
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
        ColumnView.argmin [api.ColumnView.argmin]

        Return all tied extremizers in original order and Frame type.

        ColumnView.argmin()

        When to use: Return all tied extremizers in original order and Frame type.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
        MIPFRAME HELP END
        """
        return self._arg(False)

    def argmax(self):
        """
        MIPFRAME HELP BEGIN
        ColumnView.argmax [api.ColumnView.argmax]

        Return all tied extremizers in original order and Frame type.

        ColumnView.argmax()

        When to use: Return all tied extremizers in original order and Frame type.

        Returns: Typed Ref ids for ids; otherwise the documented exact scalar, Frame or view.

        Establishes: Reductions ignore None; infinities remain. Empty sum is zero. Empty min/max raises; empty argmin/argmax returns an empty Frame.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.exact
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
