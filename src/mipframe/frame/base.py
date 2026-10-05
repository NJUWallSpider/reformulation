"""Immutable relational frames, live records and explicit detached exports."""

from mipframe._help.diagnostics import hint as _help_hint

from collections.abc import Mapping
from collections.abc import Sequence
from fractions import Fraction
from itertools import islice
from types import MappingProxyType
import json
from mipframe.frame.schema import DType
from mipframe.frame.schema import Ref
from mipframe.frame.schema import SchemaError
from mipframe.frame.schema import frozen_schema
from mipframe.frame.schema import normalize
from mipframe.frame.schema import encode
from mipframe.frame.schema import decode
from mipframe.frame.expr import QueryExpr
from mipframe.frame.expr import Aggregate
from mipframe.frame.expr import col
from mipframe.frame.expr import literal
from mipframe.frame.executor import Context
from mipframe.frame.executor import MaterializationLimitError
from mipframe.frame.executor import size
from mipframe.frame.executor import blocking


def merge_models(*frames):
    owners = []
    versions = {}
    for f in frames:
        f._guard()
        for owner in f._models:
            uid, version = owner.uid, owner.version
            if uid in versions and versions[uid] != version:
                raise SchemaError("Snapshot uid has conflicting versions")
            versions[uid] = version
            if not any(owner is x for x in owners):
                owners.append(owner)
    return tuple(owners)


class Record(Mapping):
    """
    MIPFRAME HELP BEGIN
    Live read-only record; to_record exports detached values.

    Full contract: mf.help("api.Record")
    MIPFRAME HELP END
    """
    def __init__(self, frame, values):
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "_values", dict(values))

    def __setattr__(self, n, v):
        raise AttributeError("Records are read-only")

    def __getitem__(self, n):
        self._frame._guard()
        value = self._values[n]
        typ = self._frame.schema[n]
        if typ.kind == "draft_ref" and value is not None:
            from mipframe.edit.draft import DraftRef
            return DraftRef(typ.uid, typ.axis, value).bind()
        if typ.kind == "ref" and value is not None:
            from mipframe.entity import bind_identity
            return bind_identity(self._frame._owner_for(typ),typ.axis,int(value))
        return value

    def __getattr__(self, n):
        if n.startswith("_"):
            raise AttributeError(n)
        try:
            return self[n]
        except KeyError:
            raise AttributeError(n) from None

    def __iter__(self):
        self._frame._guard()
        return iter(self._values)

    def __len__(self):
        self._frame._guard()
        return len(self._values)

    def to_record(self):
        self._frame._guard()
        return {n: self._frame._detach(n, v) for n, v in self._values.items()}

    def __repr__(self):
        self._frame._guard()
        return (
            "Record("
            + ", ".join(
                f"{n}={self._frame._display(n, v)}" for n, v in self._values.items()
            )
            + ")"
        )


class Locator:
    def __init__(self, frame, positional):
        self.frame, self.positional = frame, positional

    def __getitem__(self, value):
        f = self.frame
        f._guard()
        if self.positional:
            if isinstance(value, slice):
                if value.step == 0:
                    raise ValueError("slice step cannot be zero")
                for x in (value.start, value.stop, value.step):
                    if x is not None and type(x) is not int:
                        raise TypeError("Slice positions must be integers")
                if (
                    (value.start is None or value.start >= 0)
                    and (value.stop is None or value.stop >= 0)
                    and (value.step is None or value.step > 0)
                ):
                    return f._derive(
                        "limit",
                        {
                            "start": value.start or 0,
                            "stop": value.stop,
                            "step": value.step or 1,
                        },
                        preserve=True,
                    )
                return f._derive("slice", {"slice": value}, preserve=True)
            if type(value) is not int:
                raise TypeError("iloc requires an integer or slice")
            if value < 0:
                value += len(f)
            if value < 0:
                raise IndexError(value)
            found = list(islice(iter(f), value, value + 1))
            if not found:
                raise IndexError(value)
            return found[0]
        if f.key is None:
            raise SchemaError("loc requires a declared key")
        values = value if len(f.key) > 1 else (value,)
        if not isinstance(values, tuple) or len(values) != len(f.key):
            raise KeyError(value)
        predicates = []
        for name, v in zip(f.key, values):
            typ = f.schema[name]
            if typ.kind == "ref" and type(v) is int:
                v = Ref(typ.uid, typ.version, typ.axis, v)
            predicates.append(col(name).is_null() if v is None else col(name) == v)
        try:
            return f.where(*predicates).one()
        except Exception as e:
            from mipframe.check.report import CardinalityError

            if isinstance(e, CardinalityError):
                raise KeyError(value) from e
            raise


class PlanIDs(Sequence):
    unique = True

    def __init__(self, frame, column):
        self.frame, self.column = frame, column

    def __len__(self):
        return len(self.frame)

    def __iter__(self):
        yield from (r[self.column] for r in self.frame._iterate((self.column,)))

    def __getitem__(self, key):
        if isinstance(key, slice):
            return PlanIDs(self.frame.iloc[key], self.column)
        value = self.frame.iloc[key]
        return value._values[self.column]

    def __contains__(self, value):
        typ = self.frame.schema[self.column]
        ref = Ref(typ.uid, typ.version, typ.axis, int(value))
        return (
            next(iter(self.frame.where(col(self.column) == ref).head(1)), None)
            is not None
        )


from mipframe.frame.column import ColumnView


class Frame:
    """
    MIPFRAME HELP BEGIN
    Typed exact relation with declared key, domains and composable query plans.

    Full contract: mf.help("api.Frame")
    MIPFRAME HELP END
    """
    @property
    def index(self):
        self._guard()
        raise _help_hint(AttributeError("Frame has no hidden index; use .ids on entity Frames, .iloc for positions, or .loc for declared keys"), "error.navigation")

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            raise AttributeError(
                "Frames are immutable; use select() to construct another Frame"
            )
        object.__setattr__(self, name, value)

    def __init__(
        self,
        schema,
        *,
        key=None,
        models=(),
        op="source",
        parents=(),
        params=None,
        source=None,
        domains=None,
        unique=(),
        known_count=None,
        relations=None,
    ):
        self._schema = frozen_schema(schema)
        self._key = tuple(key) if key is not None else None
        if self._key is not None and not set(self._key) <= set(schema):
            raise SchemaError("Key columns absent from schema")
        self._models = tuple(models)
        from mipframe.frame.query import QuerySource
        self._query = QuerySource(scan=source, display_columns=getattr(type(self), "_display_columns", None))
        self._op, self._parents, self._params = (
            op,
            tuple(parents),
            params or {},
        )
        self._domains = MappingProxyType(dict(domains or {}))
        self._relations = MappingProxyType(dict(relations or {}))
        self._unique = frozenset(tuple(k) for k in unique) | (
            frozenset((self._key,)) if self._key is not None else frozenset()
        )
        self._known_count = known_count
        self._verified_unique = set()

    def _guard(self):
        for typ in self._schema.values():
            if typ.kind == "draft_ref":
                from mipframe.edit.draft import EDITS
                edit = EDITS.get(typ.uid)
                if edit is None: raise SchemaError("Draft Edit must be bound and open")
                edit._guard()
        for m in self._models:
            m.model
        resource = self._query.resource
        if resource is not None:
            resource.guard()

    @property
    def schema(self):
        """
        MIPFRAME HELP BEGIN
        Read the mapping from column names to DType metadata.

        Full contract: mf.help("api.Frame.schema")
        MIPFRAME HELP END
        """
        self._guard()
        return self._schema

    @property
    def columns(self):
        """
        MIPFRAME HELP BEGIN
        Read the Frame columns metadata.

        Full contract: mf.help("api.Frame.columns")
        MIPFRAME HELP END
        """
        return tuple(self.schema)

    @property
    def key(self):
        """
        MIPFRAME HELP BEGIN
        Read the Frame key metadata.

        Full contract: mf.help("api.Frame.key")
        MIPFRAME HELP END
        """
        self._guard()
        return self._key

    @property
    def unique(self):
        """
        MIPFRAME HELP BEGIN
        Read the Frame unique metadata.

        Full contract: mf.help("api.Frame.unique")
        MIPFRAME HELP END
        """
        self._guard()
        return self._unique | frozenset(self._verified_unique)

    @property
    def models(self):
        """
        MIPFRAME HELP BEGIN
        Read the Frame models metadata.

        Full contract: mf.help("api.Frame.models")
        MIPFRAME HELP END
        """
        self._guard()
        return frozenset(m.uid for m in self._models)

    @property
    def domains(self):
        """
        MIPFRAME HELP BEGIN
        Declared domains retained through filtering; not reduced to observed values.

        Full contract: mf.help("api.Frame.domains")
        MIPFRAME HELP END
        """
        self._guard()
        return self._domains

    @property
    def relations(self):
        """
        MIPFRAME HELP BEGIN
        Read-only mapping of attached normalized Frames, such as members and search metadata.

        Full contract: mf.help("api.Frame.relations")
        MIPFRAME HELP END
        """
        self._guard()
        return self._relations

    def related(self, name):
        """
        Navigate a declared relation, restricted to this Frame's current keys.

        MIPFRAME HELP BEGIN
        Navigate a declared relation using the current selected keys.

        Full contract: mf.help("api.Frame.related")
        MIPFRAME HELP END
        """
        self._guard()
        navigation = self._query.navigation
        if navigation is None:
            if name in self.relations:
                return self.relations[name]
            raise KeyError(f'No declared relation {name!r}; retain its linking columns')
        provider, keys = navigation
        return provider.related(self, name, keys)

    @property
    def rows(self):
        """
        Original row members of a declared row-family relation.

        MIPFRAME HELP BEGIN
        Read declared row members of the current family or indexed selection.

        Full contract: mf.help("api.Frame.rows")
        MIPFRAME HELP END
        """
        return self.related('rows')

    @property
    def vars(self):
        """
        Original variable members of a declared variable-family relation.

        MIPFRAME HELP BEGIN
        Read declared variable members of the current family or indexed selection.

        Full contract: mf.help("api.Frame.vars")
        MIPFRAME HELP END
        """
        return self.related('vars')

    def inspect(self, *, verbose=False, samples=3, sample_formula='auto', max_terms=12,
                max_items=15, max_splits=5):
        """
        Render bounded details without constructing another public result object.

        MIPFRAME HELP BEGIN
        Render a bounded table or provider-specific family/indexed details.

        Full contract: mf.help("api.Frame.inspect")
        MIPFRAME HELP END
        """
        self._guard()
        if type(verbose) is not bool: raise TypeError('verbose must be a boolean')
        self._size(samples); self._size(max_terms)
        if sample_formula != 'auto' and type(sample_formula) is not bool:
            raise ValueError('sample_formula must be auto or boolean')
        if any(type(v) is not int or v < 1 for v in (max_items, max_splits)):
            raise ValueError('display limits must be positive integers')
        navigation = self._query.navigation
        provider = navigation[0] if navigation is not None else None
        settings = dict(verbose=verbose, samples=samples, sample_formula=sample_formula,
                        max_terms=max_terms, max_items=max_items, max_splits=max_splits)
        defaults = dict(verbose=False, samples=3, sample_formula='auto',
                        max_terms=12, max_items=15, max_splits=5)
        supported = getattr(provider, 'inspect_options', {'max_items'})
        unsupported = [name for name,value in settings.items()
                       if name not in supported and value != defaults[name]]
        if unsupported:
            raise ValueError('This view does not support inspect options: '+', '.join(unsupported))
        if provider is None or not hasattr(provider, 'inspect'):
            return self._render(max_rows=max_items)
        return provider.inspect(self, navigation[1],
            **{name:value for name,value in settings.items() if name in supported})

    @property
    def iloc(self):
        """
        MIPFRAME HELP BEGIN
        Position lookup or slice; supports negative positions, rejects bool.

        Full contract: mf.help("api.Frame.iloc")
        MIPFRAME HELP END
        """
        return Locator(self, True)

    @property
    def loc(self):
        """
        MIPFRAME HELP BEGIN
        Lookup by declared key, with tuple for compound keys; no-key lookup fails.

        Full contract: mf.help("api.Frame.loc")
        MIPFRAME HELP END
        """
        return Locator(self, False)

    @property
    def shape(self):
        """
        MIPFRAME HELP BEGIN
        Exact row count and number of fields; unlike repr this can evaluate the query.

        Full contract: mf.help("api.Frame.shape")
        MIPFRAME HELP END
        """
        return len(self), len(self.schema)

    def __getitem__(self, key):
        self._guard()
        raise _help_hint(TypeError(
            "Use .iloc for positions, .loc for keys, Model.family(), .where() or .column(); Frame [] is not supported"
        ), 'error.navigation')

    def __bool__(self):
        raise _help_hint(TypeError("Frame has no implicit truth value; use len(frame) or .one()"), 'error.expressions')

    def _owner_for(self, typ):
        self._guard()
        for owner in self._models:
            if owner.uid == typ.uid and owner.version == typ.version:
                return owner
        raise SchemaError("Reference snapshot must be explicitly bound to a live model")

    def _detach(self, name, value):
        typ = self.schema[name]
        if typ.kind == "draft_ref" and value is not None:
            from mipframe.edit.draft import DraftRef
            return DraftRef(typ.uid, typ.axis, value)
        return (
            Ref(typ.uid, typ.version, typ.axis, value)
            if typ.kind == "ref" and value is not None
            else value
        )

    def _display(self, name, value):
        typ = self.schema[name]
        if typ.kind == "draft_ref" and value is not None:
            from mipframe.edit.draft import DraftRef
            return repr(DraftRef(typ.uid, typ.axis, value).bind())
        if typ.kind == "ref" and value is not None:
            owner = self._owner_for(typ)
            return owner.model.name(typ.axis, value)
        return str(value)

    def _iterate(self, fields=None, memory_mb=None, disk_mb=None):
        self._guard()
        fields = tuple(self.schema if fields is None else fields)
        ctx = Context(self._models, memory_mb, disk_mb)
        iterator = None
        try:
            iterator = iter(self._raw(fields, ctx))
            while True:
                # Guard before resuming any sparse generator: model.close()
                # may have unmapped arrays while this iterator was suspended.
                self._guard()
                try:
                    token=ctx.work.activate()
                    try:row = next(iterator)
                    finally:ctx.work.deactivate(token)
                except StopIteration:
                    break
                ctx.check_size(row)
                yield row
        finally:
            try:
                if iterator is not None and hasattr(iterator, "close"):
                    iterator.close()
            finally:
                ledger=ctx.work.ledger
                self._last_execution_stats=dict(peak_working_bytes=ledger.peak_resident,peak_temporary_disk_bytes=ledger.peak_disk,
                                               memory_limit_bytes=ledger.memory,disk_limit_bytes=ledger.disk_limit)
                if not ctx.retained:
                    ctx.close()

    def _raw(self, fields, ctx):
        self._guard()
        ctx = ctx.query_context
        op, p = self._op, self._params
        from mipframe.frame.stream import batches
        stream=batches(self,fields,ctx)
        if stream is not None:
            for batch in stream:yield from batch.records(fields)
            return
        if op == "source":
            yield from self._query.scan(fields, ctx)
            return
        if op in ("join", "group", "sort", "distinct", "slice", "sample"):
            yield from blocking(self, fields, ctx)
            return
        parent = self._parents[0]
        if op == "filter":
            needed = tuple(
                dict.fromkeys(
                    (*fields, *(n for e in p["predicates"] for n in e.dependencies))
                )
            )
            for row in parent._raw(needed, ctx):
                if all(e.evaluate(row, ctx) is True for e in p["predicates"]):
                    yield {n: row[n] for n in fields}
        elif op == "select":
            needed = tuple(
                dict.fromkeys(
                    n for f in fields for n in p["expressions"][f].dependencies
                )
            )
            for row in parent._raw(needed, ctx):
                yield {n: p["expressions"][n].evaluate(row, ctx) for n in fields}
        elif op == "limit":
            # First advance the upstream iterator, even for an empty limit, so
            # explicit project/join preconditions cannot be bypassed.
            iterator = iter(parent._raw(fields, ctx))
            if p["stop"] == 0:
                next(iterator, None)
                return
            yield from islice(iterator, p["start"], p["stop"], p["step"])
        else:
            raise NotImplementedError(op)

    def __iter__(self):
        self._guard()
        return (Record(self, row) for row in self._iterate())

    def __len__(self):
        self._guard()
        if self._known_count is not None:
            return self._known_count
        return self.count()

    def count(self, *, memory_mb=None, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Compute an exact record count.

        Full contract: mf.help("api.Frame.count")
        MIPFRAME HELP END
        """
        self._guard()
        if self._known_count is not None:return self._known_count
        from mipframe.frame.stream import batches
        with Context(self._models,memory_mb,disk_mb) as ctx:
            token=ctx.work.activate()
            try:
                stream=batches(self,(),ctx)
                if stream is not None:return sum(batch.size for batch in stream)
            finally:
                ctx.work.deactivate(token)
                ledger=ctx.work.ledger
                self._last_execution_stats=dict(peak_working_bytes=ledger.peak_resident,peak_temporary_disk_bytes=ledger.peak_disk,
                                               memory_limit_bytes=ledger.memory,disk_limit_bytes=ledger.disk_limit)
        return sum(1 for _ in self._iterate((),memory_mb,disk_mb))

    def _derive(
        self,
        op,
        params,
        *,
        schema=None,
        key="inherit",
        unique=None,
        preserve=False,
        domains=None,
    ):
        from mipframe.frame.query import expression_frames
        result = Frame(
            self.schema if schema is None else schema,
            key=self.key if key == "inherit" else key,
            models=merge_models(self, *expression_frames(params)),
            op=op,
            parents=(self,),
            params=params,
            unique=self.unique if unique is None else unique,
            domains=self.domains if domains is None else domains,
            relations=self.relations,
        )
        if op != 'group':
            renames = None
            if op == 'select':
                renames = {e.args[0]: n for n, e in params['expressions'].items() if e.op == 'col'}
                navigation = self._query.navigation
                if navigation and not set(navigation[1].values()) <= set(renames):
                    return result
            self._query.inherit(result, renames)
        if preserve and getattr(self, "_entity_axis", None):
            from mipframe.frame.entity import RowFrame
            from mipframe.frame.entity import VarFrame

            cls = RowFrame if self._entity_axis == "row" else VarFrame
            return cls(self._owner, PlanIDs(result, self._entity_axis))
        if preserve and getattr(self, "_term_frame", False):
            from mipframe.frame.terms import TermFrame

            return TermFrame._from_frame(result)
        return result

    def where(self, *predicates, **equalities):
        """
        MIPFRAME HELP BEGIN
        Filter by serializable boolean predicates and keyword equalities.

        Full contract: mf.help("api.Frame.where")
        MIPFRAME HELP END
        """
        if self._op == 'filter':
            return self._parents[0].where(*self._params['predicates'], *predicates, **equalities)
        values = list(predicates)
        for n, v in equalities.items():
            values.append(
                col(n).isin(v)
                if isinstance(v, Frame)
                else col(n).is_null() if v is None else col(n) == v
            )
        for e in values:
            if not isinstance(e, QueryExpr):
                raise TypeError(
                    "where accepts QueryExpr predicates, not callbacks or booleans"
                )
            if e.type(self.schema).kind != "bool":
                raise SchemaError("where requires boolean expressions")
        source = self
        if self._query.pushdown is not None:
            candidate = self._query.pushdown(self, predicates, equalities)
            if candidate is not None:
                source = candidate
        result = source._derive("filter", {"predicates": tuple(values)}, preserve=True)

        return result

    def select(self, *names, **expressions):
        """
        MIPFRAME HELP BEGIN
        Choose, rename or calculate columns.

        Full contract: mf.help("api.Frame.select")
        MIPFRAME HELP END
        """
        projected = {}
        for name in names:
            for n in self.schema if name == "*" else (name,):
                if n in projected:
                    raise SchemaError(f"Duplicate output column {n!r}")
                projected[n] = col(n)
        for n, e in expressions.items():
            if n in projected:
                raise SchemaError(f"Duplicate output column {n!r}")
            projected[n] = literal(e)
        if not projected:
            raise SchemaError("select requires at least one column")
        schema = {n: e.type(self.schema) for n, e in projected.items()}
        renames = {e.args[0]: n for n, e in projected.items() if e.op == "col"}
        uniques = [
            tuple(renames[n] for n in k)
            for k in self.unique
            if all(n in renames for n in k)
        ]
        key = (
            tuple(renames[n] for n in self.key)
            if self.key is not None and all(n in renames for n in self.key)
            else None
        )
        domains = {renames[n]: v for n, v in self.domains.items() if n in renames}
        return self._derive(
            "select",
            {"expressions": projected},
            schema=schema,
            key=key,
            unique=uniques,
            domains=domains,
        )

    def join(self, other, *, on, how="inner", cardinality="auto", require=None):
        """
        MIPFRAME HELP BEGIN
        Join relations with explicit cardinality and match requirements.

        Full contract: mf.help("api.Frame.join")
        MIPFRAME HELP END
        """
        if not isinstance(other, Frame):
            raise TypeError("join requires a Frame")
        if how not in ("inner", "left"):
            raise ValueError("how must be inner or left")
        if cardinality not in ("auto", "1:1", "1:m", "m:1", "m:m"):
            raise _help_hint(ValueError("Unknown join cardinality"), 'error.join')
        if require not in (None, "left", "right", "both"):
            raise ValueError("Unknown match requirement")
        pairs = (
            {on: on}
            if isinstance(on, str)
            else dict(on) if isinstance(on, Mapping) else {n: n for n in on}
        )
        if not pairs or len(set(pairs.values())) != len(pairs):
            raise SchemaError("Join needs distinct key pairs")
        for a, b in pairs.items():
            if a not in self.schema or b not in other.schema:
                raise SchemaError("Unknown join key")
            if not self.schema[a].compatible(other.schema[b]):
                raise _help_hint(SchemaError("Join key types or reference snapshots differ"), 'concept.identity')
        lu = any(set(k) <= set(pairs) for k in self.unique)
        ru = any(set(k) <= set(pairs.values()) for k in other.unique)
        if cardinality == "auto":
            if not (lu or ru):
                raise _help_hint(SchemaError(
                    "Unknown join cardinality; verify uniqueness or specify cardinality explicitly"
                ), 'error.join')
            cardinality = "1:1" if lu and ru else "1:m" if lu else "m:1"
        schema = dict(self.schema)
        mapping = {v: k for k, v in pairs.items()}
        for n, t in other.schema.items():
            if n in pairs.values():
                continue
            if n in schema:
                raise _help_hint(SchemaError(
                    f"Column {n!r} exists on both sides; rename explicitly with select()"
                ), 'error.join')
            schema[n] = t.optional() if how == "left" else t
        unique = []
        if cardinality in ("1:1", "m:1"):
            unique.extend(self.unique)
        if cardinality in ("1:1", "1:m") and how == "inner":
            unique.extend(tuple(mapping.get(n, n) for n in k) for k in other.unique)
        key = self.key if cardinality in ("1:1", "m:1") else None
        if key is None and self.key is not None and other.key is not None:
            key = tuple(
                dict.fromkeys((*self.key, *(mapping.get(n, n) for n in other.key)))
            )
        result = Frame(
            schema,
            key=key,
            unique=unique,
            models=merge_models(self, other),
            op="join",
            parents=(self, other),
            params=dict(on=pairs, how=how, cardinality=cardinality, require=require),
            domains={
                **{mapping.get(k, k): v for k, v in other.domains.items()},
                **self.domains,
            },
        )
        return self._query.inherit(result)

    def group_by(self, *keys):
        """
        MIPFRAME HELP BEGIN
        Declare grouping keys before aggregation.

        Full contract: mf.help("api.Frame.group_by")
        MIPFRAME HELP END
        """
        return Grouped(self, _names(keys))

    def distinct(self):
        """
        MIPFRAME HELP BEGIN
        Remove duplicate complete output records.

        Full contract: mf.help("api.Frame.distinct")
        MIPFRAME HELP END
        """
        return self._derive(
            "distinct", {}, key=tuple(self.schema), unique=(tuple(self.schema),)
        )

    def sort_by(self, *by, descending=False):
        """
        MIPFRAME HELP BEGIN
        Stable exact ordering by selected columns.

        Full contract: mf.help("api.Frame.sort_by")
        MIPFRAME HELP END
        """
        names = _names(by)
        if not names or not set(names) <= set(self.schema):
            raise SchemaError("Specify existing sort columns")
        directions = (
            (descending,) * len(names)
            if type(descending) is bool
            else tuple(descending)
        )
        if len(directions) != len(names) or any(
            type(x) is not bool for x in directions
        ):
            raise TypeError("Invalid sort directions")
        return self._derive(
            "sort", dict(by=names, descending=directions), preserve=True
        )

    def head(self, n=5):
        """
        MIPFRAME HELP BEGIN
        Construct a lazy prefix limit.

        Full contract: mf.help("api.Frame.head")
        MIPFRAME HELP END
        """
        return self.iloc[: self._size(n)]

    def tail(self, n=5):
        """
        MIPFRAME HELP BEGIN
        Select a final suffix.

        Full contract: mf.help("api.Frame.tail")
        MIPFRAME HELP END
        """
        n = self._size(n)
        return self.iloc[-n:] if n else self.head(0)

    def sample(self, n=5, *, random_state=None):
        """
        MIPFRAME HELP BEGIN
        Sample without replacement and retain relative input order.

        Full contract: mf.help("api.Frame.sample")
        MIPFRAME HELP END
        """
        if random_state is not None and type(random_state) is not int:
            raise TypeError("random_state must be an integer")
        if random_state is None:
            from secrets import randbits

            random_state = randbits(128)
        return self._derive(
            "sample", dict(n=self._size(n), seed=random_state), preserve=True
        )

    @staticmethod
    def _size(n):
        if type(n) is not int:
            raise TypeError("Size must be a nonnegative integer")
        if n < 0:
            raise ValueError("Size must be nonnegative")
        return n

    def one(self):
        """
        MIPFRAME HELP BEGIN
        Require exactly one output record.

        Full contract: mf.help("api.Frame.one")
        MIPFRAME HELP END
        """
        from mipframe.check.report import CardinalityError

        iterator = iter(self)
        first = next(iterator, None)
        second = next(iterator, None)
        if first is None or second is not None:
            error = CardinalityError(
                f"Expected exactly one record; found {0 if first is None else '>1'}"
            )
            error.expected, error.actual = 1, 0 if first is None else ">1"
            raise error
        return first

    def refs(self, name):
        """
        MIPFRAME HELP BEGIN
        Extract unique nonmissing typed entity references.

        Full contract: mf.help("api.Frame.refs")
        MIPFRAME HELP END
        """
        typ = self.schema[name]
        if typ.kind != "ref":
            raise SchemaError("refs requires a reference column")
        from mipframe.frame.entity import RowFrame
        from mipframe.frame.entity import VarFrame

        source = (
            self.select(**{typ.axis: col(name)})
            .where(col(typ.axis).is_not_null())
            .distinct()
            .sort_by(typ.axis)
        )
        result = (RowFrame if typ.axis == "row" else VarFrame)(
            self._owner_for(typ), PlanIDs(source, typ.axis)
        )
        result._models = self._models
        result._query.spec = {"kind": "entity_refs", "source": self, "column": name}
        return result

    def column(self, name):
        """
        MIPFRAME HELP BEGIN
        Read only one column and its dependencies.

        Full contract: mf.help("api.Frame.column")
        MIPFRAME HELP END
        """
        return ColumnView(self, name)

    def nunique(self, columns):
        """
        MIPFRAME HELP BEGIN
        Count distinct combinations of selected columns.

        Full contract: mf.help("api.Frame.nunique")
        MIPFRAME HELP END
        """
        names = (columns,) if isinstance(columns, str) else tuple(columns)
        return len(self.select(*names).distinct())

    def check(self, kind, columns=None, **kwargs):
        """
        MIPFRAME HELP BEGIN
        Verify a declared relational property.

        Full contract: mf.help("api.Frame.check")
        MIPFRAME HELP END
        """
        from mipframe.check.frame import check

        return check(self, kind, columns, **kwargs)

    def to_records(self, *, memory_mb=None, disk_mb=None, materialize_mb=None):
        """
        MIPFRAME HELP BEGIN
        Export independent records within the materialization budget.

        Full contract: mf.help("api.Frame.to_records")
        MIPFRAME HELP END
        """
        from mipframe.options import options
        from mipframe.core.resources import memory_bytes

        budget = memory_bytes(
            options.query.materialize_mb if materialize_mb is None else materialize_mb
        )
        result = []
        used = size(result)
        for raw in self._iterate(memory_mb=memory_mb, disk_mb=disk_mb):
            row = {n: self._detach(n, v) for n, v in raw.items()}
            used += size(row) + 16
            if used > budget:
                raise MaterializationLimitError(
                    "Full result exceeds materialization budget; use iter_batches() or write()"
                )
            result.append(row)
        return result


    def iter_batches(self, batch_size=65536, *, memory_mb=None, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Stream complete schema-carrying detached batches.

        Full contract: mf.help("api.Frame.iter_batches")
        MIPFRAME HELP END
        """
        from mipframe.options import options
        from mipframe.core.resources import memory_bytes

        if self._size(batch_size) == 0:
            raise ValueError("Batch size must be positive")
        budget = memory_bytes(
            options.query.memory_mb if memory_mb is None else memory_mb
        )
        from mipframe.data.runtime import WorkContext
        from mipframe.data.relations import RelationBuilder
        root=WorkContext(budget/1024**2,options.query.disk_mb if disk_mb is None else disk_mb)
        iterator=self._iterate(memory_mb=memory_mb,disk_mb=disk_mb);builder=None
        try:
            while True:
                token=root.activate()
                try:
                    try:raw=next(iterator)
                    except StopIteration:break
                    if builder is None:builder=RelationBuilder(root.fork(),self.schema)
                    builder.append(raw)
                    if builder.count>=batch_size:
                        batch=RecordBatch._from_relation(self.schema,builder.finish());builder=None
                    else:batch=None
                finally:root.deactivate(token)
                if batch is not None:yield batch
            if builder is not None:yield RecordBatch._from_relation(self.schema,builder.finish());builder=None
        finally:
            iterator.close()
            if builder is not None:builder.work.close()
            root.close()

    def to_spec(self, *, memory_mb=None, disk_mb=None, materialize_mb=None):
        """
        MIPFRAME HELP BEGIN
        Serialize a versioned query AST.

        Full contract: mf.help("api.Frame.to_spec")
        MIPFRAME HELP END
        """
        from mipframe.frame.serialization import to_spec

        from mipframe.check.artifact import materialize
        from mipframe.data.runtime import WorkContext
        with WorkContext(memory_mb or 128,disk_mb):return materialize(to_spec(self),materialize_mb)

    def write(self, path, *, format=None, memory_mb=None, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Atomically write a complete relation.

        Full contract: mf.help("api.Frame.write")
        MIPFRAME HELP END
        """
        from mipframe.frame.serialization import write

        return write(self, path, format=format,memory_mb=memory_mb,disk_mb=disk_mb)

    def explain(self):
        """
        MIPFRAME HELP BEGIN
        Inspect a query plan without executing it.

        Full contract: mf.help("api.Frame.explain")
        MIPFRAME HELP END
        """
        from mipframe.frame.plan import explain

        return explain(self)

    def _search_runs(self):
        """Descriptive provenance survives relational composition; never a proof fact."""
        self._guard()
        from mipframe.frame.query import dependencies
        runs = []
        for frame in dependencies(self):
            frame._guard()
            for info in getattr(frame, "_search_provenance", ()):
                if info not in runs:
                    runs.append(dict(info))
        return runs

    def _search_display(self):
        from mipframe.options import options

        runs = self._search_runs()
        limit = max(1, options.display.max_rows)
        lines = [
            (
                "Candidate search"
                if r.get("mode") == "candidate"
                else "Exhaustive search"
            )
            + f" · {r.get('algorithm')} · {r.get('stop_reason')}"
            + f" · source retained {r.get('retained')} objects"
            + f" · {r.get('scope')} · {r.get('ranking')}"
            for r in runs[:limit]
        ]
        if len(runs) > limit:
            lines.append(f"... {len(runs) - limit} more source searches")
        return lines

    def __repr__(self):
        return self._render()

    def _render(self, max_rows=None):
        from mipframe.options import options
        max_rows = options.display.max_rows if max_rows is None else max_rows

        names = tuple(self._query.display_columns or self.schema)[
            : options.display.max_cols
        ]
        records = list(self.head(max_rows + 1)._iterate(names))
        total = self._known_count
        header = (
            f"<{type(self).__name__} key={self.key} · "
            + (f"{total:,} rows" if total is not None else "total not computed")
            + ">"
        )
        lines = [*self._search_display(), header, "  ".join(names)]
        lines.extend(
            "  ".join(self._display(n, r[n]) for n in names)
            for r in records[:max_rows]
        )
        if len(records) > max_rows:
            lines.append("...")
        return "\n".join(lines)

    def _repr_pretty_(self, p, cycle):
        p.text(repr(self))


def _names(values):
    return (
        tuple(values[0])
        if len(values) == 1 and isinstance(values[0], (tuple, list))
        else tuple(values)
    )


class Grouped:
    def __init__(self, frame, keys):
        if len(set(keys)) != len(keys) or not set(keys) <= set(frame.schema):
            raise SchemaError("Invalid grouping columns")
        self.frame, self.keys = frame, keys

    def agg(self, **aggregates):
        """
        MIPFRAME HELP BEGIN
        Compute exact grouped aggregates.

        Full contract: mf.help("api.Grouped.agg")
        MIPFRAME HELP END
        """
        if not aggregates:
            raise SchemaError("Specify aggregates")
        schema = {n: self.frame.schema[n] for n in self.keys}
        for n, a in aggregates.items():
            if n in schema:
                raise SchemaError("Aggregate name conflicts with a grouping column")
            if not isinstance(a, Aggregate):
                raise TypeError("agg accepts aggregate expressions")
            typ = a.expr.type(self.frame.schema) if a.expr is not None else DType("int")
            if a.op == "sum" and typ.kind not in ("int", "number"):
                raise SchemaError("sum requires numeric input")
            schema[n] = (
                DType("int")
                if a.op in ("count", "nunique")
                else typ.optional() if a.op in ("min", "max") else typ
            )
        return self.frame._derive(
            "group",
            dict(keys=self.keys, aggregates=aggregates),
            schema=schema,
            key=self.keys,
            unique=(self.keys,),
            domains={k: v for k, v in self.frame.domains.items() if k in self.keys},
        )


class RecordBatch:
    """
    MIPFRAME HELP BEGIN
    Schema-carrying immutable batch with detached records.

    Full contract: mf.help("api.RecordBatch")
    MIPFRAME HELP END
    """
    def __init__(self, schema, rows, *, memory_mb=None, disk_mb=None):
        frame=Frame.from_records(rows,schema=schema,memory_mb=memory_mb,disk_mb=disk_mb)
        table=frame._typed_relation
        object.__setattr__(self,'schema',frame.schema)
        object.__setattr__(self,'columns',MappingProxyType(table.columns))
        object.__setattr__(self,'_size',len(table))
        object.__setattr__(self,'_work',table.work)
        object.__setattr__(self,'_resource',frame._query.resource)

    @classmethod
    def _from_relation(cls,schema,relation):
        result=object.__new__(cls)
        object.__setattr__(result,'schema',frozen_schema(schema))
        object.__setattr__(result,'columns',MappingProxyType(relation.columns))
        object.__setattr__(result,'_size',len(relation))
        object.__setattr__(result,'_work',relation.work)
        return result

    def __setattr__(self, name, value):
        raise AttributeError("Record batches are read-only")

    def __len__(self):
        return self._size

    def _records(self):
        from mipframe.edit.draft import DraftRef
        from mipframe.data.relations import scalar
        for i in range(self._size):
            row={}
            for n,t in self.schema.items():
                value=scalar(self.columns[n][i])
                row[n]=(Ref(t.uid,t.version,t.axis,value) if t.kind=='ref' and value is not None
                        else DraftRef(t.uid,t.axis,value) if t.kind=='draft_ref' and value is not None else value)
            yield row

    def to_records(self, *, materialize_mb=None):
        from mipframe.options import options
        from mipframe.core.resources import memory_bytes
        budget=memory_bytes(options.query.materialize_mb if materialize_mb is None else materialize_mb)
        result=[];used=64
        for row in self._records():
            used+=size(row)+16
            if used>budget:raise MaterializationLimitError('Batch exceeds materialize_mb')
            result.append(row)
        return result
