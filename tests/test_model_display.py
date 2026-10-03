"""Model representations for Python and notebooks, without analysis effects."""

from pathlib import Path

import pytest
import mipframe as mf

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "data"


@pytest.mark.parametrize("suffix", ["lp", "mps"])
def test_display_format_and_no_print_side_effects(suffix, capsys):
    with mf.read(EXAMPLES / f"tiny.{suffix}") as model:
        expected = (
            'MIPModel("tiny")\n'
            "  sense        : min\n"
            "  variables    : 2\n"
            "  constraints  : 2\n"
            "  nonzeros     : 4\n"
            "  density      : 1\n"
            "  var types    : B 1 | C 1\n"
            "  objective nz : 2"
        )
        assert repr(model) == str(model) == expected
        assert capsys.readouterr().out == ""
        print(model)
        assert capsys.readouterr().out == expected + "\n"
        ipython = pytest.importorskip("IPython.lib.pretty")
        assert ipython.pretty(model) == expected
    assert repr(model) == "MIPModel(closed)"


def test_exact_objective_nonzeros_and_cached_display(tmp_path):
    path = tmp_path / "mixed.lp"
    path.write_text(
        "Maximize\n o: x - x + 1e-1000 y\nSubject To\n"
        " empty: x - x = 0\n unit: y <= 1\nBounds\n z <= 2\n"
        "Binaries\n y\nGenerals\n x\nEnd\n"
    )
    with mf.read(path) as model:
        text = repr(model)
        assert "sense        : max" in text
        assert "nonzeros     : 1" in text
        assert "objective nz : 1" in text
        assert "density      : 0.167" in text
        assert "var types    : B 1 | I 1 | C 1" in text
        queries = []
        model.model.db.set_trace_callback(queries.append)
        assert repr(model) == text
        assert queries == []


def test_sparse_density_and_thousands_separators(tmp_path):
    path = tmp_path / "sparse.lp"
    path.write_text(
        "Minimize\n o: x0\nSubject To\n r0: x0 <= 1\n"
        + "".join(f" r{i}: 0 = 0\n" for i in range(1, 1001))
        + "Bounds\n"
        + "".join(f" x{i} <= 1\n" for i in range(1, 1001))
        + "End\n"
    )
    with mf.read(path) as model:
        text = repr(model)
        assert "variables    : 1,001" in text
        assert "constraints  : 1,001" in text
        assert "var types    : C 1,001" in text
        assert "density      : 9.98e-7" in text
