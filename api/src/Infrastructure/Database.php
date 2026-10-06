<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use PDO;
use Tradai\Api\Domain\FifoLedger;

final class Database
{
    private static ?PDO $pdo = null;

    public static function connection(string $dataDir): PDO
    {
        if (self::$pdo instanceof PDO) {
            return self::$pdo;
        }

        if (!is_dir($dataDir)) {
            mkdir($dataDir, 0775, true);
        }

        $path = rtrim($dataDir, '/') . '/tradai.sqlite';
        $pdo = new PDO('sqlite:' . $path, null, null, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec('PRAGMA foreign_keys = ON');
        self::migrate($pdo);
        self::$pdo = $pdo;

        return self::$pdo;
    }

    private static function migrate(PDO $pdo): void
    {
        // 0019: must run before the declarative block below, or `CREATE TABLE IF NOT
        // EXISTS tracker` wins the race and strands every existing watchlist row.
        self::renameWatchlistToTracker($pdo);

        $pdo->exec(<<<'SQL'
            CREATE TABLE IF NOT EXISTS instruments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                isin TEXT,
                symbol TEXT NOT NULL,
                mic TEXT,
                currency TEXT NOT NULL,
                name TEXT,
                kind TEXT NOT NULL CHECK (kind IN ('equity', 'etf')),
                region TEXT CHECK (region IS NULL OR region IN ('eu', 'us')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (symbol, mic)
            );

            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id INTEGER NOT NULL UNIQUE,
                quantity REAL NOT NULL CHECK (quantity >= 0),
                avg_cost REAL NOT NULL CHECK (avg_cost >= 0),
                total_cost REAL NOT NULL CHECK (total_cost >= 0),
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id INTEGER NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                trade_date TEXT NOT NULL,
                quantity REAL NOT NULL CHECK (quantity > 0),
                unit_price REAL NOT NULL CHECK (unit_price >= 0),
                commission REAL NOT NULL DEFAULT 0 CHECK (commission >= 0),
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS quotes (
                instrument_id INTEGER PRIMARY KEY,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                as_of TEXT NOT NULL,
                source TEXT NOT NULL,
                raw_json TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS price_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id INTEGER NOT NULL,
                bar_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL NOT NULL,
                volume REAL,
                source TEXT NOT NULL,
                UNIQUE (instrument_id, bar_date),
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );

            -- 0026: adjusted long history is chart-only and never replaces technical bars.
            CREATE TABLE IF NOT EXISTS historical_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_key TEXT NOT NULL UNIQUE,
                series_kind TEXT NOT NULL CHECK (series_kind IN ('instrument', 'benchmark')),
                instrument_id INTEGER,
                benchmark_region TEXT CHECK (benchmark_region IS NULL OR benchmark_region IN ('eu', 'us')),
                symbol TEXT NOT NULL,
                native_currency TEXT NOT NULL,
                source TEXT NOT NULL,
                resolution TEXT NOT NULL,
                earliest_date TEXT NOT NULL,
                as_of TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS historical_points (
                series_id INTEGER NOT NULL,
                point_date TEXT NOT NULL,
                adjusted_close REAL NOT NULL,
                resolution TEXT NOT NULL,
                PRIMARY KEY (series_id, point_date),
                FOREIGN KEY (series_id) REFERENCES historical_series(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_historical_points_date
                ON historical_points (series_id, point_date);

            CREATE TABLE IF NOT EXISTS benchmark_history_state (
                region TEXT PRIMARY KEY CHECK (region IN ('eu', 'us')),
                symbol TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                last_success_at TEXT,
                next_due_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fx_rates (
                base_currency TEXT PRIMARY KEY,
                quote_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                as_of TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                snippet TEXT,
                url TEXT,
                source_name TEXT,
                published_at TEXT,
                language TEXT,
                raw_json TEXT,
                fetched_at TEXT NOT NULL,
                adapter_source TEXT
            );

            CREATE TABLE IF NOT EXISTS news_item_instruments (
                news_item_id INTEGER NOT NULL,
                instrument_id INTEGER NOT NULL,
                PRIMARY KEY (news_item_id, instrument_id),
                FOREIGN KEY (news_item_id) REFERENCES news_items(id) ON DELETE CASCADE,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS technicals (
                instrument_id INTEGER PRIMARY KEY,
                as_of TEXT NOT NULL,
                features_json TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );

            -- 0022: one normalized, single-provider fundamentals snapshot per instrument.
            CREATE TABLE IF NOT EXISTS fundamentals (
                instrument_id INTEGER PRIMARY KEY,
                payload_json TEXT NOT NULL,
                source TEXT NOT NULL,
                as_of TEXT NOT NULL,
                completeness_state TEXT NOT NULL CHECK (completeness_state IN ('complete', 'partial')),
                coverage_score REAL NOT NULL,
                missing_fields_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );

            -- 0022: independent cadence and coverage state per instrument/dataset.
            CREATE TABLE IF NOT EXISTS ingestion_state (
                instrument_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                cadence_seconds INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                last_success_at TEXT,
                selected_source TEXT,
                coverage_score REAL,
                missing_fields_json TEXT NOT NULL DEFAULT '[]',
                gap_streak INTEGER NOT NULL DEFAULT 0,
                next_due_at TEXT,
                last_input_fingerprint TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (instrument_id, operation),
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_ingestion_due
                ON ingestion_state (operation, next_due_at, last_success_at);

            CREATE TABLE IF NOT EXISTS provider_rate_state (
                provider TEXT PRIMARY KEY,
                window_started_at TEXT,
                window_count INTEGER NOT NULL DEFAULT 0,
                last_call_at TEXT,
                cooldown_until TEXT,
                observed_limit INTEGER,
                observed_remaining INTEGER,
                observed_reset_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ingest_reports (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- 0029: operator-visible history is manual ingest only. The singleton above
            -- remains the source of the latest status/elapsed-time display.
            CREATE TABLE IF NOT EXISTS ingest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
                trigger_kind TEXT NOT NULL CHECK (trigger_kind = 'manual'),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                report_json TEXT,
                error_text TEXT
            );

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
                horizon TEXT NOT NULL CHECK (horizon IN ('3m', '6m', '12m')),
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

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id INTEGER NOT NULL UNIQUE,
                unread INTEGER NOT NULL DEFAULT 1 CHECK (unread IN (0, 1)),
                severity TEXT NOT NULL DEFAULT 'action',
                raised_at TEXT NOT NULL,
                acked_at TEXT,
                FOREIGN KEY (recommendation_id) REFERENCES recommendations(id) ON DELETE CASCADE
            );

            -- 0015: operator-approved reason a holding is owned, plus named falsifiers.
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
            CREATE INDEX IF NOT EXISTS idx_theses_instrument_status
                ON theses (instrument_id, status);

            -- 0019: the tracker — a second book of names of interest, ingested and
            -- researched like the portfolio. Was `watchlist` (0016), a name-only candidate
            -- universe for a `better_use` sell; it is now a monitored book in its own right,
            -- so `instrument_id` is mandatory (recommendations FK to instruments).
            CREATE TABLE IF NOT EXISTS tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                instrument_id INTEGER NOT NULL,
                name TEXT,
                -- Legacy compatibility only since 0024; no active API or advisory contract.
                note TEXT,
                added_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                -- Set when the operator buys the name (auto-promote). Archived entries keep
                -- their history but leave the Tracker view and the agent context.
                archived_at TEXT,
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tracker_active
                ON tracker (archived_at);

            -- 0012: cash reserve + realised-gain override that agents allocate against.
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );

            -- 0013: FIFO disposals, recomputed by the API on every transaction change.
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
                UNIQUE (sell_transaction_id),
                FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_realized_disposals_date
                ON realized_disposals (trade_date);
        SQL);

        self::ensureHoldingTotalCostColumn($pdo);
        self::ensureColumn($pdo, 'news_items', 'adapter_source', 'TEXT');
        self::ensureAgentStage5bColumns($pdo);
        self::ensureDoctrineRecommendationSchema($pdo);
        self::ensureTrackerRecommendationSchema($pdo);
        self::ensureTrackerHorizonSchema($pdo);
        // 0027: after the table rebuilds above, which copy a fixed column list.
        self::ensureColumn($pdo, 'recommendations', 'carried_from_run_id', 'INTEGER');
        // 0028: Claude's plain-language explanation, JSON {text, tension, market_read}.
        self::ensureColumn($pdo, 'recommendations', 'explanation', 'TEXT');
        // 0030: after the 0027/0028 columns, so the rebuild copies them too.
        self::ensureTakeProfitSchema($pdo);
        self::ensureTrackerInstruments($pdo);
        self::ensureProfileSplit($pdo);
        self::ensureColumn($pdo, 'holdings', 'first_trade_date', 'TEXT');
        self::ensureColumn($pdo, 'holdings', 'open_lot_count', 'INTEGER');
        self::ensureColumn($pdo, 'holdings', 'realized_pnl_native', 'REAL');
        self::ensureTradeFxColumns($pdo);
        self::ensureAlertsTable($pdo);
        self::backfillAlertsFromRecommendations($pdo);
        self::backfillTransactionsFromLegacyHoldings($pdo);
        self::backfillFifoRollups($pdo);
        self::consolidateDuplicateSymbolHoldings($pdo);
    }

    private static function ensureHoldingTotalCostColumn(PDO $pdo): void
    {
        $cols = $pdo->query('PRAGMA table_info(holdings)')->fetchAll();
        $names = array_map(static fn (array $c): string => (string) $c['name'], $cols);
        if (!in_array('total_cost', $names, true)) {
            $pdo->exec('ALTER TABLE holdings ADD COLUMN total_cost REAL NOT NULL DEFAULT 0');
            $pdo->exec('UPDATE holdings SET total_cost = quantity * avg_cost WHERE total_cost = 0');
        }
    }

    /**
     * 0031: realised P&L is locked in EUR at trade dates. EUR listings need no lookup; every
     * other transaction keeps a null rate until HoldingRepository fetches it via the worker.
     */
    private static function ensureTradeFxColumns(PDO $pdo): void
    {
        self::ensureColumn($pdo, 'transactions', 'fx_to_eur', 'REAL');
        self::ensureColumn($pdo, 'transactions', 'fx_as_of', 'TEXT');
        self::ensureColumn($pdo, 'realized_disposals', 'proceeds_eur', 'REAL');
        self::ensureColumn($pdo, 'realized_disposals', 'cost_eur', 'REAL');
        self::ensureColumn($pdo, 'realized_disposals', 'realized_pnl_eur', 'REAL');
        $pdo->exec(
            "UPDATE transactions SET fx_to_eur = 1.0, fx_as_of = trade_date
             WHERE fx_to_eur IS NULL
               AND instrument_id IN (SELECT id FROM instruments WHERE UPPER(currency) = 'EUR')"
        );
    }

    private static function ensureAgentStage5bColumns(PDO $pdo): void
    {
        self::ensureColumn($pdo, 'agent_runs', 'research_json', 'TEXT');
        self::ensureColumn($pdo, 'agent_runs', 'info_needs_json', 'TEXT');
        self::ensureColumn($pdo, 'recommendations', 'jev_lenses_json', 'TEXT');
        self::ensureColumn($pdo, 'recommendations', 'conversation_json', 'TEXT');
    }

    /**
     * 0013/0014: `recommendations` needs the doctrine columns and a widened horizon CHECK.
     * SQLite cannot ALTER a CHECK constraint, so this rebuilds the table. `3m` stays legal
     * so pre-0014 rows survive; the worker simply stops generating it.
     */
    private static function ensureDoctrineRecommendationSchema(PDO $pdo): void
    {
        $sql = $pdo->query(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'recommendations'"
        )->fetchColumn();
        if (!is_string($sql) || $sql === '') {
            return;
        }
        if (str_contains($sql, "'24m'") && str_contains($sql, 'suppressed_reason')) {
            return; // already migrated
        }

        $pdo->exec('PRAGMA foreign_keys = OFF');
        $pdo->beginTransaction();
        try {
            $pdo->exec(<<<'SQL'
                CREATE TABLE recommendations_doctrine (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_run_id INTEGER NOT NULL,
                    instrument_id INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold', 'watch')),
                    horizon TEXT NOT NULL CHECK (horizon IN ('3m', '6m', '12m', '24m')),
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
            SQL);

            $pdo->exec(<<<'SQL'
                INSERT INTO recommendations_doctrine
                    (id, agent_run_id, instrument_id, action, horizon, reason,
                     rationale, jev_payload_json, jev_lenses_json, conversation_json,
                     created_at, updated_at)
                SELECT id, agent_run_id, instrument_id, action, horizon, 'legacy',
                       rationale, jev_payload_json, jev_lenses_json, conversation_json,
                       created_at, updated_at
                FROM recommendations;
            SQL);

            $pdo->exec('DROP TABLE recommendations');
            $pdo->exec('ALTER TABLE recommendations_doctrine RENAME TO recommendations');
            $pdo->commit();
        } catch (\Throwable $e) {
            $pdo->rollBack();
            $pdo->exec('PRAGMA foreign_keys = ON');
            throw $e;
        }
        $pdo->exec('PRAGMA foreign_keys = ON');
    }

    /**
     * 0019: `watchlist` becomes `tracker`. Runs before the declarative block so the rename
     * carries the operator's existing rows instead of racing an empty CREATE.
     */
    private static function renameWatchlistToTracker(PDO $pdo): void
    {
        $names = $pdo->query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('watchlist', 'tracker')"
        )->fetchAll(PDO::FETCH_COLUMN);

        if (!in_array('watchlist', $names, true)) {
            return;
        }
        if (in_array('tracker', $names, true)) {
            // Both present: a partial migration. The tracker table is authoritative.
            $pdo->exec('DROP TABLE watchlist');
            return;
        }
        $pdo->exec('ALTER TABLE watchlist RENAME TO tracker');
        // The old table allowed a null instrument_id; ensureTrackerInstruments backfills it.
        self::ensureColumn($pdo, 'tracker', 'archived_at', 'TEXT');
    }

    /**
     * 0019: the tracker is a decided-on book now, so its rows need `drop` as an action and
     * the three tracker reasons, plus a `book` column. SQLite cannot ALTER a CHECK, so this
     * rebuilds the table the same way ensureDoctrineRecommendationSchema() does.
     *
     * `book` is stored rather than derived: a join would mislabel every historical row the
     * moment a tracked name is promoted into the portfolio.
     */
    private static function ensureTrackerRecommendationSchema(PDO $pdo): void
    {
        $sql = $pdo->query(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'recommendations'"
        )->fetchColumn();
        if (!is_string($sql) || $sql === '') {
            return;
        }
        if (str_contains($sql, "'drop'") && str_contains($sql, 'book')) {
            return; // already migrated
        }

        $pdo->exec('PRAGMA foreign_keys = OFF');
        $pdo->beginTransaction();
        try {
            $pdo->exec(<<<'SQL'
                CREATE TABLE recommendations_tracker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_run_id INTEGER NOT NULL,
                    instrument_id INTEGER NOT NULL,
                    book TEXT NOT NULL DEFAULT 'portfolio'
                        CHECK (book IN ('portfolio', 'tracker')),
                    action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold', 'watch', 'drop')),
                    horizon TEXT NOT NULL CHECK (horizon IN ('3m', '6m', '12m', '24m')),
                    reason TEXT NOT NULL DEFAULT 'legacy' CHECK (reason IN (
                        'thesis_broken', 'better_use',
                        'thesis_intact_underweight', 'new_conviction',
                        'thesis_intact', 'insufficient_evidence', 'legacy',
                        'entry_now', 'await_better_entry', 'lost_interest'
                    )),
                    loss_gate TEXT CHECK (loss_gate IS NULL OR loss_gate IN (
                        'not_at_loss', 'offset_same_year', 'no_recovery_24m', 'blocked'
                    )),
                    pair_symbol TEXT,
                    confidence REAL,
                    suppressed INTEGER NOT NULL DEFAULT 0 CHECK (suppressed IN (0, 1)),
                    suppressed_reason TEXT,
                    proposed_action TEXT CHECK (proposed_action IS NULL OR proposed_action IN (
                        'buy', 'sell', 'hold', 'watch', 'drop'
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
            SQL);

            // Everything decided before 0019 was decided about a holding.
            $pdo->exec(<<<'SQL'
                INSERT INTO recommendations_tracker
                    (id, agent_run_id, instrument_id, book, action, horizon, reason, loss_gate,
                     pair_symbol, confidence, suppressed, suppressed_reason, proposed_action,
                     price_at_rec, rationale, jev_payload_json, jev_lenses_json,
                     conversation_json, created_at, updated_at)
                SELECT id, agent_run_id, instrument_id, 'portfolio', action, horizon, reason,
                       loss_gate, pair_symbol, confidence, suppressed, suppressed_reason,
                       proposed_action, price_at_rec, rationale, jev_payload_json,
                       jev_lenses_json, conversation_json, created_at, updated_at
                FROM recommendations;
            SQL);

            $pdo->exec('DROP TABLE recommendations');
            $pdo->exec('ALTER TABLE recommendations_tracker RENAME TO recommendations');
            $pdo->commit();
        } catch (\Throwable $e) {
            $pdo->rollBack();
            $pdo->exec('PRAGMA foreign_keys = ON');
            throw $e;
        }
        $pdo->exec('PRAGMA foreign_keys = ON');
    }

    /**
     * 0020: the tracker is judged over `1m / 3m / 6m`, not the holdings' `6m / 12m / 24m`.
     * SQLite cannot ALTER a CHECK, so this rebuilds the table the same way
     * ensureTrackerRecommendationSchema() does. `3m`/`6m`/`12m`/`24m` all stay legal so every
     * pre-0020 row survives; the worker simply stops writing `12m`/`24m` for the tracker book.
     */
    private static function ensureTrackerHorizonSchema(PDO $pdo): void
    {
        $sql = $pdo->query(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'recommendations'"
        )->fetchColumn();
        if (!is_string($sql) || $sql === '') {
            return;
        }
        if (str_contains($sql, "'1m'")) {
            return; // already migrated
        }

        $pdo->exec('PRAGMA foreign_keys = OFF');
        $pdo->beginTransaction();
        try {
            $pdo->exec(<<<'SQL'
                CREATE TABLE recommendations_horizons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_run_id INTEGER NOT NULL,
                    instrument_id INTEGER NOT NULL,
                    book TEXT NOT NULL DEFAULT 'portfolio'
                        CHECK (book IN ('portfolio', 'tracker')),
                    action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold', 'watch', 'drop')),
                    horizon TEXT NOT NULL CHECK (horizon IN ('1m', '3m', '6m', '12m', '24m')),
                    reason TEXT NOT NULL DEFAULT 'legacy' CHECK (reason IN (
                        'thesis_broken', 'better_use',
                        'thesis_intact_underweight', 'new_conviction',
                        'thesis_intact', 'insufficient_evidence', 'legacy',
                        'entry_now', 'await_better_entry', 'lost_interest'
                    )),
                    loss_gate TEXT CHECK (loss_gate IS NULL OR loss_gate IN (
                        'not_at_loss', 'offset_same_year', 'no_recovery_24m', 'blocked'
                    )),
                    pair_symbol TEXT,
                    confidence REAL,
                    suppressed INTEGER NOT NULL DEFAULT 0 CHECK (suppressed IN (0, 1)),
                    suppressed_reason TEXT,
                    proposed_action TEXT CHECK (proposed_action IS NULL OR proposed_action IN (
                        'buy', 'sell', 'hold', 'watch', 'drop'
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
            SQL);

            $pdo->exec(<<<'SQL'
                INSERT INTO recommendations_horizons
                    (id, agent_run_id, instrument_id, book, action, horizon, reason, loss_gate,
                     pair_symbol, confidence, suppressed, suppressed_reason, proposed_action,
                     price_at_rec, rationale, jev_payload_json, jev_lenses_json,
                     conversation_json, created_at, updated_at)
                SELECT id, agent_run_id, instrument_id, book, action, horizon, reason, loss_gate,
                       pair_symbol, confidence, suppressed, suppressed_reason, proposed_action,
                       price_at_rec, rationale, jev_payload_json, jev_lenses_json,
                       conversation_json, created_at, updated_at
                FROM recommendations;
            SQL);

            $pdo->exec('DROP TABLE recommendations');
            $pdo->exec('ALTER TABLE recommendations_horizons RENAME TO recommendations');
            $pdo->commit();
        } catch (\Throwable $e) {
            $pdo->rollBack();
            $pdo->exec('PRAGMA foreign_keys = ON');
            throw $e;
        }
        $pdo->exec('PRAGMA foreign_keys = ON');
    }

    /**
     * 0030: holdings gain a third sell reason, `take_profit`, and are judged over
     * `3m / 6m / 12m`, so the no-recovery loss label is judged at 12m (`no_recovery_12m`).
     * SQLite cannot ALTER a CHECK, so this rebuilds the table like ensureTrackerHorizonSchema()
     * — but copying every current column, including the 0027/0028 ones added after it.
     * `no_recovery_24m` stays legal so every pre-0030 row survives.
     */
    private static function ensureTakeProfitSchema(PDO $pdo): void
    {
        $sql = $pdo->query(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'recommendations'"
        )->fetchColumn();
        if (!is_string($sql) || $sql === '') {
            return;
        }
        if (str_contains($sql, "'take_profit'") && str_contains($sql, "'no_recovery_12m'")) {
            return; // already migrated
        }

        $pdo->exec('PRAGMA foreign_keys = OFF');
        $pdo->beginTransaction();
        try {
            $pdo->exec(<<<'SQL'
                CREATE TABLE recommendations_take_profit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_run_id INTEGER NOT NULL,
                    instrument_id INTEGER NOT NULL,
                    book TEXT NOT NULL DEFAULT 'portfolio'
                        CHECK (book IN ('portfolio', 'tracker')),
                    action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'hold', 'watch', 'drop')),
                    horizon TEXT NOT NULL CHECK (horizon IN ('1m', '3m', '6m', '12m', '24m')),
                    reason TEXT NOT NULL DEFAULT 'legacy' CHECK (reason IN (
                        'thesis_broken', 'better_use', 'take_profit',
                        'thesis_intact_underweight', 'new_conviction',
                        'thesis_intact', 'insufficient_evidence', 'legacy',
                        'entry_now', 'await_better_entry', 'lost_interest'
                    )),
                    loss_gate TEXT CHECK (loss_gate IS NULL OR loss_gate IN (
                        'not_at_loss', 'offset_same_year', 'no_recovery_24m', 'no_recovery_12m',
                        'blocked'
                    )),
                    pair_symbol TEXT,
                    confidence REAL,
                    suppressed INTEGER NOT NULL DEFAULT 0 CHECK (suppressed IN (0, 1)),
                    suppressed_reason TEXT,
                    proposed_action TEXT CHECK (proposed_action IS NULL OR proposed_action IN (
                        'buy', 'sell', 'hold', 'watch', 'drop'
                    )),
                    price_at_rec REAL,
                    rationale TEXT,
                    jev_payload_json TEXT,
                    jev_lenses_json TEXT,
                    conversation_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    carried_from_run_id INTEGER,
                    explanation TEXT,
                    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
                    FOREIGN KEY (instrument_id) REFERENCES instruments(id) ON DELETE CASCADE,
                    UNIQUE (agent_run_id, instrument_id, horizon)
                );
            SQL);

            $pdo->exec(<<<'SQL'
                INSERT INTO recommendations_take_profit
                    (id, agent_run_id, instrument_id, book, action, horizon, reason, loss_gate,
                     pair_symbol, confidence, suppressed, suppressed_reason, proposed_action,
                     price_at_rec, rationale, jev_payload_json, jev_lenses_json,
                     conversation_json, created_at, updated_at, carried_from_run_id,
                     explanation)
                SELECT id, agent_run_id, instrument_id, book, action, horizon, reason, loss_gate,
                       pair_symbol, confidence, suppressed, suppressed_reason, proposed_action,
                       price_at_rec, rationale, jev_payload_json, jev_lenses_json,
                       conversation_json, created_at, updated_at, carried_from_run_id,
                       explanation
                FROM recommendations;
            SQL);

            $pdo->exec('DROP TABLE recommendations');
            $pdo->exec('ALTER TABLE recommendations_take_profit RENAME TO recommendations');
            $pdo->commit();
        } catch (\Throwable $e) {
            $pdo->rollBack();
            $pdo->exec('PRAGMA foreign_keys = ON');
            throw $e;
        }
        $pdo->exec('PRAGMA foreign_keys = ON');
    }

    /**
     * 0019: pre-existing watchlist rows may have a null `instrument_id` — the old table only
     * back-linked when the symbol happened to be owned already. Nothing can be ingested or
     * recommended without one, so mint the missing instruments.
     */
    private static function ensureTrackerInstruments(PDO $pdo): void
    {
        $rows = $pdo->query(
            'SELECT id, symbol, name FROM tracker WHERE instrument_id IS NULL'
        )->fetchAll();
        if ($rows === []) {
            return;
        }

        $now = gmdate('c');
        $instruments = new InstrumentRepository($pdo);
        foreach ($rows as $row) {
            $symbol = strtoupper(trim((string) $row['symbol']));
            if ($symbol === '') {
                continue;
            }
            $instrumentId = $instruments->upsert([
                'symbol' => $symbol,
                'name' => $row['name'],
                'isin' => null,
                'mic' => null,
                // Best guess from the symbol alone; the operator can correct it, and the
                // worker re-derives region the same way on every ingest.
                'region' => str_contains($symbol, '.') ? 'eu' : 'us',
                'currency' => str_contains($symbol, '.') ? 'EUR' : 'USD',
                'kind' => 'equity',
            ], $now);

            $pdo->prepare('UPDATE tracker SET instrument_id = :iid, updated_at = :now WHERE id = :id')
                ->execute(['iid' => $instrumentId, 'now' => $now, 'id' => (int) $row['id']]);
        }
    }

    /**
     * 0019: the single `investor_profile_text` splits in two. The operator's existing words
     * read as portfolio-management rules (tax, realising losses), so they move to the
     * portfolio profile and keep governing holdings exactly as before. The investor profile
     * starts empty and falls back to the default until written.
     */
    private static function ensureProfileSplit(PDO $pdo): void
    {
        $exists = $pdo->query(
            "SELECT 1 FROM settings WHERE key = 'portfolio_profile_text'"
        )->fetchColumn();
        if ($exists !== false) {
            return; // split already applied; never clobber the operator's edits
        }

        $existing = $pdo->query(
            "SELECT value FROM settings WHERE key = 'investor_profile_text'"
        )->fetchColumn();
        $text = $existing === false ? null : (string) $existing;

        $now = gmdate('c');
        $set = $pdo->prepare(
            'INSERT INTO settings (key, value, updated_at) VALUES (:key, :value, :updated_at)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at'
        );
        $set->execute(['key' => 'portfolio_profile_text', 'value' => $text, 'updated_at' => $now]);
        if ($text !== null && $text !== '') {
            $set->execute(['key' => 'investor_profile_text', 'value' => null, 'updated_at' => $now]);
        }
    }

    private static function ensureAlertsTable(PDO $pdo): void
    {
        $pdo->exec(<<<'SQL'
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id INTEGER NOT NULL UNIQUE,
                unread INTEGER NOT NULL DEFAULT 1 CHECK (unread IN (0, 1)),
                severity TEXT NOT NULL DEFAULT 'action',
                raised_at TEXT NOT NULL,
                acked_at TEXT,
                FOREIGN KEY (recommendation_id) REFERENCES recommendations(id) ON DELETE CASCADE
            );
        SQL);
    }

    /** Raise alerts for buy/sell recs that predate Stage 6 (0010). */
    private static function backfillAlertsFromRecommendations(PDO $pdo): void
    {
        $now = gmdate('c');
        $pdo->prepare(
            <<<'SQL'
            INSERT OR IGNORE INTO alerts (recommendation_id, unread, severity, raised_at)
            SELECT r.id, 1, 'action', COALESCE(r.created_at, :raised_at)
            FROM recommendations r
            WHERE r.action IN ('buy', 'sell')
            SQL
        )->execute(['raised_at' => $now]);
    }

    /**
     * Holdings written before 0013 were rolled up buy-only and carry no FIFO-derived
     * fields. Recompute them from the transaction log once; converges and is a no-op after.
     */
    private static function backfillFifoRollups(PDO $pdo): void
    {
        $stale = $pdo->query(
            'SELECT h.id, h.instrument_id, i.currency
             FROM holdings h
             INNER JOIN instruments i ON i.id = h.instrument_id
             WHERE h.first_trade_date IS NULL
               AND EXISTS (SELECT 1 FROM transactions t WHERE t.instrument_id = h.instrument_id)'
        )->fetchAll();

        if ($stale === []) {
            return;
        }

        $now = gmdate('c');
        $txnStmt = $pdo->prepare(
            'SELECT id, side, trade_date, quantity, unit_price, commission
             FROM transactions WHERE instrument_id = :id ORDER BY trade_date ASC, id ASC'
        );
        $upd = $pdo->prepare(
            'UPDATE holdings
             SET quantity = :quantity, avg_cost = :avg_cost, total_cost = :total_cost,
                 first_trade_date = :first_trade_date, open_lot_count = :open_lot_count,
                 realized_pnl_native = :realized_pnl_native, updated_at = :updated_at
             WHERE id = :id'
        );
        $delDisp = $pdo->prepare('DELETE FROM realized_disposals WHERE instrument_id = :id');
        $insDisp = $pdo->prepare(
            'INSERT OR REPLACE INTO realized_disposals
                (instrument_id, sell_transaction_id, trade_date, quantity,
                 proceeds, cost, realized_pnl, currency, created_at)
             VALUES
                (:instrument_id, :sell_transaction_id, :trade_date, :quantity,
                 :proceeds, :cost, :realized_pnl, :currency, :created_at)'
        );

        foreach ($stale as $row) {
            $instrumentId = (int) $row['instrument_id'];
            $txnStmt->execute(['id' => $instrumentId]);
            $ledger = FifoLedger::walk($txnStmt->fetchAll());

            $realized = 0.0;
            foreach ($ledger['disposals'] as $dsp) {
                $realized += (float) $dsp['realized_pnl'];
            }

            $upd->execute([
                'id' => (int) $row['id'],
                'quantity' => $ledger['quantity'],
                'avg_cost' => $ledger['avg_cost'],
                'total_cost' => $ledger['total_cost'],
                'first_trade_date' => $ledger['first_trade_date'],
                'open_lot_count' => $ledger['lot_count'],
                'realized_pnl_native' => $realized,
                'updated_at' => $now,
            ]);

            $delDisp->execute(['id' => $instrumentId]);
            $currency = strtoupper((string) ($row['currency'] ?? 'EUR'));
            foreach ($ledger['disposals'] as $dsp) {
                $insDisp->execute([
                    'instrument_id' => $instrumentId,
                    'sell_transaction_id' => (int) $dsp['sell_transaction_id'],
                    'trade_date' => (string) $dsp['trade_date'],
                    'quantity' => (float) $dsp['quantity'],
                    'proceeds' => (float) $dsp['proceeds'],
                    'cost' => (float) $dsp['cost'],
                    'realized_pnl' => (float) $dsp['realized_pnl'],
                    'currency' => $currency,
                    'created_at' => $now,
                ]);
            }
        }
    }

    private static function ensureColumn(PDO $pdo, string $table, string $column, string $decl): void
    {
        $cols = $pdo->query('PRAGMA table_info(' . $table . ')')->fetchAll();
        $names = array_map(static fn (array $c): string => (string) $c['name'], $cols);
        if (!in_array($column, $names, true)) {
            $pdo->exec('ALTER TABLE ' . $table . ' ADD COLUMN ' . $column . ' ' . $decl);
        }
    }

    private static function backfillTransactionsFromLegacyHoldings(PDO $pdo): void
    {
        $orphan = $pdo->query(
            'SELECT h.id, h.instrument_id, h.quantity, h.avg_cost, h.notes, h.created_at
             FROM holdings h
             LEFT JOIN transactions t ON t.instrument_id = h.instrument_id
             WHERE t.id IS NULL AND h.quantity > 0'
        )->fetchAll();

        $ins = $pdo->prepare(
            'INSERT INTO transactions
                (instrument_id, side, trade_date, quantity, unit_price, commission, notes, created_at, updated_at)
             VALUES
                (:instrument_id, \'buy\', :trade_date, :quantity, :unit_price, 0, :notes, :created_at, :updated_at)'
        );

        foreach ($orphan as $row) {
            $created = (string) $row['created_at'];
            $tradeDate = substr($created, 0, 10);
            if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $tradeDate)) {
                $tradeDate = gmdate('Y-m-d');
            }
            $ins->execute([
                'instrument_id' => (int) $row['instrument_id'],
                'trade_date' => $tradeDate,
                'quantity' => (float) $row['quantity'],
                'unit_price' => (float) $row['avg_cost'],
                'notes' => $row['notes'],
                'created_at' => $created,
                'updated_at' => $created,
            ]);
        }
    }

    /**
     * Pre–one-line-per-ticker data could create multiple instruments/holdings for the
     * same symbol (different MIC). Merge lots onto the oldest holding and drop extras.
     */
    private static function consolidateDuplicateSymbolHoldings(PDO $pdo): void
    {
        $dupes = $pdo->query(
            'SELECT UPPER(i.symbol) AS sym
             FROM holdings h
             INNER JOIN instruments i ON i.id = h.instrument_id
             GROUP BY UPPER(i.symbol)
             HAVING COUNT(*) > 1'
        )->fetchAll();

        if ($dupes === []) {
            return;
        }

        $now = gmdate('c');

        foreach ($dupes as $dupe) {
            $sym = (string) $dupe['sym'];
            $rows = $pdo->prepare(
                'SELECT h.id AS holding_id, h.instrument_id, h.notes, h.created_at
                 FROM holdings h
                 INNER JOIN instruments i ON i.id = h.instrument_id
                 WHERE i.symbol = :symbol COLLATE NOCASE
                 ORDER BY h.id ASC'
            );
            $rows->execute(['symbol' => $sym]);
            $holdings = $rows->fetchAll();
            if (count($holdings) < 2) {
                continue;
            }

            $keeper = $holdings[0];
            $keeperHoldingId = (int) $keeper['holding_id'];
            $keeperInstrumentId = (int) $keeper['instrument_id'];
            $mergedNotes = trim((string) ($keeper['notes'] ?? ''));

            $pdo->beginTransaction();
            try {
                foreach (array_slice($holdings, 1) as $extra) {
                    $extraHoldingId = (int) $extra['holding_id'];
                    $extraInstrumentId = (int) $extra['instrument_id'];
                    $extraNotes = trim((string) ($extra['notes'] ?? ''));
                    if ($extraNotes !== '' && $extraNotes !== $mergedNotes) {
                        $mergedNotes = $mergedNotes === ''
                            ? $extraNotes
                            : $mergedNotes . "\n" . $extraNotes;
                    }

                    $pdo->prepare(
                        'UPDATE transactions SET instrument_id = :keeper WHERE instrument_id = :extra'
                    )->execute([
                        'keeper' => $keeperInstrumentId,
                        'extra' => $extraInstrumentId,
                    ]);

                    self::repointInstrumentChildren($pdo, $keeperInstrumentId, $extraInstrumentId);

                    $pdo->prepare('DELETE FROM holdings WHERE id = :id')->execute(['id' => $extraHoldingId]);
                    $pdo->prepare('DELETE FROM instruments WHERE id = :id')->execute(['id' => $extraInstrumentId]);
                }

                $txns = $pdo->prepare(
                    'SELECT id, side, trade_date, quantity, unit_price, commission
                     FROM transactions WHERE instrument_id = :id
                     ORDER BY trade_date ASC, id ASC'
                );
                $txns->execute(['id' => $keeperInstrumentId]);
                $ledger = FifoLedger::walk($txns->fetchAll());
                $qty = $ledger['quantity'];
                $totalCost = $ledger['total_cost'];
                $avg = $ledger['avg_cost'];

                $pdo->prepare(
                    'UPDATE holdings
                     SET quantity = :quantity, avg_cost = :avg_cost, total_cost = :total_cost,
                         notes = :notes, updated_at = :updated_at
                     WHERE id = :id'
                )->execute([
                    'id' => $keeperHoldingId,
                    'quantity' => $qty,
                    'avg_cost' => $avg,
                    'total_cost' => $totalCost,
                    'notes' => $mergedNotes === '' ? null : $mergedNotes,
                    'updated_at' => $now,
                ]);

                $pdo->commit();
            } catch (\Throwable $e) {
                $pdo->rollBack();
                throw $e;
            }
        }
    }

    private static function repointInstrumentChildren(PDO $pdo, int $keeperId, int $extraId): void
    {
        // quotes / technicals are 1:1 — keep keeper row if present, else move extra.
        foreach (['quotes', 'technicals'] as $table) {
            $hasKeeper = $pdo->prepare("SELECT 1 FROM {$table} WHERE instrument_id = :id");
            $hasKeeper->execute(['id' => $keeperId]);
            if ($hasKeeper->fetch() !== false) {
                $pdo->prepare("DELETE FROM {$table} WHERE instrument_id = :id")->execute(['id' => $extraId]);
            } else {
                $pdo->prepare(
                    "UPDATE {$table} SET instrument_id = :keeper WHERE instrument_id = :extra"
                )->execute(['keeper' => $keeperId, 'extra' => $extraId]);
            }
        }

        $pdo->exec(
            "INSERT OR IGNORE INTO price_bars
                (instrument_id, bar_date, open, high, low, close, volume, source)
             SELECT {$keeperId}, bar_date, open, high, low, close, volume, source
             FROM price_bars WHERE instrument_id = {$extraId}"
        );
        $pdo->prepare('DELETE FROM price_bars WHERE instrument_id = :id')->execute(['id' => $extraId]);

        $pdo->prepare(
            'INSERT OR IGNORE INTO news_item_instruments (news_item_id, instrument_id)
             SELECT news_item_id, :keeper FROM news_item_instruments WHERE instrument_id = :extra'
        )->execute(['keeper' => $keeperId, 'extra' => $extraId]);
        $pdo->prepare('DELETE FROM news_item_instruments WHERE instrument_id = :id')
            ->execute(['id' => $extraId]);

        // Recommendations unique on (run, instrument, horizon) — drop extras that collide.
        $pdo->prepare(
            'DELETE FROM recommendations
             WHERE instrument_id = :extra
               AND EXISTS (
                 SELECT 1 FROM recommendations k
                 WHERE k.agent_run_id = recommendations.agent_run_id
                   AND k.instrument_id = :keeper
                   AND k.horizon = recommendations.horizon
               )'
        )->execute(['extra' => $extraId, 'keeper' => $keeperId]);
        $pdo->prepare(
            'UPDATE recommendations SET instrument_id = :keeper WHERE instrument_id = :extra'
        )->execute(['keeper' => $keeperId, 'extra' => $extraId]);
    }
}
