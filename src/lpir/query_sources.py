"""Resolve physical dependencies from relation nodes, never from literal data."""

from contextlib import closing
from dataclasses import dataclass
import json

from .numbers import LPIRError
from .sidecar import read_checkpoint
from .query_catalog import EXPLANATION_RELATIONS
from .indexed_store import RELATIONS as INDEXED_RELATIONS
from .structural_store import RELATIONS as STRUCTURE_RELATIONS


@dataclass(frozen=True)
class Sources:
    explanation: str | None = None
    indexed: dict | None = None
    structure: dict | None = None


def scans(node):
    """Walk relational operands only; expressions may contain arbitrary literals."""
    if not isinstance(node, dict):
        return
    if node.get("op") == "scan":
        yield node
    for key in ("input", "left", "right"):
        if key in node:
            yield from scans(node[key])


def selected_scope(nodes, relations):
    scopes = {n.get("analysis") for n in nodes if n.get("table") in relations}
    if len(scopes) > 1:
        raise LPIRError(
            "Materialize different analysis scopes separately before joining"
        )
    return next(iter(scopes), None)


def attach_sources(m, db, spec):
    nodes = list(scans(spec))
    tables = {n.get("table") for n in nodes}
    db.execute("ATTACH DATABASE ? AS numerical", (str(m.path / "metadata.sqlite"),))
    explanation = indexed = structure = None
    if tables.intersection(EXPLANATION_RELATIONS):
        from .compressed import open_db

        with closing(open_db(m)):
            pass
        path = m.path / "explanations.sqlite"
        db.execute(
            "ATTACH DATABASE ? AS explained", (f"file:{path.resolve()}?mode=ro",)
        )
        explanation = json.loads((m.path / "explanations.json").read_text())["sha256"]
    if tables.intersection(INDEXED_RELATIONS):
        from .indexed_store import ScopedModel, connect, filename

        scope = selected_scope(nodes, INDEXED_RELATIONS)
        model = ScopedModel(m, scope) if scope is not None else m
        with closing(connect(model, True)):
            pass
        path = m.path / filename(model, ".sqlite")
        db.execute("ATTACH DATABASE ? AS indexed", (f"file:{path.resolve()}?mode=ro",))
        indexed = read_checkpoint(db, "indexed")
    if tables.intersection(STRUCTURE_RELATIONS):
        from .structural_store import connect, filename

        scope = selected_scope(nodes, STRUCTURE_RELATIONS)
        with closing(connect(m, scope)):
            pass
        path = m.path / filename(scope)
        db.execute(
            "ATTACH DATABASE ? AS structured", (f"file:{path.resolve()}?mode=ro",)
        )
        structure = read_checkpoint(db, "structured")
    return Sources(explanation, indexed, structure)
