"""Live read-only entities; records are explicit detached exports."""
from collections.abc import Mapping


def guarded_iter(owner, values):
    """Guard before advancing a cursor or touching a potentially unmapped array."""
    owner.model
    iterator = iter(values)
    sentinel = object()
    while True:
        owner.model
        value = next(iterator, sentinel)
        if value is sentinel:
            return
        yield value


class Entity(Mapping):
    @property
    def coef(self):
        from mipframe._help.diagnostics import hint
        self._owner.model
        raise hint(AttributeError("Coefficients belong to Terms; use entity.terms.column('coef') or entity.terms.one().coef"), "error.navigation")

    def __setattr__(self, name, value):
        if not name.startswith('_'):
            raise AttributeError('Entities are read-only; use to_record() for a mutable copy')
        object.__setattr__(self, name, value)

    def __getitem__(self, key):
        self._owner.model
        if key not in self.columns and key not in getattr(self,"_fields",()):
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        self._owner.model
        for key in self.columns:
            self._owner.model
            yield key

    def __len__(self):
        self._owner.model
        return len(self.columns)

    def to_record(self):
        """
        MIPFRAME HELP BEGIN
        Export an independent exact record; subsequent mutation cannot change the model.

        Full contract: mf.help("api.Row.to_record")
        MIPFRAME HELP END
        """
        from mipframe.frame.schema import Ref
        def detach(value):
            if isinstance(value, Entity):
                return Ref(value._owner.uid, value._owner.version, 'row' if type(value).__name__ == 'Row' else 'var', value.id)
            return value
        return {key: detach(self[key]) for key in self}

    def __eq__(self, other):
        self._owner.model
        if type(other) is not type(self):
            return False
        other._owner.model
        return (self._owner.uid, self._owner.version, self.id) == (other._owner.uid, other._owner.version, other.id)

    def __ne__(self, other):
        return not self == other

    __hash__ = None
