"""Failures never carry a partially successful algorithm result."""

from mipframe.store.numbers import MIPFrameError
from mipframe.check.report import ValidationError


class QueryTooLarge(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.QueryTooLarge")
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
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.DiskBudgetExceeded")
    MIPFRAME HELP END
    """
    pass


class NotDAGError(ValidationError):
    """
    MIPFRAME HELP BEGIN
    Directed graph is not a DAG; inspect the attached cycle evidence.

    Full contract: mf.help("api.NotDAGError")
    MIPFRAME HELP END
    """
    pass


class GraphCycleError(NotDAGError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.GraphCycleError")
    MIPFRAME HELP END
    """
    pass


class SearchBudgetExceeded(MIPFrameError):
    """
    Search did not finish; no partially successful Frame is attached.

    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.SearchBudgetExceeded")
    MIPFRAME HELP END
    """

    def __init__(self, algorithm, reason, states, max_states, time_limit, scope):
        self.algorithm, self.reason, self.states = algorithm, reason, states
        self.max_states, self.time_limit, self.scope = max_states, time_limit, scope
        super().__init__(
            f"{algorithm}: search budget exceeded ({reason}); {states} states; scope={scope}"
        )
