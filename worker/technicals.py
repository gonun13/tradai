from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class BarClose:
    bar_date: str
    close: float


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    chunk = values[-window:]
    return sum(chunk) / window


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _pct_change(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback:
        return None
    base = values[-(lookback + 1)]
    if base == 0:
        return None
    return ((values[-1] / base) - 1.0) * 100.0


def compute_features(bars: list[BarClose]) -> dict:
    """Lightweight technicals from OHLCV closes — no pandas-ta dependency."""
    closes = [b.close for b in bars if b.close is not None and b.close == b.close]
    now = datetime.now(timezone.utc).isoformat()
    if not closes:
        return {
            "as_of_bar": None,
            "computed_at": now,
            "bar_count": 0,
            "last_close": None,
            "sma_20": None,
            "sma_50": None,
            "rsi_14": None,
            "return_1m_pct": None,
            "return_3m_pct": None,
            "return_6m_pct": None,
            "vs_sma20_pct": None,
            "vs_sma50_pct": None,
        }

    last = closes[-1]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    features = {
        "as_of_bar": bars[-1].bar_date if bars else None,
        "computed_at": now,
        "bar_count": len(closes),
        "last_close": last,
        "sma_20": sma20,
        "sma_50": sma50,
        "rsi_14": _rsi(closes, 14),
        # ~21 trading days ≈ 1 month; ~63 ≈ 3 months; ~126 ≈ 6 months (0020)
        "return_1m_pct": _pct_change(closes, 21),
        "return_3m_pct": _pct_change(closes, 63),
        "return_6m_pct": _pct_change(closes, 126),
        "vs_sma20_pct": None if sma20 is None or sma20 == 0 else ((last / sma20) - 1.0) * 100.0,
        "vs_sma50_pct": None if sma50 is None or sma50 == 0 else ((last / sma50) - 1.0) * 100.0,
    }
    return features
