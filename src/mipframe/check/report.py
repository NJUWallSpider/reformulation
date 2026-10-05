"""Immutable assertion reports and mathematical certificates."""

from mipframe._help.diagnostics import hint as _help_hint

from mipframe.store.numbers import MIPFrameError


from mipframe.core.errors import CardinalityError


from mipframe.core.errors import ValidationError


class Report:
    """
    One live immutable report for data assertions and mathematical certificates.

    MIPFRAME HELP BEGIN
    Structural and integrity diagnostic result; mathematical checkers return ProofRef, BatchProof or PointReport.

    Full contract: mf.help("api.Report")
    MIPFRAME HELP END
    """

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            raise AttributeError("Reports are read-only")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        store,
        ok,
        summary,
        violations,
        *,
        status=None,
        conclusion=None,
        scope=None,
        method=None,
        dependencies=None,
        evidence=None,
        counterexample=None,
        recipe=None,
    ):
        from types import MappingProxyType

        self._store, self._summary, self._violations = store, dict(summary), violations
        self._status = status or ("PROVED" if ok else "REFUTED")
        if self._status not in ("PROVED", "REFUTED", "INCONCLUSIVE"):
            raise ValueError("Unknown Report status")
        self._conclusion = (
            conclusion
            if conclusion is not None
            else dict(kind="relation", operation=summary.get("operation"))
        )
        self._scope = dict(
            scope
            or dict(
                kind="relation",
                domain=None,
                arithmetic="exact",
                complete=True,
                selection=summary.get("scope", "declared input"),
            )
        )
        if "snapshot" not in self._scope:
            self._scope["snapshots"] = tuple(
                dict(uid=m.uid, version=m.version) for m in violations._models
            )
            self._scope["assumptions"] = 0
        self._method = method or summary.get("operation", "relation check")
        self._dependencies = dependencies
        self._evidence = MappingProxyType(dict(evidence or {}))
        self._counterexample = counterexample
        self._recipe = recipe
        self._verified = False
        self._parents = ()
        self._assumption_reader = lambda: iter(())

    def _guard(self):
        self._store.guard()

    @property
    def status(self):
        """
        MIPFRAME HELP BEGIN
        Read report status with the original conclusion and scope.

        Full contract: mf.help("api.Report.status")
        MIPFRAME HELP END
        """
        self._guard()
        return self._status

    @property
    def ok(self):
        """
        MIPFRAME HELP BEGIN
        Read report ok with the original conclusion and scope.

        Full contract: mf.help("api.Report.ok")
        MIPFRAME HELP END
        """
        return self.status == "PROVED"

    @property
    def summary(self):
        """
        MIPFRAME HELP BEGIN
        Read report summary with the original conclusion and scope.

        Full contract: mf.help("api.Report.summary")
        MIPFRAME HELP END
        """
        from copy import deepcopy

        self._guard()
        return deepcopy(self._summary)

    @property
    def scope(self):
        """
        MIPFRAME HELP BEGIN
        Read report scope with the original conclusion and scope.

        Full contract: mf.help("api.Report.scope")
        MIPFRAME HELP END
        """
        from copy import deepcopy

        self._guard()
        return deepcopy(self._scope)

    @property
    def conclusion(self):
        """
        MIPFRAME HELP BEGIN
        Read report conclusion with the original conclusion and scope.

        Full contract: mf.help("api.Report.conclusion")
        MIPFRAME HELP END
        """
        from copy import deepcopy

        self._guard()
        return (
            deepcopy(self._conclusion)
            if isinstance(self._conclusion, dict)
            else self._conclusion
        )

    @property
    def method(self):
        """
        MIPFRAME HELP BEGIN
        Read report method with the original conclusion and scope.

        Full contract: mf.help("api.Report.method")
        MIPFRAME HELP END
        """
        self._guard()
        return self._method

    @property
    def violations(self):
        """
        MIPFRAME HELP BEGIN
        Read report violations with the original conclusion and scope.

        Full contract: mf.help("api.Report.violations")
        MIPFRAME HELP END
        """
        self._guard()
        return self._violations

    @property
    def evidence(self):
        """
        MIPFRAME HELP BEGIN
        Read report evidence with the original conclusion and scope.

        Full contract: mf.help("api.Report.evidence")
        MIPFRAME HELP END
        """
        self._guard()
        return self._evidence

    def _empty(self):
        from mipframe.frame.base import Frame

        return Frame(
            {
                "reason": __import__("mipframe.frame.schema", fromlist=["DType"]).DType(
                    "str"
                )
            },
            models=self._violations._models,
            source=lambda fields, ctx: iter(()),
            known_count=0,
        )

    @property
    def dependencies(self):
        """
        MIPFRAME HELP BEGIN
        Read report dependencies with the original conclusion and scope.

        Full contract: mf.help("api.Report.dependencies")
        MIPFRAME HELP END
        """
        self._guard()
        return self._dependencies if self._dependencies is not None else self._empty()

    @property
    def counterexample(self):
        """
        MIPFRAME HELP BEGIN
        Read report counterexample with the original conclusion and scope.

        Full contract: mf.help("api.Report.counterexample")
        MIPFRAME HELP END
        """
        self._guard()
        return (
            self._counterexample if self._counterexample is not None else self._empty()
        )

    def __bool__(self):
        raise _help_hint(TypeError("Report has no truth value; inspect status or use require()"), 'error.expressions')

    def require(self):
        """
        MIPFRAME HELP BEGIN
        Raise ValidationError carrying this report unless status is PROVED.

        Full contract: mf.help("api.Report.require")
        MIPFRAME HELP END
        """
        if not self.ok:
            raise ValidationError(self)
        return self

    def explain(self):
        """
        MIPFRAME HELP BEGIN
        Read report explain with the original conclusion and scope.

        Full contract: mf.help("api.Report.explain")
        MIPFRAME HELP END
        """
        from mipframe.frame.row import Formula
        from itertools import islice

        lines = [repr(self)]
        for i, parent in enumerate(islice(self._parents, 8)):
            lines.append(
                f"  depends on #{i+1}: {parent.status}; {parent.method}; domain={parent.scope.get('domain')}"
            )
            lines.append("    " + repr(parent.conclusion))
        if len(self._parents) > 8:
            lines.append("  dependencies: ... (see dependencies Frame)")
        return Formula("\n".join(lines))

    def for_target(self, key):
        """
        MIPFRAME HELP BEGIN
        Select one conclusion from a batch Report, retaining shared evidence storage.

        Full contract: mf.help("api.Report.for_target")
        MIPFRAME HELP END
        """
        self._guard()
        if not getattr(self, "_batch", None): raise TypeError("for_target requires a batch report")
        return self._batch.one(key)

    def to_spec(self):
        """
        MIPFRAME HELP BEGIN
        Read report to_spec with the original conclusion and scope.

        Full contract: mf.help("api.Report.to_spec")
        MIPFRAME HELP END
        """
        from mipframe.check.serialization import report_spec

        return report_spec(self)

    def write(self, path):
        """
        MIPFRAME HELP BEGIN
        Atomically export the exact certificate package, not the whole model.

        Full contract: mf.help("api.Report.write")
        MIPFRAME HELP END
        """
        from mipframe.check.serialization import write_report

        return write_report(self, path)

    @classmethod
    def replay(
        cls, spec_or_path, *, models, relations=None, memory_mb=128, disk_mb=None
    ):
        """
        MIPFRAME HELP BEGIN
        Bind dependencies and rerun certificate verification; never trust saved status.

        Full contract: mf.help("api.Report.replay")
        MIPFRAME HELP END
        """
        from mipframe.check.serialization import replay

        if isinstance(spec_or_path, (str, __import__('pathlib').Path)) and __import__('pathlib').Path(spec_or_path).is_dir():
            from mipframe.check.artifact import read_bundle
            spec,tree=read_bundle(spec_or_path)
            try:return cls.replay(spec,models=models,relations=relations,memory_mb=memory_mb,disk_mb=disk_mb)
            finally:
                for array in tree.maps:array._mmap.close()
        if isinstance(spec_or_path, (str, __import__('pathlib').Path)):
            import json
            try:
                candidate = json.loads(__import__('pathlib').Path(spec_or_path).read_text())
            except json.JSONDecodeError:
                candidate = None
            if isinstance(candidate, dict) and candidate.get('schema', '').startswith('mipframe-'):
                spec_or_path = candidate
        if isinstance(spec_or_path, dict) and spec_or_path.get('schema') in ('mipframe-point-1','mipframe-point-2'):
            from fractions import Fraction
            matching = [m for m in models if m.uid == spec_or_path.get('snapshot')]
            if len(matching) != 1:
                raise ValueError('Missing point snapshot binding')
            class PointInput:
                def items(self):
                    if spec_or_path['schema']=='mipframe-point-1':
                        return ((int(v),Fraction(x)) for v,x in spec_or_path['values'].items())
                    ids,values=spec_or_path['ids'],spec_or_path['values']
                    if len(ids)!=len(values):raise ValueError('Point artifact lengths differ')
                    return zip(ids,values)
            return matching[0].check.solution(PointInput(),memory_mb=memory_mb,disk_mb=disk_mb)

        if isinstance(spec_or_path, dict) and spec_or_path.get('schema') == 'mipframe-proof-ref-2':
            from mipframe.check.compact import ProofRef
            batch=cls.replay(spec_or_path['batch'],models=models,memory_mb=memory_mb,disk_mb=disk_mb)
            return ProofRef(batch,spec_or_path['index'])

        if isinstance(spec_or_path, dict) and spec_or_path.get('schema') == 'mipframe-certificate-2':
            from mipframe.check.compact import BatchProof
            matching = [m for m in models if m.uid == spec_or_path.get('snapshot')]
            if len(matching) != 1:
                raise ValueError('Missing certificate snapshot binding')
            return BatchProof.replay(spec_or_path, model=matching[0],memory_mb=memory_mb,disk_mb=disk_mb)

        return replay(
            spec_or_path,
            models=models,
            relations=relations,
            memory_mb=memory_mb,
            disk_mb=disk_mb,
        )

    def __repr__(self):
        from itertools import islice

        self._guard()
        lines = [
            f"<Report: {self.status}; {self.method}>",
            f"  scope: {self._scope.get('kind', 'relation')}; domain: {self._scope.get('domain')}; arithmetic: {self._scope.get('arithmetic')}",
        ]
        if self._scope.get("local"):
            lines.append(
                "  local premises only; counterexample extension to the model is not established"
            )
        assumptions = list(islice(self._assumption_reader(), 5))
        for a in assumptions[:4]:
            lines.append("  assume: " + repr(a))
        if len(assumptions) > 4:
            lines.append("  assume: ...")
        if self._conclusion is not None and not isinstance(self._conclusion, dict):
            lines.append("  conclusion: " + repr(self._conclusion))
        for k, v in islice(self.summary.items(), 12):
            lines.append(f"  {k}: {str(v)[:300]}")
        return "\n".join(lines)

    def _repr_pretty_(self, printer, cycle):
        printer.text(repr(self))
