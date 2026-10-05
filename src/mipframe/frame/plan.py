"""Non-executing inspection of relational plans and their dependencies."""

from mipframe.frame.row import Formula
from mipframe.frame.query import inputs, expression_nodes
from mipframe.frame.expr import QueryExpr


def explain(frame):
    frame._guard()
    count = frame._known_count
    lines = [
        f"Frame · key={frame.key} · rows={count if count is not None else 'unknown'}"
    ]
    lines.extend(frame._search_display())
    stats=getattr(frame,'_last_execution_stats',getattr(frame,'_algorithm_info',{}).get('resources'))
    if stats:lines.append("last execution: "+", ".join(f"{k}={v}" for k,v in stats.items()))
    lines.append("schema: " + ", ".join(f"{n}: {t}" for n, t in frame.schema.items()))
    lines.append("models: " + ", ".join(sorted(frame.models)))
    blocking = {"join", "group", "sort", "distinct", "slice", "sample"}

    seen = {}

    def visit(f, depth):
        if id(f) in seen:
            lines.append("  " * depth + f"ref #{seen[id(f)]}")
            return
        number = len(seen)
        seen[id(f)] = number
        source = f._query.spec
        operator = source.get("kind", "source") if f._op == "source" else f._op
        flags = []
        from mipframe.frame.planner import backend
        chosen, reason = backend(f, tuple(f.schema))
        flags.append(chosen + ': ' + reason)
        if f._op=='source':
            flags.append('source storage: typed columns' if hasattr(f,'_typed_relation') or hasattr(f,'_array_columns') else 'source storage: streaming adapter')
            if hasattr(f,'_index'):flags.append('selection storage: '+type(f._index).__name__)
        flags.append('spill backend: mmap')
        if source.get("kind") == "algorithm":
            info = getattr(f, "_algorithm_info", {})
            operator = source["algorithm"]
            flags.extend(
                (info.get("status", "complete"), info.get("path", "materialized"))
            )
            flags.extend(
                f"{name}[{','.join(columns)}]: {info.get('scope', {}).get(name, 'unknown')} input rows"
                for name, columns in info.get("columns", {}).items()
            )
        if f._op in blocking:
            flags.append("blocking, " + chosen)
        if f._op == "join" or f._params.get("barrier"):
            flags.append("full validation barrier")
        if source.get("kind") == "terms":
            flags.append("CSR, coefficient column decoded on demand")
        if source.get("kind") == "project":
            flags.append("CSR/CSC integer traversal")
        dependencies = set()
        for expr in expression_nodes(f._params):
            if isinstance(expr, QueryExpr):
                dependencies.update(expr.dependencies)
        if dependencies:
            flags.append("depends on " + ",".join(sorted(dependencies)))
        lines.append(
            "  " * depth + f"#{number} " + operator + (" · " + " · ".join(flags) if flags else "")
        )
        for parent in inputs(f):
            visit(parent, depth + 1)

    visit(frame, 0)
    return Formula("\n".join(lines))
