"""
Layer cards (0027): every ingested layer condensed, deterministically, into the few
numbers and labels a model actually reasons from.

Pure functions — no database, no network. `advisory.py` loads the inputs and attaches
the cards to each subject; Claude, every Jev lens, and the materiality check read them.
Rounded numbers, whitelisted keys, dates without times: the same inputs always produce
the same card, which is what lets `materiality.py` compare one run with the last.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

# Bumped whenever lens names, card shapes, or horizons change in a way that makes an
# older decision incomparable. A prior decision on another schema is always re-decided.
LENS_SCHEMA = "layers-v1"

NEWS_MAX = 4
NEWS_SNIPPET_MAX = 160
THESIS_MAX = 400
ETF_HOLDINGS_MAX = 5
ETF_SECTORS_MAX = 3

EQUITY_FUNDAMENTALS = (
    "pe_ratio", "price_to_book",
    "profit_margin", "operating_margin", "roe", "roa",
    "revenue_growth", "earnings_growth",
    "debt_to_equity", "current_ratio", "quick_ratio",
)
IDENTITY_FIELDS = ("sector", "industry", "category", "country")


def num(value: Any, digits: int | None = None) -> float | None:
    """Round for the prompt: 1 dp for percentages, 3 significant-ish for small ratios."""
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if digits is None:
        digits = 1 if abs(x) >= 10 else (2 if abs(x) >= 1 else 3)
    out = round(x, digits)
    return 0.0 if out == 0 else out


def day(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _raw(value: Any) -> Any:
    return value.get("raw") if isinstance(value, dict) and "raw" in value else value


def _clip(text: Any, limit: int) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- historical ---------------------------------------------------------------------


def _parse_day(value: str) -> date | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _series(points: Iterable[tuple[Any, Any]]) -> list[tuple[date, float]]:
    out: dict[date, float] = {}
    for d, close in points:
        dd = _parse_day(d) if not isinstance(d, date) else d
        try:
            c = float(close)
        except (TypeError, ValueError):
            continue
        if dd is None or not math.isfinite(c) or c <= 0:
            continue
        out[dd] = c
    return sorted(out.items())


def _value_at(series: list[tuple[date, float]], target: date, tolerance_days: int = 10) -> float | None:
    """Last close on or before `target`; None when history does not reach back that far."""
    if not series or series[0][0] > target + timedelta(days=tolerance_days):
        return None
    found = None
    for d, c in series:
        if d > target:
            break
        found = c
    return found if found is not None else series[0][1]


def _total_return(series: list[tuple[date, float]], years: float) -> float | None:
    if not series:
        return None
    end_d, end = series[-1]
    start = _value_at(series, end_d - timedelta(days=round(365.25 * years)))
    if start is None or start <= 0:
        return None
    return end / start - 1.0


def _cagr_pct(series: list[tuple[date, float]], years: float) -> float | None:
    total = _total_return(series, years)
    if total is None or total <= -1:
        return None
    return ((1.0 + total) ** (1.0 / years) - 1.0) * 100.0


def _max_drawdown_pct(series: list[tuple[date, float]], since: date) -> float | None:
    window = [c for d, c in series if d >= since]
    if len(window) < 2:
        return None
    peak = window[0]
    worst = 0.0
    for c in window:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1.0)
    return worst * 100.0


def historical_card(
    points: Iterable[tuple[Any, Any]],
    *,
    benchmark: Iterable[tuple[Any, Any]] | None = None,
    benchmark_symbol: str | None = None,
) -> dict[str, Any] | None:
    """
    Long-run record from the 0026 series (daily for the last year, weekly/monthly before).
    Only these derived statistics ever reach a model — never the points themselves.
    """
    series = _series(points)
    if len(series) < 2:
        return None
    end_d, last = series[-1]
    year_ago = end_d - timedelta(days=365)
    last_year = [c for d, c in series if d >= year_ago]

    card: dict[str, Any] = {
        "as_of": end_d.isoformat(),
        "history_years": num((end_d - series[0][0]).days / 365.25, 1),
    }
    for label, years in (("ret_1m_pct", 1 / 12), ("ret_3m_pct", 0.25)):
        r = _total_return(series, years)
        card[label] = num(r * 100.0, 1) if r is not None else None
    r1 = _total_return(series, 1)
    card["ret_1y_pct"] = num(r1 * 100.0, 1) if r1 is not None else None
    card["cagr_3y_pct"] = num(_cagr_pct(series, 3), 1)
    card["cagr_5y_pct"] = num(_cagr_pct(series, 5), 1)
    card["max_dd_5y_pct"] = num(
        _max_drawdown_pct(series, end_d - timedelta(days=round(365.25 * 5))), 1
    )
    if len(last_year) >= 2:
        hi, lo = max(last_year), min(last_year)
        card["from_52w_high_pct"] = num((last / hi - 1.0) * 100.0, 1)
        card["range_52w_pos_pct"] = num((last - lo) / (hi - lo) * 100.0, 0) if hi > lo else None
    daily = [c for d, c in series if d >= year_ago]
    if len(daily) >= 60:
        rets = [math.log(b / a) for a, b in zip(daily, daily[1:]) if a > 0 and b > 0]
        if len(rets) >= 2:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            card["vol_1y_pct"] = num(math.sqrt(var) * math.sqrt(252) * 100.0, 1)

    bench = _series(benchmark or [])
    if bench:
        b1 = _total_return(bench, 1)
        if r1 is not None and b1 is not None:
            card["vs_bench_1y_pp"] = num((r1 - b1) * 100.0, 1)
        s5, b5 = _cagr_pct(series, 5), _cagr_pct(bench, 5)
        if s5 is not None and b5 is not None:
            card["vs_bench_5y_cagr_pp"] = num(s5 - b5, 1)
        card["bench"] = benchmark_symbol
    return {k: v for k, v in card.items() if v is not None}


# --- fundamentals -------------------------------------------------------------------


def _etf_holdings(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("symbol") or item.get("holdingName") or item.get("name")
        weight = _raw(item.get("holdingPercent") if "holdingPercent" in item else item.get("weight"))
        if not name:
            continue
        out.append({"n": str(name)[:40], "w": num(weight)})
        if len(out) >= ETF_HOLDINGS_MAX:
            break
    return out


def _etf_sectors(raw: Any) -> list[dict[str, Any]]:
    pairs: list[tuple[str, float]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            try:
                pairs.append((str(key), float(_raw(value))))
            except (TypeError, ValueError):
                continue
    pairs.sort(key=lambda p: p[1], reverse=True)
    return [{"n": k, "w": num(w)} for k, w in pairs[:ETF_SECTORS_MAX]]


def fundamentals_card(
    snapshot: dict[str, Any] | None,
    *,
    kind: str | None,
    thesis: dict[str, Any] | None = None,
    book: str = "portfolio",
) -> dict[str, Any] | None:
    """The business case: whitelisted metrics, coverage, and (for holdings) the thesis."""
    card: dict[str, Any] = {}
    if snapshot:
        payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
        for key in IDENTITY_FIELDS:
            if payload.get(key):
                card[key] = str(payload[key])[:60]
        if kind == "etf":
            card["aum_bn"] = num((num(payload.get("aum")) or 0) / 1e9, 2) if payload.get("aum") else None
            card["expense_ratio"] = num(payload.get("expense_ratio"))
            card["top_holdings"] = _etf_holdings(payload.get("holdings")) or None
            card["top_sectors"] = _etf_sectors(payload.get("allocations")) or None
        else:
            mc = num(payload.get("market_cap"))
            card["market_cap_bn"] = num(mc / 1e9, 2) if mc else None
            for key in EQUITY_FUNDAMENTALS:
                card[key] = num(payload.get(key))
        card["coverage"] = snapshot.get("completeness_state")
        missing = snapshot.get("missing_fields")
        if isinstance(missing, list) and missing:
            card["missing"] = [str(m) for m in missing]
        card["as_of"] = day(snapshot.get("as_of"))
    if book == "portfolio":
        t = thesis or {}
        card["thesis"] = _clip(t.get("text"), THESIS_MAX)
        falsifiers = t.get("falsifiers")
        card["falsifiers"] = (
            [c for c in (_clip(f, 160) for f in falsifiers) if c] if isinstance(falsifiers, list) else None
        ) or None
        card["thesis_v"] = t.get("version")
    out = {k: v for k, v in card.items() if v is not None}
    return out or None


# --- technicals ---------------------------------------------------------------------


def _trend(vs20: float | None, vs50: float | None) -> str | None:
    if vs20 is None or vs50 is None:
        return None
    if vs20 >= 0 and vs50 >= 0:
        return "above_both"
    if vs20 < 0 and vs50 < 0:
        return "below_both"
    return "mixed"


def _rsi_zone(rsi: float | None) -> str | None:
    if rsi is None:
        return None
    if rsi < 30:
        return "oversold"
    if rsi > 70:
        return "overbought"
    return "neutral"


def technicals_card(features: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(features, dict):
        return None
    vs20 = num(features.get("vs_sma20_pct"), 1)
    vs50 = num(features.get("vs_sma50_pct"), 1)
    rsi = num(features.get("rsi_14"), 0)
    card = {
        "as_of": day(features.get("as_of_bar")),
        "rsi": rsi,
        "rsi_zone": _rsi_zone(rsi),
        "vs_sma20_pct": vs20,
        "vs_sma50_pct": vs50,
        "trend": _trend(vs20, vs50),
        "r1m_pct": num(features.get("return_1m_pct"), 1),
        "r3m_pct": num(features.get("return_3m_pct"), 1),
        "r6m_pct": num(features.get("return_6m_pct"), 1),
    }
    out = {k: v for k, v in card.items() if v is not None}
    return out if len(out) > 1 else None


# --- news ---------------------------------------------------------------------------


def news_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()[:80]


def news_card(items: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Newest-first, title-deduplicated. Snippets ride along for Claude only (see `for_jev`)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in items or []:
        title = _clip(n.get("title"), 160)
        if not title:
            continue
        key = news_key(title)
        if key in seen:
            continue
        seen.add(key)
        entry = {"d": day(n.get("published_at")), "src": n.get("source"), "title": title}
        snippet = _clip(n.get("snippet"), NEWS_SNIPPET_MAX)
        if snippet:
            entry["snip"] = snippet
        out.append({k: v for k, v in entry.items() if v})
        if len(out) >= NEWS_MAX:
            break
    return out


HEADLINE_TAPE_MAX = 12


def headline_tape(subjects: Iterable[dict[str, Any]], limit: int = HEADLINE_TAPE_MAX) -> list[str]:
    """
    Book-wide headlines, newest first and deduplicated (0028) — the closest thing to a market
    mood the ingested data offers. Titles only; each is tagged with the subject it came from.
    """
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for s_ in subjects:
        for n in ((s_.get("cards") or {}).get("news") or []):
            title = n.get("title") if isinstance(n, dict) else None
            key = news_key(title or "")
            if not key or key in seen:
                continue
            seen.add(key)
            items.append((n.get("d") or "", f"{n.get('d') or ''} [{s_.get('symbol')}] {title}".strip()))
    items.sort(key=lambda it: it[0], reverse=True)
    return [text for _d, text in items[:limit]]


def news_for_jev(card: list[dict[str, Any]] | None) -> list[str]:
    return [f"{n.get('d') or ''} {n['title']}".strip() for n in card or [] if n.get("title")]


# --- position -----------------------------------------------------------------------


def position_card(subject: dict[str, Any]) -> dict[str, Any]:
    quote = subject.get("quote") or {}
    if subject.get("book") == "tracker":
        card = {
            "px": num(quote.get("price")),
            "px_display": num(subject.get("price_display")),
            "ccy": subject.get("currency"),
            "tracked_since": day(subject.get("added_at")),
        }
    else:
        card = {
            "qty": num(subject.get("quantity"), 4),
            "avg_cost": num(subject.get("avg_cost")),
            "px": num(quote.get("price")),
            "ccy": subject.get("currency"),
            "cost_display": num(subject.get("cost_display"), 0),
            "mv_display": num(subject.get("market_value_display"), 0),
            "pnl_pct": num(subject.get("pnl_pct"), 1),
            "wgt": num(subject.get("weight_pct"), 1),
            "wgt_cost": num(subject.get("weight_cost_pct"), 1),
            "held_days": subject.get("held_days"),
            "open_lots": subject.get("open_lot_count"),
        }
    return {k: v for k, v in card.items() if v is not None}


def build_cards(
    subject: dict[str, Any],
    *,
    history: Iterable[tuple[Any, Any]] | None = None,
    benchmark: Iterable[tuple[Any, Any]] | None = None,
    benchmark_symbol: str | None = None,
) -> dict[str, Any]:
    book = subject.get("book", "portfolio")
    return {
        "historical": historical_card(
            history or [], benchmark=benchmark, benchmark_symbol=benchmark_symbol
        ),
        "fundamentals": fundamentals_card(
            subject.get("fundamentals"),
            kind=subject.get("kind"),
            thesis=subject.get("thesis"),
            book=book,
        ),
        "technicals": technicals_card(subject.get("technicals")),
        "news": news_card(subject.get("news")),
        "position": position_card(subject),
    }
