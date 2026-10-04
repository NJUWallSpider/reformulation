"""Public API for the current numerical and structural Model."""

from __future__ import annotations

from mipframe._help.diagnostics import hint as _help_hint

from pathlib import Path
from os import PathLike
from mipframe.store.names import Objective
from mipframe.store.names import NameIndex
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from collections import Counter
import json
from mipframe.store.numbers import MIPFrameError
from mipframe.store.model_store import NumericalStore


from mipframe.store.lifecycle import finalize_store

class Model:
    """
    MIPFRAME HELP BEGIN
    Immutable numerical snapshot with derived IR and independent Edit construction.

    Full contract: mf.help("api.Model")
    MIPFRAME HELP END
    """
    def __init__(self, path: str | PathLike):
        self._store = None
        self._temporary = None
        self._ir = None
        self._analysis_memory_mb = 128
        try:
            self._store = NumericalStore(path)
            from mipframe.store.lifecycle import register
            register(self, self._store)
            m = self._store
            from mipframe.store.integrity import dimensions_valid
            if not dimensions_valid(m):
                raise MIPFrameError("Invalid native model dimensions")
            self._build_ir()
        except BaseException:
            self.close()
            raise

    @classmethod
    def _from_store(cls, store, temporary, *, memory_mb=128, prepare="indexed"):
        self = cls.__new__(cls)
        self._store, self._temporary = store, temporary
        self._ir = None
        self._analysis_memory_mb = memory_mb
        try:
            from mipframe.store.lifecycle import register
            register(self, store)
            if prepare == "indexed":
                self._build_ir(memory_mb=memory_mb)
        except BaseException:
            self.close()
            raise
        return self

    def _build_ir(self, *, memory_mb=128):
        if self._ir is not None:
            return
        from mipframe.ir.compressed import CompressedIR
        ir = CompressedIR(self, memory_mb=memory_mb)
        try:
            self._restore_indexed()
            self.indexed(memory_mb=memory_mb)
        except BaseException:
            ir._release()
            raise
        self._ir = ir

    def _restore_indexed(self):
        if not getattr(self, "_indexed_restored", False):
            from mipframe.ir.indexing import restore
            restore(self)
            self._indexed_restored = True

    @property
    def ir(self):
        """
        Compressed model map (template depth 2); lazy after a logical Edit build.

        MIPFRAME HELP BEGIN
        Factual compressed IR; prepared by read/load, or lazily on first access after a logical Edit build.

        Full contract: mf.help("api.Model.ir")
        MIPFRAME HELP END
        """
        self.model
        if self._ir is None:
            self._build_ir(memory_mb=self._analysis_memory_mb)
        return self._ir

    @property
    def model(self) -> NumericalStore:
        """Internal engine access; public numerical data uses named views."""
        if self._store is None:
            raise _help_hint(MIPFrameError("Model is closed"), 'concept.identity')
        return self._store

    @property
    def uid(self):
        """
        MIPFRAME HELP BEGIN
        Snapshot UUID; independent imports differ.

        Full contract: mf.help("api.Model.uid")
        MIPFRAME HELP END
        """
        return self.model.manifest["uid"]

    @property
    def version(self):
        """
        MIPFRAME HELP BEGIN
        Ordered canonical mathematical content fingerprint.

        Full contract: mf.help("api.Model.version")
        MIPFRAME HELP END
        """
        return self.model.manifest["model_version"]

    @property
    def storage_version(self):
        """
        MIPFRAME HELP BEGIN
        Native storage format version, independent of mathematical identity.

        Full contract: mf.help("api.Model.storage_version")
        MIPFRAME HELP END
        """
        return self.model.manifest["schema_version"]

    @property
    def parent(self):
        """
        MIPFRAME HELP BEGIN
        Parent snapshot identity or None.

        Full contract: mf.help("api.Model.parent")
        MIPFRAME HELP END
        """
        from copy import deepcopy
        return deepcopy(self.model.manifest["parent"])

    @property
    def stage(self):
        """
        MIPFRAME HELP BEGIN
        Explicit snapshot stage such as imported or edited.

        Full contract: mf.help("api.Model.stage")
        MIPFRAME HELP END
        """
        return self.model.manifest["stage"]

    @property
    def source(self):
        """
        MIPFRAME HELP BEGIN
        Import or Edit provenance; not a second numerical model.

        Full contract: mf.help("api.Model.source")
        MIPFRAME HELP END
        """
        from types import MappingProxyType
        from copy import deepcopy
        data = self.model.manifest
        return MappingProxyType(dict(path=data["source"], sha256=data["source_sha256"],
            format=data["format"], options=MappingProxyType(deepcopy(data["build_options"])),
            numeric_provenance=data["numeric_provenance"]))

    @property
    def shape(self) -> tuple[int, int]:
        """
        MIPFRAME HELP BEGIN
        Number of rows and variables.

        Full contract: mf.help("api.Model.shape")
        MIPFRAME HELP END
        """
        return len(self.model.rows), len(self.model.variables)

    @property
    def nnz(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Number of original nonzero constraint terms.

        Full contract: mf.help("api.Model.nnz")
        MIPFRAME HELP END
        """
        return len(self.model.col_idx)



    @property
    def rows(self) -> RowFrame:
        """
        MIPFRAME HELP BEGIN
        RowFrame of all rows.

        Full contract: mf.help("api.Model.rows")
        MIPFRAME HELP END
        """
        return RowFrame(self)

    @property
    def vars(self) -> VarFrame:
        """
        MIPFRAME HELP BEGIN
        VarFrame of all variables.

        Full contract: mf.help("api.Model.vars")
        MIPFRAME HELP END
        """
        return VarFrame(self)

    def terms(self, *, rows=None, vars=None):
        """
        MIPFRAME HELP BEGIN
        Select original nonzero matrix terms on optional row/variable axes.

        Full contract: mf.help("api.Model.terms")
        MIPFRAME HELP END
        """
        from mipframe.frame.terms import TermFrame
        return TermFrame(self, rows=rows, vars=vars)

    def linexpr(self, T=None, *, vars=None, var="var", coef="coef", constant=0,
                memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Construct a canonical affine expression from a term Frame or selected variables.

        Full contract: mf.help("api.Model.linexpr")
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import linexpr
        return linexpr(self, T, vars=vars, var=var, coef=coef, constant=constant,
                       memory_mb=memory_mb, disk_mb=disk_mb)

    def edit(self, *, memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Create a source-relative Edit transaction.

        Full contract: mf.help("api.Model.edit")
        MIPFRAME HELP END
        """
        from mipframe.edit.plan import Edit
        return Edit(self, memory_mb=memory_mb, disk_mb=disk_mb)

    @property
    def lineage(self):
        """
        MIPFRAME HELP BEGIN
        Saved Edit history, not automatically trusted live proof.

        Full contract: mf.help("api.Model.lineage")
        MIPFRAME HELP END
        """
        from mipframe.edit.lineage import Lineage
        self.model
        return Lineage(self)

    def diff(self, source, *, verify="auto", memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Compare the current snapshot with its direct parent or identical snapshot.

        Full contract: mf.help("api.Model.diff")
        MIPFRAME HELP END
        """
        from mipframe.edit.diff import ModelDiff
        return ModelDiff(self, source, verify=verify, memory_mb=memory_mb, disk_mb=disk_mb)

    @property
    def check(self):
        """
        MIPFRAME HELP BEGIN
        Checker for exact certificate verification.

        Full contract: mf.help("api.Model.check")
        MIPFRAME HELP END
        """
        from mipframe.check.accessor import Checker
        self.model
        return Checker(self)

    @property
    def matrix(self):
        """
        MIPFRAME HELP BEGIN
        MatrixAccessor; exact sparse counts/activity and explicit SciPy export.

        Full contract: mf.help("api.Model.matrix")
        MIPFRAME HELP END
        """
        from mipframe.ops.matrix import MatrixAccessor
        self.model
        return MatrixAccessor(self)


    @property
    def row_index(self) -> NameIndex:
        """
        Original constraint name -> row position in A (original row ID).

        MIPFRAME HELP BEGIN
        Original row IDs for full matrix axis mapping.

        Full contract: mf.help("api.Model.row_index")
        MIPFRAME HELP END
        """
        self.model
        return NameIndex(self, "row")

    @property
    def var_index(self) -> NameIndex:
        """
        Original variable name -> column position in A (original variable ID).

        MIPFRAME HELP BEGIN
        Original variable IDs for full matrix axis mapping.

        Full contract: mf.help("api.Model.var_index")
        MIPFRAME HELP END
        """
        self.model
        return NameIndex(self, "var")




    def families(self, *, kind='row', depth=2, **options):
        """
        Template structural family directory, with original entity members.

        MIPFRAME HELP BEGIN
        Inspect template structural family directories and their members.

        Full contract: mf.help("api.Model.families")
        MIPFRAME HELP END
        """
        from mipframe.analysis import families
        if kind not in ('row', 'var'): raise ValueError('kind must be row or var')
        return families(self.rows if kind == 'row' else self.vars,
                        method='template', depth=depth, **options)

    def family(self, label, *, depth=None, **options):
        """
        Select exactly one template family as a Frame; F/V identifies its axis.

        MIPFRAME HELP BEGIN
        Select one template family by F/V label and explicit depth.

        Full contract: mf.help("api.Model.family")
        MIPFRAME HELP END
        """
        from mipframe.store.incidence import _FAMILY
        from mipframe.frame.expr import col
        if not isinstance(label, str): raise TypeError('Family label must be a string')
        match = _FAMILY.fullmatch(label)
        if match is None: raise KeyError(label)
        explicit = int(match[3]) if match[3] is not None else None
        if depth is not None and explicit is not None and depth != explicit:
            raise ValueError('Family label and explicit depth disagree')
        depth = explicit if depth is None and explicit is not None else 2 if depth is None else depth
        directory = self.families(kind='row' if match[1]=='F' else 'var',depth=depth,**options)
        selected = directory.where(id=label.split('@')[0])
        if selected.count() != 1: raise KeyError(label)
        selected._family_label = label.split('@')[0]
        return selected

    @property
    def row_families(self):
        """
        Template row families at depth 2.

        MIPFRAME HELP BEGIN
        Template row-family Frame at depth 2.

        Full contract: mf.help("api.Model.row_families")
        MIPFRAME HELP END
        """
        return self.families(kind='row')

    @property
    def var_families(self):
        """
        Template variable families at depth 2.

        MIPFRAME HELP BEGIN
        Template variable-family Frame at depth 2.

        Full contract: mf.help("api.Model.var_families")
        MIPFRAME HELP END
        """
        return self.families(kind='var')

    def refinement(self, start=1, stop=2, *, kind='row', entities=None, **options):
        """
        Parent/child family transitions as a Frame.

        MIPFRAME HELP BEGIN
        Inspect parent/child family transitions, including unchanged groups.

        Full contract: mf.help("api.Model.refinement")
        MIPFRAME HELP END
        """
        from mipframe.analysis import _transitions
        return _transitions(self,start,stop,kind=kind,entities=entities,**options)

    def indexed(self, *, rows=None, vars=None, depth=2, method='template', memory_mb=128, **options):
        """
        Retrieve or discover indexed structure using explicit refinement options.

        MIPFRAME HELP BEGIN
        Retrieve or discover indexed coordinates with explicit structural configuration.

        Full contract: mf.help("api.Model.indexed")
        MIPFRAME HELP END
        """
        from mipframe.ir.indexing import indexed
        from mipframe.ir.signatures import refinement
        self._restore_indexed()
        if method not in ('template', 'exact_structural'):
            raise ValueError('Indexed analysis requires template or exact_structural')
        result = refinement(self,depth=depth,mode='template' if method=='template' else 'exact',memory_mb=memory_mb,**options)
        return indexed(self,rows=rows,vars=vars,signatures=result,memory_mb=memory_mb)

    @property
    def objective(self) -> Objective:
        """
        MIPFRAME HELP BEGIN
        Original objective direction, constant and coefficients.

        Full contract: mf.help("api.Model.objective")
        MIPFRAME HELP END
        """
        from mipframe.store.names import Objective

        self.model
        return Objective(self)

    def save(self, directory: str | PathLike) -> Path:
        """
        MIPFRAME HELP BEGIN
        Atomically save a new native v3 directory.

        Full contract: mf.help("api.Model.save")
        MIPFRAME HELP END
        """
        from mipframe.io import save

        return save(self, directory)

    def write_lp(self, path: str | PathLike, **options) -> None:
        """
        MIPFRAME HELP BEGIN
        Atomically export exact LP where decimal representation is lossless.

        Full contract: mf.help("api.Model.write_lp")
        MIPFRAME HELP END
        """
        from mipframe.store.exporter import export

        return export(self.model, path, **options)

    def close(self) -> None:
        """
        MIPFRAME HELP BEGIN
        Close owned resources and invalidate live dependent queries.

        Full contract: mf.help("api.Model.close")
        MIPFRAME HELP END
        """
        from mipframe.store.lifecycle import release_all, cleanup_directory, retry_cleanup
        resources = list(getattr(self, '_query_stores', ()))
        # The base store may exist before registration if construction failed.
        if self._store is not None and self._store not in resources:
            resources.append(self._store)
        ir, temporary = getattr(self, '_ir', None), self._temporary
        self._store = self._temporary = None
        self._refinement_store = None
        self._analysis_groups = {}
        disconnect = [r._close_connection for r in resources if hasattr(r, '_close_connection')]
        actions = [*disconnect, *(r.close for r in resources)]
        if ir is not None:
            actions.append(ir._release)
        actions.extend((lambda: cleanup_directory(temporary), retry_cleanup))
        release_all(actions)

    def __del__(self):
        if hasattr(self, '_store'):
            finalize_store(self)

    def __enter__(self) -> Model:
        self.model
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        if self._store is None:
            return "MIPModel(closed)"
        if getattr(self, "_display", None) is None:
            m = self.model
            rows, variables = self.shape
            # Aggregate on disk instead of materializing all variable records.
            counts = m.db.execute(
                "SELECT v.kind, count(*), "
                "sum(CASE WHEN n.numerator <> '0' THEN 1 ELSE 0 END) "
                "FROM variables v JOIN numbers n ON n.id=v.obj GROUP BY v.kind"
            ).fetchall()
            types = Counter(m.variable_type(v) for v in m.variables)
            objective_nnz = sum(count for _, _, count in counts)
            density = self.nnz / (rows * variables) if rows and variables else 0.0
            density_text = f"{density:.3g}"
            if "e" in density_text:
                mantissa, exponent = density_text.split("e")
                density_text = f"{mantissa}e{int(exponent):+d}"
            name = Path(m.manifest.get("source") or m.path).stem
            fields = (
                ("sense", self.objective.sense),
                ("variables", f"{variables:,}"),
                ("constraints", f"{rows:,}"),
                ("nonzeros", f"{self.nnz:,}"),
                ("density", density_text),
                ("var types", " | ".join(
                    f"{kind} {types[kind]:,}" for kind in ("B", "I", "C")
                    if types.get(kind)
                ) or "—"),
                ("objective nz", f"{objective_nnz:,}"),
            )
            self._display = (
                f"MIPModel({json.dumps(name, ensure_ascii=False)})\n"
                + "\n".join(f"  {label:<13}: {value}" for label, value in fields)
            )
        return self._display

    def _repr_pretty_(self, printer, cycle):
        """Keep the multiline representation intact in IPython notebooks."""
        printer.text(repr(self))

    def verify(self):
        """
        Verify immutable numerical artifacts and all prepared indexed results.

        MIPFRAME HELP BEGIN
        Validate numerical storage and replay prepared indexed results.

        Full contract: mf.help("api.Model.verify")
        MIPFRAME HELP END
        """
        from mipframe.check.model import verify

        return verify(self)
