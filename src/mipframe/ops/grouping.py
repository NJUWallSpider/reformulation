"""Public exact grouped equality on typed indexes."""
from mipframe.ops._runtime import operation

@operation("equal_groups")
def equal_groups(ctx, F, *, by, rows, groups=None):
    """
    MIPFRAME HELP BEGIN
    Compare grouped payload multisets exactly.

    Full contract: mf.help("api.ops.equal_groups")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')
