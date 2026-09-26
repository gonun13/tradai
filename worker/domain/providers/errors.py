from __future__ import annotations


class AdapterError(RuntimeError):
    """A classified provider failure safe for fallback and structured logging."""

    kind = "permanent"

    def __init__(self, detail: str, *, retry_after: float | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after = retry_after


class AdapterDisabledError(AdapterError):
    kind = "disabled"


class AdapterUnsupportedError(AdapterError):
    kind = "unsupported"


class AdapterIncompleteError(AdapterError):
    kind = "incomplete"


class AdapterQuotaDeferredError(AdapterError):
    kind = "quota-deferred"


class AdapterRateLimitError(AdapterError):
    kind = "rate-limited"


class AdapterTransientError(AdapterError):
    kind = "transient"


class AdapterPermanentError(AdapterError):
    kind = "permanent"


class SymbolNotFoundError(AdapterError):
    """Vendor has no quoteable series for this symbol."""

    kind = "not-found"

    def __init__(self, symbol: str, vendor: str, detail: str | None = None) -> None:
        self.symbol = symbol
        self.vendor = vendor
        msg = f"symbol not found: {symbol} ({vendor})"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)
