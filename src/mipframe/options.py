"""Package-wide display and numeric policy settings."""



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




class Options:
    def __init__(self):
        self.display = DisplayOptions()
        self.ir = IROptions()
        self.query = QueryOptions()


class IROptions:
    """Independent budgets for compressed IR rendering (not machine data)."""

    def __init__(self):
        self.max_local_shapes = 8
        self.max_name_templates = 8
        self.max_values = 8
        self.max_histogram = 8
        self.max_chars = 6000
        self.max_var_roles = 12
        self.max_row_families = 15
        self.max_attention = 6
        self.max_splits = 8

    def __setattr__(self, name, value):
        if name not in {"max_local_shapes", "max_name_templates", "max_values", "max_histogram", "max_chars", "max_var_roles", "max_row_families", "max_attention", "max_splits"}:
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
        self.disk_mb = None

    def __setattr__(self, name, value):
        if name == 'disk_mb':
            if value is not None:
                from math import isfinite
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value) or value<=0:raise ValueError('disk_mb must be finite and positive')
            object.__setattr__(self,name,value)
            return
        if name not in ('memory_mb', 'materialize_mb'):
            raise AttributeError(name)
        from mipframe.core.resources import memory_bytes
        memory_bytes(value)
        object.__setattr__(self, name, value)


options = Options()
