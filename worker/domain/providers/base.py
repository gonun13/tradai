from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from domain.providers.errors import (
    AdapterDisabledError,
    AdapterError,
    AdapterPermanentError,
    AdapterRateLimitError,
    AdapterTransientError,
    AdapterUnsupportedError,
)


@dataclass(frozen=True)
class RatePolicy:
    minimum_interval_seconds: float = 0.0
    window_seconds: int = 60
    window_limit: int | None = None
    request_cost: int = 1
    minimum_window_seconds: int | None = None
    maximum_window_limit: int | None = None


@dataclass(frozen=True)
class AdapterMetadata:
    provider: str
    regions: frozenset[str]
    instrument_kinds: frozenset[str]
    operations: frozenset[str]
    enabled: bool
    rate_policy: RatePolicy = RatePolicy()


@dataclass
class CandidateResult:
    provider: str
    payload: Any
    complete: bool
    score: float
    missing_fields: list[str] = field(default_factory=list)
    as_of: str | None = None


@dataclass
class Attempt:
    provider: str
    status: str
    score: float | None = None
    missing_fields: list[str] = field(default_factory=list)
    detail: str | None = None
    retry_after: float | None = None


@dataclass
class Selection:
    selected: CandidateResult | None
    attempts: list[Attempt]


def select_first_complete(
    adapters: Iterable[Any],
    *,
    operation: str,
    region: str,
    kind: str,
    invoke: Callable[[Any], Any],
    evaluate: Callable[[str, Any], CandidateResult],
    before_call: Callable[[Any], None] | None = None,
    after_call: Callable[[Any], None] | None = None,
) -> Selection:
    """Try an ordered registry; never merge responses; stop at the first complete one."""
    attempts: list[Attempt] = []
    partials: list[CandidateResult] = []
    for adapter in adapters:
        meta: AdapterMetadata = adapter.metadata
        if not meta.enabled:
            attempts.append(Attempt(meta.provider, "disabled"))
            continue
        if operation not in meta.operations or region not in meta.regions or kind not in meta.instrument_kinds:
            attempts.append(Attempt(meta.provider, "unsupported"))
            continue
        try:
            if before_call:
                before_call(adapter)
            payload = invoke(adapter)
            if after_call:
                after_call(adapter)
            result = evaluate(meta.provider, payload)
            attempts.append(Attempt(
                meta.provider, "complete" if result.complete else "incomplete",
                result.score, result.missing_fields,
            ))
            if result.complete:
                return Selection(result, attempts)
            partials.append(result)
        except Exception as exc:
            classified = classify_failure(exc)
            attempts.append(Attempt(
                meta.provider, classified.kind, detail=str(classified),
                retry_after=classified.retry_after,
            ))
            if after_call:
                after_call(adapter)
    best = max(partials, key=lambda r: (r.score, _timestamp_value(r.as_of)), default=None)
    return Selection(best, attempts)


def classify_failure(exc: Exception) -> AdapterError:
    if isinstance(exc, AdapterError):
        return exc
    text = str(exc).lower()
    if "not set" in text or "disabled" in text:
        return AdapterDisabledError(str(exc))
    if "unsupported" in text or "not implemented" in text:
        return AdapterUnsupportedError(str(exc))
    if "429" in text or "too many requests" in text or "rate limit" in text:
        return AdapterRateLimitError(str(exc))
    if any(token in text for token in ("timeout", "temporar", "connection", "502", "503", "504")):
        return AdapterTransientError(str(exc))
    return AdapterPermanentError(str(exc))


def _timestamp_value(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0
