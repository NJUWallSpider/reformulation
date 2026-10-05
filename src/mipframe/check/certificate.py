"""Public certificate diagnostics; execution lives in the sparse exact kernel."""
from mipframe.store.numbers import MIPFrameError

class InvalidCertificateError(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.InvalidCertificateError")
    MIPFRAME HELP END
    """
    def __init__(self, message, violations=None):
        super().__init__(message)
        self.violations = violations


class UnsupportedEnumerationDomain(MIPFrameError):
    """
    MIPFRAME HELP BEGIN
    Diagnostic exception; inspect original message and any attached complete Report.

    Full contract: mf.help("api.UnsupportedEnumerationDomain")
    MIPFRAME HELP END
    """
    pass
