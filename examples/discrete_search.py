"""Composition from membership to graph candidates, without a business detector."""

from fractions import Fraction
import mipframe as mf
from mipframe import Frame, col


def main():
    # Each member labels a source witness. This fixture assumes evidence has
    # already been checked by its caller; graph search does not check MIP validity.
    membership = Frame.from_records(
        [
            {"set": 0, "member": "packing_a"},
            {"set": 1, "member": "packing_a"},
            {"set": 2, "member": "packing_a"},
            {"set": 2, "member": "packing_b"},
            {"set": 3, "member": "packing_b"},
        ],
        schema={"set": "int", "member": "str"},
    )
    pairs = mf.ops.sets.pairs(membership, relation="overlap")
    witnesses = mf.ops.sets.members(membership, pairs=pairs)
    edges = pairs.select(u=col("left"), v=col("right"))
    scores = Frame.from_records(
        [{"vertex": v, "value": Fraction(1, 2)} for v in range(4)],
        schema={"vertex": "int", "value": "number"},
    )
    candidates = mf.alg.graph.clique_candidates(edges, score=scores, top=10)
    print(candidates)
    print(candidates.relations["summary"])
    print(witnesses)
    assert candidates.relations["summary"].head(1).one().score == Fraction(3, 2)
    assert len(mf.alg.graph.maximal_cliques(edges).relations["summary"]) == 2
    # This is a numerical observation, not a generated cut or a validity proof.
    assert len(candidates.relations["summary"].where(col("score") > 1)) == 1


if __name__ == "__main__":
    main()
