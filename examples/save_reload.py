"""Native persistence and exact LP export; generated files are temporary."""

from pathlib import Path
from tempfile import TemporaryDirectory
import mipframe as mf


def main():
    source = Path(__file__).parent / "data" / "tiny.mps"
    with TemporaryDirectory(prefix="mipframe-example-") as directory:
        directory = Path(directory)
        with mf.read(source) as model:
            saved = model.save(directory / "native_model")
            expected = (model.shape, model.nnz, model.objective.sense, model.objective.constant)
        with mf.load(saved) as restored:
            assert (restored.shape, restored.nnz, restored.objective.sense, restored.objective.constant) == expected
            print("Reloaded:", restored)
            print("Verification:", restored.verify()["status"])
            restored.write_lp(directory / "exported.lp")
        with mf.read(directory / "exported.lp") as reread:
            assert (reread.shape, reread.nnz, reread.objective.sense, reread.objective.constant) == expected
            print("LP round trip:", reread.verify()["status"])


if __name__ == "__main__":
    main()
