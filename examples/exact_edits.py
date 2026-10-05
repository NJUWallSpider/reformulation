"""Verified add → new snapshot → verified drop, with exact native and LP audits."""

from pathlib import Path
from tempfile import TemporaryDirectory
import mipframe as mf


def main():
    with mf.read(Path(__file__).parent / "data" / "tiny.lp") as source:
        row = source.rows.iloc[0]
        claim = row.lb_claim
        proof = source.check.linear(claim, using=[claim])
        edit = source.edit(memory_mb=0.015625)
        new_row = edit.add_row(claim, name="verified_copy", because=proof)
        print(edit.summary())
        candidate = edit.build()
        try:
            diff = candidate.diff(source)
            assert diff.semantic["lp"] == diff.semantic["mip"] == "SAME"
            copy = candidate.lineage.resolve(new_row)
            retained = candidate.rows.by_name(row.name)
            proof = candidate.check.linear(copy.lb_claim, using=[retained.lb_claim])
            second = candidate.edit()
            second.drop_rows(copy, because=proof)
            restored = second.build()
            try:
                assert restored.shape == source.shape and restored.nnz == source.nnz
                with TemporaryDirectory() as tmp:
                    restored.save(Path(tmp) / "native")
                    lp = Path(tmp) / "restored.lp"
                    restored.write(lp)
                    mf.audit.roundtrip(restored, lp).require()
                    with mf.load(Path(tmp) / "native") as loaded:
                        assert loaded.diff(source).semantic["mip"] == "UNKNOWN"
                        assert loaded.audit(source=source)["status"] == "PROVED"
                print("Verified add/drop, native reload and exact LP audit passed.")
            finally:
                restored.close()
        finally:
            candidate.close()


if __name__ == "__main__":
    main()
