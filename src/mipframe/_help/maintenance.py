"""Standard-library-only release integrity checks, also usable in isolated builds."""
import ast
import hashlib
import json
from pathlib import Path


def _ast_value(node):
    # ast.dump changed its empty-field representation in Python 3.13. The
    # fingerprint must be portable across all supported build interpreters.
    if isinstance(node, ast.AST):
        return [type(node).__name__, {name: _ast_value(value)
                for name, value in ast.iter_fields(node) if value is not None and value != []}]
    if isinstance(node, list):
        return [_ast_value(value) for value in node]
    if isinstance(node, (bytes, complex)) or node is Ellipsis:
        return [type(node).__name__, repr(node)]
    return node


def source_hash(package):
    parts = []
    class Strip(ast.NodeTransformer):
        def visit_Constant(self, node):
            return node
        def strip(self, node):
            self.generic_visit(node)
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                node.body.pop(0)
            return node
        visit_Module = strip
        visit_ClassDef = strip
        visit_FunctionDef = strip
        visit_AsyncFunctionDef = strip
    for path in sorted(package.rglob('*.py')):
        tree = Strip().visit(ast.parse(path.read_text(encoding='utf8')))
        parts.append((path.relative_to(package).as_posix(), _ast_value(tree)))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def content_hash(package):
    parts = []
    for name in ('content.py','recipes.py','skill.md','search.py','diagnostics.py'):
        p = package / '_help' / name
        parts.append((name,p.read_text(encoding='utf8')))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def docstring_hash(package):
    """Include generated source documentation in release integrity validation."""
    parts = []
    for path in sorted(package.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf8'))
        docs = [ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        parts.append((path.relative_to(package).as_posix(), docs))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def validate(package):
    package = Path(package)
    data = json.loads((package / '_help/catalog.json').read_text(encoding='utf8'))
    source = ast.parse((package / '__init__.py').read_text(encoding='utf8'))
    version = next(n.value.value for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in n.targets))
    if data['version'] != version or data['source_hash'] != source_hash(package) or data['content_hash'] != content_hash(package):
        raise RuntimeError('Documentation mismatch: run python scripts/build_help.py and commit generated resources')
    if data.get('docstring_hash') != docstring_hash(package):
        raise RuntimeError('Generated docstring mismatch: run python scripts/build_help.py')
    payload = {k:v for k,v in data.items() if k!='checksum'}
    if hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()!=data['checksum']:
        raise RuntimeError('Help resource checksum mismatch')
    return data
