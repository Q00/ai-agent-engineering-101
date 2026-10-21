"""Typed domain failure raised at the market boundary."""


class MarketError(RuntimeError):
    """A market rule rejected a tool call."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code: str = code
        self.detail: str = detail
