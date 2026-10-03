"""Run exact Edit acceptance on supplied LP/MPS models (including test10).

Usage: python examples/edit_real_models.py data/assign1-5-8.lp data/test10.mps
Each model prepares indexed results for three independent snapshots.
"""

import argparse
from math import inf
from pathlib import Path
from tempfile import TemporaryDirectory

import mipframe as mf


def exercise(path, *, native=None):
    print(f"Reading {path}", flush=True)
    with mf.load(native) if native is not None else mf.read(path) as source:
        row = next(r for r in source.rows if r.lb != -inf or r.ub != inf)
        side = "ub" if row.ub != inf else "lb"
        claim = getattr(row, side + "_claim")
        proof = source.check.linear(claim, using=[claim]).require()
        edit = source.edit()
        draft = edit.add_row(claim, because=proof)
        print("Building verified addition", flush=True)
        with edit.build() as candidate:
            assert candidate.diff(source).semantic["lp"] == "SAME"
            added = candidate.lineage.resolve(draft)
            retained = candidate.rows.by_name(row.name)
            proof = candidate.check.linear(
                getattr(added, side + "_claim"),
                using=[getattr(retained, side + "_claim")],
            ).require()
            second = candidate.edit()
            second.drop_rows(added, because=proof)
            print("Building verified deletion", flush=True)
            with second.build() as restored:
                assert restored.version == source.version
                assert restored.diff(candidate).semantic["mip"] == "SAME"
                indexed = restored.structure.indexed()
                indexed.verify().require()
                if path.name == "test10.mps":
                    found = False
                    for record in indexed.tensors:
                        tensor = indexed.tensor(record.id)
                        if tuple(tensor.shape) in ((950, 200), (200, 950)):
                            assert len(tensor.vars) == 190000
                            assert tensor.missing_cells == 0
                            tensor.coordinates.check("product", ("i", "j")).require()
                            found = True
                    assert found, "Expected the test10 950 × 200 coordinate view"
                with TemporaryDirectory(prefix="mipframe-edit-example-") as tmp:
                    out = Path(tmp)
                    restored.save(out / "native")
                    with mf.load(out / "native") as loaded:
                        assert loaded.diff(candidate).semantic["mip"] == "SAME"
                    # The assign LP fixture is exactly representable as finite decimals.
                    if path.suffix.lower() == ".lp":
                        restored.write_lp(out / "restored.lp")
                        mf.audit.roundtrip(restored, out / "restored.lp").require()
    print(f"Passed: {path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", type=Path, nargs="+")
    for model_path in parser.parse_args().models:
        exercise(model_path)
