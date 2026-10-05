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


from mipframe.data.runtime import resource_scope


@resource_scope
def read(
    path: str | os.PathLike,
    *,
    format: str | None = None,
    memory_mb: float = 128,
    disk_mb: float | None = None,
    **format_options,
) -> "Model":
    """
    Read a numerical model and cache its cheap numerical scan.

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
            source, Path(temp.name) / "model", memory_mb, format=fmt,disk_mb=disk_mb, **format_options
        )
        # Ownership (including failure cleanup) transfers before eager IR work.
        owned_store, store = store, None
        return Model._from_store(owned_store, temp, memory_mb=memory_mb)
    except BaseException:
        if store is not None:
            store.close()
        temp.cleanup()
        raise


@resource_scope
def load(directory: str | os.PathLike, *, memory_mb=128,disk_mb=None) -> "Model":
    """
    Load validated numerical content and cache its scan; analyses restore lazily.

    MIPFRAME HELP BEGIN
    Load validated native v7 numerical content and a cheap scan; saved analyses restore on explicit access.

    Full contract: mf.help("api.load")
    MIPFRAME HELP END
    """
    from mipframe.model import Model

    return Model(directory,memory_mb=memory_mb,disk_mb=disk_mb)


@resource_scope
def save(model: "Model", directory: str | os.PathLike, *, memory_mb=128,disk_mb=None) -> Path:
    from mipframe.store.native7 import save as save_native
    return save_native(model, directory)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mipframe.model import Model
