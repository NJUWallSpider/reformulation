"""Public exact set operations dispatched to typed membership kernels."""
from mipframe.ops._runtime import operation

@operation("sets.equal_groups")
def equal_groups(ctx, S, *, set="set", member="member", value=None, sets=None):
    """
    MIPFRAME HELP BEGIN
    Classify exact equal supports or member-to-value maps.

    Full contract: mf.help("api.ops.sets.equal_groups")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("sets.pairs")
def pairs(
    ctx,
    S,
    *,
    set="set",
    member="member",
    sets=None,
    left=None,
    right=None,
    candidates=None,
    relation="overlap",
    ordered=None,
    include_self=False,
):
    """
    MIPFRAME HELP BEGIN
    Compare exact set supports and their cardinalities.

    Full contract: mf.help("api.ops.sets.pairs")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("sets.members")
def members(ctx, S, *, pairs, op="intersection", set="set", member="member", sets=None):
    """
    MIPFRAME HELP BEGIN
    Expand a specified pair relation into exact members.

    Full contract: mf.help("api.ops.sets.members")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("sets.components")
def components(ctx, S, *, set="set", member="member", sets=None, vertices=None):
    """
    MIPFRAME HELP BEGIN
    Find hypergraph member components without clique expansion.

    Full contract: mf.help("api.ops.sets.components")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("sets.aggregate")
def aggregate(
    ctx,
    membership,
    values,
    *,
    set="set",
    member="member",
    by=(),
    value="value",
    agg="sum",
    sets=None,
):
    """
    MIPFRAME HELP BEGIN
    Sum exact member attributes per set and optional group.

    Full contract: mf.help("api.ops.sets.aggregate")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("sets.pair_aggregate")
def pair_aggregate(
    ctx,
    membership,
    pairs,
    values,
    *,
    set="set",
    member="member",
    op="union",
    by=(),
    value="value",
    agg="sum",
    sets=None,
):
    """
    MIPFRAME HELP BEGIN
    Aggregate members of pair unions/intersections/differences without double counting.

    Full contract: mf.help("api.ops.sets.pair_aggregate")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')
