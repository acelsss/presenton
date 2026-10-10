class OperationRejected(ValueError):
    """A public machine-readable rejection code, without input or internal paths."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
