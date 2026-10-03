"""Synchronous execution and versioned, explicitly bound operator recipes."""

from functools import wraps
from inspect import signature, Parameter
from mipframe.frame.base import Frame, merge_models
from mipframe.frame.schema import SchemaError, encode, decode
from mipframe.store.algorithm_store import AlgorithmStore

REGISTRY = {}


def operation(name, *, search=False):
    def decorate(fn):
        sig = signature(fn)

        @wraps(fn)
        def run(
            *args, memory_mb=128, max_output_rows=1_000_000, disk_mb=None, **kwargs
        ):
            bound = sig.bind(None, *args, **kwargs)
            bound.apply_defaults()
            params = {
                k: tuple(v) if isinstance(v, list) else v
                for k, v in bound.arguments.items()
                if k != "ctx"
            }
            # Scalar entity arguments are immutable identity constants; recipes
            # must not retain a mutable Python list or a second live load handle.
            from mipframe.entity import Entity
            from mipframe.frame.schema import Ref

            for key, value in tuple(params.items()):
                if isinstance(value, Entity):
                    params[key] = Ref(
                        value._owner.uid,
                        value._owner.version,
                        "row" if type(value).__name__ == "Row" else "var",
                        value.id,
                    )
            frames = []
            seen = set()

            def dependencies(value):
                if isinstance(value, Frame) and id(value) not in seen:
                    seen.add(id(value))
                    frames.append(value)
                    for domain in value.domains.values():
                        dependencies(domain)

            for value in params.values():
                dependencies(value)
            store = AlgorithmStore
            if search:
                from mipframe.store.search_store import SearchStore

                store = SearchStore
            ctx = store(
                merge_models(*frames),
                memory_mb=memory_mb,
                max_output_rows=max_output_rows,
                disk_mb=disk_mb,
            )
            ctx.operation = name
            try:
                if search:
                    ctx.configure(
                        candidate=name.endswith("_candidates"),
                        max_states=params["max_states"],
                        time_limit=params["time_limit"],
                    )
                result = fn(ctx, **params)
                ctx.disk_check()
                provenance = list(getattr(result, "_search_provenance", ()))
                for frame in frames:
                    for run_info in frame._search_runs():
                        if run_info not in provenance:
                            provenance.append(run_info)
                if provenance:
                    result._search_provenance = tuple(provenance)
                    for relation in result.relations.values():
                        relation._search_provenance = tuple(provenance)
                result._source_spec = {
                    "kind": "algorithm",
                    "algorithm": name,
                    "algorithm_version": 1,
                    "arguments": params,
                    "resources": dict(
                        memory_mb=memory_mb,
                        max_output_rows=max_output_rows,
                        disk_mb=disk_mb,
                    ),
                }
                columns = {}
                scope = {}
                for key, frame in params.items():
                    if not isinstance(frame, Frame):
                        continue
                    stages = [
                        (fields, table)
                        for (ident, fields), (table, _) in ctx.staged.items()
                        if ident == id(frame)
                    ]
                    columns[key] = (
                        tuple(dict.fromkeys(n for fields, _ in stages for n in fields))
                        or frame.key
                        or ()
                    )
                    scope[key] = (
                        ctx.execute(
                            f"SELECT coalesce(max(ordinal),0) FROM {stages[0][1]}"
                        ).fetchone()[0]
                        if stages
                        else frame._known_count
                    )
                result._algorithm_info = {
                    "status": (
                        (
                            ctx.search_info["mode"]
                            + ": "
                            + ctx.search_info["stop_reason"]
                        )
                        if search
                        else "complete"
                    ),
                    "path": ", ".join(sorted(ctx.paths)) or "exact SQLite spill",
                    "columns": columns,
                    "scope": scope,
                }
                # Related tables carry a recipe back to the same complete search call.
                if search:
                    for relation_name, relation in result.relations.items():
                        relation._source_spec = {
                            "kind": "algorithm_relation",
                            "source": result,
                            "name": relation_name,
                        }
                # Commit before publishing. Every public result is a complete snapshot.
                ctx.db.commit()
                if search:
                    ctx.searching = False
                return result
            except BaseException as exc:
                if search:
                    ctx.searching = False
                if getattr(exc, "report", None) is None:
                    ctx.close()
                else:
                    exc.report._recipe = dict(kind="algorithm_contract", algorithm=name,
                        arguments=params, resources=dict(memory_mb=memory_mb,
                        max_output_rows=max_output_rows,disk_mb=disk_mb))
                raise

        run.__signature__ = sig.replace(
            parameters=[p for n, p in sig.parameters.items() if n != "ctx"]
            + [
                Parameter(n, Parameter.KEYWORD_ONLY, default=v)
                for n, v in (
                    ("memory_mb", 128),
                    ("max_output_rows", 1_000_000),
                    ("disk_mb", None),
                )
            ]
        )
        REGISTRY[name] = run
        return run

    return decorate


def recipe_arguments(arguments, node):
    def item(value):
        if isinstance(value, Frame):
            return {"frame": node(value)}
        if isinstance(value, (tuple, list)):
            return {"tuple": [item(v) for v in value]}
        if hasattr(value, "_owner") and hasattr(value, "id"):
            from mipframe.frame.schema import Ref

            value = Ref(
                value._owner.uid,
                value._owner.version,
                "row" if type(value).__name__ == "Row" else "var",
                value.id,
            )
        return {"scalar": encode(value)}

    # Seconds are an execution resource, not an inexact mathematical constant.
    return {
        k: {"seconds": repr(v)} if k == "time_limit" and type(v) is float else item(v)
        for k, v in arguments.items()
    }


def restore(node, build):
    if node.get("algorithm_version") != 1:
        raise SchemaError("Unsupported algorithm version")
    from mipframe.ops import sets, graph, grouping, _matrix
    from mipframe.alg import graph as search_graph, integer

    fn = REGISTRY.get(node["algorithm"])
    if fn is None:
        raise SchemaError("Unknown algorithm")

    def value(v):
        if set(v) == {"frame"}:
            return build(v["frame"])
        if set(v) == {"tuple"}:
            return tuple(value(x) for x in v["tuple"])
        if set(v) == {"scalar"}:
            return decode(v["scalar"])
        raise SchemaError("Invalid algorithm argument")

    resources = node["resources"]
    if set(resources) != {"memory_mb", "max_output_rows", "disk_mb"}:
        raise SchemaError("Invalid algorithm resources")
    arguments = {}
    for key, encoded in node["arguments"].items():
        if set(encoded) == {"seconds"}:
            if key != "time_limit" or not isinstance(encoded["seconds"], str):
                raise SchemaError("Seconds encoding is only valid for a time limit")
            try:
                arguments[key] = float(encoded["seconds"])
            except ValueError as exc:
                raise SchemaError("Invalid time limit") from exc
        else:
            arguments[key] = value(encoded)
    return fn(**arguments, **resources)
