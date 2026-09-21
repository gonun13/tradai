from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import doctrine
from adapters.claude_cli import ClaudeCliAdapter, ClaudeCliError
from adapters.jev import LENSES, TRACKER_LENSES, JevAdapter, JevError

# 0014: 3m sits below the operator's holding period; 24m backs the no_recovery_24m gate.
# 0020: the tracker has no position and no loss gate, so it is judged over its own, shorter
# set — an entry-timing question, not a multi-year thesis one.
HORIZONS_BY_BOOK = {
    "portfolio": ("6m", "12m", "24m"),
    "tracker": ("1m", "3m", "6m"),
}


def horizons_for(book: str) -> tuple[str, ...]:
    return HORIZONS_BY_BOOK.get(book, HORIZONS_BY_BOOK["portfolio"])
# `drop` is tracker-only (0019): stop spending attention on a name never owned.
ACTIONS = frozenset({"buy", "sell", "hold", "watch", "drop"})
# Default 0 = four-lens pass only (saves Claude scenario + extra Jev calls). Set 3 for full 0009 loop.
DEFAULT_SCENARIO_ROUNDS = 0
DEFAULT_ADVISORY_INTERVAL_SECONDS = 86400
RESEARCH_MAX_CHARS = 900
NEWS_TITLES_MAX = 4




def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(date_str: Any) -> int | None:
    if not date_str:
        return None
    try:
        start = datetime.fromisoformat(str(date_str)[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - start).days)


def max_scenario_rounds() -> int:
    raw = (os.environ.get("ADVISORY_MAX_SCENARIO_ROUNDS") or str(DEFAULT_SCENARIO_ROUNDS)).strip()
    try:
        return max(0, min(10, int(raw)))
    except ValueError:
        return DEFAULT_SCENARIO_ROUNDS


def advisory_interval_seconds() -> int:
    raw = (os.environ.get("ADVISORY_INTERVAL_SECONDS") or str(DEFAULT_ADVISORY_INTERVAL_SECONDS)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_ADVISORY_INTERVAL_SECONDS


def _clip(text: str | None, limit: int = RESEARCH_MAX_CHARS) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class AdvisoryService:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.claude = ClaudeCliAdapter()
        self.jev = JevAdapter()

    def connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('schedule', 'manual')),
                status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'partial')),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                model_refs_json TEXT,
                log_text TEXT,
                error_text TEXT,
                context_json TEXT,
                research_json TEXT,
                info_needs_json TEXT
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_run_id INTEGER NOT NULL,
                instrument_id INTEGER NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold', 'watch')),
                horizon TEXT NOT NULL CHECK (horizon IN ('1m', '3m', '6m', '12m', '24m')),
                reason TEXT NOT NULL DEFAULT 'legacy' CHECK (reason IN (
                    'thesis_broken', 'better_use',
                    'thesis_intact_underweight', 'new_conviction',
                    'thesis_intact', 'insufficient_evidence', 'legacy'
                )),
                loss_gate TEXT CHECK (loss_gate IS NULL OR loss_gate IN (
                    'not_at_loss', 'offset_same_year', 'no_recovery_24m', 'blocked'
                )),
                pair_symbol TEXT,
                confidence REAL,
                suppressed INTEGER NOT NULL DEFAULT 0 CHECK (suppressed IN (0, 1)),
                suppressed_reason TEXT,
                proposed_action TEXT CHECK (proposed_action IS NULL OR proposed_action IN (
                    'buy', 'sell', 'hold', 'watch'
                )),
                price_at_rec REAL,
                rationale TEXT,
                jev_payload_json TEXT,
                jev_lenses_json TEXT,
                conversation_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE,
                UNIQUE (agent_run_id, instrument_id, horizon)
            );
            -- 0015 / 0016 / 0012: the API owns writes to these, the worker reads them and
            -- writes thesis drafts. Declared here too so a fresh volume works whichever
            -- service boots first.
            CREATE TABLE IF NOT EXISTS theses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id INTEGER NOT NULL,
                thesis TEXT NOT NULL,
                falsifiers_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'superseded')),
                version INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'claude' CHECK (source IN ('claude', 'operator')),
                agent_run_id INTEGER,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                superseded_at TEXT,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                instrument_id INTEGER NOT NULL,
                name TEXT,
                note TEXT,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS realized_disposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id INTEGER NOT NULL,
                sell_transaction_id INTEGER NOT NULL,
                trade_date TEXT NOT NULL,
                quantity REAL NOT NULL,
                proceeds REAL NOT NULL,
                cost REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                currency TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (sell_transaction_id)
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id INTEGER NOT NULL UNIQUE,
                unread INTEGER NOT NULL DEFAULT 1 CHECK (unread IN (0, 1)),
                severity TEXT NOT NULL DEFAULT 'action',
                raised_at TEXT NOT NULL,
                acked_at TEXT,
                FOREIGN KEY (recommendation_id) REFERENCES recommendations(id) ON DELETE CASCADE
            );
            """
        )
        self._ensure_columns(
            conn,
            "agent_runs",
            {
                "research_json": "TEXT",
                "info_needs_json": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "recommendations",
            {
                "jev_lenses_json": "TEXT",
                "conversation_json": "TEXT",
                # 0013 — present already when the API migrated first; added here otherwise.
                "reason": "TEXT NOT NULL DEFAULT 'legacy'",
                "loss_gate": "TEXT",
                "pair_symbol": "TEXT",
                "confidence": "REAL",
                "suppressed": "INTEGER NOT NULL DEFAULT 0",
                "suppressed_reason": "TEXT",
                "proposed_action": "TEXT",
                "price_at_rec": "REAL",
            },
        )
        return conn

    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        conn.commit()

    def fresh_cached_run(self, *, force: bool = False) -> dict[str, Any] | None:
        """Return latest succeeded/partial run if still within the daily advisory window."""
        interval = advisory_interval_seconds()
        if force or interval <= 0:
            return None
        conn = self.connect()
        try:
            row = conn.execute(
                """
                SELECT id, status, started_at, finished_at
                FROM agent_runs
                WHERE status IN ('succeeded', 'partial')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            stamp = row["finished_at"] or row["started_at"]
            if not stamp:
                return None
            try:
                last_dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                return None
            age = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if age >= interval:
                return None
            rec_count = conn.execute(
                "SELECT COUNT(*) AS c FROM recommendations WHERE agent_run_id = ?",
                (int(row["id"]),),
            ).fetchone()["c"]
            return {
                "ok": True,
                "from_cache": True,
                "run_id": int(row["id"]),
                "status": row["status"],
                "age_seconds": int(age),
                "interval_seconds": interval,
                "recommendation_count": int(rec_count),
                "message": (
                    f"Using cached advisory run #{row['id']} "
                    f"({int(age)}s old; refresh every {interval}s). Pass force=1 to re-run."
                ),
            }
        finally:
            conn.close()

    def create_run(self, trigger_kind: str = "manual", target_symbol: str | None = None) -> int:
        conn = self.connect()
        try:
            model_refs = {
                "claude": self.claude.status(),
                "jev": self.jev.status(),
                "max_scenario_rounds": max_scenario_rounds(),
                "advisory_interval_seconds": advisory_interval_seconds(),
            }
            if target_symbol:
                model_refs["target_symbol"] = target_symbol
            
            cur = conn.execute(
                """
                INSERT INTO agent_runs (trigger_kind, status, started_at, model_refs_json, log_text)
                VALUES (?, 'pending', ?, ?, ?)
                """,
                (
                    trigger_kind,
                    utc_now(),
                    json.dumps(model_refs),
                    "queued",
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _get_target_symbol(self, conn: sqlite3.Connection, run_id: int) -> str | None:
        row = conn.execute(
            "SELECT model_refs_json FROM agent_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row and row["model_refs_json"]:
            refs = json.loads(row["model_refs_json"])
            return refs.get("target_symbol")
        return None

    def run(self, run_id: int) -> dict[str, Any]:
        conn = self.connect()
        log: list[str] = []
        try:
            self._set_status(conn, run_id, "running", log_text="running")
            target_symbol = self._get_target_symbol(conn, run_id)
            context = self._build_context(conn, target_symbol=target_symbol)
            conn.execute(
                "UPDATE agent_runs SET context_json = ? WHERE id = ?",
                (json.dumps(context), run_id),
            )
            conn.commit()

            # 0019: a tracker-only book is a legitimate state — you can be researching
            # names before owning any of them.
            if not (context["holdings"] or context["tracked"]):
                self._finish(
                    conn,
                    run_id,
                    status="failed",
                    log=log + ["nothing to advise on"],
                    error="Nothing to advise on — add a holding or a tracked name first.",
                )
                return self._run_payload(conn, run_id)

            log.append(
                f"context holdings={len(context['holdings'])} "
                f"tracked={len(context['tracked'])} "
                f"total_eur={context.get('portfolio_market_value_eur')}"
            )

            try:
                research_pack = self._claude_research(context, log)
            except ClaudeCliError as exc:
                self._finish(
                    conn,
                    run_id,
                    status="failed",
                    log=log,
                    error=str(exc),
                    models_extra={"claude_error": str(exc)},
                )
                return self._run_payload(conn, run_id)

            research_by_symbol = research_pack["research_by_symbol"]
            info_needs = research_pack["info_needs"]
            synthesis = research_pack.get("synthesis") or ""
            # Carried on context so every lens pass and the doctrine gate see the same
            # thesis findings without threading another argument through six call sites.
            context["thesis_by_symbol"] = research_pack.get("thesis_by_symbol") or {}
            context["tracked_by_symbol"] = research_pack.get("tracked_by_symbol") or {}
            drafted = self._persist_thesis_drafts(
                conn, run_id, context, research_pack.get("thesis_drafts") or {}
            )
            if drafted:
                log.append(f"claude: thesis notes written={drafted}")

            conn.execute(
                """
                UPDATE agent_runs
                SET research_json = ?, info_needs_json = ?
                WHERE id = ?
                """,
                (
                    json.dumps(
                        {
                            "synthesis": synthesis,
                            "by_symbol": research_by_symbol,
                            "thesis_by_symbol": context.get("thesis_by_symbol") or {},
                            "tracked_by_symbol": context.get("tracked_by_symbol") or {},
                        }
                    ),
                    json.dumps(info_needs),
                    run_id,
                ),
            )
            conn.commit()
            log.append(f"claude: research symbols={len(research_by_symbol)} info_needs={len(info_needs)}")

            conversations: dict[int, list[dict[str, Any]]] = {
                int(h["instrument_id"]): [] for h in self._subjects(context)
            }
            lens_answers: dict[str, dict[str, Any]] = {}

            try:
                for lens in LENSES:
                    result = self._run_lens(
                        context,
                        research_by_symbol,
                        lens=lens,
                        log=log,
                        conversations=conversations,
                    )
                    lens_answers[lens] = result["answers"]

                combined_answers = dict(lens_answers.get("combined") or {})
                scenario_cap = max_scenario_rounds()
                for round_idx in range(1, scenario_cap + 1):
                    try:
                        scenario = self._claude_scenario_plan(
                            context,
                            research_by_symbol,
                            lens_answers,
                            combined_answers,
                            round_idx=round_idx,
                            log=log,
                        )
                    except ClaudeCliError as exc:
                        log.append(f"claude scenario round {round_idx} skipped: {exc}")
                        break

                    if scenario.get("done", True):
                        log.append(f"claude: scenario rounds complete after {round_idx - 1}")
                        break

                    result = self._run_scenario_round(
                        context,
                        research_by_symbol,
                        scenario=scenario,
                        round_idx=round_idx,
                        log=log,
                        conversations=conversations,
                    )
                    # Scenario rounds refresh the canonical combined answers.
                    for qid, ans in (result.get("answers") or {}).items():
                        combined_answers[qid] = ans
                    lens_answers["combined"] = combined_answers
                    log.append(f"jev: scenario round {round_idx} applied to combined lens")
                else:
                    log.append(f"claude: scenario round cap reached ({scenario_cap})")

            except JevError as exc:
                written = self._persist_partial_from_claude(
                    conn,
                    run_id,
                    context,
                    research_by_symbol,
                    conversations,
                    jev_error=str(exc),
                )
                self._finish(
                    conn,
                    run_id,
                    status="partial" if written else "failed",
                    log=log,
                    error=str(exc),
                    models_extra={"jev_error": str(exc)},
                )
                return self._run_payload(conn, run_id)

            written = self._persist_recommendations(
                conn,
                run_id,
                context,
                research_by_symbol,
                lens_answers,
                combined_answers,
                conversations,
                log=log,
            )
            status = "succeeded" if written else "failed"
            error = None if written else "No recommendations written"
            self._finish(conn, run_id, status=status, log=log, error=error)
            return self._run_payload(conn, run_id)
        except Exception as exc:  # noqa: BLE001
            log.append(f"unhandled: {exc}")
            self._finish(conn, run_id, status="failed", log=log, error=str(exc))
            return self._run_payload(conn, run_id)
        finally:
            conn.close()

    def _build_context(self, conn: sqlite3.Connection, target_symbol: str | None = None) -> dict[str, Any]:
        rows = conn.execute(
            """
            SELECT h.id AS holding_id, h.quantity, h.avg_cost, h.total_cost, h.notes,
                   h.first_trade_date, h.open_lot_count, h.realized_pnl_native,
                   i.id AS instrument_id, i.isin, i.symbol, i.mic, i.currency, i.name, i.kind, i.region,
                   q.price AS quote_price, q.currency AS quote_currency, q.as_of AS quote_as_of, q.source AS quote_source,
                   t.features_json, t.as_of AS tech_as_of,
                   fx.rate AS fx_to_eur
            FROM holdings h
            INNER JOIN instruments i ON i.id = h.instrument_id
            LEFT JOIN quotes q ON q.instrument_id = i.id
            LEFT JOIN technicals t ON t.instrument_id = i.id
            LEFT JOIN fx_rates fx ON fx.base_currency = i.currency AND fx.quote_currency = 'EUR'
            ORDER BY i.symbol COLLATE NOCASE ASC
            """
        ).fetchall()

        holdings: list[dict[str, Any]] = []
        total_eur = 0.0
        have_eur = False

        for row in rows:
            currency = (row["currency"] or "EUR").upper()
            qty = float(row["quantity"] or 0)
            total_cost = float(row["total_cost"] or 0)
            fx = 1.0 if currency == "EUR" else (float(row["fx_to_eur"]) if row["fx_to_eur"] is not None else None)
            cost_eur = total_cost * fx if fx is not None else None

            quote_price = float(row["quote_price"]) if row["quote_price"] is not None else None
            mv_native = qty * quote_price if quote_price is not None else None
            mv_eur = mv_native * fx if mv_native is not None and fx is not None else None
            if mv_eur is not None:
                total_eur += mv_eur
                have_eur = True

            pnl_eur = (mv_eur - cost_eur) if mv_eur is not None and cost_eur is not None else None
            pnl_pct = (
                ((mv_eur - cost_eur) / cost_eur * 100.0)
                if mv_eur is not None and cost_eur and cost_eur > 0
                else None
            )

            features = None
            if row["features_json"]:
                try:
                    features = json.loads(row["features_json"])
                except json.JSONDecodeError:
                    features = None

            news = conn.execute(
                """
                SELECT n.title, n.snippet, n.url, n.source_name, n.published_at
                FROM news_item_instruments nii
                INNER JOIN news_items n ON n.id = nii.news_item_id
                WHERE nii.instrument_id = ?
                ORDER BY COALESCE(n.published_at, n.fetched_at) DESC
                LIMIT 5
                """,
                (int(row["instrument_id"]),),
            ).fetchall()

            holdings.append(
                {
                    "holding_id": int(row["holding_id"]),
                    "instrument_id": int(row["instrument_id"]),
                    "symbol": row["symbol"],
                    "isin": row["isin"],
                    "mic": row["mic"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "region": row["region"],
                    "currency": currency,
                    "quantity": qty,
                    "avg_cost": float(row["avg_cost"] or 0),
                    "total_cost": total_cost,
                    "cost_eur": cost_eur,
                    "market_value_eur": mv_eur,
                    "pnl_eur": pnl_eur,
                    "pnl_pct": pnl_pct,
                    "weight_pct": None,
                    "quote": {
                        "price": quote_price,
                        "currency": row["quote_currency"],
                        "as_of": row["quote_as_of"],
                        "source": row["quote_source"],
                    }
                    if quote_price is not None
                    else None,
                    "technicals": features,
                    "news": [
                        {
                            "title": n["title"],
                            "snippet": n["snippet"],
                            "url": n["url"],
                            "source": n["source_name"],
                            "published_at": n["published_at"],
                        }
                        for n in news
                    ],
                    "notes": row["notes"],
                    # 0013: a 12-year-old dead position and a fresh conviction add both show a
                    # deep negative P&L. Nothing but the holding period separates them.
                    "first_trade_date": row["first_trade_date"],
                    "held_days": _days_since(row["first_trade_date"]),
                    "open_lot_count": row["open_lot_count"],
                    "realized_pnl_native": row["realized_pnl_native"],
                    "thesis": self._approved_thesis(conn, int(row["instrument_id"])),
                    "weight_cost_pct": None,
                }
            )

        total_cost_eur = sum(h["cost_eur"] for h in holdings if h["cost_eur"] is not None)
        if have_eur and total_eur > 0:
            for h in holdings:
                if h["market_value_eur"] is not None:
                    h["weight_pct"] = round(h["market_value_eur"] / total_eur * 100.0, 2)
        # Market-value weight alone makes a position that already lost 95% look like a
        # rounding error — precisely the holdings that need attention. Cost weight restores it.
        if total_cost_eur > 0:
            for h in holdings:
                if h["cost_eur"] is not None:
                    h["weight_cost_pct"] = round(h["cost_eur"] / total_cost_eur * 100.0, 2)

        cash_eur = self._setting_float(conn, "cash_eur")
        realized = self._realized_gains_ytd_eur(conn)
        mv = total_eur if have_eur else None

        for h in holdings:
            h["book"] = "portfolio"
        tracked = self._tracked(conn)

        # 0019: two free-text fields the operator writes on the Setup page. The investor
        # profile says who is asking and what makes a name worth buying; the portfolio
        # profile carries the rules for what is already owned. Each falls back to its own
        # default so the system still has *some* mandate before they are written.
        profiles = {
            "investor": self._setting_str(conn, "investor_profile_text"),
            "portfolio": self._setting_str(conn, "portfolio_profile_text"),
        }

        # Filter to target_symbol if specified (single-symbol run)
        if target_symbol:
            holdings = [h for h in holdings if h.get("symbol") == target_symbol]
            tracked = [t for t in tracked if t.get("symbol") == target_symbol]
            # Reset portfolio totals for single-symbol analysis
            mv = None
            total_eur = 0.0
            have_eur = False
            total_cost_eur = 0.0
            cash_eur = None

        return {
            "display_currency": "EUR",
            "portfolio_market_value_eur": mv,
            "portfolio_cost_eur": round(total_cost_eur, 2) if total_cost_eur else None,
            # 0012: the operator holds a cash reserve; a buy does not require a sell.
            "cash_eur": cash_eur,
            "portfolio_total_eur": round((mv or 0.0) + (cash_eur or 0.0), 2) if mv is not None else None,
            # 0013: drives the offset_same_year loss gate.
            "realized_gains_ytd_eur": realized,
            "calendar_year": datetime.now(timezone.utc).year,
            "tracker": [
                {"symbol": t["symbol"], "name": t["name"], "note": t["note"]} for t in tracked
            ],
            "profiles": profiles,
            # The text each book was *actually* judged by, defaults already substituted.
            # The log page shows this rather than `profiles`, which is null wherever the
            # operator hasn't written their half — and "no mandate" would be a lie, the
            # models were given one either way.
            "mandates": {b: doctrine.compose_mandate(profiles, b) for b in doctrine.BOOKS},
            # Kept as a string for the log page and the payload contract (spec/data.md):
            # the text actually applied to holdings.
            "mandate": doctrine.compose_mandate(profiles, "portfolio"),
            "holdings": holdings,
            "tracked": tracked,
            "built_at": utc_now(),
        }

    def _subjects(
        self, context: dict[str, Any], *, lens: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Every instrument this run decides on, each tagged with its `book` (0019).

        Holdings and tracked names go through the same pipeline — same lenses, same
        transcript, same recommendation rows — so every call site iterates this rather than
        `context["holdings"]`. The one asymmetry: no news is ingested for tracked names, so
        they are left out of the news lens instead of being asked a blind question.
        """
        subjects = list(context.get("holdings") or [])
        if lens not in ("news",):
            subjects.extend(context.get("tracked") or [])
        return subjects

    def _approved_thesis(self, conn: sqlite3.Connection, instrument_id: int) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT id, thesis, falsifiers_json, version, approved_at
            FROM theses
            WHERE instrument_id = ? AND status = 'approved'
            ORDER BY version DESC LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            falsifiers = json.loads(row["falsifiers_json"] or "[]")
        except json.JSONDecodeError:
            falsifiers = []
        return {
            "id": int(row["id"]),
            "text": row["thesis"],
            "falsifiers": falsifiers if isinstance(falsifiers, list) else [],
            "version": int(row["version"]),
            "approved_at": row["approved_at"],
        }

    def _tracked(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        """
        The tracker book (0019). Shaped to sit beside a holding in the same lists — same
        `symbol` / `instrument_id` / `quote` / `technicals` keys — with the position fields
        absent rather than zeroed, because a zero cost basis would read as a real fact.
        """
        rows = conn.execute(
            """
            SELECT t.symbol, t.name, t.note, t.instrument_id, t.added_at,
                   i.currency, i.kind, i.region, i.isin, i.mic, i.name AS instrument_name,
                   q.price AS quote_price, q.currency AS quote_currency,
                   q.as_of AS quote_as_of, q.source AS quote_source,
                   tech.features_json, tech.as_of AS tech_as_of,
                   fx.rate AS fx_to_eur
            FROM tracker t
            INNER JOIN instruments i ON i.id = t.instrument_id
            LEFT JOIN quotes q ON q.instrument_id = i.id
            LEFT JOIN technicals tech ON tech.instrument_id = i.id
            LEFT JOIN fx_rates fx ON fx.base_currency = i.currency AND fx.quote_currency = 'EUR'
            WHERE t.archived_at IS NULL
            ORDER BY t.symbol COLLATE NOCASE ASC
            """
        ).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            currency = (r["currency"] or "EUR").upper()
            fx = 1.0 if currency == "EUR" else (
                float(r["fx_to_eur"]) if r["fx_to_eur"] is not None else None
            )
            price = float(r["quote_price"]) if r["quote_price"] is not None else None

            features = None
            if r["features_json"]:
                try:
                    features = json.loads(r["features_json"])
                except json.JSONDecodeError:
                    features = None

            out.append(
                {
                    "book": "tracker",
                    "instrument_id": int(r["instrument_id"]),
                    "symbol": r["symbol"],
                    "name": r["name"] or r["instrument_name"],
                    "note": r["note"],
                    "added_at": r["added_at"],
                    "isin": r["isin"],
                    "mic": r["mic"],
                    "currency": currency,
                    "kind": r["kind"],
                    "region": r["region"],
                    "quote": {
                        "price": price,
                        "currency": r["quote_currency"],
                        "as_of": r["quote_as_of"],
                        "source": r["quote_source"],
                    } if price is not None else None,
                    "price_eur": round(price * fx, 4) if price is not None and fx is not None else None,
                    "technicals": features,
                    "technicals_as_of": r["tech_as_of"],
                    # No news is ingested for the tracker; empty rather than absent so any
                    # caller that reaches for it gets a list.
                    "news": [],
                }
            )
        return out

    def _setting_float(self, conn: sqlite3.Connection, key: str) -> float | None:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None or row["value"] in (None, ""):
            return None
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return None

    def _setting_str(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None or row["value"] in (None, ""):
            return None
        return str(row["value"])

    def _realized_gains_ytd_eur(self, conn: sqlite3.Connection) -> float:
        """This calendar year's FIFO disposals in EUR, plus gains booked elsewhere (0012)."""
        year = str(datetime.now(timezone.utc).year)
        rows = conn.execute(
            """
            SELECT d.realized_pnl, d.currency, fx.rate
            FROM realized_disposals d
            LEFT JOIN fx_rates fx
              ON fx.base_currency = d.currency AND fx.quote_currency = 'EUR'
            WHERE substr(d.trade_date, 1, 4) = ?
            """,
            (year,),
        ).fetchall()
        total = 0.0
        for r in rows:
            currency = (r["currency"] or "EUR").upper()
            rate = 1.0 if currency == "EUR" else (float(r["rate"]) if r["rate"] is not None else None)
            if rate is None:
                continue  # no FX on hand — skip rather than mis-state a gate input
            total += float(r["realized_pnl"]) * rate
        override = self._setting_float(conn, "realized_gains_ytd_override_eur") or 0.0
        return round(total + override, 2)

    def _compact_technicals(self, features: Any) -> dict[str, Any] | None:
        if not isinstance(features, dict):
            return None
        keys = (
            "rsi_14", "sma_20", "sma_50",
            "return_1m_pct", "return_3m_pct", "return_6m_pct",
            "last_close",
        )
        out = {k: features.get(k) for k in keys if features.get(k) is not None}
        return out or None

    def _slim_portfolio(self, context: dict[str, Any]) -> dict[str, Any]:
        """Book blob for Claude — compact, but carrying everything the doctrine turns on."""
        return {
            "display_currency": "EUR",
            "portfolio_market_value_eur": context.get("portfolio_market_value_eur"),
            "cash_eur": context.get("cash_eur"),
            "realized_gains_ytd_eur": context.get("realized_gains_ytd_eur"),
            "calendar_year": context.get("calendar_year"),
            "holdings": [
                {
                    "symbol": h["symbol"],
                    "kind": h["kind"],
                    "region": h["region"],
                    "qty": h["quantity"],
                    "avg_cost": h["avg_cost"],
                    "cost_eur": h["cost_eur"],
                    "mv_eur": h["market_value_eur"],
                    "pnl_pct": h["pnl_pct"],
                    "wgt": h["weight_pct"],
                    "wgt_cost": h.get("weight_cost_pct"),
                    "held_days": h.get("held_days"),
                    "first_trade_date": h.get("first_trade_date"),
                    "open_lots": h.get("open_lot_count"),
                    "thesis": (h.get("thesis") or {}).get("text"),
                    "falsifiers": (h.get("thesis") or {}).get("falsifiers"),
                    "px": (h["quote"] or {}).get("price") if h.get("quote") else None,
                    "tech": self._compact_technicals(h.get("technicals")),
                    "news": [n.get("title") for n in (h.get("news") or [])[:NEWS_TITLES_MAX] if n.get("title")],
                }
                for h in context["holdings"]
            ],
            # 0019: the second book. No cost basis, no P&L, no news — deliberately, so the
            # shape itself tells Claude these are names to judge, not positions to manage.
            "tracked": [
                {
                    "symbol": t["symbol"],
                    "name": t.get("name"),
                    "kind": t.get("kind"),
                    "region": t.get("region"),
                    "note": t.get("note"),
                    "tracked_since": t.get("added_at"),
                    "px": (t["quote"] or {}).get("price") if t.get("quote") else None,
                    "px_eur": t.get("price_eur"),
                    "tech": self._compact_technicals(t.get("technicals")),
                }
                for t in (context.get("tracked") or [])
            ],
        }

    def _claude_research(self, context: dict[str, Any], log: list[str]) -> dict[str, Any]:
        log.append("claude: researcher pass (notes + info-needs)")
        symbols = [h["symbol"] for h in context["holdings"]]
        tracked_symbols = [t["symbol"] for t in (context.get("tracked") or [])]
        needs_thesis = [h["symbol"] for h in context["holdings"] if not h.get("thesis")]
        profiles = context.get("profiles") or {}
        prompt = (
            "Tradai RESEARCHER (not decider). Advisory only. Jev alone chooses the action.\n"
            # 0019: two profiles. The investor profile says who is asking and applies to
            # everything; the portfolio profile governs only what is already owned.
            f"INVESTOR PROFILE (who the operator is, what makes a name worth buying — "
            f"applies to BOTH books): {doctrine.compose_mandate(profiles, 'tracker')}\n"
            f"PORTFOLIO PROFILE (rules for names already owned — applies to HOLDINGS only): "
            f"{(profiles.get('portfolio') or '').strip() or doctrine.DEFAULT_PORTFOLIO_PROFILE}\n"
            "SELL DOCTRINE (0013): the only valid reasons to sell are (a) the recorded investment "
            "thesis is broken, or (b) the capital has a specific better named use. Price action, "
            "momentum, moving averages, drawdown depth and concentration are NEVER reasons to sell. "
            "A position being down is not a reason to sell it.\n"
            "YOUR JOB, per HOLDING (owned):\n"
            "1. thesis_status — test the recorded thesis and its falsifiers against current evidence. "
            "'broken' means a named falsifier actually tripped on the facts; 'weakening' means it is "
            "under pressure; 'intact' otherwise. A price decline alone is NOT a broken thesis. "
            "Only claim 'broken' when you can cite the specific fact in `evidence`.\n"
            "2. evidence — the concrete fact that moved thesis_status, or \"no change\".\n"
            "3. better_use_target — if this capital would clearly do more work in another named "
            "symbol from HOLDINGS or TRACKED, name it; otherwise null. Do not invent tickers.\n"
            "4. research — ≤3 sentences covering 6m/12m/24m.\n"
            f"Also draft a thesis + 2-4 falsifiers for holdings that have none: {json.dumps(needs_thesis)}. "
            "A falsifier must be a specific checkable condition, not a mood.\n"
            "YOUR JOB, per TRACKED name (not owned — the operator is considering it):\n"
            "1. entry_case — ≤2 sentences on why this would be worth owning, judged against the "
            "INVESTOR PROFILE. Say plainly if there isn't one.\n"
            "2. what_would_make_me_buy — the specific condition that would turn this into a buy: "
            "a price, a result, an event. Not \"further research\".\n"
            "3. research — ≤3 sentences covering 1m/3m/6m (0020: the tracker is judged on "
            "entry timing, not the holdings' 6m/12m/24m).\n"
            "No news has been ingested for tracked names, so do not claim news you cannot see.\n"
            "Return ONLY JSON (no fences):\n"
            '{"synthesis":"≤2 sentences",'
            '"by_symbol":{"SYM":{"research":"","thesis_status":"intact|weakening|broken",'
            '"evidence":"","better_use_target":null}},'
            '"tracked_by_symbol":{"SYM":{"research":"","entry_case":"",'
            '"what_would_make_me_buy":""}},'
            '"thesis_drafts":{"SYM":{"thesis":"","falsifiers":["",""]}},'
            '"info_needs":[{"description":"gap","symbols":["SYM"],"priority":"low|medium|high"}]}\n'
            f"Holdings: {json.dumps(symbols)}\n"
            f"Tracked: {json.dumps(tracked_symbols)}\n"
            f"BOOK:\n{json.dumps(self._slim_portfolio(context), ensure_ascii=False, separators=(',', ':'))}"
        )
        text = self.claude.analyze(prompt)
        log.append(f"claude: got {len(text)} chars research")
        return self._parse_research(text, context)

    def _persist_thesis_drafts(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        context: dict[str, Any],
        drafts: dict[str, dict[str, Any]],
    ) -> int:
        """
        Store Claude's proposed thesis for any holding that doesn't have one yet — the "why
        I own this" note it tests on later runs. Written straight in, no operator sign-off:
        Claude and Jev are trusted to reason with it, the same way they're trusted with the
        buy/sell/hold verdict itself. Visible (and editable) on the Setup page.
        """
        if not drafts:
            return 0
        by_symbol = {h["symbol"]: h for h in context["holdings"]}
        now = utc_now()
        written = 0
        for symbol, draft in drafts.items():
            h = by_symbol.get(symbol)
            if h is None or h.get("thesis"):
                continue
            iid = int(h["instrument_id"])
            version = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM theses WHERE instrument_id = ?",
                    (iid,),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO theses
                    (instrument_id, thesis, falsifiers_json, status, version, source,
                     agent_run_id, created_at, approved_at)
                VALUES (?, ?, ?, 'approved', ?, 'claude', ?, ?, ?)
                """,
                (
                    iid,
                    str(draft.get("thesis") or "").strip(),
                    json.dumps(draft.get("falsifiers") or []),
                    version,
                    run_id,
                    now,
                    now,
                ),
            )
            written += 1
        conn.commit()
        return written

    def _parse_research(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        symbols = [h["symbol"] for h in context["holdings"]]
        tracked_symbols = [t["symbol"] for t in (context.get("tracked") or [])]
        data = self._parse_json_object(text)
        research_by_symbol: dict[str, str] = {}
        thesis_by_symbol: dict[str, dict[str, Any]] = {}
        tracked_by_symbol: dict[str, dict[str, Any]] = {}
        thesis_drafts: dict[str, dict[str, Any]] = {}
        synthesis = ""
        info_needs: list[dict[str, Any]] = []
        # 0019: the tracker is still the universe a `better_use` sell may name (0016) — it
        # is just a real book now rather than a bare list of tickers.
        known = {s.upper() for s in symbols} | {s.upper() for s in tracked_symbols}

        if isinstance(data, dict):
            syn = data.get("synthesis")
            if isinstance(syn, str):
                synthesis = syn.strip()
            by_sym = (
                data.get("by_symbol")
                or data.get("research_by_symbol")
                or data.get("research")
                or data
            )
            if isinstance(by_sym, dict):
                for sym in symbols:
                    val = by_sym.get(sym) or by_sym.get(sym.upper()) or by_sym.get(sym.lower())
                    if isinstance(val, str) and val.strip():
                        research_by_symbol[sym] = val.strip()
                    elif isinstance(val, dict):
                        note = val.get("research")
                        research_by_symbol[sym] = (
                            note.strip() if isinstance(note, str) and note.strip()
                            else json.dumps(val, ensure_ascii=False)
                        )
                        status = str(val.get("thesis_status") or "").strip().lower()
                        target = val.get("better_use_target")
                        target = str(target).strip().upper() if isinstance(target, str) and target.strip() else None
                        thesis_by_symbol[sym] = {
                            # Fails closed: an unrecognised status is not "broken".
                            "thesis_status": status if status in ("intact", "weakening", "broken") else None,
                            "evidence": str(val.get("evidence") or "").strip() or None,
                            # Never let the researcher invent a ticker to justify a switch.
                            "better_use_target": target if target in known and target != sym.upper() else None,
                        }

            # 0019: the tracker's mirror of thesis_status — why this would be worth owning
            # and what would turn it into a buy. Feeds the `thesis` lens for tracked names.
            tracked_raw = data.get("tracked_by_symbol")
            if isinstance(tracked_raw, dict):
                for sym in tracked_symbols:
                    val = (
                        tracked_raw.get(sym)
                        or tracked_raw.get(sym.upper())
                        or tracked_raw.get(sym.lower())
                    )
                    if isinstance(val, str) and val.strip():
                        research_by_symbol[sym] = val.strip()
                        continue
                    if not isinstance(val, dict):
                        continue
                    note = val.get("research")
                    research_by_symbol[sym] = (
                        note.strip() if isinstance(note, str) and note.strip()
                        else json.dumps(val, ensure_ascii=False)
                    )
                    tracked_by_symbol[sym] = {
                        "entry_case": str(val.get("entry_case") or "").strip() or None,
                        "what_would_make_me_buy": str(
                            val.get("what_would_make_me_buy") or ""
                        ).strip() or None,
                    }

            drafts = data.get("thesis_drafts")
            if isinstance(drafts, dict):
                for sym in symbols:
                    d = drafts.get(sym) or drafts.get(sym.upper()) or drafts.get(sym.lower())
                    if not isinstance(d, dict):
                        continue
                    text_val = str(d.get("thesis") or "").strip()
                    if not text_val:
                        continue
                    raw_f = d.get("falsifiers")
                    falsifiers = [
                        str(f).strip() for f in raw_f if str(f).strip()
                    ] if isinstance(raw_f, list) else []
                    thesis_drafts[sym] = {"thesis": text_val, "falsifiers": falsifiers}
            needs = data.get("info_needs")
            if isinstance(needs, list):
                for item in needs:
                    if isinstance(item, dict) and item.get("description"):
                        info_needs.append(
                            {
                                "description": str(item["description"]),
                                "symbols": item.get("symbols")
                                if isinstance(item.get("symbols"), list)
                                else [],
                                "priority": str(item.get("priority") or "medium"),
                            }
                        )
                    elif isinstance(item, str) and item.strip():
                        info_needs.append(
                            {"description": item.strip(), "symbols": [], "priority": "medium"}
                        )

        cleaned = text.strip()
        for sym in symbols + tracked_symbols:
            research_by_symbol.setdefault(sym, synthesis or cleaned[:1500])
        return {
            "synthesis": synthesis,
            "research_by_symbol": research_by_symbol,
            "thesis_by_symbol": thesis_by_symbol,
            "tracked_by_symbol": tracked_by_symbol,
            "thesis_drafts": thesis_drafts,
            "info_needs": info_needs,
        }

    def _claude_scenario_plan(
        self,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        lens_answers: dict[str, dict[str, Any]],
        combined_answers: dict[str, Any],
        *,
        round_idx: int,
        log: list[str],
    ) -> dict[str, Any]:
        log.append(f"claude: scenario plan round {round_idx}")
        lens_summary = self._summarize_lens_answers(context, lens_answers)
        combined_summary = self._summarize_answers(context, combined_answers)
        # Keep research tiny for scenario planning.
        research_brief = {s: _clip(t, 160) for s, t in research_by_symbol.items()}
        prompt = (
            "Tradai RESEARCHER. Four Jev lenses already ran.\n"
            f"Scenario round {round_idx}/{max_scenario_rounds()}. Prefer done=true to save calls.\n"
            "ONLY JSON: "
            '{"done":true|false,"scenario_label":"","hypothesis":"","focus_symbols":[],'
            '"question_hint":"","state_addendum":{}}\n'
            f"RESEARCH:{json.dumps(research_brief, ensure_ascii=False, separators=(',', ':'))}\n"
            f"LENSES:{json.dumps(lens_summary, ensure_ascii=False, separators=(',', ':'))}\n"
            f"COMBINED:{json.dumps(combined_summary, ensure_ascii=False, separators=(',', ':'))}"
        )
        text = self.claude.analyze(prompt)
        data = self._parse_json_object(text)
        if not isinstance(data, dict):
            return {"done": True}
        done = data.get("done")
        if done is None:
            # If they provided a hypothesis, treat as wanting another round.
            done = not bool(data.get("hypothesis") or data.get("question_hint"))
        return {
            "done": bool(done),
            "scenario_label": str(data.get("scenario_label") or f"scenario-{round_idx}"),
            "hypothesis": str(data.get("hypothesis") or ""),
            "focus_symbols": data.get("focus_symbols")
            if isinstance(data.get("focus_symbols"), list)
            else [],
            "question_hint": str(data.get("question_hint") or data.get("hypothesis") or ""),
            "state_addendum": data.get("state_addendum")
            if isinstance(data.get("state_addendum"), dict)
            else {},
        }

    def _summarize_lens_answers(
        self,
        context: dict[str, Any],
        lens_answers: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for lens, answers in lens_answers.items():
            out[lens] = self._summarize_answers(context, answers)
        return out

    def _summarize_answers(
        self,
        context: dict[str, Any],
        answers: dict[str, Any],
    ) -> dict[str, str]:
        summary: dict[str, str] = {}
        for h in self._subjects(context):
            iid = int(h["instrument_id"])
            sym = h["symbol"]
            parts = []
            for horizon in horizons_for(h.get("book", "portfolio")):
                qid = f"{iid}_{horizon}"
                action, _ = self._extract_action(answers.get(qid), h.get("book", "portfolio"))
                parts.append(f"{horizon}:{action}")
            summary[sym] = ", ".join(parts)
        return summary

    def _instrument_keys(
        self, context: dict[str, Any], *, lens: str | None = None
    ) -> list[tuple[str, str, int, str]]:
        """One Jev question per subject x horizon, tagged with its book (0019)."""
        keys: list[tuple[str, str, int, str]] = []
        for h in self._subjects(context, lens=lens):
            for horizon in horizons_for(h.get("book", "portfolio")):
                qid = f"{h['instrument_id']}_{horizon}"
                keys.append((qid, h["symbol"], int(h["instrument_id"]), h.get("book", "portfolio")))
        return keys

    def _mandates(self, context: dict[str, Any]) -> dict[str, str]:
        """The text each book is actually judged by (0019), as recorded on the context."""
        stored = context.get("mandates")
        if isinstance(stored, dict) and stored:
            return stored
        profiles = context.get("profiles") or {}
        return {book: doctrine.compose_mandate(profiles, book) for book in doctrine.BOOKS}

    def _lens_state(
        self,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        lens: str,
        *,
        addendum: dict[str, Any] | None = None,
        scenario: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        holdings_out: list[dict[str, Any]] = []
        for h in context["holdings"]:
            sym = h["symbol"]
            tinfo = (context.get("thesis_by_symbol") or {}).get(sym) or {}
            base: dict[str, Any] = {
                "symbol": sym,
                "qty": h["quantity"],
                "wgt": h["weight_pct"],
                "wgt_cost": h.get("weight_cost_pct"),
                "held_days": h.get("held_days"),
                "cost_eur": h["cost_eur"],
                "mv_eur": h["market_value_eur"],
                "pnl_pct": h["pnl_pct"],
                "note": _clip(research_by_symbol.get(sym, "")),
            }
            if lens in ("thesis", "combined"):
                thesis = h.get("thesis") or {}
                base["thesis"] = thesis.get("text")
                base["falsifiers"] = thesis.get("falsifiers")
                base["has_approved_thesis"] = bool(thesis)
                base["thesis_status"] = tinfo.get("thesis_status")
                base["thesis_evidence"] = tinfo.get("evidence")
                base["better_use_target"] = tinfo.get("better_use_target")
            if lens == "combined":
                base["px"] = (h["quote"] or {}).get("price") if h.get("quote") else None
                base["avg_cost"] = h["avg_cost"]
            if lens in ("news", "combined"):
                base["news"] = [
                    n.get("title") for n in (h.get("news") or [])[:NEWS_TITLES_MAX] if n.get("title")
                ]
            if lens in ("technicals", "combined"):
                base["tech"] = self._compact_technicals(h.get("technicals"))
            holdings_out.append(base)

        # 0019: tracked names carry no position fields at all rather than zeroed ones — a
        # qty of 0 and a cost of 0 would read as facts about a position that doesn't exist.
        tracked_out: list[dict[str, Any]] = []
        for t in (context.get("tracked") or []):
            sym = t["symbol"]
            entry = (context.get("tracked_by_symbol") or {}).get(sym) or {}
            row: dict[str, Any] = {
                "symbol": sym,
                "name": t.get("name"),
                "owned": False,
                "tracked_since": t.get("added_at"),
                "operator_note": t.get("note"),
                "note": _clip(research_by_symbol.get(sym, "")),
            }
            if lens in ("thesis", "combined"):
                row["entry_case"] = entry.get("entry_case")
                row["what_would_make_me_buy"] = entry.get("what_would_make_me_buy")
            if lens in ("technicals", "combined"):
                row["tech"] = self._compact_technicals(t.get("technicals"))
            if lens == "combined":
                row["px"] = (t["quote"] or {}).get("price") if t.get("quote") else None
                row["px_eur"] = t.get("price_eur")
            tracked_out.append(row)

        state: dict[str, Any] = {
            "lens": lens,
            "mandate": context.get("mandate"),
            "profiles": context.get("profiles") or {},
            "portfolio_mv_eur": context.get("portfolio_market_value_eur"),
            "cash_eur": context.get("cash_eur"),
            "realized_gains_ytd_eur": context.get("realized_gains_ytd_eur"),
            "calendar_year": context.get("calendar_year"),
            "holdings": holdings_out,
            # The news lens has no evidence for tracked names, so they are not asked about
            # there and are omitted from its state too.
            "tracked": tracked_out if lens != "news" else [],
        }
        if scenario:
            state["scenario"] = {
                "label": scenario.get("scenario_label"),
                "hypothesis": _clip(str(scenario.get("hypothesis") or ""), 200),
                "focus": scenario.get("focus_symbols"),
            }
        if addendum:
            state["addendum"] = addendum
        return state

    def _run_lens(
        self,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        *,
        lens: str,
        log: list[str],
        conversations: dict[int, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        log.append(f"jev: lens={lens}")
        state = self._lens_state(context, research_by_symbol, lens)
        subjects = self._subjects(context, lens=lens)
        keys = self._instrument_keys(context, lens=lens)
        at = utc_now()
        for h in subjects:
            iid = int(h["instrument_id"])
            conversations[iid].append(
                {
                    "role": "researcher",
                    "kind": "lens_request",
                    "lens": lens,
                    "at": at,
                    "summary": (
                        f"Built Jev {lens} lens request for {h['symbol']} "
                        f"with research notes and lens-scoped market features."
                    ),
                    "research_excerpt": _clip(research_by_symbol.get(h["symbol"]), 200),
                }
            )

        result = self.jev.choose_actions(
            state, keys, lens=lens, mandates=self._mandates(context)
        )
        answers = result.get("answers") or {}
        log.append(f"jev: lens={lens} answers={len(answers)}")
        at2 = utc_now()
        for h in subjects:
            iid = int(h["instrument_id"])
            per_h: dict[str, Any] = {}
            for horizon in horizons_for(h.get("book", "portfolio")):
                qid = f"{iid}_{horizon}"
                if qid in answers:
                    action, payload = self._extract_action(
                        answers[qid], h.get("book", "portfolio")
                    )
                    per_h[horizon] = {"action": action, "payload": payload}
            conversations[iid].append(
                {
                    "role": "decider",
                    "kind": "lens_answer",
                    "lens": lens,
                    "at": at2,
                    "answers": per_h,
                }
            )
        return result

    def _run_scenario_round(
        self,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        *,
        scenario: dict[str, Any],
        round_idx: int,
        log: list[str],
        conversations: dict[int, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        log.append(
            f"jev: scenario round={round_idx} label={scenario.get('scenario_label')}"
        )
        state = self._lens_state(
            context,
            research_by_symbol,
            "combined",
            addendum=scenario.get("state_addendum") or {},
            scenario=scenario,
        )
        keys = self._instrument_keys(context, lens="combined")
        subjects = self._subjects(context, lens="combined")
        focus = {str(s).upper() for s in (scenario.get("focus_symbols") or [])}
        at = utc_now()
        for h in subjects:
            iid = int(h["instrument_id"])
            if focus and h["symbol"].upper() not in focus:
                # Still record that a scenario ran book-wide; mark non-focus lightly.
                conversations[iid].append(
                    {
                        "role": "researcher",
                        "kind": "scenario_request",
                        "round": round_idx,
                        "at": at,
                        "summary": (
                            f"Scenario '{scenario.get('scenario_label')}' focused elsewhere; "
                            f"{h['symbol']} still included in combined re-ask."
                        ),
                        "hypothesis": scenario.get("hypothesis"),
                    }
                )
            else:
                conversations[iid].append(
                    {
                        "role": "researcher",
                        "kind": "scenario_request",
                        "round": round_idx,
                        "at": at,
                        "summary": f"Scenario '{scenario.get('scenario_label')}'",
                        "hypothesis": scenario.get("hypothesis"),
                        "question_hint": scenario.get("question_hint"),
                    }
                )

        result = self.jev.choose_actions(
            state,
            keys,
            lens="combined",
            extra_instructions=str(scenario.get("question_hint") or scenario.get("hypothesis") or ""),
            mandates=self._mandates(context),
        )
        answers = result.get("answers") or {}
        at2 = utc_now()
        for h in subjects:
            iid = int(h["instrument_id"])
            per_h: dict[str, Any] = {}
            for horizon in horizons_for(h.get("book", "portfolio")):
                qid = f"{iid}_{horizon}"
                if qid in answers:
                    action, payload = self._extract_action(
                        answers[qid], h.get("book", "portfolio")
                    )
                    per_h[horizon] = {"action": action, "payload": payload}
            conversations[iid].append(
                {
                    "role": "decider",
                    "kind": "scenario_answer",
                    "round": round_idx,
                    "lens": "combined",
                    "at": at2,
                    "answers": per_h,
                }
            )
        return result

    def _persist_recommendations(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        lens_answers: dict[str, dict[str, Any]],
        combined_answers: dict[str, Any],
        conversations: dict[int, list[dict[str, Any]]],
        log: list[str] | None = None,
    ) -> int:
        """
        Write what Claude and Jev actually decided. Nothing here overrides the action — the
        `reason` (parsed from Jev's composite choice) and `loss_gate` (a same-year-offset /
        no-recovery label) are recorded for transparency on the log page, not enforced.
        """
        now = utc_now()
        written = 0

        for h in self._subjects(context):
            iid = int(h["instrument_id"])
            symbol = h["symbol"]
            book = h.get("book", "portfolio")
            # A tracked name is only asked the three lenses it has evidence for.
            lenses = TRACKER_LENSES if book == "tracker" else LENSES
            rationale = research_by_symbol.get(symbol, "")
            conversation = conversations.get(iid) or []
            price_at_rec = (h["quote"] or {}).get("price") if h.get("quote") else None

            # The 24m verdict feeds the no_recovery_24m label on the 6m/12m rows, so collect
            # every horizon's decision before writing any of them.
            book_horizons = horizons_for(book)
            decisions: dict[str, dict[str, Any]] = {}
            supporting_by_horizon: dict[str, dict[str, Any]] = {}
            for horizon in book_horizons:
                qid = f"{iid}_{horizon}"
                supporting: dict[str, Any] = {}
                for lens in lenses:
                    d = self._extract_decision(
                        (lens_answers.get(lens) or {}).get(qid), book=book
                    )
                    supporting[lens] = {
                        "action": d["action"],
                        "reason": d["reason"],
                        "confidence": d["confidence"],
                        "payload": d["payload"],
                    }

                if combined_answers.get(qid) is not None:
                    d = self._extract_decision(combined_answers[qid], book=book)
                    supporting["combined"] = {
                        "action": d["action"],
                        "reason": d["reason"],
                        "confidence": d["confidence"],
                        "payload": d["payload"],
                    }
                else:
                    c = supporting.get("combined") or {}
                    d = {
                        "action": c.get("action") or "watch",
                        "reason": c.get("reason") or "insufficient_evidence",
                        "confidence": c.get("confidence"),
                        "payload": c.get("payload") or {"partial": True},
                    }
                decisions[horizon] = d
                supporting_by_horizon[horizon] = supporting

            horizon_choices = {hz: d["action"] for hz, d in decisions.items()}

            for horizon in book_horizons:
                d = decisions[horizon]
                loss_gate = None
                pair_symbol = None
                # Only a holding can be sold, so only a holding has a loss to gate or a
                # pair to name. Tracker rows leave both null (0019).
                if d["action"] == "sell" and book == "portfolio":
                    loss_gate = doctrine.label_loss_gate(
                        pnl_pct=h.get("pnl_pct"),
                        realized_gains_ytd_eur=context.get("realized_gains_ytd_eur"),
                        horizon_choices=horizon_choices,
                    )
                    tinfo = (context.get("thesis_by_symbol") or {}).get(symbol) or {}
                    pair_symbol = tinfo.get("better_use_target")

                conn.execute(
                    """
                    INSERT INTO recommendations
                        (agent_run_id, instrument_id, book, action, horizon, reason, loss_gate,
                         pair_symbol, confidence, suppressed, suppressed_reason,
                         proposed_action, price_at_rec, rationale,
                         jev_payload_json, jev_lenses_json, conversation_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_run_id, instrument_id, horizon) DO UPDATE SET
                        book = excluded.book,
                        action = excluded.action,
                        reason = excluded.reason,
                        loss_gate = excluded.loss_gate,
                        pair_symbol = excluded.pair_symbol,
                        confidence = excluded.confidence,
                        price_at_rec = excluded.price_at_rec,
                        rationale = excluded.rationale,
                        jev_payload_json = excluded.jev_payload_json,
                        jev_lenses_json = excluded.jev_lenses_json,
                        conversation_json = excluded.conversation_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        run_id,
                        iid,
                        book,
                        d["action"],
                        horizon,
                        d["reason"],
                        loss_gate,
                        pair_symbol,
                        d["confidence"],
                        price_at_rec,
                        rationale,
                        json.dumps(d["payload"]),
                        json.dumps(supporting_by_horizon[horizon]),
                        json.dumps(conversation),
                        now,
                        now,
                    ),
                )
                written += 1

        conn.commit()
        if written:
            self._raise_alerts_for_run(conn, run_id)
        return written

    def _persist_partial_from_claude(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        conversations: dict[int, list[dict[str, Any]]],
        jev_error: str,
    ) -> int:
        now = utc_now()
        written = 0
        for h in self._subjects(context):
            iid = int(h["instrument_id"])
            book = h.get("book", "portfolio")
            rationale = research_by_symbol.get(h["symbol"], "")
            conversation = conversations.get(iid) or []
            conversation = list(conversation) + [
                {
                    "role": "system",
                    "kind": "error",
                    "at": now,
                    "summary": f"Jev failed: {jev_error}",
                }
            ]
            for horizon in horizons_for(book):
                payload = {
                    "partial": True,
                    "jev_error": jev_error,
                    "action_fallback": "watch",
                    "confidence": None,
                }
                conn.execute(
                    """
                    INSERT INTO recommendations
                        (agent_run_id, instrument_id, book, action, horizon, reason, suppressed,
                         rationale, jev_payload_json, jev_lenses_json, conversation_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, 'watch', ?, 'insufficient_evidence', 0, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_run_id, instrument_id, horizon) DO UPDATE SET
                        book = excluded.book,
                        action = excluded.action,
                        rationale = excluded.rationale,
                        jev_payload_json = excluded.jev_payload_json,
                        jev_lenses_json = excluded.jev_lenses_json,
                        conversation_json = excluded.conversation_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        run_id,
                        iid,
                        book,
                        horizon,
                        rationale,
                        json.dumps(payload),
                        json.dumps({"error": jev_error}),
                        json.dumps(conversation),
                        now,
                        now,
                    ),
                )
                written += 1
        conn.commit()
        # Partial Jev failure stores watch fallbacks — policy skips hold/watch; still run for safety.
        if written:
            self._raise_alerts_for_run(conn, run_id)
        return written

    def _raise_alerts_for_run(self, conn: sqlite3.Connection, run_id: int) -> int:
        """
        Alert on a buy or sell (any horizon) — same rule as the original policy (0010) — plus
        a thesis reported broken, which is worth a look regardless of what action followed.
        """
        rows = conn.execute(
            "SELECT id, action FROM recommendations WHERE agent_run_id = ? AND action IN ('buy', 'sell')",
            (run_id,),
        ).fetchall()

        now = utc_now()
        raised = 0
        for row in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO alerts (recommendation_id, unread, severity, raised_at)
                VALUES (?, 1, 'action', ?)
                """,
                (int(row["id"]), now),
            )
            raised += int(cur.rowcount or 0)

        raised += self._raise_thesis_break_alerts(conn, run_id, now)
        conn.commit()
        if raised:
            print(f"[tradai-worker] alerts raised={raised} for run_id={run_id}", flush=True)
        return raised

    def _raise_thesis_break_alerts(
        self, conn: sqlite3.Connection, run_id: int, now: str
    ) -> int:
        """
        A thesis reported broken is worth telling the operator even when the sell that would
        follow was gated. Anchored to the 12m row so it reuses the Alert->Recommendation
        relation (domain.md) rather than inventing a second alert subject.
        """
        research = conn.execute(
            "SELECT research_json FROM agent_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if research is None or not research["research_json"]:
            return 0
        try:
            payload = json.loads(research["research_json"])
        except json.JSONDecodeError:
            return 0

        by_symbol = payload.get("thesis_by_symbol") or {}
        broken = [
            sym for sym, info in by_symbol.items()
            if isinstance(info, dict) and info.get("thesis_status") == "broken"
        ]
        if not broken:
            return 0

        raised = 0
        for symbol in broken:
            row = conn.execute(
                """
                SELECT r.id FROM recommendations r
                INNER JOIN instruments i ON i.id = r.instrument_id
                WHERE r.agent_run_id = ? AND i.symbol = ? AND r.horizon = '12m'
                LIMIT 1
                """,
                (run_id, symbol),
            ).fetchone()
            if row is None:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO alerts (recommendation_id, unread, severity, raised_at)
                VALUES (?, 1, 'thesis', ?)
                """,
                (int(row["id"]), now),
            )
            if int(cur.rowcount or 0) == 0:
                # A sell alert already claimed this row; promote it — a broken thesis is
                # the more informative label.
                conn.execute(
                    "UPDATE alerts SET severity = 'thesis' WHERE recommendation_id = ? AND severity = 'sell'",
                    (int(row["id"]),),
                )
            raised += int(cur.rowcount or 0)
        return raised

    def _extract_decision(self, ans: Any, book: str = "portfolio") -> dict[str, Any]:
        """
        Unpack one Jev answer into action + reason + confidence.

        Jev answers with a composite choice (`sell_thesis_broken`, not `sell`), so the reason
        travels with the action and cannot be omitted. A bare legacy action is still accepted
        but carries no reason, which the doctrine gate then refuses to act on.
        """
        payload: dict[str, Any]
        if isinstance(ans, dict):
            payload = dict(ans)
        elif ans is None:
            payload = {"missing": True}
        else:
            payload = {"raw": ans}

        confidence = None
        raw_conf = payload.get("confidence")
        if isinstance(raw_conf, (int, float)):
            confidence = float(raw_conf)

        choice = None
        if isinstance(ans, dict):
            choice = ans.get("choice") or ans.get("answer") or ans.get("value")
            if not isinstance(choice, str):
                for key in ("result", "data"):
                    nested = ans.get(key)
                    if isinstance(nested, dict):
                        cand = nested.get("choice")
                        if isinstance(cand, str):
                            choice = cand
                            if confidence is None and isinstance(nested.get("confidence"), (int, float)):
                                confidence = float(nested["confidence"])
                            break

        split = doctrine.split_choice(choice, book)
        if split is not None:
            action, reason = split
        elif isinstance(choice, str) and choice.strip().lower() in ACTIONS:
            # Legacy bare action — no reason attached, so the gate will not let it act.
            action, reason = choice.strip().lower(), "legacy"
        else:
            action, reason = "watch", "insufficient_evidence"

        return {"action": action, "reason": reason, "confidence": confidence, "payload": payload}

    def _extract_action(self, ans: Any, book: str = "portfolio") -> tuple[str, dict[str, Any]]:
        d = self._extract_decision(ans, book)
        return d["action"], d["payload"]

    def _parse_json_object(self, text: str) -> Any:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def _set_status(self, conn: sqlite3.Connection, run_id: int, status: str, log_text: str | None = None) -> None:
        if log_text is not None:
            conn.execute(
                "UPDATE agent_runs SET status = ?, log_text = ? WHERE id = ?",
                (status, log_text, run_id),
            )
        else:
            conn.execute("UPDATE agent_runs SET status = ? WHERE id = ?", (status, run_id))
        conn.commit()

    def _finish(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        *,
        status: str,
        log: list[str],
        error: str | None = None,
        models_extra: dict[str, Any] | None = None,
    ) -> None:
        models = {
            "claude": self.claude.status(),
            "jev": self.jev.status(),
            "max_scenario_rounds": max_scenario_rounds(),
            "advisory_interval_seconds": advisory_interval_seconds(),
            "pipeline": "5b-researcher-decider",
        }
        if models_extra:
            models.update(models_extra)
        conn.execute(
            """
            UPDATE agent_runs
            SET status = ?, finished_at = ?, log_text = ?, error_text = ?, model_refs_json = ?
            WHERE id = ?
            """,
            (status, utc_now(), "\n".join(log), error, json.dumps(models), run_id),
        )
        conn.commit()

    def _run_payload(self, conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "run not found", "run_id": run_id}
        rec_count = conn.execute(
            "SELECT COUNT(*) AS c FROM recommendations WHERE agent_run_id = ?",
            (run_id,),
        ).fetchone()["c"]
        info_needs = None
        if row["info_needs_json"]:
            try:
                info_needs = json.loads(row["info_needs_json"])
            except json.JSONDecodeError:
                info_needs = None
        return {
            "ok": row["status"] in ("succeeded", "partial"),
            "run_id": run_id,
            "status": row["status"],
            "trigger": row["trigger_kind"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error": row["error_text"],
            "log": row["log_text"],
            "recommendation_count": int(rec_count),
            "info_needs": info_needs,
            "models": json.loads(row["model_refs_json"] or "{}"),
        }
