"""Shared artifact serialization and checksums."""

from pathlib import Path
import hashlib
import json
import os
import tempfile


def sha256(path,*,work=None):
    from mipframe.data.runtime import WorkContext
    h = hashlib.sha256()
    with (work.fork() if work is not None else WorkContext()) as scratch:
        count=max(1,min(1024*1024,scratch.memory//16,scratch.available//2))
        with scratch.reserve(count+64):
            buffer=bytearray(count);view=memoryview(buffer)
            with open(path, "rb") as source:
                from mipframe.check.budget import current_budget
                budget=current_budget()
                while n:=source.readinto(buffer):
                    if budget is not None:budget.poll()
                    h.update(view[:n])
    return h.hexdigest()


def dump(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def atomic_write(path, text):
    """Publish a complete UTF-8 snapshot, leaving the old file intact on failure."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as target:
            temporary = Path(target.name)
            target.write(text)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
