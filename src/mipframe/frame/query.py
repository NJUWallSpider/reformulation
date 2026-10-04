"""Private source capabilities and dependency traversal for every Frame."""
from dataclasses import dataclass, field


@dataclass
class QuerySource:
    scan: object = None
    spec: dict = field(default_factory=dict)
    navigation: object = None
    pushdown: object = None
    selection: object = None
    resource: object = None
    display_columns: object = None

    def inherit(self, result, renames=None):
        """Keep only capabilities whose identity columns survived unchanged."""
        if self.navigation is not None:
            provider, keys = self.navigation
            remapped = {a:(renames or {}).get(b,b) for a,b in keys.items()}
            retained = renames is None or set(keys.values()) <= set(renames)
            if retained and set(remapped.values()) <= set(result.schema):
                result._query.navigation = provider, remapped
                result._query.selection = self.selection
        if self.display_columns is not None:
            columns = tuple((renames or {}).get(n,n) for n in self.display_columns)
            if set(columns) <= set(result.schema):
                result._query.display_columns = columns
        return result


def expression_nodes(value):
    """Walk expression containers without crossing into a referenced Frame."""
    from mipframe.frame.expr import QueryExpr, Aggregate
    yield value
    if isinstance(value, QueryExpr):
        yield from expression_nodes(value.args)
    elif isinstance(value, Aggregate):
        yield from expression_nodes(value.expr)
    elif isinstance(value, dict):
        for child in value.values():
            yield from expression_nodes(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from expression_nodes(child)


def expression_frames(value):
    from mipframe.frame.base import Frame
    return (item for item in expression_nodes(value) if isinstance(item, Frame))


def inputs(frame, *, include_domains=False):
    """Yield direct query inputs in stable order, once per identity."""
    from mipframe.frame.base import Frame, PlanIDs
    children = [*frame._parents, *expression_frames(frame._query.spec),
                *expression_frames(frame._params)]
    index = getattr(frame, '_index', None)
    if isinstance(index, PlanIDs):
        children.append(index.frame)
    children.extend(getattr(frame, name, None) for name in ('_rows', '_vars'))
    if include_domains:
        children.extend(frame.domains.values())
    seen = set()
    for child in children:
        if isinstance(child, Frame) and id(child) not in seen:
            seen.add(id(child))
            yield child


def dependencies(frame, *, include_domains=False):
    """Yield a query and all live sources, including expression memberships."""
    pending = [frame]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(reversed(tuple(inputs(current, include_domains=include_domains))))
