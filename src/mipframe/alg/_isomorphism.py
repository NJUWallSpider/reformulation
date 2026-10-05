"""Exact whole-graph classes: refinement proposes, bijections establish identity."""

import hashlib
from mipframe.ops._runtime import operation
from mipframe.frame.schema import DType, SchemaError


def labels_tuple(value):
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(x, str) for x in value
    ):
        raise TypeError("Labels must be column names")
    return tuple(value)






@operation("alg.graph.isomorphism_classes", search=True)
def isomorphism_classes(
    ctx,
    E,
    *,
    vertices,
    graph="graph",
    u="u",
    v="v",
    graphs=None,
    directed=False,
    vertex_labels=(),
    edge_labels=(),
    max_states=None,
    time_limit=None,
):
    """
    MIPFRAME HELP BEGIN
    Classify complete explicitly labeled graph instances by exact isomorphism.

    Full contract: mf.help("api.alg.graph.isomorphism_classes")
    MIPFRAME HELP END
    """
    from mipframe.execution.isomorphism import classify
    return classify(ctx,E,vertices=vertices,graph=graph,u=u,v=v,graphs=graphs,directed=directed,vertex_labels=vertex_labels,edge_labels=edge_labels,max_states=max_states,time_limit=time_limit)
