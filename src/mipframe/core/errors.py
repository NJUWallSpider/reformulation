"""Shared failures; numerical layers do not depend on report implementations."""
class MIPFrameError(ValueError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.MIPFrameError")
    MIPFRAME HELP END
    """
    def __init__(self, *args):
        super().__init__(*args)
        from mipframe._help.diagnostics import annotate
        annotate(self)


class QueryMemoryError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.QueryMemoryError")
    MIPFRAME HELP END
    """
    pass


class MaterializationLimitError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.MaterializationLimitError")
    MIPFRAME HELP END
    """
    pass


class CardinalityError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.CardinalityError")
    MIPFRAME HELP END
    """
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


class ValidationError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.ValidationError")
    MIPFRAME HELP END
    """
    def __init__(self, report):
        super().__init__("Validation failed: " + str(report.summary))
        self.report = report


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


class SchemaError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.SchemaError")
    MIPFRAME HELP END
    """
    pass


class ExactArithmeticError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.ExactArithmeticError")
    MIPFRAME HELP END
    """
    pass
