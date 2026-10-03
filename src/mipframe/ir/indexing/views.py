"""Live indexed entities, bounded collections and shared validation reports."""

import json
from copy import deepcopy
from fractions import Fraction
from collections.abc import Mapping
from collections.abc import Sequence
from mipframe.check.report import ValidationReport
from mipframe.check.report import CardinalityError
from mipframe.store.query_store import SQLIndex
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from mipframe.frame.entity import _Frame
from mipframe.frame.row import Formula


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def report(s, errors, **summary):
    from mipframe.frame.adapters import sql_frame
    from mipframe.frame.schema import DType
    table = s.table("reason TEXT,detail TEXT")
    s.db.executemany(
        f"INSERT INTO {table} VALUES(?,?)",
        ((e.get("reason", "validation failure"), json.dumps(e)) for e in errors),
    )
    return ValidationReport(
        s,
        not errors,
        dict(scope="full declared selection", **summary),
        sql_frame(s, f"SELECT * FROM {table}", {"reason": DType("str"), "detail": DType("str")}),
    )


class Frozen:
    def __setattr__(self, name, value):
        if not name.startswith("_"):
            raise AttributeError("Indexed results are read-only")
        object.__setattr__(self, name, value)


class IndexedCard(Frozen, Mapping):
    def __init__(self, result, record):
        self._result, self._store, self._data = result, result._store, record

    def __getitem__(self, key):
        self._store.guard()
        return deepcopy(self._data[key])

    def __iter__(self):
        for key in self._data:
            self._store.guard()
            yield key

    def __len__(self):
        self._store.guard()
        return len(self._data)

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def to_record(self):
        self._store.guard()
        return deepcopy(self._data)

    def __repr__(self):
        self._store.guard()
        if "reason" in self._data:
            return f"<Indexed evidence: {self['reason']}>"
        if "sets" in self._data:
            return f"<Indexed partition: {self['sets']:,} sets · {self['variables']:,} variables>"
        return f"<{type(self).__name__}>"

    def _repr_pretty_(self, p, cycle):
        p.text(repr(self))


def domain_frame(s, g, axis):
    cls = RowFrame if axis == "row" else VarFrame
    return cls(
        s.owner,
        SQLIndex(
            s,
            f"SELECT id FROM domain_members WHERE g={int(g)} ORDER BY id",
            unique=True,
        ),
    )


class IndexedTensor(IndexedCard):
    """
    MIPFRAME HELP BEGIN
    IndexedTensor [api.IndexedTensor]

    Verified coordinate view with explicit declared axes.

    IndexedTensor [type]

    When to use: Verified coordinate view with explicit declared axes.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: recipe.coordinates
    MIPFRAME HELP END
    """
    @property
    def vars(self):
        """
        MIPFRAME HELP BEGIN
        IndexedTensor.vars [api.IndexedTensor.vars]

        Inspect or verify IndexedTensor vars.

        IndexedTensor.vars [property]

        When to use: Inspect or verify IndexedTensor vars.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return domain_frame(self._store, self.vars_domain, "var")

    @property
    def axes(self):
        """
        MIPFRAME HELP BEGIN
        IndexedTensor.axes [api.IndexedTensor.axes]

        Inspect or verify IndexedTensor axes.

        IndexedTensor.axes [property]

        When to use: Inspect or verify IndexedTensor axes.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return tuple(domain_frame(self._store, g, "row") for g in self.rows_domains)

    @property
    def coordinates(self):
        """
        MIPFRAME HELP BEGIN
        IndexedTensor.coordinates [api.IndexedTensor.coordinates]

        Inspect or verify IndexedTensor coordinates.

        IndexedTensor.coordinates [property]

        When to use: Inspect or verify IndexedTensor coordinates.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import sql_frame
        from mipframe.frame.schema import ref_type
        s = self._store
        return sql_frame(s,
            f"SELECT v AS var,i,j FROM coordinates WHERE t={quote(self.id)} ORDER BY v",
            {'var':ref_type(s.owner,'var'),'i':ref_type(s.owner,'row'),'j':ref_type(s.owner,'row')},
            key=('var',), domains=dict(zip(('i','j'), self.axes)))

    def verify(self):
        """
        MIPFRAME HELP BEGIN
        IndexedTensor.verify [api.IndexedTensor.verify]

        Inspect or verify IndexedTensor verify.

        IndexedTensor.verify()

        When to use: Inspect or verify IndexedTensor verify.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._result.validate(dict(kind="cartesian", id=self.id))

    def __repr__(self):
        return f"<IndexedTensor: {self.shape[0]:,} × {self.shape[1]:,}; {self.variables:,} variables; missing={self.missing_cells:,}>"


class IndexedOperator(IndexedCard):
    """
    MIPFRAME HELP BEGIN
    IndexedOperator [api.IndexedOperator]

    Recovered exact operator with original bindings and kernel.

    IndexedOperator [type]

    When to use: Recovered exact operator with original bindings and kernel.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.IndexedOperator.verify
    MIPFRAME HELP END
    """
    @property
    def rows(self):
        """
        MIPFRAME HELP BEGIN
        IndexedOperator.rows [api.IndexedOperator.rows]

        Inspect or verify IndexedOperator rows.

        IndexedOperator.rows [property]

        When to use: Inspect or verify IndexedOperator rows.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        s = self._store
        return RowFrame(
            s.owner,
            SQLIndex(
                s,
                f"SELECT r AS id FROM bindings WHERE o={quote(self.id)} ORDER BY r",
                unique=True,
            ),
        )

    @property
    def bindings(self):
        """
        MIPFRAME HELP BEGIN
        IndexedOperator.bindings [api.IndexedOperator.bindings]

        Inspect or verify IndexedOperator bindings.

        IndexedOperator.bindings [property]

        When to use: Inspect or verify IndexedOperator bindings.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import sql_frame
        from mipframe.frame.schema import DType
        from mipframe.frame.schema import ref_type
        return sql_frame(self._store,
            f"SELECT r AS row,p,k,anchor,scale,lo AS lower,hi AS upper FROM bindings WHERE o={quote(self.id)} ORDER BY p,k",
            dict(row=ref_type(self._store.owner,'row'),p=DType('int'),k=DType('int'),anchor=ref_type(self._store.owner,'var'),scale=DType('number'),lower=DType('number',True),upper=DType('number',True)),key=('row',))

    @property
    def kernel(self):
        """
        MIPFRAME HELP BEGIN
        IndexedOperator.kernel [api.IndexedOperator.kernel]

        Inspect or verify IndexedOperator kernel.

        IndexedOperator.kernel [property]

        When to use: Inspect or verify IndexedOperator kernel.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import sql_frame
        from mipframe.frame.schema import DType
        return sql_frame(self._store,
            f"SELECT p,CASE WHEN k=-1 THEN NULL ELSE k END AS k,j,value FROM kernel WHERE o={quote(self.id)} ORDER BY p,k,j",
            dict(p=DType('int'),k=DType('int',True),j=DType('int'),value=DType('number')),key=('p','k','j'))

    @property
    def alignment(self):
        """
        MIPFRAME HELP BEGIN
        IndexedOperator.alignment [api.IndexedOperator.alignment]

        Inspect or verify IndexedOperator alignment.

        IndexedOperator.alignment [property]

        When to use: Inspect or verify IndexedOperator alignment.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.frame.adapters import sql_frame
        from mipframe.frame.schema import DType
        return sql_frame(self._store,f"SELECT p,j FROM alignment WHERE o={quote(self.id)} ORDER BY p",dict(p=DType('int'),j=DType('int')),key=('p',))

    def formula(self):
        """
        MIPFRAME HELP BEGIN
        IndexedOperator.formula [api.IndexedOperator.formula]

        Inspect or verify IndexedOperator formula.

        IndexedOperator.formula()

        When to use: Inspect or verify IndexedOperator formula.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return Formula(self["formula"])

    def verify(self):
        """
        MIPFRAME HELP BEGIN
        IndexedOperator.verify [api.IndexedOperator.verify]

        Inspect or verify IndexedOperator verify.

        IndexedOperator.verify()

        When to use: Inspect or verify IndexedOperator verify.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._result.validate(dict(kind="indexed_operator", id=self.id))

    def __repr__(self):
        return f"<IndexedOperator: {self.kind}; {self['rows']:,} rows; exact>"


class IndexedResult(Frozen):
    """
    MIPFRAME HELP BEGIN
    IndexedResult [api.IndexedResult]

    Completed coordinate and operator discovery, with exact verification.

    IndexedResult [type]

    When to use: Completed coordinate and operator discovery, with exact verification.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: recipe.coordinates
    MIPFRAME HELP END
    """
    def __init__(self, store, run):
        self._store, self._id = store, run

    @property
    def id(self):
        self._store.guard()
        return self._id

    @property
    def scope(self):
        return self.to_record()["scope"]

    def _card(self, kind, ident):
        self._store.guard()
        row = self._store.execute("SELECT record FROM cards WHERE run=? AND kind=? AND id=?",(self.id,kind,ident)).fetchone()
        if row is None: raise KeyError(ident)
        cls = IndexedTensor if kind == 'tensors' else IndexedOperator if kind == 'operators' else IndexedCard
        return cls(self,json.loads(row[0]))

    def tensor(self, ident):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.tensor [api.IndexedResult.tensor]

        Inspect or verify IndexedResult tensor.

        IndexedResult.tensor(ident)

        When to use: Inspect or verify IndexedResult tensor.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._card('tensors',ident)
    def operator(self, ident):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.operator [api.IndexedResult.operator]

        Inspect or verify IndexedResult operator.

        IndexedResult.operator(ident)

        When to use: Inspect or verify IndexedResult operator.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._card('operators',ident)
    def partition(self, ident):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.partition [api.IndexedResult.partition]

        Inspect or verify IndexedResult partition.

        IndexedResult.partition(ident)

        When to use: Inspect or verify IndexedResult partition.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._card('partitions',ident)
    def evidence_item(self, ident):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.evidence_item [api.IndexedResult.evidence_item]

        Inspect or verify IndexedResult evidence_item.

        IndexedResult.evidence_item(ident)

        When to use: Inspect or verify IndexedResult evidence_item.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._card('evidence',ident)

    def _directory(self, kind):
        from mipframe.frame import Frame
        from mipframe.frame import DType
        fields = {
            'tensors': dict(id=DType('str'),nrows=DType('int'),ncols=DType('int'),variables=DType('int'),complete_product=DType('bool'),missing_cells=DType('int'),verification=DType('str')),
            'operators': dict(id=DType('str'),tensor=DType('str'),kind=DType('str'),rows=DType('int'),axis_invariant=DType('bool'),verification=DType('str')),
            'partitions': dict(id=DType('str'),sets=DType('int',True),variables=DType('int',True),verification=DType('str',True)),
            'evidence': dict(id=DType('str'),reason=DType('str')),
        }[kind]
        def scan(columns, context):
            for (payload,) in self._store.execute("SELECT record FROM cards WHERE run=? AND kind=? ORDER BY id",(self.id,kind)):
                record = json.loads(payload)
                if kind == 'tensors': record.update(nrows=record['shape'][0],ncols=record['shape'][1])
                yield {n:record.get(n) for n in columns}
        result = Frame(fields,key=('id',),models=(self._store.owner,),source=scan)
        result._resource = self._store
        result._display_columns = tuple(n for n in fields if n not in ("id", "tensor"))
        result._source_spec = {'kind':'relation','relation':f'indexed:{self.id}:{kind}'}
        return result

    @property
    def tensors(self):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.tensors [api.IndexedResult.tensors]

        Inspect or verify IndexedResult tensors.

        IndexedResult.tensors [property]

        When to use: Inspect or verify IndexedResult tensors.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._directory('tensors')
    @property
    def partitions(self):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.partitions [api.IndexedResult.partitions]

        Inspect or verify IndexedResult partitions.

        IndexedResult.partitions [property]

        When to use: Inspect or verify IndexedResult partitions.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._directory('partitions')
    @property
    def operators(self):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.operators [api.IndexedResult.operators]

        Inspect or verify IndexedResult operators.

        IndexedResult.operators [property]

        When to use: Inspect or verify IndexedResult operators.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._directory('operators')
    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.evidence [api.IndexedResult.evidence]

        Inspect or verify IndexedResult evidence.

        IndexedResult.evidence [property]

        When to use: Inspect or verify IndexedResult evidence.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return self._directory('evidence')

    def to_record(self):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.to_record [api.IndexedResult.to_record]

        Inspect or verify IndexedResult to_record.

        IndexedResult.to_record()

        When to use: Inspect or verify IndexedResult to_record.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        return json.loads(
            self._store.execute(
                "SELECT record FROM runs WHERE id=?", (self.id,)
            ).fetchone()[0]
        )

    def validate(self, spec):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.validate [api.IndexedResult.validate]

        Inspect or verify IndexedResult validate.

        IndexedResult.validate(spec)

        When to use: Inspect or verify IndexedResult validate.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        from mipframe.ir.indexing.validation import validate

        result=validate(self, spec)
        return self._report_recipe(result, spec)

    def verify(self):
        """
        MIPFRAME HELP BEGIN
        IndexedResult.verify [api.IndexedResult.verify]

        Inspect or verify IndexedResult verify.

        IndexedResult.verify()

        When to use: Inspect or verify IndexedResult verify.

        Returns: Public summary, domain handle, Frame or verification Report.

        Establishes: Large coordinates, bindings and kernels stay in queryable Frames; rendering is bounded.

        Does not establish: A structural signature alone does not prove coordinates, symmetry or a valid cut.

        See also: recipe.coordinates, concept.frame
        MIPFRAME HELP END
        """
        errors = []
        checked = 0
        for collection, factory in ((self.tensors, self.tensor), (self.operators, self.operator)):
            for record in collection:
                item = factory(record.id)
                answer = item.verify()
                checked += 1
                if not answer.ok:
                    errors.append(
                        dict(
                            reason="verification failed",
                            id=item.id,
                            summary=answer.summary,
                        )
                    )
        return self._report_recipe(report(self._store, errors, operation="indexed", checked_results=checked))

    def _report_recipe(self, answer, spec=None):
        answer._recipe = dict(kind='indexed', run=self.id)
        if spec is not None:
            answer._recipe['spec'] = deepcopy(spec)
        answer._scope['snapshot'] = dict(uid=self._store.owner.uid, version=self._store.owner.version)
        return answer

    def __repr__(self):
        return f"<IndexedResult complete: {len(self.tensors)} coordinate views, {len(self.operators)} operators; heuristic search>"

    def _repr_pretty_(self, p, cycle):
        p.text(repr(self))


def ir_summary(owner, limit=None):
    results = getattr(owner, "_indexed_results", {})
    runs = []
    for key, result in sorted(results.items())[:limit]:
        record = result.to_record()
        record.update(
            total_tensors=len(result.tensors),
            total_operators=len(result.operators),
            tensors=[
                dict(
                    id=t.id,
                    shape=t.shape,
                    variables=t.variables,
                    complete_product=t.complete_product,
                    missing_cells=t.missing_cells,
                )
                for t in (result.tensor(record.id) for record in (
                    result.tensors if limit is None else result.tensors.head(limit)
                ))
            ],
            operators=[
                dict(
                    id=o.id,
                    rows=o["rows"],
                    kind=o.kind,
                    axis_invariant=o.axis_invariant,
                )
                for o in (result.operator(record.id) for record in (
                    result.operators if limit is None else result.operators.head(limit)
                ))
            ],
        )
        runs.append(record)
    return dict(
        total_runs=len(results),
        status="complete" if runs else "not_run",
        search_scope="declared candidate rules; not exhaustive structural discovery",
        runs=runs,
    )
