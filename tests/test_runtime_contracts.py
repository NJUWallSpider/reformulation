"""Atomic publication and package dependency boundaries."""

import ast
import json
from pathlib import Path

import pytest
from mipframe.store import artifacts as artifact_io


def test_atomic_snapshot_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    artifact_io.dump(path, {"revision": 1})

    def fail_replace(*args):
        raise OSError("injected replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(artifact_io.os, "replace", fail_replace)
        with pytest.raises(OSError):
            artifact_io.dump(path, {"revision": 2})
    assert json.loads(path.read_text()) == {"revision": 1}
    assert list(tmp_path.iterdir()) == [path]
    artifact_io.dump(path, {"revision": 2})
    assert json.loads(path.read_text()) == {"revision": 2}


def test_storage_and_ir_do_not_import_public_facade():
    from importlib.util import resolve_name

    root = Path(__file__).resolve().parents[1] / "src" / "mipframe"

    class RuntimeImports(ast.NodeVisitor):
        def visit_If(self, node):
            # Type annotations may name the facade without importing it at runtime.
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                for child in node.orelse:
                    self.visit(child)
            else:
                self.generic_visit(node)

        def check(self, module, node):
            assert module not in {"mipframe", "mipframe.model", "mipframe.io"}, (
                f"{self.path.relative_to(root)}:{node.lineno} imports public facade {module}"
            )
            if self.path == root / "store" / "model_store.py":
                assert not module.startswith("mipframe.ir"), f"{self.path.name}:{node.lineno} imports analysis module {module}"

        def visit_Import(self, node):
            for alias in node.names:
                self.check(alias.name, node)

        def visit_ImportFrom(self, node):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, self.package)
            self.check(module, node)
            for alias in node.names:
                self.check(f"{module}.{alias.name}", node)

    for package in ("store", "ir"):
        paths = sorted((root / package).rglob("*.py"))
        assert paths, package
        for path in paths:
            visitor = RuntimeImports()
            visitor.path = path
            parts = path.relative_to(root).with_suffix("").parts
            visitor.package = ".".join(("mipframe", *parts[:-1]))
            visitor.visit(ast.parse(path.read_text()))
