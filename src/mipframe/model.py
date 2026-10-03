"""Public API for the current numerical and structural Model."""

from __future__ import annotations

from mipframe._help.diagnostics import hint as _help_hint

from pathlib import Path
from os import PathLike
from mipframe.store.names import Records
from mipframe.store.names import Variable
from mipframe.store.names import Constraint
from mipframe.store.names import Objective
from mipframe.store.names import NameIndex
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from collections import Counter
import json
from mipframe.store.numbers import MIPFrameError
from mipframe.store.model_store import NumericalStore


class Model:
    """
    MIPFRAME HELP BEGIN
    Model [api.Model]

    Immutable numerical snapshot with derived IR and independent Edit construction.

    Model [type]

    When to use: Immutable numerical snapshot with derived IR and independent Edit construction.

    Returns: A public domain type. Obtain live handles through their owning Model or query.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: home, concept.identity
    MIPFRAME HELP END
    """
    def __init__(self, path: str | PathLike):
        self._store = None
        self._temporary = None
        try:
            self._store = NumericalStore(path)
            m = self._store
            if (
                len(m.row_ptr) != len(m.rows) + 1
                or len(m.col_ptr) != len(m.variables) + 1
                or int(m.row_ptr[-1]) != len(m.col_idx)
                or len(m.coeff_id) != len(m.col_idx)
                or int(m.col_ptr[-1]) != len(m.col_idx)
                or len(m.col_positions) != len(m.col_idx)
            ):
                raise MIPFrameError("Invalid native model dimensions")
            self._build_ir()
        except BaseException:
            self.close()
            raise

    @classmethod
    def _from_store(cls, store, temporary, *, memory_mb=128):
        self = cls.__new__(cls)
        self._store, self._temporary = store, temporary
        try:
            self._build_ir(memory_mb=memory_mb)
        except BaseException:
            self.close()
            raise
        return self

    def _build_ir(self, *, memory_mb=128):
        from mipframe.ir.compressed import CompressedIR
        self._ir = CompressedIR(self, memory_mb=memory_mb)
        from mipframe.ir.indexing import restore
        restore(self)
        self.structure.indexed(memory_mb=memory_mb)

    @property
    def ir(self):
        """
        Compressed model map, generated at model creation (template depth 2).

        MIPFRAME HELP BEGIN
        Model.ir [api.Model.ir]

        Prepared factual compressed IR; reading this property does not start another search.

        Model.ir [property]

        When to use: Prepared factual compressed IR; reading this property does not start another search.

        Returns: Prepared factual compressed IR; reading this property does not start another search.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        self.model
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
        Model.uid [api.Model.uid]

        Snapshot UUID; independent imports differ.

        Model.uid [property]

        When to use: Snapshot UUID; independent imports differ.

        Returns: Snapshot UUID; independent imports differ.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return self.model.manifest["uid"]

    @property
    def version(self):
        """
        MIPFRAME HELP BEGIN
        Model.version [api.Model.version]

        Ordered canonical mathematical content fingerprint.

        Model.version [property]

        When to use: Ordered canonical mathematical content fingerprint.

        Returns: Ordered canonical mathematical content fingerprint.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return self.model.manifest["model_version"]

    @property
    def storage_version(self):
        """
        MIPFRAME HELP BEGIN
        Model.storage_version [api.Model.storage_version]

        Native storage format version, independent of mathematical identity.

        Model.storage_version [property]

        When to use: Native storage format version, independent of mathematical identity.

        Returns: Native storage format version, independent of mathematical identity.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return self.model.manifest["schema_version"]

    @property
    def parent(self):
        """
        MIPFRAME HELP BEGIN
        Model.parent [api.Model.parent]

        Parent snapshot identity or None.

        Model.parent [property]

        When to use: Parent snapshot identity or None.

        Returns: Parent snapshot identity or None.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from copy import deepcopy
        return deepcopy(self.model.manifest["parent"])

    @property
    def stage(self):
        """
        MIPFRAME HELP BEGIN
        Model.stage [api.Model.stage]

        Explicit snapshot stage such as imported or edited.

        Model.stage [property]

        When to use: Explicit snapshot stage such as imported or edited.

        Returns: Explicit snapshot stage such as imported or edited.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return self.model.manifest["stage"]

    @property
    def source(self):
        """
        MIPFRAME HELP BEGIN
        Model.source [api.Model.source]

        Import or Edit provenance; not a second numerical model.

        Model.source [property]

        When to use: Import or Edit provenance; not a second numerical model.

        Returns: Import or Edit provenance; not a second numerical model.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
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
        Model.shape [api.Model.shape]

        Number of rows and variables.

        Model.shape [property]

        When to use: Number of rows and variables.

        Returns: Number of rows and variables.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return len(self.model.rows), len(self.model.variables)

    @property
    def nnz(self) -> int:
        """
        MIPFRAME HELP BEGIN
        Model.nnz [api.Model.nnz]

        Number of original nonzero constraint terms.

        Model.nnz [property]

        When to use: Number of original nonzero constraint terms.

        Returns: Number of original nonzero constraint terms.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return len(self.model.col_idx)

    @property
    def variables(self) -> Records[Variable]:
        from mipframe.store.names import Records

        self.model
        return Records(self, "variable")

    @property
    def constraints(self) -> Records[Constraint]:
        from mipframe.store.names import Records

        self.model
        return Records(self, "constraint")

    @property
    def rows(self) -> RowFrame:
        """
        MIPFRAME HELP BEGIN
        Model.rows [api.Model.rows]

        RowFrame of all rows.

        Model.rows [property]

        When to use: RowFrame of all rows.

        Returns: RowFrame of all rows.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return RowFrame(self)

    @property
    def vars(self) -> VarFrame:
        """
        MIPFRAME HELP BEGIN
        Model.vars [api.Model.vars]

        VarFrame of all variables.

        Model.vars [property]

        When to use: VarFrame of all variables.

        Returns: VarFrame of all variables.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return VarFrame(self)

    def terms(self, *, rows=None, vars=None):
        """
        MIPFRAME HELP BEGIN
        Model.terms [api.Model.terms]

        Select original nonzero matrix terms on optional row/variable axes.

        Model.terms(*, rows=None, vars=None)

        When to use: Select original nonzero matrix terms on optional row/variable axes.

        Returns: TermFrame(row,var,coef); includes no objective or boundary terms.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.frame.terms import TermFrame
        return TermFrame(self, rows=rows, vars=vars)

    def linexpr(self, T=None, *, vars=None, var="var", coef="coef", constant=0,
                memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Model.linexpr [api.Model.linexpr]

        Construct a canonical affine expression from a term Frame or selected variables.

        Model.linexpr(T=None, *, vars=None, var='var', coef='coef', constant=0, memory_mb=128, disk_mb=None)

        When to use: Construct a canonical affine expression from a term Frame or selected variables.

        Returns: LinExpr with duplicate variables summed and zero terms removed.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.linear.expr import linexpr
        return linexpr(self, T, vars=vars, var=var, coef=coef, constant=constant,
                       memory_mb=memory_mb, disk_mb=disk_mb)

    def edit(self, *, memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Model.edit [api.Model.edit]

        Create a source-relative Edit transaction.

        Model.edit(*, memory_mb=128, disk_mb=None)

        When to use: Create a source-relative Edit transaction.

        Returns: Edit; no snapshot is built yet.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.edit.plan import Edit
        return Edit(self, memory_mb=memory_mb, disk_mb=disk_mb)

    @property
    def lineage(self):
        """
        MIPFRAME HELP BEGIN
        Model.lineage [api.Model.lineage]

        Saved Edit history, not automatically trusted live proof.

        Model.lineage [property]

        When to use: Saved Edit history, not automatically trusted live proof.

        Returns: Saved Edit history, not automatically trusted live proof.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.edit.lineage import Lineage
        self.model
        return Lineage(self)

    def diff(self, source, *, memory_mb=128, disk_mb=None):
        """
        MIPFRAME HELP BEGIN
        Model.diff [api.Model.diff]

        Compare the current snapshot with its direct parent or identical snapshot.

        Model.diff(source, *, memory_mb=128, disk_mb=None)

        When to use: Compare the current snapshot with its direct parent or identical snapshot.

        Returns: ModelDiff; unrelated snapshots are rejected.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.edit.diff import ModelDiff
        return ModelDiff(self, source, memory_mb=memory_mb, disk_mb=disk_mb)

    @property
    def check(self):
        """
        MIPFRAME HELP BEGIN
        Model.check [api.Model.check]

        Checker for exact certificate verification.

        Model.check [property]

        When to use: Checker for exact certificate verification.

        Returns: Checker for exact certificate verification.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.check.accessor import Checker
        self.model
        return Checker(self)

    @property
    def matrix(self):
        """
        MIPFRAME HELP BEGIN
        Model.matrix [api.Model.matrix]

        MatrixAccessor; exact sparse counts/activity and explicit SciPy export.

        Model.matrix [property]

        When to use: MatrixAccessor; exact sparse counts/activity and explicit SciPy export.

        Returns: MatrixAccessor; exact sparse counts/activity and explicit SciPy export.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.ops.matrix import MatrixAccessor
        self.model
        return MatrixAccessor(self)

    @property
    def A(self):
        """
        A fresh, mutable float64 scipy.sparse.csr_matrix of all constraints.

                        Explicitly materializes sparse coefficients; requires mipframe[scipy].
                        The copy remains independent of the exact model and its lifetime.

        MIPFRAME HELP BEGIN
        Model.A [api.Model.A]

        Independent float64 SciPy CSR export of the full model; conversion is explicit and can fail.

        Model.A [property]

        When to use: Independent float64 SciPy CSR export of the full model; conversion is explicit and can fail.

        Returns: Independent float64 SciPy CSR export of the full model; conversion is explicit and can fail.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        return self.matrix.view().to_scipy()

    @property
    def row_index(self) -> NameIndex:
        """
        Original constraint name -> row position in A (original row ID).

        MIPFRAME HELP BEGIN
        Model.row_index [api.Model.row_index]

        Original row IDs for full matrix axis mapping.

        Model.row_index [property]

        When to use: Original row IDs for full matrix axis mapping.

        Returns: Original row IDs for full matrix axis mapping.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        self.model
        return NameIndex(self, "row")

    @property
    def var_index(self) -> NameIndex:
        """
        Original variable name -> column position in A (original variable ID).

        MIPFRAME HELP BEGIN
        Model.var_index [api.Model.var_index]

        Original variable IDs for full matrix axis mapping.

        Model.var_index [property]

        When to use: Original variable IDs for full matrix axis mapping.

        Returns: Original variable IDs for full matrix axis mapping.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        self.model
        return NameIndex(self, "var")

    @property
    def structure(self):
        """
        MIPFRAME HELP BEGIN
        Model.structure [api.Model.structure]

        Signature and indexed analysis access.

        Model.structure [property]

        When to use: Signature and indexed analysis access.

        Returns: Signature and indexed analysis access.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.ir.signatures import Structure
        self.model
        return Structure(self)

    @property
    def row_families(self):
        """Stored template depth-2 row-family directory; access records with .loc["F0"]."""
        return self.ir._family_directory("row", 2)

    @property
    def var_families(self):
        """Stored template depth-1 variable-role directory; access records with .loc["V0"]."""
        return self.ir._family_directory("var", 1)

    @property
    def objective(self) -> Objective:
        """
        MIPFRAME HELP BEGIN
        Model.objective [api.Model.objective]

        Original objective direction, constant and coefficients.

        Model.objective [property]

        When to use: Original objective direction, constant and coefficients.

        Returns: Original objective direction, constant and coefficients.

        Does not establish: No model change or additional mathematical conclusion is implied.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.store.names import Objective

        self.model
        return Objective(self)

    def save(self, directory: str | PathLike) -> Path:
        """
        MIPFRAME HELP BEGIN
        Model.save [api.Model.save]

        Atomically save a new native v3 directory.

        Model.save(directory: 'str | PathLike') -> 'Path'

        When to use: Atomically save a new native v3 directory.

        Returns: Saved path; existing destinations are rejected.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.io import save

        return save(self, directory)

    def write_lp(self, path: str | PathLike, **options) -> None:
        """
        MIPFRAME HELP BEGIN
        Model.write_lp [api.Model.write_lp]

        Atomically export exact LP where decimal representation is lossless.

        Model.write_lp(path: 'str | PathLike', **options) -> 'None'

        When to use: Atomically export exact LP where decimal representation is lossless.

        Returns: Output path; unsupported coefficients/free rows fail without publishing a partial file.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.store.exporter import export

        return export(self.model, path, **options)

    def close(self) -> None:
        """
        MIPFRAME HELP BEGIN
        Model.close [api.Model.close]

        Close owned resources and invalidate live dependent queries.

        Model.close() -> 'None'

        When to use: Close owned resources and invalidate live dependent queries.

        Returns: None; independently built snapshots and detached exports remain usable.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        # Free all descriptors before recursive directory cleanup needs its own
        # descriptors. One cleanup failure must not strand the remaining stores.
        stores = list(getattr(self, "_query_stores", ()))
        errors = []
        for store in stores:
            release = getattr(store, "_close_connection", None)
            if release is not None:
                try:
                    release()
                except Exception as exc:
                    errors.append(exc)
        for store in stores:
            try:
                store.close()
            except Exception as exc:
                errors.append(exc)
        ir = getattr(self, "_ir", None)
        if ir is not None:
            ir._release()
        refinement = getattr(self, "_refinement_store", None)
        if refinement is not None:
            refinement.close()
            self._refinement_store = None
        if self._store is not None:
            self._store.close()
            self._store = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        if errors:
            import sys
            active = sys.exception()
            if active is not None:
                active.add_note(f"Model resource cleanup also failed: {errors[0]}")
            else:
                raise errors[0]

    def __del__(self):
        if hasattr(self, "_store"):
            self.close()

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
        Model.verify [api.Model.verify]

        Validate numerical storage and replay prepared indexed results.

        Model.verify()

        When to use: Validate numerical storage and replay prepared indexed results.

        Returns: Verification summary; not a general MIP feasibility or optimality proof.

        Does not establish: No model change or additional mathematical conclusion is implied.

        Live results require all referenced snapshots to remain open. Explicit records are detached.

        See also: concept.identity
        MIPFRAME HELP END
        """
        from mipframe.check.model import verify

        result = verify(self.model)
        runs = getattr(self, "_indexed_results", {})
        for indexed in runs.values():
            indexed.verify().require()
        result["indexed"] = {"status": "PASS" if runs else "not_run", "runs": len(runs)}
        return result
