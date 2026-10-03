"""Typed, exact, lazy relations for immutable MIP snapshots."""

from mipframe.frame.schema import DType
from mipframe.frame.schema import Ref
from mipframe.frame.schema import SchemaError
from mipframe.frame.expr import QueryExpr
from mipframe.frame.expr import col
from mipframe.frame.expr import count
from mipframe.frame.base import Frame
from mipframe.frame.base import Record
from mipframe.frame.base import RecordBatch
from mipframe.frame.column import ColumnView
from mipframe.frame.terms import Term
from mipframe.frame.terms import TermFrame
from mipframe.frame.executor import QueryMemoryError
from mipframe.frame.executor import MaterializationLimitError

from mipframe.frame.serialization import from_records
from mipframe.frame.serialization import frame_from_spec
from mipframe.frame.serialization import read_frame

Frame.from_records = staticmethod(from_records)
