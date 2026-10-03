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
    Record [api.Record]

    Live read-only record; to_record exports detached values.

    Record [type]

    When to use: Live read-only record; to_record exports detached values.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.identity
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
            return (
                self._frame._owner_for(typ).rows.by_id(value)
                if typ.axis == "row"
                else self._frame._owner_for(typ).vars.by_id(value)
            )
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
    Frame [api.Frame]

    Typed exact relation with declared key, domains and composable query plans.

    Frame [type]

    When to use: Typed exact relation with declared key, domains and composable query plans.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.frame, api.Frame.from_records
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
        self._op, self._parents, self._params, self._source = (
            op,
            tuple(parents),
            params or {},
            source,
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
        resource = getattr(self, "_resource", None)
        if resource is not None:
            resource.guard()

    @property
    def schema(self):
        """
        MIPFRAME HELP BEGIN
        Frame.schema [api.Frame.schema]

        Read the mapping from column names to DType metadata.

        Frame.schema [property]

        When to use: Read the mapping from column names to DType metadata.

        Returns: Column DTypes, including snapshot-qualified row and variable references.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Illustrative example (requires caller-provided inputs):
        schema = {"target": "str", "var": m.vars.schema["var"], "coef": "number"}

        For Frame.from_records, provide a schema mapping such as str, int or number for scalar fields.
        Copy reference types from m.vars.schema["var"] or m.rows.schema["row"] and pass models=(m,) when building model-bound records.
        Use key=("target",) only for genuinely unique target records. A terms table can contain several variables per target.
        Schema describes column types; it does not establish uniqueness, coverage or a mathematical claim.

        See also: api.Frame.from_records, api.DType, api.Frame.key, concept.frame
        MIPFRAME HELP END
        """
        self._guard()
        return self._schema

    @property
    def columns(self):
        """
        MIPFRAME HELP BEGIN
        Frame.columns [api.Frame.columns]

        Read the Frame columns metadata.

        Frame.columns [property]

        When to use: Read the Frame columns metadata.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return tuple(self.schema)

    @property
    def key(self):
        """
        MIPFRAME HELP BEGIN
        Frame.key [api.Frame.key]

        Read the Frame key metadata.

        Frame.key [property]

        When to use: Read the Frame key metadata.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        self._guard()
        return self._key

    @property
    def unique(self):
        """
        MIPFRAME HELP BEGIN
        Frame.unique [api.Frame.unique]

        Read the Frame unique metadata.

        Frame.unique [property]

        When to use: Read the Frame unique metadata.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        self._guard()
        return self._unique | frozenset(self._verified_unique)

    @property
    def models(self):
        """
        MIPFRAME HELP BEGIN
        Frame.models [api.Frame.models]

        Read the Frame models metadata.

        Frame.models [property]

        When to use: Read the Frame models metadata.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        self._guard()
        return frozenset(m.uid for m in self._models)

    @property
    def domains(self):
        """
        MIPFRAME HELP BEGIN
        Frame.domains [api.Frame.domains]

        Declared domains retained through filtering; not reduced to observed values.

        Frame.domains [property]

        When to use: Declared domains retained through filtering; not reduced to observed values.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        self._guard()
        return self._domains

    @property
    def relations(self):
        """
        MIPFRAME HELP BEGIN
        Frame.relations [api.Frame.relations]

        Read-only mapping of attached normalized Frames, such as members and search metadata.

        Frame.relations [property]

        When to use: Read-only mapping of attached normalized Frames, such as members and search metadata.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        self._guard()
        return self._relations

    @property
    def iloc(self):
        """
        MIPFRAME HELP BEGIN
        Frame.iloc [api.Frame.iloc]

        Position lookup or slice; supports negative positions, rejects bool.

        Frame.iloc [property]

        When to use: Position lookup or slice; supports negative positions, rejects bool.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return Locator(self, True)

    @property
    def loc(self):
        """
        MIPFRAME HELP BEGIN
        Frame.loc [api.Frame.loc]

        Lookup by declared key, with tuple for compound keys; no-key lookup fails.

        Frame.loc [property]

        When to use: Lookup by declared key, with tuple for compound keys; no-key lookup fails.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return Locator(self, False)

    @property
    def shape(self):
        """
        MIPFRAME HELP BEGIN
        Frame.shape [api.Frame.shape]

        Exact row count and number of fields; unlike repr this can evaluate the query.

        Frame.shape [property]

        When to use: Exact row count and number of fields; unlike repr this can evaluate the query.

        Returns: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return len(self), len(self.schema)

    def __getitem__(self, key):
        self._guard()
        raise _help_hint(TypeError(
            "Use .iloc for positions, .loc for keys, .family(), .where() or .column(); Frame [] is not supported"
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

    def _iterate(self, fields=None, memory_mb=None):
        self._guard()
        fields = tuple(self.schema if fields is None else fields)
        ctx = Context(self._models, memory_mb)
        try:
            iterator = iter(self._raw(fields, ctx))
            while True:
                # Guard before resuming any sparse generator: model.close()
                # may have unmapped arrays while this iterator was suspended.
                self._guard()
                try:
                    row = next(iterator)
                except StopIteration:
                    break
                ctx.check_size(row)
                yield row
        finally:
            if not ctx.retained:
                ctx.close()

    def _raw(self, fields, ctx):
        self._guard()
        op, p = self._op, self._params
        if op == "source":
            yield from self._source(fields, ctx)
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
        return sum(1 for _ in self._iterate(()))

    def count(self):
        """
        MIPFRAME HELP BEGIN
        Frame.count [api.Frame.count]

        Compute an exact record count.

        Frame.count()

        When to use: Compute an exact record count.

        Inputs: No arguments.

        Returns: Integer; arbitrary queries may require complete execution.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return len(self)

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
        result = Frame(
            self.schema if schema is None else schema,
            key=self.key if key == "inherit" else key,
            models=self._models,
            op=op,
            parents=(self,),
            params=params,
            unique=self.unique if unique is None else unique,
            domains=self.domains if domains is None else domains,
            relations=self.relations,
        )
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
        Frame.where [api.Frame.where]

        Filter by serializable boolean predicates and keyword equalities.

        Frame.where(*predicates, **equalities)

        When to use: Filter by serializable boolean predicates and keyword equalities.

        Inputs: Predicates combine by intersection. Ref membership validates axis/snapshot.

        Returns: Same Frame type and order; nullable filters retain only True.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
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
        result = self._derive("filter", {"predicates": tuple(values)}, preserve=True)

        # Membership queries also own the referenced live selection dependency.
        def dependencies(e):
            for a in e.args:
                if isinstance(a, Frame):
                    yield a
                elif isinstance(a, QueryExpr):
                    yield from dependencies(a)

        dependencies = tuple(f for e in values for f in dependencies(e))
        if dependencies:
            result._models = merge_models(self, *dependencies)
        return result

    def select(self, *names, **expressions):
        """
        MIPFRAME HELP BEGIN
        Frame.select [api.Frame.select]

        Choose, rename or calculate columns.

        Frame.select(*names, **expressions)

        When to use: Choose, rename or calculate columns.

        Inputs: Column names and named QueryExpr expressions; duplicate outputs are errors.

        Returns: Ordinary Frame. Direct retained/renamed keys can propagate; computed columns gain no inferred uniqueness.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
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
        Frame.join [api.Frame.join]

        Join relations with explicit cardinality and match requirements.

        Frame.join(other, *, on, how='inner', cardinality='auto', require=None)

        When to use: Join relations with explicit cardinality and match requirements.

        Inputs: on=str/tuple/mapping; inner or left; auto/1:1/1:m/m:1/m:m; require=None/left/right/both.

        Returns: Ordinary Frame ordered by left then right. Complete validation precedes output.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame, error.join
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
        return Frame(
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

    def group_by(self, *keys):
        """
        MIPFRAME HELP BEGIN
        Frame.group_by [api.Frame.group_by]

        Declare grouping keys before aggregation.

        Frame.group_by(*keys)

        When to use: Declare grouping keys before aggregation.

        Inputs: One or more scalar columns.

        Returns: Grouped object; use agg to compute a Frame sorted by group keys.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return Grouped(self, _names(keys))

    def distinct(self):
        """
        MIPFRAME HELP BEGIN
        Frame.distinct [api.Frame.distinct]

        Remove duplicate complete output records.

        Frame.distinct()

        When to use: Remove duplicate complete output records.

        Inputs: Current output columns.

        Returns: Ordinary Frame retaining first occurrences, with all output columns unique.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return self._derive(
            "distinct", {}, key=tuple(self.schema), unique=(tuple(self.schema),)
        )

    def sort_by(self, *by, descending=False):
        """
        MIPFRAME HELP BEGIN
        Frame.sort_by [api.Frame.sort_by]

        Stable exact ordering by selected columns.

        Frame.sort_by(*by, descending=False)

        When to use: Stable exact ordering by selected columns.

        Inputs: Column names; descending bool or per-column directions.

        Returns: Same type; missing values last.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
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
        Frame.head [api.Frame.head]

        Construct a lazy prefix limit.

        Frame.head(n=5)

        When to use: Construct a lazy prefix limit.

        Inputs: Nonnegative integer n.

        Returns: Same type, original domains; does not count first or skip validation barriers.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return self.iloc[: self._size(n)]

    def tail(self, n=5):
        """
        MIPFRAME HELP BEGIN
        Frame.tail [api.Frame.tail]

        Select a final suffix.

        Frame.tail(n=5)

        When to use: Select a final suffix.

        Inputs: Nonnegative integer n; may require full input evaluation.

        Returns: Same type and relative order.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        n = self._size(n)
        return self.iloc[-n:] if n else self.head(0)

    def sample(self, n=5, *, random_state=None):
        """
        MIPFRAME HELP BEGIN
        Frame.sample [api.Frame.sample]

        Sample without replacement and retain relative input order.

        Frame.sample(n=5, *, random_state=None)

        When to use: Sample without replacement and retain relative input order.

        Inputs: n and optional integer random_state.

        Returns: Same type; reproducible with fixed random_state.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
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
        Frame.one [api.Frame.one]

        Require exactly one output record.

        Frame.one()

        When to use: Require exactly one output record.

        Inputs: Zero-argument query.

        Returns: One record/entity; checks at most two outputs after upstream barriers.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
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
        Frame.refs [api.Frame.refs]

        Extract unique nonmissing typed entity references.

        Frame.refs(name)

        When to use: Extract unique nonmissing typed entity references.

        Inputs: A Ref column; not an untyped integer column.

        Returns: RowFrame or VarFrame in model order.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
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
        result._source_spec = {"kind": "entity_refs", "source": self, "column": name}
        return result

    def column(self, name):
        """
        MIPFRAME HELP BEGIN
        Frame.column [api.Frame.column]

        Read only one column and its dependencies.

        Frame.column(name)

        When to use: Read only one column and its dependencies.

        Inputs: Column name.

        Returns: ColumnView with exact reductions and aligned comparisons.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return ColumnView(self, name)

    def nunique(self, columns):
        """
        MIPFRAME HELP BEGIN
        Frame.nunique [api.Frame.nunique]

        Count distinct combinations of selected columns.

        Frame.nunique(columns)

        When to use: Count distinct combinations of selected columns.

        Inputs: Column name or tuple.

        Returns: Exact integer count.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        names = (columns,) if isinstance(columns, str) else tuple(columns)
        return len(self.select(*names).distinct())

    def check(self, kind, columns=None, **kwargs):
        """
        MIPFRAME HELP BEGIN
        Frame.check [api.Frame.check]

        Verify a declared relational property.

        Frame.check(kind, columns=None, **kwargs)

        When to use: Verify a declared relational property.

        Inputs: unique/product with columns; function with key/value.

        Returns: Report; product includes all declared domains, never a materialized Cartesian product.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        from mipframe.check.frame import check

        return check(self, kind, columns, **kwargs)

    def collect(self, *, memory_mb=None):
        """
        MIPFRAME HELP BEGIN
        Frame.collect [api.Frame.collect]

        Explicitly materialize detached records within a budget.

        Frame.collect(*, memory_mb=None)

        When to use: Explicitly materialize detached records within a budget.

        Inputs: Optional memory_mb.

        Returns: Independent list; over limit raises MaterializationLimitError without a partial return.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        from mipframe.options import options
        from mipframe.store.query_store import memory_bytes

        budget = memory_bytes(
            options.query.materialize_mb if memory_mb is None else memory_mb
        )
        result = []
        used = size(result)
        for raw in self._iterate():
            row = {n: self._detach(n, v) for n, v in raw.items()}
            used += size(row) + 16
            if used > budget:
                raise MaterializationLimitError(
                    "Full result exceeds materialization budget; use iter_batches() or write()"
                )
            result.append(row)
        return result

    def to_records(self, *, memory_mb=None):
        """
        MIPFRAME HELP BEGIN
        Frame.to_records [api.Frame.to_records]

        Export independent records using the collect contract.

        Frame.to_records(*, memory_mb=None)

        When to use: Export independent records using the collect contract.

        Inputs: Optional memory_mb.

        Returns: Detached Ref identities and exact scalar values.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        return self.collect(memory_mb=memory_mb)

    def iter_batches(self, batch_size=65536, *, memory_mb=None):
        """
        MIPFRAME HELP BEGIN
        Frame.iter_batches [api.Frame.iter_batches]

        Stream complete schema-carrying detached batches.

        Frame.iter_batches(batch_size=65536, *, memory_mb=None)

        When to use: Stream complete schema-carrying detached batches.

        Inputs: Positive batch_size and optional memory_mb.

        Returns: Iterator; interrupted/closed live queries raise instead of silently ending.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        from mipframe.options import options
        from mipframe.store.query_store import memory_bytes

        if self._size(batch_size) == 0:
            raise ValueError("Batch size must be positive")
        budget = memory_bytes(
            options.query.memory_mb if memory_mb is None else memory_mb
        )
        batch = []
        used = 0
        for raw in self._iterate(memory_mb=memory_mb):
            cost = size(raw)
            if cost > budget:
                raise MaterializationLimitError("One record exceeds the batch budget")
            if batch and (len(batch) >= batch_size or used + cost > budget):
                yield RecordBatch(self.schema, batch)
                batch = []
                used = 0
            batch.append(raw)
            used += cost
        if batch:
            yield RecordBatch(self.schema, batch)

    def to_spec(self):
        """
        MIPFRAME HELP BEGIN
        Frame.to_spec [api.Frame.to_spec]

        Serialize a versioned query AST.

        Frame.to_spec()

        When to use: Serialize a versioned query AST.

        Inputs: No implicit execution or trusted proof flags.

        Returns: Independent spec; restoration requires explicit model/relation bindings.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.serialization import to_spec

        return to_spec(self)

    def write(self, path, *, format=None):
        """
        MIPFRAME HELP BEGIN
        Frame.write [api.Frame.write]

        Atomically write a complete relation.

        Frame.write(path, *, format=None)

        When to use: Atomically write a complete relation.

        Inputs: Path; JSONL or optional Parquet.

        Returns: Exact rational and Ref identity encoding; Parquet needs mipframe[parquet].

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.serialization import write

        return write(self, path, format=format)

    def explain(self):
        """
        MIPFRAME HELP BEGIN
        Frame.explain [api.Frame.explain]

        Inspect a query plan without executing it.

        Frame.explain()

        When to use: Inspect a query plan without executing it.

        Inputs: No arguments.

        Returns: Schema, keys, dependencies, paths and blocking/validation stages.

        Establishes: Live results require all referenced snapshots to remain open. Explicit records are detached.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Failures: Schema/type errors or execution/resource errors preserve their diagnostics.

        Resources: Working memory defaults to 128 MiB (minimum 16 KiB). Disk spill is exact; the budget is not a process RSS limit.

        Use explicit declared domains for coverage checks. A subset check never proves an ancestor query correct.

        See also: concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.plan import explain

        return explain(self)

    def _search_runs(self):
        """Descriptive provenance survives relational composition; never a proof fact."""
        self._guard()
        runs, seen, pending = [], set(), [self]
        while pending:
            frame = pending.pop()
            if id(frame) in seen:
                continue
            seen.add(id(frame))
            frame._guard()
            for info in getattr(frame, "_search_provenance", ()):
                if info not in runs:
                    runs.append(dict(info))
            pending.extend(reversed(frame._parents))
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
        from mipframe.options import options

        names = tuple(getattr(self, "_display_columns", self.schema))[
            : options.display.max_cols
        ]
        records = list(islice(self._iterate(names), options.display.max_rows + 1))
        total = self._known_count
        header = (
            f"<{type(self).__name__} key={self.key} · "
            + (f"{total:,} rows" if total is not None else "total not computed")
            + ">"
        )
        lines = [*self._search_display(), header, "  ".join(names)]
        lines.extend(
            "  ".join(self._display(n, r[n]) for n in names)
            for r in records[: options.display.max_rows]
        )
        if len(records) > options.display.max_rows:
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
        Grouped.agg [api.Grouped.agg]

        Compute exact grouped aggregates.

        Grouped.agg(**aggregates)

        When to use: Compute exact grouped aggregates.

        Inputs: Named mf.count() or col(...).count/sum/min/max/nunique expressions.

        Returns: Ordinary Frame keyed by the grouping columns.

        Establishes: Only observed groups are emitted. Missing values ignored; empty group extrema are None.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Illustrative example (requires caller-provided inputs):
        G = T.group_by("row").agg(n=mf.count(), total=mf.col("coef").sum())

        See also: api.Frame.group_by
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
    RecordBatch [api.RecordBatch]

    Schema-carrying immutable batch with detached records.

    RecordBatch [type]

    When to use: Schema-carrying immutable batch with detached records.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.Frame.iter_batches
    MIPFRAME HELP END
    """
    def __init__(self, schema, rows):
        object.__setattr__(self, "schema", frozen_schema(schema))
        object.__setattr__(
            self,
            "columns",
            MappingProxyType({n: tuple(r[n] for r in rows) for n in schema}),
        )
        object.__setattr__(self, "_size", len(rows))

    def __setattr__(self, name, value):
        raise AttributeError("Record batches are read-only")

    def __len__(self):
        return self._size

    def to_records(self):
        from mipframe.edit.draft import DraftRef
        return [
            {
                n: (
                    Ref(t.uid, t.version, t.axis, self.columns[n][i])
                    if t.kind == "ref" and self.columns[n][i] is not None
                    else DraftRef(t.uid,t.axis,self.columns[n][i]) if t.kind == "draft_ref" and self.columns[n][i] is not None else self.columns[n][i]
                )
                for n, t in self.schema.items()
            }
            for i in range(self._size)
        ]
