"""Public graph operations dispatched to integer adjacency kernels."""
from mipframe.ops._runtime import operation

@operation("graph.components")
def components(ctx, E, *, u="u", v="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    Compute undirected components of an endpoint relation.

    Full contract: mf.help("api.ops.graph.components")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("graph.scc")
def scc(ctx, E, *, tail="u", head="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    Compute directed strongly connected components.

    Full contract: mf.help("api.ops.graph.scc")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("graph.topological_order")
def topological_order(ctx, E, *, tail="u", head="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    Order a directed acyclic graph deterministically.

    Full contract: mf.help("api.ops.graph.topological_order")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("graph.reachability")
def reachability(
    ctx,
    E,
    *,
    tail="u",
    head="v",
    vertices=None,
    sources=None,
    targets=None,
    include_self=False,
):
    """
    MIPFRAME HELP BEGIN
    Compute exact reachable endpoint pairs.

    Full contract: mf.help("api.ops.graph.reachability")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("graph.path")
def path(ctx, E, *, source, target, tail="u", head="v", vertices=None):
    """
    MIPFRAME HELP BEGIN
    Return one stable shortest directed path.

    Full contract: mf.help("api.ops.graph.path")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')

@operation("graph.transitive_reduction")
def transitive_reduction(ctx, E, *, tail="u", head="v", vertices=None, witnesses=False, edge_id=None, max_witness_edges=10_000_000):
    """
    MIPFRAME HELP BEGIN
    Reduce a DAG while preserving reachability.

    Full contract: mf.help("api.ops.graph.transitive_reduction")
    MIPFRAME HELP END
    """
    raise RuntimeError('Operator must execute through its physical planner')
