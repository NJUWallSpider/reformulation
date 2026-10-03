"""Non-executing inspection of relational plans and their dependencies."""

from mipframe.frame.row import Formula


def explain(frame):
    frame._guard()
    count = frame._known_count
    lines = [
        f"Frame · key={frame.key} · rows={count if count is not None else 'unknown'}"
    ]
    lines.extend(frame._search_display())
    lines.append("schema: " + ", ".join(f"{n}: {t}" for n, t in frame.schema.items()))
    lines.append("models: " + ", ".join(sorted(frame.models)))
    blocking = {"join", "group", "sort", "distinct", "slice", "sample"}

    def visit(f, depth):
        source = getattr(f, "_source_spec", {})
        operator = source.get("kind", "source") if f._op == "source" else f._op
        flags = []
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
            flags.append("blocking, SQLite spill")
        if f._op == "join" or f._params.get("barrier"):
            flags.append("full validation barrier")
        if source.get("kind") == "terms":
            flags.append("CSR, coefficient column decoded on demand")
        if source.get("kind") == "project":
            flags.append("CSR/CSC integer traversal")
        dependencies = set()
        for expr in f._params.get("predicates", ()):
            dependencies.update(expr.dependencies)
        for expr in f._params.get("expressions", {}).values():
            dependencies.update(expr.dependencies)
        if dependencies:
            flags.append("depends on " + ",".join(sorted(dependencies)))
        lines.append(
            "  " * depth + operator + (" · " + " · ".join(flags) if flags else "")
        )
        for parent in f._parents:
            visit(parent, depth + 1)

    visit(frame, 0)
    return Formula("\n".join(lines))
