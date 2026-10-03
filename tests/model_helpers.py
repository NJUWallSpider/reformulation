"""Internal family catalogs for mathematical regression tests."""

from mipframe.ir.families import families as algebraic_catalog
from mipframe.ir.signatures import signature_result, structural_families


def family_catalog(rows, signature=None, *, depth=None, **options):
    if signature in ("structural", "exact_structural", "exact_strctural"):
        result = signature_result(rows._owner, signature, depth, options)
        return structural_families(result, rows, result.depth)
    return algebraic_catalog(rows, "algebratic" if signature is None else signature)
