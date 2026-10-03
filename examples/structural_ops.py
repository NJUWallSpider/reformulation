"""Compose exact graph closure and union-resource queries without a detector."""

from fractions import Fraction
import mipframe as mf
from mipframe import col


def main():
    edges = mf.Frame.from_records(
        [dict(u=0, v=1), dict(u=1, v=2), dict(u=0, v=2), dict(u=3, v=2)],
        schema={"u": "int", "v": "int"},
    )
    closure = mf.ops.graph.reachability(edges, include_self=True)
    membership = closure.select(set=col("source"), member=col("target"))
    pairs = mf.Frame.from_records(
        [dict(left=0, right=3)], schema={"left": "int", "right": "int"}
    )
    weights = mf.Frame.from_records(
        [dict(member=i, resource="capacity", coef=Fraction(1, 3)) for i in range(4)],
        schema={"member": "int", "resource": "str", "coef": "number"},
    )
    usage = mf.ops.sets.pair_aggregate(
        membership, pairs, weights, by="resource", value="coef"
    )
    assert usage.one().value == Fraction(4, 3)
    print(usage)
    reduced = mf.ops.graph.transitive_reduction(edges)
    print(mf.ops.graph.path(reduced, source=0, target=2))
    print(usage.explain())


if __name__ == "__main__":
    main()
