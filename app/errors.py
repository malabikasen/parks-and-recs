class DomainError(Exception):
    """A business-rule violation, shown to the user as an inline message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
