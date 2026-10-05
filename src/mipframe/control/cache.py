"""Writable derived-cache directories independent of read-only native archives."""
from pathlib import Path
from tempfile import TemporaryDirectory


def directory(owner):
    store=owner.model
    data=getattr(store,'data',None)
    if data is None or not getattr(data,'read_only_source',False):return store.path
    if getattr(data,'cache_directory',None) is None:
        from mipframe.store.native7 import copy_artifact
        import sqlite3
        from contextlib import closing
        data.cache_temporary=TemporaryDirectory(prefix='mipframe-derived-')
        target=Path(data.cache_temporary.name)
        try:
            for name in ('refinement.sqlite','indexing.sqlite'):
                source=data.path/name
                if source.exists():
                    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src:
                        with closing(sqlite3.connect(target/name)) as dst:src.backup(dst)
            source=data.path/'derived'
            if source.exists():
                (target/'derived').mkdir()
                for p in source.iterdir():
                    if p.is_file():copy_artifact(p,target/'derived'/p.name)
            data.cache_directory=target
        except BaseException:data.cache_temporary.cleanup();data.cache_temporary=None;raise
    return data.cache_directory
