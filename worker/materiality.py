"""
Materiality gate (0027): has anything changed enough since a subject's last real decision to
be worth another Claude/Jev pass?

Pure: compares this run's layer cards with the ones stored beside the deciding run. An empty
list of triggers means carry the last recommendation forward; any trigger means re-decide.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from digest import LENS_SCHEMA, news_key

DEFAULT_MATERIAL_MOVE_PCT = 5.0
DEFAULT_MAX_CARRY_DAYS = 7


def material_move_pct() -> float:
    try:
        return max(0.0, float(os.environ.get("ADVISORY_MATERIAL_MOVE_PCT") or DEFAULT_MATERIAL_MOVE_PCT))
    except ValueError:
        return DEFAULT_MATERIAL_MOVE_PCT


def max_carry_days() -> int:
    try:
        return max(0, int(os.environ.get("ADVISORY_MAX_CARRY_DAYS") or DEFAULT_MAX_CARRY_DAYS))
    except ValueError:
        return DEFAULT_MAX_CARRY_DAYS


def text_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _age_days(stamp: Any, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 86400.0


def _news_keys(card: Any) -> set[str]:
    return {news_key(n["title"]) for n in card or [] if isinstance(n, dict) and n.get("title")}


def triggers(
    *,
    book: str,
    cards: dict[str, Any],
    mandate_hash: str,
    book_fp: str | None,
    prior: dict[str, Any] | None,
    now: datetime | None = None,
    forced: bool = False,
    targeted: bool = False,
    move_pct: float | None = None,
    carry_days: int | None = None,
) -> list[str]:
    """
    `prior` is the stored decision record for this subject:
    {schema, decided_at, cards, mandate_hash, book_fp, price_at_rec, fallback}.
    """
    now = now or datetime.now(timezone.utc)
    move_pct = material_move_pct() if move_pct is None else move_pct
    carry_days = max_carry_days() if carry_days is None else carry_days

    out: list[str] = []
    if forced:
        out.append("forced")
    if targeted:
        out.append("targeted")
    if not prior:
        return out + ["no_prior"]
    if prior.get("schema") != LENS_SCHEMA:
        return out + ["lens_schema"]
    if prior.get("fallback"):
        out.append("prior_fallback")

    before = prior.get("cards") or {}

    if _news_keys(cards.get("news")) - _news_keys(before.get("news")):
        out.append("news")

    f_now, f_before = cards.get("fundamentals") or {}, before.get("fundamentals") or {}
    if (f_now.get("as_of"), f_now.get("coverage")) != (f_before.get("as_of"), f_before.get("coverage")):
        out.append("fundamentals")

    t_now, t_before = cards.get("technicals") or {}, before.get("technicals") or {}
    if (t_now.get("trend"), t_now.get("rsi_zone")) != (t_before.get("trend"), t_before.get("rsi_zone")):
        out.append("technicals_zone")

    px = (cards.get("position") or {}).get("px")
    ref = prior.get("price_at_rec")
    if px is not None and ref:
        try:
            if abs(float(px) / float(ref) - 1.0) * 100.0 >= move_pct:
                out.append("price_move")
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    if mandate_hash != prior.get("mandate_hash"):
        out.append("mandate")

    if book == "portfolio":
        if not f_now.get("thesis"):
            out.append("no_thesis")
        elif (f_now.get("thesis"), f_now.get("falsifiers")) != (
            f_before.get("thesis"), f_before.get("falsifiers")
        ):
            out.append("thesis")
        p_now, p_before = cards.get("position") or {}, before.get("position") or {}
        if (p_now.get("qty"), p_now.get("open_lots")) != (p_before.get("qty"), p_before.get("open_lots")):
            out.append("position")
        if book_fp != prior.get("book_fp"):
            out.append("cash")

    age = _age_days(prior.get("decided_at"), now)
    if age is None or age >= carry_days:
        out.append("stale")
    return out
