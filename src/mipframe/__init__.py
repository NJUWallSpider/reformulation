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
from mipframe.ir.algebraic import AlgebraicSignature
from mipframe.ir.algebraic import canonical_ratio
from mipframe.ir.typed import TypedSignature
from mipframe.ir.signatures import StructuralSignature
from mipframe.ir.signatures import StructuralSignatures
from mipframe.ir.signatures import VariableFamily
from mipframe.ir.families import ConstraintFamily
from mipframe.store.names import NameIndex
from mipframe.ops.matrix import MatrixView
from mipframe.ir.compressed import CompressedIR
from mipframe.ir.indexing import IndexedResult
from mipframe.ir.indexing import IndexedTensor
from mipframe.ir.indexing import IndexedOperator
from mipframe.check.report import ValidationReport
from mipframe.check.report import CardinalityError
from mipframe.check.report import ValidationError

__version__ = "0.8.0"
__all__ = [
    "read",
    "load",
    "Model",
    "RowFrame",
    "VarFrame",
    "TermFrame",
    "Row",
    "Var",
    "AlgebraicSignature",
    "TypedSignature",
    "StructuralSignature",
    "StructuralSignatures",
    "VariableFamily",
    "canonical_ratio",
    "ConstraintFamily",
    "NameIndex",
    "MatrixView",
    "CompressedIR",
    "IndexedResult",
    "IndexedTensor",
    "IndexedOperator",
    "ValidationReport",
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

from mipframe.check.report import Report
__all__ += ["Report"]

from mipframe.check.certificate import InvalidCertificateError, UnsupportedEnumerationDomain
__all__ += ["InvalidCertificateError", "UnsupportedEnumerationDomain"]

from mipframe.check.batch import LinearBatch
__all__ += ["LinearBatch"]

from mipframe.edit import Edit, EditConflictError, EditValidationError, NonIsolatedVariableError, UndischargedAssumptionError, DraftRef
from mipframe.edit.diff import ModelDiff
from mipframe import audit
__all__ += ["Edit", "EditConflictError", "EditValidationError", "NonIsolatedVariableError", "UndischargedAssumptionError", "DraftRef", "ModelDiff", "audit"]

from mipframe._help import help
__all__ += ["help"]
