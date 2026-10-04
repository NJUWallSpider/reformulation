"""Public-API acceptance checks on local test9/10/11 models.

Run: python scripts/evaluate_ops.py test10
This script does not edit models or publish derived mathematical claims.
"""

from pathlib import Path
import argparse
import time
from fractions import Fraction
import mipframe as mf
from mipframe import col


def test9(m):
    for rows in (m.rows, m.rows.iloc[::2]):
        terms = m.terms(rows=rows)
        got = mf.ops.sets.components(terms, set="row", member="var", memory_mb=0.015625)
        # Independent set-merging oracle on this modest fixture.
        groups = {i: {i} for i in m.vars.ids}
        for row in rows:
            members = list(row.vars.ids)
            if not members:
                continue
            merged = set().union(*(groups[v] for v in members))
            for v in merged:
                groups[v] = merged
        expected = {v: min(group) for v, group in groups.items()}
        assert {r.var.id: r.component.id for r in got} == expected
        print(
            "test9",
            len(rows),
            "rows:",
            got.column("component").nunique(),
            "components; oracle PASS",
            flush=True,
        )


def test10(m):
    f0 = m.family("F0@2").rows
    f1 = m.family("F1@2").rows
    f2 = m.family("F2@2").rows
    v = m.family("V2@1").vars
    support = m.terms(rows=f0 | f1, vars=v)
    pairs = mf.ops.sets.pairs(
        support, set="row", member="var", left=f0, right=f1, relation="equal"
    )
    assert len(pairs) == 950
    assert pairs.column("left").nunique() == pairs.column("right").nunique() == 950
    print("test10: 950 exact cross-family support pairs", flush=True)
    weighted = mf.ops.sets.equal_groups(support, set="row", member="var", value="coef")
    lookup = {r.row.id: r["class"].id for r in weighted}
    assert all(lookup[p.left.id] != lookup[p.right.id] for p in pairs)
    projection = v.project(i=f0, j=f2)
    projection.check("product", ("i", "j")).require()
    assert len(projection) == projection.nunique(("i", "j")) == 190000
    indexed = m.indexed()
    tensor = next(
        indexed.tensor(r.id)
        for r in indexed.tensors
        if list(indexed.tensor(r.id).related("dimensions").where(component="shape").column("size")) == [950, 200]
        and len(indexed.tensor(r.id).vars - v) == 0
    )
    tensor.related("coordinates").check("product", ("i", "j")).require()
    operators = [
        indexed.operator(r.id)
        for r in indexed.operators
        if indexed.operator(r.id).one().tensor == tensor.one().id
    ]
    assert operators
    for operator in operators:
        indexed.verify(operator).require()
    print(
        "test10: coefficients differ exactly; 190000 unique coordinates; indexed operators PASS",
        flush=True,
    )


def test11(m):
    terms = m.terms()
    groups = mf.ops.equal_groups(terms, by="row", rows=("var", "coef"))
    sets = mf.ops.sets.equal_groups(terms, set="row", member="var", value="coef")
    assert len(groups) == len(sets) == len(m.rows)
    agreement = groups.select("row", a=col("class")).join(
        sets.select("row", b=col("class")), on="row", cardinality="1:1", require="both"
    )
    assert len(agreement.where(col("a") != col("b"))) == 0
    # Directly compare complete exact vectors for deterministic sampled rows.
    for record in groups.sample(min(20, len(groups)), random_state=91):
        a = record.row.terms.select("var", "coef").to_records()
        b = record["class"].terms.select("var", "coef").to_records()
        assert a == b
    print(
        "test11:",
        len(groups),
        "rows;",
        groups.column("class").nunique(),
        "exact classes; cross-check PASS",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("test9", "test10", "test11"))
    args = parser.parse_args()
    start = time.monotonic()
    source = Path(__file__).resolve().parents[1] / "data" / f"{args.case}.mps"
    print("Reading", source, flush=True)
    with mf.read(source) as m:
        print(
            "Loaded", m.shape, "in", round(time.monotonic() - start, 1), "s", flush=True
        )
        {"test9": test9, "test10": test10, "test11": test11}[args.case](m)
    print("PASS", args.case, round(time.monotonic() - start, 1), "s", flush=True)


if __name__ == "__main__":
    main()
