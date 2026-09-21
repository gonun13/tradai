from __future__ import annotations


class SymbolNotFoundError(LookupError):
    """Vendor has no quoteable series for this symbol (typo, wrong venue suffix, delisted)."""

    def __init__(self, symbol: str, vendor: str, detail: str | None = None) -> None:
        self.symbol = symbol
        self.vendor = vendor
        self.detail = detail
        msg = f"symbol not found: {symbol} ({vendor})"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)
