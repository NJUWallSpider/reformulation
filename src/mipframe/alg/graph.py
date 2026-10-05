"""Verified graph searches; candidate discovery does not prove a MIP inequality."""

from mipframe.ops._runtime import operation
from mipframe.store.search_store import Objects, SearchStopped, nonnegative
from mipframe.alg._graph import SearchGraph


def minimum(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


@operation("alg.graph.clique_candidates", search=True)
def clique_candidates(
    ctx,
    E,
    *,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    score=None,
    value="value",
    top=100,
    min_size=2,
    max_states=1_000_000,
    time_limit=None,
):
    """
    Deterministic greedy maximal cliques, ranked among the objects found.

    MIPFRAME HELP BEGIN
    Find greedy inclusion-maximal cliques, ranked among those found.

    Full contract: mf.help("api.alg.graph.clique_candidates")
    MIPFRAME HELP END
    """
    from mipframe.execution.search import graph_search
    return graph_search(ctx,'clique_candidates',E,u=u,v=v,vertices=vertices,scope=scope,score=score,value=value,top=top,min_size=min_size,max_states=max_states,time_limit=time_limit)


@operation("alg.graph.maximal_cliques", search=True)
def maximal_cliques(
    ctx,
    E,
    *,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    min_size=2,
    max_states=None,
    time_limit=None,
):
    """
    All inclusion-maximal cliques in the induced scope, or an exception.

    MIPFRAME HELP BEGIN
    Enumerate every inclusion-maximal clique in the declared scope.

    Full contract: mf.help("api.alg.graph.maximal_cliques")
    MIPFRAME HELP END
    """
    from mipframe.execution.search import graph_search
    return graph_search(ctx,'maximal_cliques',E,u=u,v=v,vertices=vertices,scope=scope,min_size=min_size,max_states=max_states,time_limit=time_limit)




@operation("alg.graph.odd_cycle_candidates", search=True)
def odd_cycle_candidates(
    ctx,
    E,
    *,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    lengths=(5, 7, 9),
    chordless=True,
    score=None,
    value="value",
    top=100,
    max_states=1_000_000,
    time_limit=None,
):
    """
    MIPFRAME HELP BEGIN
    Find verified undirected odd cycles of specified lengths.

    Full contract: mf.help("api.alg.graph.odd_cycle_candidates")
    MIPFRAME HELP END
    """
    from mipframe.execution.search import graph_search
    return graph_search(ctx,'odd_cycle_candidates',E,u=u,v=v,vertices=vertices,scope=scope,lengths=lengths,chordless=chordless,score=score,value=value,top=top,max_states=max_states,time_limit=time_limit)


@operation("alg.graph.simple_cycles", search=True)
def simple_cycles(
    ctx,
    E,
    *,
    directed,
    u="u",
    v="v",
    vertices=None,
    scope=None,
    min_length=None,
    max_length=None,
    chordless=False,
    max_states=None,
    time_limit=None,
):
    """
    MIPFRAME HELP BEGIN
    Enumerate all simple cycles in an explicitly directed or undirected graph.

    Full contract: mf.help("api.alg.graph.simple_cycles")
    MIPFRAME HELP END
    """
    from mipframe.execution.search import graph_search
    return graph_search(ctx,'simple_cycles',E,directed=directed,u=u,v=v,vertices=vertices,scope=scope,min_length=min_length,max_length=max_length,chordless=chordless,max_states=max_states,time_limit=time_limit)


from mipframe.alg._isomorphism import isomorphism_classes
