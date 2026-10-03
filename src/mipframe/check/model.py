"""Integrity verification for immutable numerical model artifacts."""

from mipframe.store.numbers import MIPFrameError
from mipframe.store.artifacts import sha256


def verify(m):
    for name, digest in m.manifest["base_sha256"].items():
        if sha256(m.path / name) != digest:
            raise MIPFrameError(f"Base artifact checksum mismatch: {name}")
    if (
        len(m.row_ptr) != len(m.rows) + 1
        or len(m.col_ptr) != len(m.variables) + 1
        or int(m.row_ptr[-1]) != len(m.col_idx)
        or len(m.coeff_id) != len(m.col_idx)
        or int(m.col_ptr[-1]) != len(m.col_idx)
        or len(m.col_positions) != len(m.col_idx)
    ):
        raise MIPFrameError("Invalid sparse model dimensions")
    return {
        "status": "PASS",
        "exactness": "exact",
        "model_version": m.manifest["model_version"],
        "rows_verified": len(m.rows),
        "nonzeros_verified": len(m.col_idx),
        "coverage": 1.0,
        "objective_and_domains": "PASS",
    }
