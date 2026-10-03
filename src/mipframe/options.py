"""Package-wide display and numeric policy settings."""

from math import isfinite


class DisplayOptions:
    def __init__(self):
        self.max_rows = 12
        self.max_cols = 12

    @staticmethod
    def _validate(value):
        if type(value) is not int:
            raise TypeError("Display limits must be integers")
        if value < 1:
            raise ValueError("Display limits must be positive")
        return value

    @property
    def max_rows(self) -> int:
        return self._max_rows

    @max_rows.setter
    def max_rows(self, value: int):
        self._max_rows = self._validate(value)

    @property
    def max_cols(self) -> int:
        return self._max_cols

    @max_cols.setter
    def max_cols(self, value: int):
        self._max_cols = self._validate(value)


class NumericOptions:
    """Reserved approximate-input tolerances; exact file signatures ignore them."""

    def __init__(self):
        self.rtol = 1e-9
        self.atol = 1e-12

    @staticmethod
    def _validate(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Numeric tolerances must be real numbers")
        if not isfinite(value) or value < 0:
            raise ValueError("Numeric tolerances must be finite and nonnegative")
        return float(value)

    @property
    def rtol(self):
        return self._rtol

    @rtol.setter
    def rtol(self, value):
        self._rtol = self._validate(value)

    @property
    def atol(self):
        return self._atol

    @atol.setter
    def atol(self, value):
        self._atol = self._validate(value)


class Options:
    def __init__(self):
        self.display = DisplayOptions()
        self.numeric = NumericOptions()
        self.ir = IROptions()
        self.query = QueryOptions()


class IROptions:
    """Independent budgets for compressed IR rendering (not machine data)."""

    def __init__(self):
        self.max_var_roles = 12
        self.max_row_families = 15
        self.max_attention = 6
        self.max_splits = 8

    def __setattr__(self, name, value):
        if name not in {"max_var_roles", "max_row_families", "max_attention", "max_splits"}:
            raise AttributeError(name)
        if type(value) is not int:
            raise TypeError("IR display limits must be integers")
        if value < 0:
            raise ValueError("IR display limits must be nonnegative")
        object.__setattr__(self, name, value)


class QueryOptions:
    def __init__(self):
        self.memory_mb = 128
        self.materialize_mb = 128

    def __setattr__(self, name, value):
        if name not in ('memory_mb', 'materialize_mb'):
            raise AttributeError(name)
        from mipframe.store.query_store import memory_bytes
        memory_bytes(value)
        object.__setattr__(self, name, value)


options = Options()
