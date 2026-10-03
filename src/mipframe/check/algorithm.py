"""Failures never carry a partially successful algorithm result."""

from mipframe.store.numbers import MIPFrameError
from mipframe.check.report import ValidationError


class QueryTooLarge(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    QueryTooLarge [api.QueryTooLarge]

    Diagnostic exception; inspect original message and any attached complete Report.

    QueryTooLarge [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.resources
    MIPFRAME HELP END
    """
    def __init__(self, limit, count):
        self.limit, self.count = limit, count
        super().__init__(
            f"Result exceeds max_output_rows={limit}; at least {count} rows"
        )


class DiskBudgetExceeded(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    DiskBudgetExceeded [api.DiskBudgetExceeded]

    Diagnostic exception; inspect original message and any attached complete Report.

    DiskBudgetExceeded [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.resources
    MIPFRAME HELP END
    """
    pass


class NotDAGError(ValidationError):
    """
    MIPFRAME HELP BEGIN
    NotDAGError [api.NotDAGError]

    Directed graph is not a DAG; inspect the attached cycle evidence.

    NotDAGError [type]

    When to use: Directed graph is not a DAG; inspect the attached cycle evidence.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.ops.graph.topological_order
    MIPFRAME HELP END
    """
    pass


class GraphCycleError(NotDAGError):
    """
    MIPFRAME HELP BEGIN
    GraphCycleError [api.GraphCycleError]

    Diagnostic exception; inspect original message and any attached complete Report.

    GraphCycleError [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: api.ops.graph.topological_order
    MIPFRAME HELP END
    """
    pass


class SearchBudgetExceeded(MIPFrameError):
    """
    Search did not finish; no partially successful Frame is attached.

    MIPFRAME HELP BEGIN
    SearchBudgetExceeded [api.SearchBudgetExceeded]

    Diagnostic exception; inspect original message and any attached complete Report.

    SearchBudgetExceeded [type]

    When to use: Diagnostic exception; inspect original message and any attached complete Report.

    Returns: Exception type, not a mathematical result.

    Does not establish: No model change or additional mathematical conclusion is implied.

    See also: concept.search
    MIPFRAME HELP END
    """

    def __init__(self, algorithm, reason, states, max_states, time_limit, scope):
        self.algorithm, self.reason, self.states = algorithm, reason, states
        self.max_states, self.time_limit, self.scope = max_states, time_limit, scope
        super().__init__(
            f"{algorithm}: search budget exceeded ({reason}); {states} states; scope={scope}"
        )
