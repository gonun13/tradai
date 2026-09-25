from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import digest
import doctrine
import materiality
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
# Default 0 = lens pass only (saves Claude scenario + extra Jev calls). Set 3 for full 0009 loop.
DEFAULT_SCENARIO_ROUNDS = 0
DEFAULT_ADVISORY_INTERVAL_SECONDS = 86400
RESEARCH_MAX_CHARS = 900
# 0027: the four evidence lenses run first; `combined` then weighs their verdicts.
EVIDENCE_LENSES = tuple(lens for lens in LENSES if lens != "combined")
LAYER_NOTE_MAX = 200
# Prior runs searched for a subject's last real decision.
PRIOR_RUN_SCAN = 60
# 0026 regional comparison proxies, cached by the history refresh.
BENCHMARK_BY_REGION = {"us": "SPY", "eu": "EXSA.DE"}

# 0027: static, so it replaces the Claude Code system prompt instead of riding in every
# message. The dynamic parts (profiles, book, subjects) go in the message itself.
RESEARCH_SYSTEM_PROMPT = (
    "You are the RESEARCHER for Tradai, a personal investment advisory tool. Advisory only: "
    "a separate decider (Jev) chooses every action from your notes. You have no tools — "
    "reason only from the data in the message.\n"
    "SELL DOCTRINE (0013): the only valid reasons to sell are (a) the recorded investment "
    "thesis is broken, or (b) the capital has a specific better named use. Price action, "
    "momentum, moving averages, drawdown depth and concentration are NEVER reasons to sell. "
    "A position being down is not a reason to sell it.\n"
    "DATA: each subject has layer cards — historical (long-run returns, drawdowns, volatility, "
    "excess return vs a regional proxy), fundamentals (metrics as the source reports them, "
    "coverage, and for holdings the recorded thesis and falsifiers), technicals (RSI, SMA "
    "distance, 1m/3m/6m returns), news (newest first), and position. Keys ending _pct are "
    "percentages; missing keys mean no data — say so rather than guess. `why_now` lists what "
    "changed since the prior decision; `prior` is that decision and your notes then.\n"
    "FOR EVERY SUBJECT: notes — one note per layer (historical, fundamentals, technicals, "
    "news), at most 20 words each, stating what that layer says for this subject. With a "
    "prior, lead with what changed; write \"unchanged\" when nothing did.\n"
    "HOLDINGS (book=portfolio), in by_symbol: thesis_status — 'broken' only when a named "
    "falsifier actually tripped on the facts, citing the fact in `evidence`; 'weakening' when "
    "under pressure; else 'intact'. A price decline alone is NOT a broken thesis. evidence — "
    "the concrete fact, or \"no change\". better_use_target — a UNIVERSE symbol where this "
    "capital would clearly do more work, else null; never invent tickers. research — at most "
    "2 sentences over 6m/12m/24m.\n"
    "TRACKED (book=tracker, not owned), in tracked_by_symbol: entry_case — at most 2 sentences "
    "on why it is worth owning against the INVESTOR PROFILE; say plainly if there is none. "
    "what_would_make_me_buy — a specific price, result or event, never \"further research\". "
    "research — at most 2 sentences over 1m/3m/6m (entry timing).\n"
    "thesis_drafts: for each NEEDS_THESIS symbol, a thesis and 2-4 falsifiers, each a "
    "specific checkable condition. info_needs: data gaps that would most improve the next run."
)

# 0028: runs after Jev has decided. Claude explains the choice to the operator; it never
# chooses, so nothing it writes here can change an action.
EXPLAIN_SYSTEM_PROMPT = (
    "You are the EXPLAINER for Tradai, a personal investment advisory tool. A separate decider "
    "(Jev) has already chosen every action; your job is to tell the operator, in plain language, "
    "why. Never propose a different action or suggest the action will change. You have no tools "
    "— use only the data in the message; do not invent figures, events or tickers.\n"
    "Each SUBJECT carries the decider's combined choice per horizon (action, reason, confidence, "
    "and its top choice probabilities), the four lens verdicts (historical, fundamentals, "
    "technicals, news), the researcher's layer notes and research, the thesis or entry case, "
    "and the position. MARKET holds regional proxy statistics (SPY for US, EXSA.DE for Europe); "
    "HEADLINES is the book-wide news tape.\n"
    "by_symbol.<SYM>.explanation — at most 90 words of plain prose: why the combined lens landed "
    "on these actions (name the horizons when they differ), which specific evidence carried it, "
    "how sure the decider was, and how the market backdrop bears on it.\n"
    "by_symbol.<SYM>.tension — one sentence when a lens verdict, the researcher's notes, or the "
    "decider's own probabilities point meaningfully the other way; otherwise null.\n"
    "market_read — 2-3 sentences on the overall market mood, drawn only from MARKET and HEADLINES.\n"
    "SELL DOCTRINE (0013): the operator sells only on a broken thesis or a specific better use. "
    "Price action alone is never a reason to sell, so never frame a hold on a losing position as "
    "a mistake for that reason."
)

EXPLAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "market_read": {"type": "string"},
        "by_symbol": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "explanation": {"type": "string"},
                    "tension": {"type": ["string", "null"]},
                },
                "required": ["explanation", "tension"],
            },
        },
    },
    "required": ["market_read", "by_symbol"],
}
EXPLANATION_MAX = 900

_NOTE = {"type": "string"}
_NOTES_SCHEMA = {
    "type": "object",
    "properties": {lens: _NOTE for lens in ("historical", "fundamentals", "technicals", "news")},
    "required": ["historical", "fundamentals", "technicals", "news"],
}
RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "synthesis": {"type": "string"},
        "by_symbol": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "notes": _NOTES_SCHEMA,
                    "research": {"type": "string"},
                    "thesis_status": {"type": "string", "enum": ["intact", "weakening", "broken"]},
                    "evidence": {"type": "string"},
                    "better_use_target": {"type": ["string", "null"]},
                },
                "required": ["notes", "research", "thesis_status", "evidence"],
            },
        },
        "tracked_by_symbol": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "notes": _NOTES_SCHEMA,
                    "research": {"type": "string"},
                    "entry_case": {"type": "string"},
                    "what_would_make_me_buy": {"type": "string"},
                },
                "required": ["notes", "research", "entry_case", "what_would_make_me_buy"],
            },
        },
        "thesis_drafts": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "thesis": {"type": "string"},
                    "falsifiers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["thesis", "falsifiers"],
            },
        },
        "info_needs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["description"],
            },
        },
    },
    "required": ["synthesis", "by_symbol", "tracked_by_symbol", "thesis_drafts", "info_needs"],
}


def _money_round(value: float, digits: int = 2) -> float:
    quantum = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(date_str: Any, *, now: datetime) -> int | None:
    if not date_str:
        return None
    try:
        start = datetime.fromisoformat(str(date_str)[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (now - start).days)


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
                # 0027: set on a row copied forward unchanged; points at the deciding run.
                "carried_from_run_id": "INTEGER",
                # 0028: Claude's plain-language why, as JSON {text, tension, market_read}.
                "explanation": "TEXT",
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

    def create_run(
        self,
        trigger_kind: str = "manual",
        target_symbol: str | None = None,
        *,
        force: bool = False,
    ) -> int:
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
            # 0027: a forced run spends tokens on purpose, so nothing carries forward.
            if force:
                model_refs["force"] = True

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

    def _run_refs(self, conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT model_refs_json FROM agent_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row and row["model_refs_json"]:
            try:
                refs = json.loads(row["model_refs_json"])
            except json.JSONDecodeError:
                return {}
            return refs if isinstance(refs, dict) else {}
        return {}

    def _get_target_symbol(self, conn: sqlite3.Connection, run_id: int) -> str | None:
        return self._run_refs(conn, run_id).get("target_symbol")

    def run(self, run_id: int) -> dict[str, Any]:
        conn = self.connect()
        log: list[str] = []
        budget: dict[str, Any] = {"jev_request_chars": {}}
        try:
            self._set_status(conn, run_id, "running", log_text="running")
            refs = self._run_refs(conn, run_id)
            target_symbol = refs.get("target_symbol")
            context = self._build_context(conn, target_symbol=target_symbol)

            # 0019: a tracker-only book is a legitimate state — you can be researching
            # names before owning any of them.
            if not (context["holdings"] or context["tracked"]):
                self._store_context(conn, run_id, context)
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
                f"total_display={context.get('portfolio_market_value_display')} "
                f"currency={context.get('display_currency')}"
            )

            # 0027: re-decide only what changed materially since its last real decision.
            priors = self._prior_decisions(conn, run_id, context)
            plan = self._materiality_plan(
                context,
                priors,
                forced=bool(refs.get("force")),
                targeted=bool(target_symbol),
            )
            decide_ids = [iid for iid, trig in plan.items() if trig]
            carried_ids = [iid for iid, trig in plan.items() if not trig]
            context["decide_ids"] = decide_ids
            context["materiality"] = {str(iid): trig for iid, trig in plan.items()}
            budget["decided"] = len(decide_ids)
            budget["carried"] = len(carried_ids)
            self._store_context(conn, run_id, context)
            for s_ in self._all_subjects(context):
                trig = plan.get(int(s_["instrument_id"])) or []
                log.append(
                    f"materiality {s_['symbol']}: "
                    + (", ".join(trig) if trig else "unchanged — carried")
                )
            log.append(f"materiality decided={len(decide_ids)} carried={len(carried_ids)}")

            if not decide_ids:
                self._write_research(conn, run_id, context, {}, [], "", priors)
                carried = self._carry_forward_all(conn, run_id, context, priors, carried_ids)
                log.append(f"no material changes — {carried} recommendation rows carried forward")
                self._finish(
                    conn, run_id, status="succeeded" if carried else "failed", log=log,
                    error=None if carried else "No recommendations written",
                    models_extra={"token_budget": budget},
                )
                return self._run_payload(conn, run_id)

            try:
                research_pack = self._claude_research(context, priors, log)
            except ClaudeCliError as exc:
                self._finish(
                    conn,
                    run_id,
                    status="failed",
                    log=log,
                    error=str(exc),
                    models_extra={"claude_error": str(exc), "token_budget": budget},
                )
                return self._run_payload(conn, run_id)
            budget["claude_research"] = self.claude.last_usage

            research_by_symbol = research_pack["research_by_symbol"]
            info_needs = research_pack["info_needs"]
            synthesis = research_pack.get("synthesis") or ""
            # Carried on context so every lens pass sees the same thesis findings and layer
            # notes without threading more arguments through every call site.
            context["thesis_by_symbol"] = research_pack.get("thesis_by_symbol") or {}
            context["tracked_by_symbol"] = research_pack.get("tracked_by_symbol") or {}
            context["layer_notes"] = research_pack.get("layer_notes") or {}
            context["synthesis"] = synthesis
            drafted = self._persist_thesis_drafts(
                conn, run_id, context, research_pack.get("thesis_drafts") or {}
            )
            if drafted:
                log.append(f"claude: thesis notes written={drafted}")

            self._write_research(
                conn, run_id, context, research_by_symbol, info_needs, synthesis, priors
            )
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
                        lens_answers=lens_answers,
                    )
                    lens_answers[lens] = result["answers"]
                    budget["jev_request_chars"][lens] = result.get("request_chars")

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
                        lens_answers=lens_answers,
                    )
                    budget["jev_request_chars"][f"scenario_{round_idx}"] = result.get("request_chars")
                    # Scenario rounds refresh the canonical combined answers.
                    for qid, ans in (result.get("answers") or {}).items():
                        combined_answers[qid] = ans
                    lens_answers["combined"] = combined_answers
                    log.append(f"jev: scenario round {round_idx} applied to combined lens")
                else:
                    if scenario_cap:
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
                written += self._carry_forward_all(conn, run_id, context, priors, carried_ids)
                self._finish(
                    conn,
                    run_id,
                    status="partial" if written else "failed",
                    log=log,
                    error=str(exc),
                    models_extra={"jev_error": str(exc), "token_budget": budget},
                )
                return self._run_payload(conn, run_id)

            # 0028: Claude explains what Jev decided — after the decision, never feeding it.
            explained = self._claude_explain(
                context, research_by_symbol, lens_answers, combined_answers, log
            )
            budget["claude_explain"] = explained.get("usage")
            self._update_research(
                conn,
                run_id,
                {
                    "market_read": explained["market_read"],
                    "explanations": explained["by_symbol"],
                },
            )

            written = self._persist_recommendations(
                conn,
                run_id,
                context,
                research_by_symbol,
                lens_answers,
                combined_answers,
                conversations,
                log=log,
                explanations=explained,
            )
            carried = self._carry_forward_all(conn, run_id, context, priors, carried_ids)
            if carried:
                log.append(f"carried forward rows={carried}")
            status = "succeeded" if written else "failed"
            error = None if written else "No recommendations written"
            self._finish(
                conn, run_id, status=status, log=log, error=error,
                models_extra={"token_budget": budget},
            )
            return self._run_payload(conn, run_id)
        except Exception as exc:  # noqa: BLE001
            log.append(f"unhandled: {exc}")
            self._finish(
                conn, run_id, status="failed", log=log, error=str(exc),
                models_extra={"token_budget": budget},
            )
            return self._run_payload(conn, run_id)
        finally:
            conn.close()

    def _update_research(self, conn: sqlite3.Connection, run_id: int, fields: dict[str, Any]) -> None:
        row = conn.execute("SELECT research_json FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        try:
            research = json.loads((row["research_json"] if row else None) or "{}")
        except json.JSONDecodeError:
            research = {}
        if not isinstance(research, dict):
            research = {}
        research.update(fields)
        conn.execute(
            "UPDATE agent_runs SET research_json = ? WHERE id = ?", (json.dumps(research), run_id)
        )
        conn.commit()

    def _store_context(self, conn: sqlite3.Connection, run_id: int, context: dict[str, Any]) -> None:
        conn.execute(
            "UPDATE agent_runs SET context_json = ? WHERE id = ?",
            (json.dumps(context), run_id),
        )
        conn.commit()

    def _write_research(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        info_needs: list[dict[str, Any]],
        synthesis: str,
        priors: dict[int, dict[str, Any]],
    ) -> None:
        """
        Research for the decided subjects, plus the decision record (0027) for every subject:
        the cards a decision was made on. A carried subject keeps its deciding run's record,
        so the next comparison stays anchored there rather than drifting a day at a time.
        """
        records: dict[str, Any] = {}
        decide = set(context.get("decide_ids") or [])
        now = utc_now()
        mandate_hashes = (context.get("fingerprints") or {}).get("mandate") or {}
        book_fp = (context.get("fingerprints") or {}).get("book")
        for s_ in self._all_subjects(context):
            iid = int(s_["instrument_id"])
            book = s_.get("book", "portfolio")
            if iid in decide:
                records[str(iid)] = {
                    "schema": digest.LENS_SCHEMA,
                    "symbol": s_["symbol"],
                    "book": book,
                    "decided_run_id": run_id,
                    "decided_at": now,
                    "mandate_hash": mandate_hashes.get(book),
                    "book_fp": book_fp,
                    "cards": s_.get("cards") or {},
                    # Handed back to Claude as `prior` on the next changed run.
                    "notes": (context.get("layer_notes") or {}).get(s_["symbol"]),
                    "status": (
                        (context.get("thesis_by_symbol") or {}).get(s_["symbol"])
                        if book == "portfolio"
                        else (context.get("tracked_by_symbol") or {}).get(s_["symbol"])
                    ),
                }
            elif iid in priors:
                records[str(iid)] = priors[iid]["record"]
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
                        "layer_notes": context.get("layer_notes") or {},
                        "materiality": context.get("materiality") or {},
                        "cards": records,
                    }
                ),
                json.dumps(info_needs),
                run_id,
            ),
        )
        conn.commit()

    def _build_context(self, conn: sqlite3.Connection, target_symbol: str | None = None) -> dict[str, Any]:
        display_currency = self._display_currency(conn)
        rows = conn.execute(
            """
            SELECT h.id AS holding_id, h.quantity, h.avg_cost, h.total_cost, h.notes,
                   h.first_trade_date, h.open_lot_count, h.realized_pnl_native,
                   i.id AS instrument_id, i.isin, i.symbol, i.mic, i.currency, i.name, i.kind, i.region,
                   q.price AS quote_price, q.currency AS quote_currency, q.as_of AS quote_as_of, q.source AS quote_source,
                   t.features_json, t.as_of AS tech_as_of,
                   f.payload_json AS fundamentals_json, f.source AS fundamentals_source,
                   f.as_of AS fundamentals_as_of, f.completeness_state AS fundamentals_state,
                   f.coverage_score AS fundamentals_score,
                   f.missing_fields_json AS fundamentals_missing
            FROM holdings h
            INNER JOIN instruments i ON i.id = h.instrument_id
            LEFT JOIN quotes q ON q.instrument_id = i.id
            LEFT JOIN technicals t ON t.instrument_id = i.id
            LEFT JOIN fundamentals f ON f.instrument_id = i.id
            ORDER BY i.symbol COLLATE NOCASE ASC
            """
        ).fetchall()

        holdings: list[dict[str, Any]] = []
        total_display = 0.0
        total_cost_display = 0.0
        market_complete = bool(rows)
        cost_complete = bool(rows)

        for row in rows:
            currency = (row["currency"] or "EUR").upper()
            qty = float(row["quantity"] or 0)
            total_cost = float(row["total_cost"] or 0)
            fx = self._fx_rate(conn, currency, display_currency)
            cost_display = total_cost * fx if fx is not None else None
            if cost_display is None:
                cost_complete = False
            else:
                total_cost_display += cost_display

            quote_price = float(row["quote_price"]) if row["quote_price"] is not None else None
            quote_currency = (row["quote_currency"] or currency).upper()
            quote_fx = self._fx_rate(conn, quote_currency, display_currency)
            mv_native = qty * quote_price if quote_price is not None else None
            mv_display = mv_native * quote_fx if mv_native is not None and quote_fx is not None else None
            if mv_display is None:
                market_complete = False
            else:
                total_display += mv_display

            pnl_display = (
                mv_display - cost_display
                if mv_display is not None and cost_display is not None
                else None
            )
            pnl_pct = (
                ((mv_display - cost_display) / cost_display * 100.0)
                if mv_display is not None and cost_display and cost_display > 0
                else None
            )

            features = None
            if row["features_json"]:
                try:
                    features = json.loads(row["features_json"])
                except json.JSONDecodeError:
                    features = None

            fundamentals = self._fundamentals_snapshot(row)

            news = conn.execute(
                """
                SELECT n.title, n.snippet, n.url, n.source_name, n.adapter_source, n.published_at
                FROM news_item_instruments nii
                INNER JOIN news_items n ON n.id = nii.news_item_id
                WHERE nii.instrument_id = ?
                ORDER BY COALESCE(n.published_at, n.fetched_at) DESC
                LIMIT 8
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
                    "cost_display": cost_display,
                    "market_value_display": mv_display,
                    "pnl_display": pnl_display,
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
                    "fundamentals": fundamentals,
                    "news": [
                        {
                            "title": n["title"],
                            "snippet": n["snippet"],
                            "url": n["url"],
                            "source": n["source_name"],
                            "adapter_source": n["adapter_source"],
                            "published_at": n["published_at"],
                        }
                        for n in news
                    ],
                    "notes": row["notes"],
                    # 0013: a 12-year-old dead position and a fresh conviction add both show a
                    # deep negative P&L. Nothing but the holding period separates them.
                    "first_trade_date": row["first_trade_date"],
                    "held_days": _days_since(row["first_trade_date"], now=datetime.now(timezone.utc)),
                    "open_lot_count": row["open_lot_count"],
                    "realized_pnl_native": row["realized_pnl_native"],
                    "thesis": self._approved_thesis(conn, int(row["instrument_id"])),
                    "weight_cost_pct": None,
                }
            )

        portfolio_market_value_display = total_display if market_complete else None
        portfolio_cost_display = total_cost_display if cost_complete else None
        if portfolio_market_value_display is not None and portfolio_market_value_display > 0:
            for h in holdings:
                if h["market_value_display"] is not None:
                    h["weight_pct"] = round(
                        h["market_value_display"] / portfolio_market_value_display * 100.0, 2
                    )
        # Market-value weight alone makes a position that already lost 95% look like a
        # rounding error — precisely the holdings that need attention. Cost weight restores it.
        if portfolio_cost_display is not None and portfolio_cost_display > 0:
            for h in holdings:
                if h["cost_display"] is not None:
                    h["weight_cost_pct"] = round(
                        h["cost_display"] / portfolio_cost_display * 100.0, 2
                    )

        cash_display = self._money_setting_display(
            conn, "cash_amount", "cash_currency", display_currency, legacy_key="cash_eur"
        )
        cash_configured = (
            self._setting_float(conn, "cash_amount") is not None
            or self._setting_float(conn, "cash_eur") is not None
        )
        realized = self._realized_gains_ytd_display(conn, display_currency)

        for h in holdings:
            h["book"] = "portfolio"
        tracked = self._tracked(conn, display_currency)

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
            portfolio_market_value_display = None
            portfolio_cost_display = None
            cash_display = None

        # 0027: every layer condensed into the cards the models (and materiality) read.
        market = self._attach_cards(conn, holdings + tracked)
        mandates = {b: doctrine.compose_mandate(profiles, b) for b in doctrine.BOOKS}

        return {
            "display_currency": display_currency,
            "portfolio_market_value_display": (
                _money_round(portfolio_market_value_display)
                if portfolio_market_value_display is not None else None
            ),
            "portfolio_cost_display": (
                _money_round(portfolio_cost_display) if portfolio_cost_display is not None else None
            ),
            # 0012: the operator holds a cash reserve; a buy does not require a sell.
            "cash_display": cash_display,
            "portfolio_total_display": (
                _money_round(portfolio_market_value_display + (cash_display or 0.0))
                if portfolio_market_value_display is not None
                and (not cash_configured or cash_display is not None)
                else None
            ),
            # 0013: drives the offset_same_year loss gate.
            "realized_gains_ytd_display": realized,
            "calendar_year": datetime.now(timezone.utc).year,
            "tracker": [{"symbol": t["symbol"], "name": t["name"]} for t in tracked],
            "profiles": profiles,
            # The text each book was *actually* judged by, defaults already substituted.
            # The log page shows this rather than `profiles`, which is null wherever the
            # operator hasn't written their half — and "no mandate" would be a lie, the
            # models were given one either way.
            "mandates": mandates,
            # 0027: what, besides a subject's own cards, would make a carried decision stale.
            "fingerprints": {
                "mandate": {b: materiality.text_hash(m) for b, m in mandates.items()},
                "book": materiality.text_hash([
                    self._setting_str(conn, "cash_amount"),
                    self._setting_str(conn, "cash_currency"),
                    self._setting_str(conn, "cash_eur"),
                ]),
            },
            # Kept as a string for the log page and the payload contract (spec/data.md):
            # the text actually applied to holdings.
            "mandate": doctrine.compose_mandate(profiles, "portfolio"),
            "holdings": holdings,
            "tracked": tracked,
            "market": market,
            "built_at": utc_now(),
        }

    def _all_subjects(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Every instrument in both books, each tagged with its `book` (0019)."""
        subjects = list(context.get("holdings") or [])
        subjects.extend(context.get("tracked") or [])
        return subjects

    def _subjects(
        self, context: dict[str, Any], *, lens: str | None = None
    ) -> list[dict[str, Any]]:
        """
        The instruments this run actually decides on. Holdings and tracked names go through
        the same lens pipeline, transcript, and persistence. Once the materiality plan has
        run (0027), carried subjects drop out — nothing is asked about them.
        """
        subjects = self._all_subjects(context)
        if "decide_ids" in context:
            decide = set(context.get("decide_ids") or [])
            subjects = [s_ for s_ in subjects if int(s_["instrument_id"]) in decide]
        return subjects

    def _attach_cards(
        self, conn: sqlite3.Connection, subjects: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Attach each subject's layer cards; return the market card of every region in use."""
        benchmarks: dict[str, list[tuple[str, float]]] = {}
        for region, symbol in BENCHMARK_BY_REGION.items():
            benchmarks[region] = self._history_points(
                conn,
                "SELECT id FROM historical_series WHERE series_kind = 'benchmark' "
                "AND benchmark_region = ? ORDER BY id DESC LIMIT 1",
                (region,),
            )
        for s_ in subjects:
            iid = int(s_["instrument_id"])
            history = self._history_points(
                conn,
                "SELECT id FROM historical_series WHERE series_kind = 'instrument' "
                "AND instrument_id = ? ORDER BY id DESC LIMIT 1",
                (iid,),
            )
            if not history:
                # No long-history series yet: the daily bars still give a 1y picture.
                history = self._bar_points(conn, iid)
            region = (s_.get("region") or "").lower()
            s_["cards"] = digest.build_cards(
                s_,
                history=history,
                benchmark=benchmarks.get(region),
                benchmark_symbol=BENCHMARK_BY_REGION.get(region),
            )
        # 0028: the regional proxies double as the market backdrop for the explanations.
        market: dict[str, Any] = {}
        for region in sorted({(s_.get("region") or "").lower() for s_ in subjects}):
            card = digest.historical_card(benchmarks.get(region) or [])
            if card:
                market[region] = {"bench": BENCHMARK_BY_REGION[region], **card}
        return market

    def _history_points(
        self, conn: sqlite3.Connection, series_sql: str, params: tuple[Any, ...]
    ) -> list[tuple[str, float]]:
        """0026 long-history points — read here only to be condensed into statistics."""
        try:
            row = conn.execute(series_sql, params).fetchone()
            if row is None:
                return []
            return [
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT point_date, adjusted_close FROM historical_points "
                    "WHERE series_id = ? ORDER BY point_date",
                    (int(row[0]),),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            return []

    def _bar_points(self, conn: sqlite3.Connection, instrument_id: int) -> list[tuple[str, float]]:
        try:
            return [
                (r[0], r[1])
                for r in conn.execute(
                    "SELECT bar_date, close FROM price_bars WHERE instrument_id = ? "
                    "AND close IS NOT NULL ORDER BY bar_date",
                    (instrument_id,),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            return []

    def _prior_decisions(
        self, conn: sqlite3.Connection, run_id: int, context: dict[str, Any]
    ) -> dict[int, dict[str, Any]]:
        """
        Each subject's last real decision: the stored decision record (0027) and the rows
        it produced. Legacy runs carry no record, so their subjects are simply re-decided.
        """
        wanted = {int(s_["instrument_id"]): s_.get("book", "portfolio") for s_ in self._all_subjects(context)}
        found: dict[int, dict[str, Any]] = {}
        runs = conn.execute(
            """
            SELECT id, research_json FROM agent_runs
            WHERE id < ? AND status IN ('succeeded', 'partial')
            ORDER BY id DESC LIMIT ?
            """,
            (run_id, PRIOR_RUN_SCAN),
        ).fetchall()
        for run in runs:
            if len(found) == len(wanted):
                break
            try:
                research = json.loads(run["research_json"] or "{}")
            except json.JSONDecodeError:
                continue
            records = research.get("cards") if isinstance(research, dict) else None
            if not isinstance(records, dict):
                continue
            for key, record in records.items():
                try:
                    iid = int(key)
                except ValueError:
                    continue
                if iid not in wanted or iid in found or not isinstance(record, dict):
                    continue
                rows = conn.execute(
                    "SELECT * FROM recommendations WHERE agent_run_id = ? AND instrument_id = ?",
                    (int(run["id"]), iid),
                ).fetchall()
                if not rows:
                    continue
                found[iid] = {"record": record, "rows": rows, "run_id": int(run["id"])}
        return found

    def _materiality_plan(
        self,
        context: dict[str, Any],
        priors: dict[int, dict[str, Any]],
        *,
        forced: bool,
        targeted: bool,
    ) -> dict[int, list[str]]:
        fps = context.get("fingerprints") or {}
        horizons_ok = lambda rows, book: {r["horizon"] for r in rows} == set(horizons_for(book))  # noqa: E731
        plan: dict[int, list[str]] = {}
        for s_ in self._all_subjects(context):
            iid = int(s_["instrument_id"])
            book = s_.get("book", "portfolio")
            prior = priors.get(iid)
            record = None
            if prior is not None and horizons_ok(prior["rows"], book) and (
                (prior["record"].get("book") or book) == book
            ):
                rows = prior["rows"]
                record = dict(prior["record"])
                record["price_at_rec"] = next(
                    (r["price_at_rec"] for r in rows if r["price_at_rec"] is not None), None
                )
                record["fallback"] = any(self._is_fallback(r) for r in rows)
            plan[iid] = materiality.triggers(
                book=book,
                cards=s_.get("cards") or {},
                mandate_hash=(fps.get("mandate") or {}).get(book) or "",
                book_fp=fps.get("book"),
                prior=record,
                forced=forced,
                targeted=targeted,
            )
        return plan

    def _is_fallback(self, row: sqlite3.Row) -> bool:
        try:
            payload = json.loads(row["jev_payload_json"] or "{}")
        except json.JSONDecodeError:
            return True
        return isinstance(payload, dict) and bool(payload.get("partial") or payload.get("jev_error"))

    def _carry_forward_all(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        context: dict[str, Any],
        priors: dict[int, dict[str, Any]],
        carried_ids: list[int],
    ) -> int:
        """
        Copy each unchanged subject's last rows into this run (0027). The action is exactly
        what Jev last decided; `carried_from_run_id` names the run that decided it. Carried
        rows never alert — the operator already saw that decision when it was made.
        """
        now = utc_now()
        written = 0
        for iid in carried_ids:
            prior = priors.get(iid)
            if prior is None:
                continue
            deciding_run = int(prior["record"].get("decided_run_id") or prior["run_id"])
            decided_at = str(prior["record"].get("decided_at") or "")[:10]
            for r in prior["rows"]:
                try:
                    conversation = json.loads(r["conversation_json"] or "[]")
                except json.JSONDecodeError:
                    conversation = []
                conversation = [
                    t for t in conversation if not (isinstance(t, dict) and t.get("kind") == "carried")
                ] + [
                    {
                        "role": "system",
                        "kind": "carried",
                        "at": now,
                        "summary": (
                            f"No material change since run #{deciding_run} ({decided_at}); "
                            "recommendation carried forward without a new Claude/Jev pass."
                        ),
                        "decided_run_id": deciding_run,
                    }
                ]
                conn.execute(
                    """
                    INSERT INTO recommendations
                        (agent_run_id, instrument_id, book, action, horizon, reason, loss_gate,
                         pair_symbol, confidence, suppressed, suppressed_reason,
                         proposed_action, price_at_rec, rationale,
                         jev_payload_json, jev_lenses_json, conversation_json, explanation,
                         carried_from_run_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_run_id, instrument_id, horizon) DO NOTHING
                    """,
                    (
                        run_id,
                        iid,
                        r["book"],
                        r["action"],
                        r["horizon"],
                        r["reason"],
                        r["loss_gate"],
                        r["pair_symbol"],
                        r["confidence"],
                        r["price_at_rec"],
                        r["rationale"],
                        r["jev_payload_json"],
                        r["jev_lenses_json"],
                        json.dumps(conversation),
                        r["explanation"] if "explanation" in r.keys() else None,
                        deciding_run,
                        now,
                        now,
                    ),
                )
                written += 1
        conn.commit()
        return written

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

    def _tracked(self, conn: sqlite3.Connection, display_currency: string) -> list[dict[str, Any]]:
        """
        The tracker book (0019). Shaped to sit beside a holding in the same lists — same
        `symbol` / `instrument_id` / `quote` / `technicals` keys — with the position fields
        absent rather than zeroed, because a zero cost basis would read as a real fact.
        """
        rows = conn.execute(
            """
            SELECT t.symbol, t.name, t.instrument_id, t.added_at,
                   i.currency, i.kind, i.region, i.isin, i.mic, i.name AS instrument_name,
                   q.price AS quote_price, q.currency AS quote_currency,
                   q.as_of AS quote_as_of, q.source AS quote_source,
                   tech.features_json, tech.as_of AS tech_as_of,
                   f.payload_json AS fundamentals_json, f.source AS fundamentals_source,
                   f.as_of AS fundamentals_as_of, f.completeness_state AS fundamentals_state,
                   f.coverage_score AS fundamentals_score,
                   f.missing_fields_json AS fundamentals_missing
            FROM tracker t
            INNER JOIN instruments i ON i.id = t.instrument_id
            LEFT JOIN quotes q ON q.instrument_id = i.id
            LEFT JOIN technicals tech ON tech.instrument_id = i.id
            LEFT JOIN fundamentals f ON f.instrument_id = i.id
            WHERE t.archived_at IS NULL
            ORDER BY t.symbol COLLATE NOCASE ASC
            """
        ).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            currency = (r["currency"] or "EUR").upper()
            price = float(r["quote_price"]) if r["quote_price"] is not None else None
            quote_currency = (r["quote_currency"] or currency).upper()
            fx = self._fx_rate(conn, quote_currency, display_currency)

            features = None
            if r["features_json"]:
                try:
                    features = json.loads(r["features_json"])
                except json.JSONDecodeError:
                    features = None

            news = conn.execute(
                """
                SELECT n.title, n.snippet, n.url, n.source_name, n.adapter_source, n.published_at
                FROM news_item_instruments nii
                INNER JOIN news_items n ON n.id = nii.news_item_id
                WHERE nii.instrument_id = ?
                ORDER BY COALESCE(n.published_at, n.fetched_at) DESC
                LIMIT 8
                """,
                (int(r["instrument_id"]),),
            ).fetchall()

            out.append(
                {
                    "book": "tracker",
                    "instrument_id": int(r["instrument_id"]),
                    "symbol": r["symbol"],
                    "name": r["name"] or r["instrument_name"],
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
                    "price_display": _money_round(price * fx, 4) if price is not None and fx is not None else None,
                    "technicals": features,
                    "fundamentals": self._fundamentals_snapshot(r),
                    "technicals_as_of": r["tech_as_of"],
                    # 0022: tracked names carry a real, instrument-linked news snapshot.
                    "news": [{
                        "title": n["title"], "snippet": n["snippet"], "url": n["url"],
                        "source": n["source_name"], "adapter_source": n["adapter_source"],
                        "published_at": n["published_at"],
                    } for n in news],
                }
            )
        return out

    def _fundamentals_snapshot(self, row: sqlite3.Row) -> dict[str, Any] | None:
        if not row["fundamentals_json"]:
            return None
        try:
            payload = json.loads(row["fundamentals_json"])
        except json.JSONDecodeError:
            payload = {}
        try:
            missing = json.loads(row["fundamentals_missing"] or "[]")
        except json.JSONDecodeError:
            missing = []
        return {
            "payload": payload if isinstance(payload, dict) else {},
            "source": row["fundamentals_source"],
            "as_of": row["fundamentals_as_of"],
            "completeness_state": row["fundamentals_state"],
            "coverage_score": row["fundamentals_score"],
            "missing_fields": missing if isinstance(missing, list) else [],
        }

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

    def _display_currency(self, conn: sqlite3.Connection) -> str:
        value = (self._setting_str(conn, "display_currency") or "EUR").upper()
        return value if value in {"EUR", "USD", "GBP", "CHF"} else "EUR"

    def _fx_rate(self, conn: sqlite3.Connection, base: str, display: str) -> float | None:
        base = base.upper()
        display = display.upper()
        if base == display:
            return 1.0

        def to_eur(currency: str) -> float | None:
            if currency == "EUR":
                return 1.0
            row = conn.execute(
                "SELECT rate FROM fx_rates WHERE base_currency=? AND quote_currency='EUR'",
                (currency,),
            ).fetchone()
            return float(row["rate"]) if row and row["rate"] is not None else None

        base_rate = to_eur(base)
        display_rate = to_eur(display)
        if base_rate is None or display_rate in (None, 0.0):
            return None
        return base_rate / display_rate

    def _money_setting_display(
        self,
        conn: sqlite3.Connection,
        amount_key: str,
        currency_key: str,
        display: str,
        *,
        legacy_key: str | None = None,
    ) -> float | None:
        amount = self._setting_float(conn, amount_key)
        currency = self._setting_str(conn, currency_key)
        if amount is None and legacy_key:
            amount = self._setting_float(conn, legacy_key)
            currency = "EUR" if amount is not None else currency
        if amount is None:
            return None
        rate = self._fx_rate(conn, (currency or display).upper(), display)
        return _money_round(amount * rate) if rate is not None else None

    def _realized_gains_ytd_display(
        self, conn: sqlite3.Connection, display_currency: str
    ) -> float | None:
        """This year's FIFO disposals and external override in display currency."""
        year = str(datetime.now(timezone.utc).year)
        rows = conn.execute(
            """
            SELECT d.realized_pnl, d.currency
            FROM realized_disposals d
            WHERE substr(d.trade_date, 1, 4) = ?
            """,
            (year,),
        ).fetchall()
        total = 0.0
        for r in rows:
            currency = (r["currency"] or "EUR").upper()
            rate = self._fx_rate(conn, currency, display_currency)
            if rate is None:
                return None
            total += float(r["realized_pnl"]) * rate
        override_amount = self._setting_float(conn, "realized_gains_ytd_override_amount")
        override_currency = self._setting_str(conn, "realized_gains_ytd_override_currency")
        if override_amount is None:
            override_amount = self._setting_float(conn, "realized_gains_ytd_override_eur")
            override_currency = "EUR" if override_amount is not None else override_currency
        override = 0.0
        if override_amount is not None:
            rate = self._fx_rate(conn, (override_currency or display_currency).upper(), display_currency)
            if rate is None:
                return None
            override = override_amount * rate
        return _money_round(total + override)

    def _slim_portfolio(
        self,
        context: dict[str, Any],
        priors: dict[int, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Book blob for Claude (0027): book totals plus the layer cards of every subject being
        decided, each with its prior decision so the research is about what changed.
        """
        priors = priors or {}
        subjects = []
        for s_ in self._subjects(context):
            iid = int(s_["instrument_id"])
            book = s_.get("book", "portfolio")
            entry: dict[str, Any] = {
                "sym": s_["symbol"],
                "book": book,
                "name": s_.get("name"),
                "kind": s_.get("kind"),
                "region": s_.get("region"),
                "cards": s_.get("cards") or {},
            }
            trig = (context.get("materiality") or {}).get(str(iid))
            if trig:
                entry["why_now"] = trig
            prior = priors.get(iid)
            if prior is not None:
                record = prior["record"]
                # Snippets only for headlines Claude has not already read at the last decision.
                seen = {
                    digest.news_key(n.get("title") or "")
                    for n in ((record.get("cards") or {}).get("news") or [])
                    if isinstance(n, dict)
                }
                cards = dict(entry["cards"])
                cards["news"] = [
                    {k: v for k, v in n.items() if k != "snip"}
                    if digest.news_key(n.get("title") or "") in seen else n
                    for n in cards.get("news") or []
                ]
                entry["cards"] = cards
                entry["prior"] = {
                    "decided": str(record.get("decided_at") or "")[:10] or None,
                    "combined": self._rows_summary(prior["rows"], book),
                    "notes": record.get("notes"),
                    "status": record.get("status"),
                }
            subjects.append(entry)
        return {
            "display_currency": context.get("display_currency", "EUR"),
            "portfolio_market_value_display": context.get("portfolio_market_value_display"),
            "cash_display": context.get("cash_display"),
            "realized_gains_ytd_display": context.get("realized_gains_ytd_display"),
            "calendar_year": context.get("calendar_year"),
            "subjects": subjects,
        }

    def _rows_summary(self, rows: list[sqlite3.Row], book: str) -> str:
        by_horizon = {r["horizon"]: r["action"] for r in rows}
        return ", ".join(f"{hz}:{by_horizon.get(hz, '?')}" for hz in horizons_for(book))

    def _claude_research(
        self,
        context: dict[str, Any],
        priors: dict[int, dict[str, Any]] | None,
        log: list[str],
    ) -> dict[str, Any]:
        log.append("claude: researcher pass (notes + info-needs)")
        decided = self._subjects(context)
        universe = [s_["symbol"] for s_ in self._all_subjects(context)]
        needs_thesis = [
            h["symbol"] for h in decided if h.get("book") == "portfolio" and not h.get("thesis")
        ]
        profiles = context.get("profiles") or {}
        prompt = (
            # 0019: two profiles. The investor profile says who is asking and applies to
            # everything; the portfolio profile governs only what is already owned.
            f"INVESTOR PROFILE (applies to BOTH books): {doctrine.compose_mandate(profiles, 'tracker')}\n"
            f"PORTFOLIO PROFILE (HOLDINGS only): {doctrine.resolved_portfolio_profile(profiles)}\n"
            f"UNIVERSE (the only symbols better_use_target may name): {json.dumps(universe)}\n"
            f"NEEDS_THESIS (draft a thesis + 2-4 falsifiers): {json.dumps(needs_thesis)}\n"
            f"BOOK:\n{json.dumps(self._slim_portfolio(context, priors), ensure_ascii=False, separators=(',', ':'))}"
        )
        log.append(f"claude: prompt chars={len(prompt)} subjects={len(decided)}")
        text = self.claude.analyze(
            prompt, system_prompt=RESEARCH_SYSTEM_PROMPT, json_schema=RESEARCH_SCHEMA
        )
        log.append(f"claude: got {len(text)} chars research")
        return self._parse_research(text, context)

    def _decision_brief(self, ans: Any, book: str) -> dict[str, Any]:
        d = self._extract_decision(ans, book=book)
        brief: dict[str, Any] = {"action": d["action"], "reason": d["reason"]}
        if d["confidence"] is not None:
            brief["confidence"] = round(d["confidence"], 2)
        probs = (d["payload"] or {}).get("probabilities")
        if isinstance(probs, dict):
            ranked = sorted(
                ((k, float(v)) for k, v in probs.items() if isinstance(v, (int, float)) and v > 0),
                key=lambda kv: kv[1],
                reverse=True,
            )
            if ranked:
                brief["top"] = {k: round(v, 2) for k, v in ranked[:3]}
        return brief

    def _explain_prompt(
        self,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        lens_answers: dict[str, dict[str, Any]],
        combined_answers: dict[str, Any],
    ) -> str:
        verdicts = {
            ev: self._summarize_answers(context, lens_answers.get(ev) or {})
            for ev in EVIDENCE_LENSES
            if lens_answers.get(ev)
        }
        subjects = []
        for s_ in self._subjects(context):
            iid = int(s_["instrument_id"])
            sym = s_["symbol"]
            book = s_.get("book", "portfolio")
            entry: dict[str, Any] = {
                "sym": sym,
                "book": book,
                "name": s_.get("name"),
                "decision": {
                    hz: self._decision_brief(combined_answers.get(f"{iid}_{hz}"), book)
                    for hz in horizons_for(book)
                },
                "lenses": {ev: v.get(sym) for ev, v in verdicts.items()},
                "notes": (context.get("layer_notes") or {}).get(sym),
                "research": _clip(research_by_symbol.get(sym, ""), 600),
                "case": (
                    (context.get("thesis_by_symbol") or {}).get(sym)
                    if book == "portfolio"
                    else (context.get("tracked_by_symbol") or {}).get(sym)
                ),
                "pos": (s_.get("cards") or {}).get("position"),
            }
            subjects.append({k: v for k, v in entry.items() if v not in (None, {}, "")})
        return (
            f"MARKET: {json.dumps(context.get('market') or {}, separators=(',', ':'))}\n"
            f"HEADLINES: {json.dumps(digest.headline_tape(self._all_subjects(context)), ensure_ascii=False)}\n"
            f"RESEARCH SYNTHESIS: {context.get('synthesis') or ''}\n"
            f"SUBJECTS:\n{json.dumps(subjects, ensure_ascii=False, separators=(',', ':'))}"
        )

    def _claude_explain(
        self,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        lens_answers: dict[str, dict[str, Any]],
        combined_answers: dict[str, Any],
        log: list[str],
    ) -> dict[str, Any]:
        """
        One Claude pass over the decisions Jev just made (0028). Best-effort: the decisions
        already exist, so a failure here costs the operator the explanation, never the run.
        """
        prompt = self._explain_prompt(context, research_by_symbol, lens_answers, combined_answers)
        log.append(f"claude: explain pass prompt chars={len(prompt)} subjects={len(self._subjects(context))}")
        try:
            text = self.claude.analyze(
                prompt, system_prompt=EXPLAIN_SYSTEM_PROMPT, json_schema=EXPLAIN_SCHEMA
            )
        except ClaudeCliError as exc:
            log.append(f"claude explain skipped: {exc}")
            return {"market_read": "", "by_symbol": {}, "usage": None}
        parsed = self._parse_explanations(text, context)
        parsed["usage"] = self.claude.last_usage
        log.append(f"claude: explanations={len(parsed['by_symbol'])}")
        return parsed

    def _parse_explanations(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        data = self._parse_json_object(text)
        out: dict[str, Any] = {"market_read": "", "by_symbol": {}}
        if not isinstance(data, dict):
            return out
        read = data.get("market_read")
        out["market_read"] = _clip(read, EXPLANATION_MAX) if isinstance(read, str) else ""
        raw = data.get("by_symbol")
        if not isinstance(raw, dict):
            return out
        for s_ in self._subjects(context):
            sym = s_["symbol"]
            val = raw.get(sym) or raw.get(sym.upper()) or raw.get(sym.lower())
            if not isinstance(val, dict):
                continue
            explanation = val.get("explanation")
            if not isinstance(explanation, str) or not explanation.strip():
                continue
            tension = val.get("tension")
            out["by_symbol"][sym] = {
                "text": _clip(explanation, EXPLANATION_MAX),
                "tension": _clip(tension, 300) if isinstance(tension, str) and tension.strip() else None,
            }
        return out

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
            # Used the same run (agent-advisory.md), and recorded in this run's cards so the
            # next run doesn't read its own draft as a thesis change (0027).
            h["thesis"] = {
                "text": str(draft.get("thesis") or "").strip(),
                "falsifiers": list(draft.get("falsifiers") or []),
                "version": version,
            }
            if isinstance(h.get("cards"), dict):
                h["cards"]["fundamentals"] = digest.fundamentals_card(
                    h.get("fundamentals"), kind=h.get("kind"), thesis=h["thesis"], book="portfolio"
                )
            written += 1
        conn.commit()
        return written

    def _parse_research(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        decided = self._subjects(context)
        symbols = [h["symbol"] for h in decided if h.get("book", "portfolio") == "portfolio"]
        tracked_symbols = [t["symbol"] for t in decided if t.get("book") == "tracker"]
        data = self._parse_json_object(text)
        research_by_symbol: dict[str, str] = {}
        thesis_by_symbol: dict[str, dict[str, Any]] = {}
        tracked_by_symbol: dict[str, dict[str, Any]] = {}
        layer_notes: dict[str, dict[str, str]] = {}
        thesis_drafts: dict[str, dict[str, Any]] = {}
        synthesis = ""
        info_needs: list[dict[str, Any]] = []
        # 0019: the tracker is still the universe a `better_use` sell may name (0016) — it
        # is just a real book now rather than a bare list of tickers. Carried subjects count.
        known = {s_["symbol"].upper() for s_ in self._all_subjects(context)}

        def pick(mapping: Any, sym: str) -> Any:
            if not isinstance(mapping, dict):
                return None
            return mapping.get(sym) or mapping.get(sym.upper()) or mapping.get(sym.lower())

        def notes_of(val: dict[str, Any]) -> dict[str, str]:
            raw = val.get("notes")
            if not isinstance(raw, dict):
                return {}
            return {
                lens: _clip(str(raw.get(lens) or ""), LAYER_NOTE_MAX)
                for lens in EVIDENCE_LENSES
                if str(raw.get(lens) or "").strip()
            }

        def research_of(val: dict[str, Any]) -> str:
            note = val.get("research")
            return (
                note.strip() if isinstance(note, str) and note.strip()
                else json.dumps(val, ensure_ascii=False)
            )

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
            for sym in symbols:
                val = pick(by_sym, sym)
                if isinstance(val, str) and val.strip():
                    research_by_symbol[sym] = val.strip()
                elif isinstance(val, dict):
                    research_by_symbol[sym] = research_of(val)
                    layer_notes[sym] = notes_of(val)
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
            # and what would turn it into a buy. Feeds the `fundamentals` lens for tracked names.
            tracked_raw = data.get("tracked_by_symbol")
            for sym in tracked_symbols:
                val = pick(tracked_raw, sym)
                if isinstance(val, str) and val.strip():
                    research_by_symbol[sym] = val.strip()
                    continue
                if not isinstance(val, dict):
                    continue
                research_by_symbol[sym] = research_of(val)
                layer_notes[sym] = notes_of(val)
                tracked_by_symbol[sym] = {
                    "entry_case": str(val.get("entry_case") or "").strip() or None,
                    "what_would_make_me_buy": str(
                        val.get("what_would_make_me_buy") or ""
                    ).strip() or None,
                }

            drafts = data.get("thesis_drafts")
            for sym in symbols:
                d = pick(drafts, sym)
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
            "layer_notes": layer_notes,
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
            "Tradai RESEARCHER. The Jev lens pass already ran.\n"
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

    def _layer_note(self, context: dict[str, Any], research_by_symbol: dict[str, str], sym: str, lens: str) -> str:
        note = ((context.get("layer_notes") or {}).get(sym) or {}).get(lens)
        # Claude answered in the old free-text shape: its research is the best note there is.
        return note or _clip(research_by_symbol.get(sym, ""), LAYER_NOTE_MAX)

    def _lens_state(
        self,
        context: dict[str, Any],
        research_by_symbol: dict[str, str],
        lens: str,
        *,
        addendum: dict[str, Any] | None = None,
        scenario: dict[str, Any] | None = None,
        lens_answers: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        What one Jev lens sees (0027): only the subjects being decided, and only its own
        layer's card plus Claude's note on it. `combined` sees the position, every note and
        the four evidence verdicts — not the raw layers again.
        """
        verdicts = {
            ev: self._summarize_answers(context, (lens_answers or {}).get(ev) or {})
            for ev in EVIDENCE_LENSES
            if lens == "combined" and (lens_answers or {}).get(ev)
        }

        holdings_out: list[dict[str, Any]] = []
        tracked_out: list[dict[str, Any]] = []
        for s_ in self._subjects(context):
            sym = s_["symbol"]
            book = s_.get("book", "portfolio")
            cards = s_.get("cards") or {}
            position = cards.get("position") or {}
            row: dict[str, Any] = {"symbol": sym}
            if book == "tracker":
                # 0019: tracked names carry no position fields at all rather than zeroed
                # ones — a qty of 0 would read as a fact about a position that doesn't exist.
                row["name"] = s_.get("name")
                row["owned"] = False
                entry = (context.get("tracked_by_symbol") or {}).get(sym) or {}
                case = {
                    "entry_case": entry.get("entry_case"),
                    "what_would_make_me_buy": entry.get("what_would_make_me_buy"),
                }
            else:
                tinfo = (context.get("thesis_by_symbol") or {}).get(sym) or {}
                case = {
                    "thesis_status": tinfo.get("thesis_status"),
                    "thesis_evidence": tinfo.get("evidence"),
                    "better_use_target": tinfo.get("better_use_target"),
                }

            if lens == "historical":
                row["hist"] = cards.get("historical")
            elif lens == "fundamentals":
                row["fund"] = cards.get("fundamentals")
                row.update(case)
                if book == "portfolio":
                    # buy_thesis_intact_underweight is a sizing question.
                    row["wgt"] = position.get("wgt")
                    row["wgt_cost"] = position.get("wgt_cost")
            elif lens == "technicals":
                row["tech"] = cards.get("technicals")
            elif lens == "news":
                row["news"] = digest.news_for_jev(cards.get("news"))
            if lens in EVIDENCE_LENSES:
                row["note"] = self._layer_note(context, research_by_symbol, sym, lens)
            else:
                row["pos"] = position
                row.update(case)
                row["notes"] = {
                    ev: self._layer_note(context, research_by_symbol, sym, ev)
                    for ev in EVIDENCE_LENSES
                }
                row["research"] = _clip(research_by_symbol.get(sym, ""), 300)
                tech = cards.get("technicals") or {}
                row["tech_zone"] = {k: tech.get(k) for k in ("trend", "rsi_zone") if tech.get(k)} or None
                if verdicts:
                    row["lens_verdicts"] = {ev: v.get(sym) for ev, v in verdicts.items()}
            row = {k: v for k, v in row.items() if v is not None}
            (tracked_out if book == "tracker" else holdings_out).append(row)

        state: dict[str, Any] = {
            "lens": lens,
            "display_currency": context.get("display_currency", "EUR"),
            "holdings": holdings_out,
            "tracked": tracked_out,
        }
        if lens == "combined":
            state.update({
                "portfolio_mv_display": context.get("portfolio_market_value_display"),
                "cash_display": context.get("cash_display"),
                "realized_gains_ytd_display": context.get("realized_gains_ytd_display"),
                "calendar_year": context.get("calendar_year"),
            })
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
        lens_answers: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        log.append(f"jev: lens={lens}")
        state = self._lens_state(context, research_by_symbol, lens, lens_answers=lens_answers)
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
                        + (
                            "with the four lens verdicts, layer notes and position."
                            if lens == "combined"
                            else f"with the {lens} layer card and Claude's {lens} note."
                        )
                    ),
                    "research_excerpt": _clip(
                        self._layer_note(context, research_by_symbol, h["symbol"], lens)
                        if lens in EVIDENCE_LENSES
                        else research_by_symbol.get(h["symbol"]),
                        200,
                    ),
                }
            )

        result = self.jev.choose_actions(
            state, keys, lens=lens, mandates=self._mandates(context)
        )
        answers = result.get("answers") or {}
        log.append(f"jev: lens={lens} answers={len(answers)} request_chars={result.get('request_chars')}")
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
        lens_answers: dict[str, dict[str, Any]] | None = None,
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
            lens_answers=lens_answers,
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
        explanations: dict[str, Any] | None = None,
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
            # 0027: both books use the four layer lenses plus combined.
            lenses = TRACKER_LENSES if book == "tracker" else LENSES
            rationale = research_by_symbol.get(symbol, "")
            conversation = list(conversations.get(iid) or [])
            price_at_rec = (h["quote"] or {}).get("price") if h.get("quote") else None
            # 0028: one explanation covers every horizon of the subject.
            why = ((explanations or {}).get("by_symbol") or {}).get(symbol)
            explanation_json = None
            if why:
                explanation_json = json.dumps({
                    "text": why["text"],
                    "tension": why.get("tension"),
                    "market_read": (explanations or {}).get("market_read") or None,
                })
                conversation.append({
                    "role": "researcher",
                    "kind": "explanation",
                    "at": now,
                    "summary": why["text"],
                    "tension": why.get("tension"),
                })

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
                        realized_gains_ytd_display=context.get("realized_gains_ytd_display"),
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
                         jev_payload_json, jev_lenses_json, conversation_json, explanation,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        explanation = excluded.explanation,
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
                        explanation_json,
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
        Carried rows (0027) never alert: that decision was already raised when it was made.
        """
        rows = conn.execute(
            "SELECT id, action FROM recommendations WHERE agent_run_id = ? "
            "AND action IN ('buy', 'sell') AND carried_from_run_id IS NULL",
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
                  AND r.carried_from_run_id IS NULL
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
        refs = self._run_refs(conn, run_id)
        models = {
            "claude": self.claude.status(),
            "jev": self.jev.status(),
            "max_scenario_rounds": max_scenario_rounds(),
            "advisory_interval_seconds": advisory_interval_seconds(),
            "material_move_pct": materiality.material_move_pct(),
            "max_carry_days": materiality.max_carry_days(),
            "pipeline": "0027-layered-researcher-decider",
            "lenses": list(LENSES),
        }
        for key in ("target_symbol", "force"):
            if key in refs:
                models[key] = refs[key]
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
