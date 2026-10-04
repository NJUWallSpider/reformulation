"""Offline documentation. Importing this module does not load its corpus."""


def help(topic=None, *, limit=3, detail="brief", format="text"):
    """
    Retrieve installed-version documentation without executing model queries.

        Accept a topic ID, English query, registered Python object, or exception.
        Text returns Formula without printing; JSON returns a detached dictionary.

    MIPFRAME HELP BEGIN
    Retrieve current offline documentation without executing model queries.

    Full contract: mf.help("api.help")
    MIPFRAME HELP END
    """
    from .query import query
    result = query(topic, limit=limit, detail=detail, format=format)
    if format == "json":
        return result
    from .render import render
    from mipframe.frame.row import Formula
    return Formula(render(result))
