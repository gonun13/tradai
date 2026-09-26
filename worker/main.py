from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from domain.schedule import schedule_check_seconds, should_fire_now
from infrastructure.adapters.symbol_search import SymbolSearchAdapter
from infrastructure.schedule_marker import already_fired_today, mark_fired_today, schedule_status
from services.advisory import AdvisoryService
from services.refresh import MarketRefreshService


DATA_DIR = os.environ.get("TRADAI_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "tradai.sqlite")
HEARTBEAT_SECONDS = int(os.environ.get("WORKER_HEARTBEAT_SECONDS", "30"))
QUOTE_INTERVAL = int(os.environ.get("WORKER_QUOTE_INTERVAL_SECONDS", "900"))
NEWS_INTERVAL = int(os.environ.get("WORKER_NEWS_INTERVAL_SECONDS", "86400"))
HTTP_PORT = int(os.environ.get("WORKER_HTTP_PORT", "8090"))

_refresh_lock = threading.Lock()
_advisory_lock = threading.Lock()
_service = MarketRefreshService(DB_PATH, news_interval_seconds=NEWS_INTERVAL)
_advisory = AdvisoryService(DB_PATH)
_symbol_search = SymbolSearchAdapter()
_advisory_thread: threading.Thread | None = None


def run_refresh(*, force_news: bool = False, manual_gap_retry: bool = False) -> dict:
    with _refresh_lock:
        print(
            f"[tradai-worker] market refresh starting force_news={force_news} "
            f"manual_gap_retry={manual_gap_retry}",
            flush=True,
        )
        result = _service.refresh(
            force_news=force_news, manual_gap_retry=manual_gap_retry
        )
        print(
            f"[tradai-worker] market refresh done instruments={len(result.get('instruments', []))} "
            f"errors={len(result.get('errors', []))}",
            flush=True,
        )
        return result


def start_advisory(trigger_kind: str = "manual", *, force: bool = False) -> dict:
    global _advisory_thread
    with _advisory_lock:
        if _advisory_thread is not None and _advisory_thread.is_alive():
            return {
                "ok": False,
                "error": "Advisory already running",
                "busy": True,
            }

        cached = _advisory.fresh_cached_run(force=force)
        if cached is not None:
            print(
                json.dumps({"service": "tradai-worker", "event": "advisory_cache_hit",
                            "run_id": cached["run_id"], "age_seconds": cached.get("age_seconds"),
                            "force": force, "trigger": trigger_kind}),
                flush=True,
            )
            return cached

        run_id = _advisory.create_run(trigger_kind, force=force)

        def _job() -> None:
            print(json.dumps({"service": "tradai-worker", "event": "advisory_started",
                              "run_id": run_id, "trigger": trigger_kind}), flush=True)
            try:
                result = _advisory.run(run_id)
                print(
                    json.dumps({"service": "tradai-worker", "event": "advisory_finished",
                                "run_id": run_id, "status": result.get("status"),
                                "recommendation_count": result.get("recommendation_count")}),
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"service": "tradai-worker", "event": "advisory_crashed",
                                  "run_id": run_id, "error": str(exc)}), flush=True)

        _advisory_thread = threading.Thread(target=_job, daemon=True, name=f"advisory-{run_id}")
        _advisory_thread.start()
        return {
            "ok": True,
            "from_cache": False,
            "run_id": run_id,
            "status": "pending",
            "message": "Advisory started",
            "trigger": trigger_kind,
        }


def start_advisory_single(symbol: str, force: bool = False) -> dict:
    global _advisory_thread
    with _advisory_lock:
        if _advisory_thread is not None and _advisory_thread.is_alive():
            return {
                "ok": False,
                "error": "Advisory already running",
                "busy": True,
            }

        cached = _advisory.fresh_cached_run(force=force)
        if cached is not None:
            print(
                json.dumps({"service": "tradai-worker", "event": "advisory_cache_hit",
                            "run_id": cached["run_id"], "age_seconds": cached.get("age_seconds"),
                            "force": force, "trigger": "manual"}),
                flush=True,
            )
            return cached

        run_id = _advisory.create_run("manual", target_symbol=symbol, force=force)

        def _job() -> None:
            print(json.dumps({"service": "tradai-worker", "event": "advisory_started",
                              "run_id": run_id, "trigger": "manual", "targeted": True}), flush=True)
            try:
                result = _advisory.run(run_id)
                print(
                    json.dumps({"service": "tradai-worker", "event": "advisory_finished",
                                "run_id": run_id, "status": result.get("status"),
                                "recommendation_count": result.get("recommendation_count")}),
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"service": "tradai-worker", "event": "advisory_crashed",
                                  "run_id": run_id, "error": str(exc)}), flush=True)

        _advisory_thread = threading.Thread(target=_job, daemon=True, name=f"advisory-{run_id}")
        _advisory_thread.start()
        return {
            "ok": True,
            "from_cache": False,
            "run_id": run_id,
            "status": "pending",
            "message": "Advisory started",
            "trigger": "manual",
            "target_symbol": symbol,
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print(f"[tradai-worker-http] {self.address_string()} {fmt % args}", flush=True)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/health", "/"):
            self._json(
                200,
                {
                    "ok": True,
                    "service": "tradai-worker",
                    "stage": "7",
                    "time": datetime.now(timezone.utc).isoformat(),
                    "claude": _advisory.claude.status(),
                    "jev": _advisory.jev.status(),
                    "schedule": schedule_status(DATA_DIR),
                },
            )
            return
        if path == "/search":
            # 0019: name-or-ticker lookup for the tracker. Lives here rather than in the API
            # because the vendor adapters and their keys do.
            qs = parse_qs(urlparse(self.path).query)
            query = (qs.get("q") or [""])[0]
            try:
                results, warnings = _symbol_search.search(query)
                self._json(200, {"ok": True, "results": results, "warnings": warnings})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/refresh", "/ingest"):
            try:
                # News is daily-cached; ?force_news=1 bypasses the cache.
                qs = parse_qs(urlparse(self.path).query)
                force_news = (qs.get("force_news") or ["0"])[0].lower() in {
                    "1",
                    "true",
                    "yes",
                }
                manual_gap_retry = (qs.get("manual_gap_retry") or ["0"])[0].lower() in {
                    "1",
                    "true",
                    "yes",
                }
                result = run_refresh(
                    force_news=force_news, manual_gap_retry=manual_gap_retry
                )
                self._json(200, result)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if path in ("/advisory/run", "/agent/run"):
            try:
                # Daily-cached like news; ?force=1 bypasses and burns Claude/Jev tokens again.
                qs = parse_qs(urlparse(self.path).query)
                force = (qs.get("force") or ["0"])[0].lower() in {"1", "true", "yes"}
                result = start_advisory("manual", force=force)
                if result.get("from_cache"):
                    self._json(200, result)
                else:
                    code = 202 if result.get("ok") else 409
                    self._json(code, result)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/advisory/run/symbol":
            try:
                qs = parse_qs(urlparse(self.path).query)
                symbol = (qs.get("symbol") or [""])[0]
                force = (qs.get("force") or ["0"])[0].lower() in {"1", "true", "yes"}
                if not symbol:
                    self._json(400, {"ok": False, "error": "symbol is required"})
                    return
                result = start_advisory_single(symbol, force=force)
                if result.get("from_cache"):
                    self._json(200, result)
                else:
                    code = 202 if result.get("ok") else 409
                    self._json(code, result)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"ok": False, "error": "not found"})


def heartbeat_loop() -> None:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    marker = os.path.join(DATA_DIR, ".worker_heartbeat")
    while True:
        now = datetime.now(timezone.utc).isoformat()
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(now + "\n")
        print(f"[tradai-worker] heartbeat {now}", flush=True)
        time.sleep(HEARTBEAT_SECONDS)


def quote_loop() -> None:
    # Initial delay so API can migrate schema first.
    time.sleep(5)
    while True:
        try:
            run_refresh()
        except Exception as exc:  # noqa: BLE001
            print(f"[tradai-worker] scheduled refresh failed: {exc}", flush=True)
        time.sleep(QUOTE_INTERVAL)


def advisory_schedule_loop() -> None:
    """Once per ET trading day after US close + offset (0011)."""
    time.sleep(8)
    check = schedule_check_seconds()
    print(
        f"[tradai-worker] advisory schedule armed check={check}s "
        f"status={schedule_status(DATA_DIR)}",
        flush=True,
    )
    while True:
        try:
            if should_fire_now() and not already_fired_today(DATA_DIR):
                print("[tradai-worker] post–US-close window — starting scheduled advisory", flush=True)
                result = start_advisory("schedule", force=False)
                # Mark the day even on cache hit / busy so we don't spam the loop.
                mark_fired_today(DATA_DIR)
                print(f"[tradai-worker] scheduled advisory result={result}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[tradai-worker] scheduled advisory failed: {exc}", flush=True)
        time.sleep(check)


def main() -> None:
    status = schedule_status(DATA_DIR)
    print(
        f"[tradai-worker] stage=7 data_dir={DATA_DIR} http=:{HTTP_PORT} "
        f"quote_interval={QUOTE_INTERVAL}s finnhub={'yes' if os.environ.get('FINNHUB_API_KEY') else 'no'} "
        f"alpha_vantage={'yes' if os.environ.get('ALPHA_VANTAGE_API_KEY') else 'no'} "
        f"marketaux={'yes' if os.environ.get('MARKETAUX_API_TOKEN') else 'no'} "
        f"claude_token={'yes' if os.environ.get('CLAUDE_CODE_OAUTH_TOKEN') else 'no'} "
        f"jev={'yes' if os.environ.get('TYPESAFE_API_KEY') else 'no'} "
        f"anthropic_api_key={'SET-UNSET-IT' if os.environ.get('ANTHROPIC_API_KEY') else 'no'} "
        f"advisory_fire_et={status.get('fire_at_et')}",
        flush=True,
    )
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=quote_loop, daemon=True).start()
    threading.Thread(target=advisory_schedule_loop, daemon=True, name="advisory-schedule").start()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
