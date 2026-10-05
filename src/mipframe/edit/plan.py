"""Public edit diagnostics and the source-relative delta editor."""
from mipframe.store.numbers import MIPFrameError
from mipframe.check.report import ValidationError

class EditConflictError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.EditConflictError")
    MIPFRAME HELP END
    """
    pass


class EditValidationError(ValidationError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.EditValidationError")
    MIPFRAME HELP END
    """
    pass


class NonIsolatedVariableError(EditValidationError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.NonIsolatedVariableError")
    MIPFRAME HELP END
    """
    pass


class UndischargedAssumptionError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.UndischargedAssumptionError")
    MIPFRAME HELP END
    """
    pass


from mipframe.edit.delta import DeltaEdit as Edit
