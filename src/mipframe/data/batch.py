"""Typed column batches shared by input adapters.

Model identity columns stay integers. Exact arithmetic uses Python integers or
rationals only in requested numeric expression columns; NumPy never wraps an
exact arithmetic operation. Unsupported relation sources use the spill backend.
"""
from dataclasses import dataclass
from fractions import Fraction
from functools import cmp_to_key
import operator
import numpy as np


@dataclass
class Columns:
    columns: dict
    size: int

    def take(self, ids):
        from mipframe.data.relations import ColumnView
        return Columns({k:ColumnView(v,ids) for k,v in self.columns.items()}, len(ids))

    def records(self, fields):
        arrays = [self.columns[n] for n in fields]
        for i in range(self.size):
            yield {n: scalar(a[i]) for n,a in zip(fields,arrays)}


def scalar(value):
    return value.item() if isinstance(value,np.generic) else value
