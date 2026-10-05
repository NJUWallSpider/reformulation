"""Exact LP/MPS models for Python."""

from mipframe.model import Model
from mipframe.io import read
from mipframe.io import load
from mipframe.store.numbers import MIPFrameError
from mipframe.options import options
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from mipframe.frame.entity import TermFrame
from mipframe.frame.row import Row
from mipframe.frame.var import Var
from mipframe.ir.algebraic import canonical_ratio
from mipframe.store.names import NameIndex
from mipframe.ops.matrix import MatrixView
from mipframe.ir.compressed import CompressedIR
from mipframe.ir.indexing import IndexedResult
from mipframe.check.report import Report
from mipframe.check.report import CardinalityError
from mipframe.check.report import ValidationError

__version__ = "1.4.0"
__all__ = [
    "read",
    "load",
    "Model",
    "RowFrame",
    "VarFrame",
    "TermFrame",
    "Row",
    "Var",
    "canonical_ratio",
    "NameIndex",
    "MatrixView",
    "CompressedIR",
    "IndexedResult",
    "Report",
    "CardinalityError",
    "ValidationError",
    "options",
    "MIPFrameError",
]

from mipframe.frame import Frame
from mipframe.frame import QueryExpr
from mipframe.frame import col
from mipframe.frame import count
from mipframe.frame import DType
from mipframe.frame import Ref
from mipframe.frame import Term
from mipframe.frame import Record
from mipframe.frame import RecordBatch
from mipframe.frame import ColumnView
from mipframe.frame import SchemaError
from mipframe.frame import QueryMemoryError
from mipframe.frame import MaterializationLimitError
__all__ += ["Frame", "QueryExpr", "col", "count", "DType", "Ref", "Term", "Record", "RecordBatch", "ColumnView", "SchemaError", "QueryMemoryError", "MaterializationLimitError"]

from mipframe.frame import frame_from_spec
from mipframe.frame import read_frame
__all__ += ["frame_from_spec", "read_frame"]

from mipframe import ops
from mipframe.check.algorithm import QueryTooLarge, DiskBudgetExceeded, GraphCycleError, NotDAGError
__all__ += ["ops", "QueryTooLarge", "DiskBudgetExceeded", "GraphCycleError", "NotDAGError"]

from mipframe import alg
from mipframe.check.algorithm import SearchBudgetExceeded
__all__ += ["alg", "SearchBudgetExceeded"]

from mipframe.linear import LinExpr, Claim, q, ExactArithmeticError, NonlinearExpressionError, MissingBoundError
__all__ += ["LinExpr", "Claim", "q", "ExactArithmeticError", "NonlinearExpressionError", "MissingBoundError"]



from mipframe.check.certificate import InvalidCertificateError, UnsupportedEnumerationDomain
__all__ += ["InvalidCertificateError", "UnsupportedEnumerationDomain"]

from mipframe.check.compiler import LinearBatch
__all__ += ["LinearBatch"]

from mipframe.edit import Edit, EditConflictError, EditValidationError, NonIsolatedVariableError, UndischargedAssumptionError, DraftRef
from mipframe.edit.diff import ModelDiff
from mipframe import audit
__all__ += ["Edit", "EditConflictError", "EditValidationError", "NonIsolatedVariableError", "UndischargedAssumptionError", "DraftRef", "ModelDiff", "audit"]

from mipframe._help import help
__all__ += ["help"]

from mipframe import analysis
__all__ += ["analysis"]

from mipframe.check.kernel import CertificateBatch, Targets
from mipframe.check.compact import BatchProof, ProofRef, ProofSelection
from mipframe.edit.delta import DeltaEdit as Edit
__all__ += ["CertificateBatch", "Targets", "BatchProof", "ProofRef", "ProofSelection"]

from mipframe.ops.reduction import LinearCertificate, TransitiveReductionResult
from mipframe.store.patch import PatchPlan, PatchNotSupportedError
from mipframe.edit.substitution import SubstitutionInfeasibleError
__all__ += ['LinearCertificate','TransitiveReductionResult','PatchPlan','PatchNotSupportedError','SubstitutionInfeasibleError']

from mipframe.store.output import WriteReceipt
__all__ += ['WriteReceipt']

from mipframe.check.graph import CheckBatch, CheckHandle, CheckResults
from mipframe.check.budget import ProofBudgetExceeded
__all__ += ['CheckBatch', 'CheckHandle', 'CheckResults', 'ProofBudgetExceeded']
