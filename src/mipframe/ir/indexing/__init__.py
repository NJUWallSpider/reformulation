"""Structural-signature assisted indexed discovery and cached results."""

from mipframe.ir.indexing.views import IndexedResult

__all__ = ["IndexedResult", "indexed", "restore"]
from mipframe.ir.indexing.store import Store
from mipframe.ir.indexing.store import ident
from mipframe.ir.indexing.store import js
from mipframe.ir.indexing.store import restore
from mipframe.frame.entity import RowFrame
from mipframe.frame.entity import VarFrame
from mipframe.core.resources import memory_bytes


def indexed(owner, *, rows=None, vars=None, signatures=None, memory_mb=128,disk_mb=None):
    owner.model
    memory_bytes(memory_mb)
    for value, cls, name in ((rows, RowFrame, "rows"), (vars, VarFrame, "vars")):
        if value is not None and (
            not isinstance(value, cls) or value._owner is not owner
        ):
            raise ValueError(f"{name} must be a same-model {cls.__name__}")
    from mipframe.ir.signatures import refinement
    sig = refinement(owner, memory_mb=memory_mb) if signatures is None else signatures
    from mipframe.ir.signatures import _Refinement

    if not isinstance(sig, _Refinement) or sig._store.owner is not owner:
        raise ValueError("signatures must belong to this model")
    if rows is None and vars is None:
        for result in getattr(owner, "_indexed_results", {}).values():
            record = result._record()
            if record["scope"]["full_model"] and record["signatures"] == {
                "config": sig.config,
                "depth": sig.depth,
            }:
                return result
    rows = owner.rows if rows is None else rows
    variables = owner.vars if vars is None else vars
    # Work in a private copy. Published stores and their live queries remain unchanged on failure.
    s = Store(owner, memory_mb,disk_mb)
    try:
        rg = s.domain("row", rows.ids)
        vg = s.domain("var", variables.ids)
        scope = dict(
            rows_domain=rg,
            vars_domain=vg,
            rows=s.size(rg),
            variables=s.size(vg),
            full_model=s.size(rg) == owner.shape[0] and s.size(vg) == owner.shape[1],
        )
        key = ident(
            "run_",
            [owner.model.manifest["model_version"], rg, vg, sig.config, sig.depth],
        )
        record = s.runs.get(key)
        if record is not None:
            if record["scope"] != scope or record["signatures"] != {
                "config": sig.config,
                "depth": sig.depth,
            }:
                from mipframe.store.numbers import MIPFrameError

                raise MIPFrameError(
                    "Indexed run identifier collision; no result was published"
                )
            s.close()
            return owner._indexed_results[key]
        from mipframe.ir.indexing.discovery import prepare
        from mipframe.ir.indexing.discovery import discover
        from mipframe.ir.indexing.operators import recover

        _, _, edges = prepare(s, key, s.domains[rg], s.domains[vg], sig)
        discover(s, key, edges)
        recover(s, key, edges)
        record = dict(
            id=key,
            status="complete",
            scope=scope,
            signatures=dict(config=sig.config, depth=sig.depth),
            search="homogeneous type/unit seeds + all available signature levels + role direction groups + bipartite components",
        )
        s.runs[key] = record
        s.publish()
        # Previous result objects keep their own valid stores; new registry uses the latest snapshot.
        owner._indexing_store = s
        owner._indexed_results = {
            r: IndexedResult(s, r)
            for r in sorted(s.runs)
        }
        return owner._indexed_results[key]
    except BaseException:
        s.close()
        raise
