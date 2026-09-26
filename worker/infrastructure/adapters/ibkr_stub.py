from __future__ import annotations

"""Optional IBKR quotes adapter — stub for Stage 3 (enable later with gateway session)."""


class IbkrHistoricalAdapter:
    name = "ibkr"
    regions = {"us", "eu"}

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    def enabled(self) -> bool:
        return self._enabled

    def get_quote(self, symbol: str, currency: str):  # noqa: ANN201
        raise RuntimeError("IBKR adapter stub — set IBKR_ENABLED=1 and wire gateway in a later stage")
