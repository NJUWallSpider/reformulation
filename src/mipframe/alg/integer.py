"""Exhaustive exact finite integer enumeration on typed arrays."""
from mipframe.ops._runtime import operation

@operation("alg.integer.enumerate", search=True)
def enumerate(ctx, variables, terms, rows, *, max_states=None, time_limit=None):
    """
    All feasible assignments of a bounded integer linear system, or an error.

    MIPFRAME HELP BEGIN
    Enumerate the complete feasible assignments of an explicit finite integer system.

    Full contract: mf.help("api.alg.integer.enumerate")
    MIPFRAME HELP END
    """
    from mipframe.execution.integer import enumerate_system
    return enumerate_system(ctx,variables,terms,rows)
