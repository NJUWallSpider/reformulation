"""File input and portable native snapshots."""

from __future__ import annotations
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
import os
import shutil
import sqlite3

from mipframe.store.numbers import MIPFrameError
from mipframe.store.model_store import build_numerical


def read(
    path: str | os.PathLike,
    *,
    format: str | None = None,
    memory_mb: float = 128,
    **format_options,
) -> "Model":
    """
    Read a numerical model and prepare its IR and default indexed results.

    MIPFRAME HELP BEGIN
    Import an LP/MPS file into a new independent exact snapshot.

    Full contract: mf.help("api.read")
    MIPFRAME HELP END
    """
    from mipframe.model import Model

    source = Path(path)
    fmt = (
        format.lower()
        if isinstance(format, str)
        else source.suffix.lower().lstrip(".")
        if format is None
        else ""
    )
    if fmt not in ("lp", "mps"):
        raise MIPFrameError("Unsupported format; specify format='lp' or format='mps'")
    temp = TemporaryDirectory(prefix="mipframe-")
    store = None
    try:
        store = build_numerical(
            source, Path(temp.name) / "model", memory_mb, format=fmt, **format_options
        )
        # Ownership (including failure cleanup) transfers before eager IR work.
        owned_store, store = store, None
        return Model._from_store(owned_store, temp, memory_mb=memory_mb)
    except BaseException:
        if store is not None:
            store.close()
        temp.cleanup()
        raise


def load(directory: str | os.PathLike) -> "Model":
    """
    Load a native model, restoring indexed results or computing them if absent.

    MIPFRAME HELP BEGIN
    Load native v3 with retained uid/version and validated saved analyses.

    Full contract: mf.help("api.load")
    MIPFRAME HELP END
    """
    from mipframe.model import Model

    return Model(directory)


def save(model: "Model", directory: str | os.PathLike) -> Path:
    source = model.model.path
    target = Path(directory).absolute()
    owned_root = Path(model._temporary.name) if model._temporary is not None else source
    if target.resolve().is_relative_to(owned_root.resolve()):
        raise MIPFrameError(
            "Save outside the source model directory and its managed temporary workspace"
        )
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Refusing to overwrite {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".mipframe-save-", dir=target.parent) as staging:
        dest = Path(staging) / "model"
        dest.mkdir()
        artifacts = set(model.model.manifest["base_sha256"]) | {
            "manifest.json", "refinement.sqlite", "indexing.sqlite",
        }
        if "edit_artifact" in model.model.manifest:
            artifacts.add("lineage.sqlite")
        for path in source.iterdir():
            if path.name not in artifacts:
                continue
            if not path.is_file() or path.name.endswith(("-wal", "-shm", "-journal")):
                continue
            if path.suffix == ".sqlite" and path.name not in ("metadata.sqlite", "lineage.sqlite"):
                with closing(
                    sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                ) as src:
                    with closing(sqlite3.connect(dest / path.name)) as dst:
                        src.backup(dst)
            else:
                shutil.copy2(path, dest / path.name)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Refusing to overwrite {target}")
        dest.rename(target)
    return target


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mipframe.model import Model
