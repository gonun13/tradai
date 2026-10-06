<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use InvalidArgumentException;
use PDO;
use RuntimeException;
use Tradai\Api\Domain\FifoLedger;
use Tradai\Api\Domain\TradeFxSource;

final class HoldingRepository
{
    private const EPS = 1e-9;

    private readonly InstrumentRepository $instruments;

    /** @var array<string, array{rate: float, as_of: string}|null> per-request cache keyed "CCY|date" */
    private array $fxCache = [];

    /**
     * `$fx` locks trade-date FX (0031). Without it non-EUR rates stay pending; nothing else changes.
     */
    public function __construct(private readonly PDO $db, private readonly ?TradeFxSource $fx = null)
    {
        $this->instruments = new InstrumentRepository($db);
    }

    /** @return list<array<string, mixed>> */
    public function all(): array
    {
        $stmt = $this->db->query(
            'SELECT h.id, h.quantity, h.avg_cost, h.total_cost, h.notes, h.created_at, h.updated_at,
                    h.first_trade_date, h.open_lot_count, h.realized_pnl_native,
                    i.id AS instrument_id, i.isin, i.symbol, i.mic, i.currency, i.name, i.kind, i.region
             FROM holdings h
             INNER JOIN instruments i ON i.id = h.instrument_id
             ORDER BY i.symbol COLLATE NOCASE ASC'
        );

        return array_map(fn (array $row): array => $this->mapHolding($row), $stmt->fetchAll());
    }

    /** @return array<string, mixed>|null */
    public function find(int $id): ?array
    {
        $stmt = $this->db->prepare(
            'SELECT h.id, h.quantity, h.avg_cost, h.total_cost, h.notes, h.created_at, h.updated_at,
                    h.first_trade_date, h.open_lot_count, h.realized_pnl_native,
                    i.id AS instrument_id, i.isin, i.symbol, i.mic, i.currency, i.name, i.kind, i.region
             FROM holdings h
             INNER JOIN instruments i ON i.id = h.instrument_id
             WHERE h.id = :id'
        );
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();
        return $row === false ? null : $this->mapHolding($row);
    }

    /**
     * @param array<string, mixed> $input
     * @return array<string, mixed>
     */
    public function create(array $input): array
    {
        $data = $this->normalizeAcquisition($input);
        $now = gmdate('c');
        // Network before the write lock: the new lot's rate, then any gaps in older history.
        $fx = $this->tradeFx($data['currency'], $data['trade_date']);
        $existingInstrument = $this->instruments->findBySymbol($data['symbol']);
        if ($existingInstrument !== null) {
            $this->fillMissingTradeFx((int) $existingInstrument['id']);
        }

        $this->db->beginTransaction();
        try {
            // One holding line per ticker: same symbol appends a lot and re-averages. A closed
            // position's instrument is reused, so a re-entry continues the same history (0031).
            $existingHolding = $this->findHoldingIdBySymbol($data['symbol']);
            if ($existingHolding !== null) {
                $instrumentId = $existingHolding['instrument_id'];
                $this->touchInstrument($instrumentId, $data, $now);
            } else {
                $instrumentId = $this->instruments->upsert($data, $now);
            }
            $this->insertTransaction($instrumentId, 'buy', $data, $fx, $now);
            $holdingId = $this->syncPosition($instrumentId, $now, $data['notes']);
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        $created = $holdingId === null ? null : $this->find($holdingId);
        if ($created === null) {
            throw new RuntimeException('Failed to load created holding.');
        }
        return $created;
    }

    /**
     * Edit one transaction of an open holding, plus instrument metadata. Kept for the
     * acquisition form; `updateTransaction()` is the generic path (0031).
     *
     * @param array<string, mixed> $input
     * @return array{holding: ?array<string, mixed>, symbol: string, was_open: bool, is_open: bool}
     */
    public function update(int $id, array $input): array
    {
        $current = $this->find($id);
        if ($current === null) {
            throw new RuntimeException('Holding not found.', 404);
        }

        /** @var list<array<string, mixed>> $txs */
        $txs = $current['transactions'];
        $txId = isset($input['transaction_id']) ? (int) $input['transaction_id'] : null;
        $tx = null;
        if ($txId !== null && $txId > 0) {
            foreach ($txs as $candidate) {
                if ((int) $candidate['id'] === $txId) {
                    $tx = $candidate;
                    break;
                }
            }
            if ($tx === null) {
                throw new InvalidArgumentException('transaction_id does not belong to this holding');
            }
        } elseif (count($txs) === 1) {
            $tx = $txs[0];
            $txId = (int) $tx['id'];
        } elseif (count($txs) === 0) {
            throw new InvalidArgumentException('Holding has no acquisition lots to edit');
        } else {
            throw new InvalidArgumentException('transaction_id is required when the holding has multiple acquisition lots');
        }

        $merged = array_merge([
            'isin' => $current['instrument']['isin'],
            'symbol' => $current['instrument']['symbol'],
            'mic' => $current['instrument']['mic'],
            'currency' => $current['instrument']['currency'],
            'name' => $current['instrument']['name'],
            'kind' => $current['instrument']['kind'],
            'region' => $current['instrument']['region'],
            'notes' => $tx['notes'] ?? $current['notes'],
            'trade_date' => $tx['trade_date'] ?? gmdate('Y-m-d'),
            'quantity' => $tx['quantity'] ?? $current['quantity'],
            'unit_price' => $tx['unit_price'] ?? $current['avg_cost'],
            'commission' => $tx['commission'] ?? 0,
        ], array_intersect_key($input, array_flip([
            'isin', 'symbol', 'mic', 'currency', 'name', 'kind', 'region', 'notes',
            'trade_date', 'quantity', 'unit_price', 'commission',
            // legacy alias
            'avg_cost',
        ])));

        if (!isset($input['unit_price']) && isset($input['avg_cost']) && !isset($input['commission'])) {
            // Legacy clients sending avg_cost alone: treat as all-in unit cost with zero commission.
            $merged['unit_price'] = $input['avg_cost'];
            $merged['commission'] = 0;
        }

        // Keep ticker identity on the existing instrument (one line per symbol).
        $merged['symbol'] = $current['instrument']['symbol'];

        $data = $this->normalizeAcquisition($merged);
        $now = gmdate('c');
        $instrumentId = (int) $current['instrument']['id'];
        $previousCurrency = strtoupper((string) $current['instrument']['currency']);
        if ($previousCurrency !== $data['currency']) {
            // Every locked rate was for the old currency.
            $this->db->prepare('UPDATE transactions SET fx_to_eur = NULL, fx_as_of = NULL WHERE instrument_id = :id')
                ->execute(['id' => $instrumentId]);
        }
        $fx = $this->rateForEdit($tx + ['currency' => $previousCurrency], $data['currency'], $data['trade_date']);
        $this->fillMissingTradeFx($instrumentId);

        $this->db->beginTransaction();
        try {
            $this->touchInstrument($instrumentId, $data, $now);
            $this->updateTransactionRow($txId, $data, $fx, $now);
            // The acquisition form's notes field is the holding's notes (pre-0031 behaviour).
            $holdingId = $this->syncPosition($instrumentId, $now, $data['notes'], replaceNotes: true);
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        return $this->mutationResult($holdingId, (string) $current['instrument']['symbol'], true);
    }

    /**
     * Erase an open holding and its whole history — for entries made in error; exiting is a
     * sell (0031). Returns the symbol so the caller can drop the name back onto the tracker
     * it was promoted from (0019).
     */
    public function delete(int $id): string
    {
        $current = $this->find($id);
        if ($current === null) {
            throw new RuntimeException('Holding not found.', 404);
        }

        $this->eraseHistory((int) $current['instrument']['id']);
        return (string) $current['instrument']['symbol'];
    }

    /** Erase a closed position's history (0031). Returns its symbol. */
    public function eraseClosed(int $instrumentId): string
    {
        $instrument = $this->closedInstrument($instrumentId);
        $this->eraseHistory($instrumentId);
        return (string) $instrument['symbol'];
    }

    /**
     * Record a sell against an open holding (0031). FIFO consumes the oldest lots; selling
     * more than is open on the trade date is rejected and nothing is written.
     *
     * @param array<string, mixed> $input trade_date, quantity, unit_price, commission?, notes?
     * @return array{holding: ?array<string, mixed>, symbol: string, was_open: bool, is_open: bool, disposal: ?array<string, mixed>}
     */
    public function recordSell(int $holdingId, array $input): array
    {
        $current = $this->find($holdingId);
        if ($current === null) {
            throw new RuntimeException('Holding not found.', 404);
        }
        $instrumentId = (int) $current['instrument']['id'];
        $currency = (string) $current['instrument']['currency'];
        $data = $this->normalizeTrade($input);
        $now = gmdate('c');
        $fx = $this->tradeFx($currency, $data['trade_date']);
        $this->fillMissingTradeFx($instrumentId);

        $this->db->beginTransaction();
        try {
            $txId = $this->insertTransaction($instrumentId, 'sell', $data, $fx, $now);
            $newHoldingId = $this->syncPosition($instrumentId, $now);
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        $result = $this->mutationResult($newHoldingId, (string) $current['instrument']['symbol'], true);
        $result['disposal'] = $this->disposalForSell($txId, $currency);
        return $result;
    }

    /**
     * Edit any transaction, buy or sell (0031). The ledger is re-walked; an oversold result
     * is rejected.
     *
     * @param array<string, mixed> $input trade_date?, quantity?, unit_price?, commission?, notes?
     * @return array{holding: ?array<string, mixed>, symbol: string, was_open: bool, is_open: bool}
     */
    public function updateTransaction(int $txId, array $input): array
    {
        $tx = $this->transactionWithInstrument($txId);
        $merged = array_merge(
            [
                'trade_date' => $tx['trade_date'],
                'quantity' => $tx['quantity'],
                'unit_price' => $tx['unit_price'],
                'commission' => $tx['commission'],
                'notes' => $tx['notes'],
            ],
            array_intersect_key($input, array_flip(['trade_date', 'quantity', 'unit_price', 'commission', 'notes']))
        );
        $data = $this->normalizeTrade($merged);
        $instrumentId = (int) $tx['instrument_id'];
        $wasOpen = $this->openHoldingId($instrumentId) !== null;
        $fx = $this->rateForEdit($tx, (string) $tx['currency'], $data['trade_date']);
        $this->fillMissingTradeFx($instrumentId);
        $now = gmdate('c');

        $this->db->beginTransaction();
        try {
            $this->updateTransactionRow($txId, $data, $fx, $now);
            $holdingId = $this->syncPosition($instrumentId, $now);
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        return $this->mutationResult($holdingId, (string) $tx['symbol'], $wasOpen);
    }

    /**
     * Delete one transaction (0031). Removing the closing sell reopens the position; removing
     * a buy that a later sell depends on is rejected.
     *
     * @return array{holding: ?array<string, mixed>, symbol: string, was_open: bool, is_open: bool}
     */
    public function deleteTransaction(int $txId): array
    {
        $tx = $this->transactionWithInstrument($txId);
        $instrumentId = (int) $tx['instrument_id'];
        $wasOpen = $this->openHoldingId($instrumentId) !== null;
        $now = gmdate('c');

        $this->db->beginTransaction();
        try {
            $this->db->prepare('DELETE FROM transactions WHERE id = :id')->execute(['id' => $txId]);
            $holdingId = $this->syncPosition($instrumentId, $now);
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        return $this->mutationResult($holdingId, (string) $tx['symbol'], $wasOpen);
    }

    /**
     * Instruments with history and no open position (0031), newest close first.
     *
     * @return list<array<string, mixed>>
     */
    public function closedPositions(): array
    {
        $rows = $this->db->query(
            'SELECT i.id, i.isin, i.symbol, i.mic, i.currency, i.name, i.kind, i.region
             FROM instruments i
             WHERE EXISTS (SELECT 1 FROM transactions t WHERE t.instrument_id = i.id)
               AND NOT EXISTS (SELECT 1 FROM holdings h WHERE h.instrument_id = i.id)'
        )->fetchAll();
        $display = CurrencyRates::display($this->db);

        $out = [];
        foreach ($rows as $row) {
            $instrumentId = (int) $row['id'];
            $raw = $this->rawTransactions($instrumentId);
            $ledger = FifoLedger::walk($raw);
            if ($ledger['quantity'] > self::EPS) {
                continue;
            }
            $bought = 0.0;
            foreach ($raw as $t) {
                if ($t['side'] === 'buy') {
                    $bought += (float) $t['quantity'];
                }
            }
            $sold = 0.0;
            $cost = 0.0;
            $proceeds = 0.0;
            $realized = 0.0;
            $realizedEur = 0.0;
            foreach ($ledger['disposals'] as $d) {
                $sold += (float) $d['quantity'];
                $cost += (float) $d['cost'];
                $proceeds += (float) $d['proceeds'];
                $realized += (float) $d['realized_pnl'];
                $realizedEur = ($realizedEur === null || $d['realized_pnl_eur'] === null)
                    ? null
                    : $realizedEur + (float) $d['realized_pnl_eur'];
            }
            $out[] = [
                'instrument' => [
                    'id' => $instrumentId,
                    'isin' => $row['isin'],
                    'symbol' => $row['symbol'],
                    'mic' => $row['mic'],
                    'currency' => (string) $row['currency'],
                    'name' => $row['name'],
                    'kind' => $row['kind'],
                    'region' => $row['region'],
                ],
                'opened_at' => $ledger['opened_at'],
                'closed_at' => $ledger['closed_at'],
                'held_days' => $this->daysBetween($ledger['opened_at'], $ledger['closed_at']),
                'quantity_bought' => $bought,
                'quantity_sold' => $sold,
                'cost_native' => $cost,
                'proceeds_native' => $proceeds,
                'realized_pnl_native' => $realized,
                'realized_pnl_eur' => $realizedEur === null ? null : round($realizedEur, 2),
                'realized_pnl_display' => RealizedGains::eurToDisplay($this->db, $realizedEur, $display),
                'realized_pct' => $cost > 0 ? ($realized / $cost) * 100.0 : null,
                'display_currency' => $display,
                'transactions' => $this->transactionsForInstrument($instrumentId),
            ];
        }
        usort($out, static fn (array $a, array $b): int => strcmp((string) $b['closed_at'], (string) $a['closed_at']));
        return $out;
    }

    /**
     * Book-level P&L for `GET /holdings` (0031). Unrealised uses today's FX on both sides;
     * realised is locked EUR. Any pending constituent makes that figure null.
     *
     * @param list<array<string, mixed>> $holdings rows from all()
     * @return array{unrealized_pnl_display: ?float, realized_ytd_display: ?float, realized_all_time_display: ?float, total_pnl_display: ?float}
     */
    public function summary(array $holdings): array
    {
        $display = CurrencyRates::display($this->db);
        $unrealized = 0.0;
        foreach ($holdings as $h) {
            if ($h['pnl_display'] === null) {
                $unrealized = null;
                break;
            }
            $unrealized += (float) $h['pnl_display'];
        }
        $allTime = RealizedGains::display($this->db, $display);

        return [
            'unrealized_pnl_display' => $unrealized === null ? null : round($unrealized, 2),
            'realized_ytd_display' => RealizedGains::display($this->db, $display, gmdate('Y')),
            'realized_all_time_display' => $allTime,
            'total_pnl_display' => ($unrealized === null || $allTime === null) ? null : round($unrealized + $allTime, 2),
        ];
    }

    /**
     * Fetch trade-date rates still missing (0031) and re-sync each instrument that gained
     * one. Best effort: a rate that stays unavailable leaves its figures pending.
     *
     * @return int number of transactions that received a rate
     */
    public function fillMissingTradeFx(?int $instrumentId = null): int
    {
        $sql = 'SELECT t.id, t.instrument_id, t.trade_date, i.currency
                FROM transactions t INNER JOIN instruments i ON i.id = t.instrument_id
                WHERE t.fx_to_eur IS NULL';
        $params = [];
        if ($instrumentId !== null) {
            $sql .= ' AND t.instrument_id = :id';
            $params['id'] = $instrumentId;
        }
        $stmt = $this->db->prepare($sql);
        $stmt->execute($params);
        $pending = $stmt->fetchAll();

        $upd = $this->db->prepare('UPDATE transactions SET fx_to_eur = :rate, fx_as_of = :as_of WHERE id = :id');
        $filled = 0;
        $touched = [];
        foreach ($pending as $row) {
            $fx = $this->tradeFx((string) $row['currency'], (string) $row['trade_date']);
            if ($fx === null) {
                continue;
            }
            $upd->execute(['rate' => $fx['rate'], 'as_of' => $fx['as_of'], 'id' => (int) $row['id']]);
            $filled++;
            $touched[(int) $row['instrument_id']] = true;
        }

        // Re-sync now rather than relying on the caller's write: that write may be rejected.
        $now = gmdate('c');
        foreach (array_keys($touched) as $id) {
            $this->db->beginTransaction();
            try {
                $this->syncPosition($id, $now);
                $this->db->commit();
            } catch (\Throwable $e) {
                $this->db->rollBack();
                throw $e;
            }
        }
        return $filled;
    }

    /**
     * @param array<string, mixed> $input
     * @return array{
     *   isin: ?string,
     *   symbol: string,
     *   mic: ?string,
     *   currency: string,
     *   name: ?string,
     *   kind: string,
     *   region: ?string,
     *   notes: ?string,
     *   trade_date: string,
     *   quantity: float,
     *   unit_price: float,
     *   commission: float
     * }
     */
    private function normalizeAcquisition(array $input): array
    {
        $symbol = strtoupper(trim((string) ($input['symbol'] ?? '')));
        if ($symbol === '') {
            throw new InvalidArgumentException('symbol is required');
        }

        $kind = strtolower(trim((string) ($input['kind'] ?? 'equity')));
        if (!in_array($kind, ['equity', 'etf'], true)) {
            throw new InvalidArgumentException('kind must be equity or etf');
        }

        $currency = strtoupper(trim((string) ($input['currency'] ?? '')));
        if ($currency === '' || strlen($currency) !== 3) {
            throw new InvalidArgumentException('currency must be a 3-letter code');
        }

        $region = $input['region'] ?? null;
        if ($region !== null && $region !== '') {
            $region = strtolower(trim((string) $region));
            if (!in_array($region, ['eu', 'us'], true)) {
                throw new InvalidArgumentException('region must be eu or us');
            }
        } else {
            $region = null;
        }

        $trade = $this->normalizeTrade($input);

        $isin = $this->nullableString($input['isin'] ?? null);
        $mic = $this->nullableString($input['mic'] ?? null);
        $name = $this->nullableString($input['name'] ?? null);

        if ($isin !== null) {
            $isin = strtoupper($isin);
        }
        if ($mic !== null) {
            $mic = strtoupper($mic);
        }

        return [
            'isin' => $isin,
            'symbol' => $symbol,
            'mic' => $mic,
            'currency' => $currency,
            'name' => $name,
            'kind' => $kind,
            'region' => $region,
        ] + $trade;
    }

    /**
     * The fields every transaction carries, buy or sell.
     *
     * @param array<string, mixed> $input
     * @return array{trade_date: string, quantity: float, unit_price: float, commission: float, notes: ?string}
     */
    private function normalizeTrade(array $input): array
    {
        $tradeDate = trim((string) ($input['trade_date'] ?? ''));
        [$y, $m, $d] = array_map('intval', explode('-', $tradeDate . '--'));
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $tradeDate) || !checkdate($m, $d, $y)) {
            throw new InvalidArgumentException('trade_date must be YYYY-MM-DD');
        }

        $quantity = $input['quantity'] ?? null;
        $unitPrice = $input['unit_price'] ?? $input['avg_cost'] ?? null;
        $commission = $input['commission'] ?? 0;
        if ($commission === null || $commission === '') {
            $commission = 0;
        }

        if (!is_numeric($quantity) || (float) $quantity <= 0) {
            throw new InvalidArgumentException('quantity must be a positive number');
        }
        if (!is_numeric($unitPrice) || (float) $unitPrice < 0) {
            throw new InvalidArgumentException('unit_price must be a non-negative number');
        }
        if (!is_numeric($commission) || (float) $commission < 0) {
            throw new InvalidArgumentException('commission must be a non-negative number');
        }

        return [
            'trade_date' => $tradeDate,
            'quantity' => (float) $quantity,
            'unit_price' => (float) $unitPrice,
            'commission' => (float) $commission,
            'notes' => $this->nullableString($input['notes'] ?? null),
        ];
    }

    /**
     * @param array<string, mixed> $data trade fields from normalizeTrade()/normalizeAcquisition()
     * @param array{rate: float, as_of: string}|null $fx
     */
    private function insertTransaction(int $instrumentId, string $side, array $data, ?array $fx, string $now): int
    {
        $stmt = $this->db->prepare(
            'INSERT INTO transactions
                (instrument_id, side, trade_date, quantity, unit_price, commission, notes,
                 fx_to_eur, fx_as_of, created_at, updated_at)
             VALUES
                (:instrument_id, :side, :trade_date, :quantity, :unit_price, :commission, :notes,
                 :fx_to_eur, :fx_as_of, :created_at, :updated_at)'
        );
        $stmt->execute([
            'instrument_id' => $instrumentId,
            'side' => $side,
            'trade_date' => $data['trade_date'],
            'quantity' => $data['quantity'],
            'unit_price' => $data['unit_price'],
            'commission' => $data['commission'],
            'notes' => $data['notes'],
            'fx_to_eur' => $fx['rate'] ?? null,
            'fx_as_of' => $fx['as_of'] ?? null,
            'created_at' => $now,
            'updated_at' => $now,
        ]);
        return (int) $this->db->lastInsertId();
    }

    /**
     * @param array<string, mixed> $data
     * @param array{rate: float, as_of: string}|null $fx
     */
    private function updateTransactionRow(int $transactionId, array $data, ?array $fx, string $now): void
    {
        $stmt = $this->db->prepare(
            'UPDATE transactions
             SET trade_date = :trade_date, quantity = :quantity, unit_price = :unit_price,
                 commission = :commission, notes = :notes, fx_to_eur = :fx_to_eur,
                 fx_as_of = :fx_as_of, updated_at = :updated_at
             WHERE id = :id'
        );
        $stmt->execute([
            'id' => $transactionId,
            'trade_date' => $data['trade_date'],
            'quantity' => $data['quantity'],
            'unit_price' => $data['unit_price'],
            'commission' => $data['commission'],
            'notes' => $data['notes'],
            'fx_to_eur' => $fx['rate'] ?? null,
            'fx_as_of' => $fx['as_of'] ?? null,
            'updated_at' => $now,
        ]);
        if ($stmt->rowCount() === 0) {
            throw new RuntimeException('Transaction not found.', 404);
        }
    }

    /**
     * @return array{holding_id: int, instrument_id: int}|null
     */
    private function findHoldingIdBySymbol(string $symbol): ?array
    {
        $stmt = $this->db->prepare(
            'SELECT h.id AS holding_id, h.instrument_id
             FROM holdings h
             INNER JOIN instruments i ON i.id = h.instrument_id
             WHERE i.symbol = :symbol COLLATE NOCASE
             ORDER BY h.id ASC
             LIMIT 1'
        );
        $stmt->execute(['symbol' => $symbol]);
        $row = $stmt->fetch();
        if ($row === false) {
            return null;
        }
        return [
            'holding_id' => (int) $row['holding_id'],
            'instrument_id' => (int) $row['instrument_id'],
        ];
    }

    /**
     * @param array<string, mixed> $data
     */
    private function touchInstrument(int $instrumentId, array $data, string $now): void
    {
        $upd = $this->db->prepare(
            'UPDATE instruments
             SET isin = COALESCE(:isin, isin),
                 mic = COALESCE(:mic, mic),
                 currency = :currency,
                 name = COALESCE(:name, name),
                 kind = :kind,
                 region = COALESCE(:region, region),
                 updated_at = :updated_at
             WHERE id = :id'
        );
        $upd->execute([
            'id' => $instrumentId,
            'isin' => $data['isin'],
            'mic' => $data['mic'],
            'currency' => $data['currency'],
            'name' => $data['name'],
            'kind' => $data['kind'],
            'region' => $data['region'],
            'updated_at' => $now,
        ]);
    }

    /**
     * Re-walk the instrument's FIFO ledger and make the rollup match it (0031): a holdings row
     * exists only while open quantity > 0. Must run inside the caller's DB transaction; an
     * oversold ledger throws so that transaction rolls back.
     *
     * @return int|null the open holding id, or null when the position is closed
     */
    private function syncPosition(
        int $instrumentId,
        string $now,
        ?string $notes = null,
        bool $replaceNotes = false,
    ): ?int {
        $ledger = FifoLedger::walk($this->rawTransactions($instrumentId));
        if ($ledger['oversold_quantity'] > self::EPS) {
            throw new InvalidArgumentException(sprintf(
                'Sell exceeds the open quantity on %s by %s.',
                (string) $ledger['oversold_on'],
                rtrim(rtrim(number_format($ledger['oversold_quantity'], 6, '.', ''), '0'), '.')
            ));
        }

        $this->persistDisposals($instrumentId, $ledger['disposals'], $now);

        $cur = $this->db->prepare('SELECT id, notes FROM holdings WHERE instrument_id = :id');
        $cur->execute(['id' => $instrumentId]);
        $existing = $cur->fetch();

        if ($ledger['quantity'] <= self::EPS) {
            // Full exit: the rollup goes, the transactions and disposals stay.
            if ($existing !== false) {
                $this->db->prepare('DELETE FROM holdings WHERE id = :id')->execute(['id' => (int) $existing['id']]);
            }
            return null;
        }

        $prev = $existing === false ? '' : trim((string) ($existing['notes'] ?? ''));
        if ($replaceNotes) {
            $merged = $notes;
        } elseif ($notes !== null && $notes !== '' && $prev !== '' && $prev !== $notes) {
            $merged = $prev . "\n" . $notes;
        } elseif ($notes !== null && $notes !== '' && $prev === '') {
            $merged = $notes;
        } else {
            $merged = $prev === '' ? null : $prev;
        }

        $realized = 0.0;
        foreach ($ledger['disposals'] as $d) {
            $realized += (float) $d['realized_pnl'];
        }

        $values = [
            'quantity' => $ledger['quantity'],
            'avg_cost' => $ledger['avg_cost'],
            'total_cost' => $ledger['total_cost'],
            'first_trade_date' => $ledger['first_trade_date'],
            'open_lot_count' => $ledger['lot_count'],
            'realized_pnl_native' => $realized,
            'notes' => $merged,
            'updated_at' => $now,
        ];

        if ($existing === false) {
            $this->db->prepare(
                'INSERT INTO holdings
                    (instrument_id, quantity, avg_cost, total_cost, first_trade_date, open_lot_count,
                     realized_pnl_native, notes, created_at, updated_at)
                 VALUES
                    (:instrument_id, :quantity, :avg_cost, :total_cost, :first_trade_date, :open_lot_count,
                     :realized_pnl_native, :notes, :created_at, :updated_at)'
            )->execute($values + ['instrument_id' => $instrumentId, 'created_at' => $now]);
            return (int) $this->db->lastInsertId();
        }

        $this->db->prepare(
            'UPDATE holdings
             SET quantity = :quantity, avg_cost = :avg_cost, total_cost = :total_cost,
                 first_trade_date = :first_trade_date, open_lot_count = :open_lot_count,
                 realized_pnl_native = :realized_pnl_native,
                 notes = :notes, updated_at = :updated_at
             WHERE id = :id'
        )->execute($values + ['id' => (int) $existing['id']]);
        return (int) $existing['id'];
    }

    /** @return list<array<string, mixed>> */
    private function rawTransactions(int $instrumentId): array
    {
        $stmt = $this->db->prepare(
            'SELECT id, side, trade_date, quantity, unit_price, commission, fx_to_eur
             FROM transactions
             WHERE instrument_id = :id
             ORDER BY trade_date ASC, id ASC'
        );
        $stmt->execute(['id' => $instrumentId]);
        return $stmt->fetchAll();
    }

    /**
     * Disposals are fully derived from the transaction log, so the instrument's rows are
     * replaced wholesale on every recompute rather than patched.
     *
     * @param list<array<string, mixed>> $disposals
     */
    private function persistDisposals(int $instrumentId, array $disposals, string $now): void
    {
        $this->db->prepare('DELETE FROM realized_disposals WHERE instrument_id = :id')
            ->execute(['id' => $instrumentId]);

        if ($disposals === []) {
            return;
        }

        $cur = $this->db->prepare('SELECT currency FROM instruments WHERE id = :id');
        $cur->execute(['id' => $instrumentId]);
        $currency = strtoupper((string) ($cur->fetchColumn() ?: 'EUR'));

        $ins = $this->db->prepare(
            'INSERT INTO realized_disposals
                (instrument_id, sell_transaction_id, trade_date, quantity,
                 proceeds, cost, realized_pnl, currency,
                 proceeds_eur, cost_eur, realized_pnl_eur, created_at)
             VALUES
                (:instrument_id, :sell_transaction_id, :trade_date, :quantity,
                 :proceeds, :cost, :realized_pnl, :currency,
                 :proceeds_eur, :cost_eur, :realized_pnl_eur, :created_at)'
        );
        foreach ($disposals as $d) {
            $ins->execute([
                'instrument_id' => $instrumentId,
                'sell_transaction_id' => (int) $d['sell_transaction_id'],
                'trade_date' => (string) $d['trade_date'],
                'quantity' => (float) $d['quantity'],
                'proceeds' => (float) $d['proceeds'],
                'cost' => (float) $d['cost'],
                'realized_pnl' => (float) $d['realized_pnl'],
                'currency' => $currency,
                'proceeds_eur' => $d['proceeds_eur'],
                'cost_eur' => $d['cost_eur'],
                'realized_pnl_eur' => $d['realized_pnl_eur'],
                'created_at' => $now,
            ]);
        }
    }

    private function eraseHistory(int $instrumentId): void
    {
        $this->db->beginTransaction();
        try {
            foreach (['realized_disposals', 'transactions', 'holdings'] as $table) {
                $this->db->prepare('DELETE FROM ' . $table . ' WHERE instrument_id = :id')
                    ->execute(['id' => $instrumentId]);
            }
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }
    }

    /** @return array<string, mixed> */
    private function closedInstrument(int $instrumentId): array
    {
        if ($this->openHoldingId($instrumentId) !== null) {
            throw new RuntimeException('Position is still open — erase it from Holdings.', 409);
        }
        $stmt = $this->db->prepare(
            'SELECT i.id, i.symbol FROM instruments i
             WHERE i.id = :id AND EXISTS (SELECT 1 FROM transactions t WHERE t.instrument_id = i.id)'
        );
        $stmt->execute(['id' => $instrumentId]);
        $row = $stmt->fetch();
        if ($row === false) {
            throw new RuntimeException('Closed position not found.', 404);
        }
        return $row;
    }

    private function openHoldingId(int $instrumentId): ?int
    {
        $stmt = $this->db->prepare('SELECT id FROM holdings WHERE instrument_id = :id');
        $stmt->execute(['id' => $instrumentId]);
        $id = $stmt->fetchColumn();
        return $id === false ? null : (int) $id;
    }

    /** @return array<string, mixed> */
    private function transactionWithInstrument(int $txId): array
    {
        $stmt = $this->db->prepare(
            'SELECT t.id, t.instrument_id, t.side, t.trade_date, t.quantity, t.unit_price, t.commission,
                    t.notes, t.fx_to_eur, t.fx_as_of, i.symbol, i.currency
             FROM transactions t INNER JOIN instruments i ON i.id = t.instrument_id
             WHERE t.id = :id'
        );
        $stmt->execute(['id' => $txId]);
        $row = $stmt->fetch();
        if ($row === false) {
            throw new RuntimeException('Transaction not found.', 404);
        }
        return $row;
    }

    /**
     * @return array{holding: ?array<string, mixed>, symbol: string, was_open: bool, is_open: bool}
     */
    private function mutationResult(?int $holdingId, string $symbol, bool $wasOpen): array
    {
        return [
            'holding' => $holdingId === null ? null : $this->find($holdingId),
            'symbol' => $symbol,
            'was_open' => $wasOpen,
            'is_open' => $holdingId !== null,
        ];
    }

    /** @return array<string, mixed>|null */
    private function disposalForSell(int $sellTxId, string $currency): ?array
    {
        $stmt = $this->db->prepare(
            'SELECT trade_date, quantity, proceeds, cost, realized_pnl, proceeds_eur, cost_eur, realized_pnl_eur
             FROM realized_disposals WHERE sell_transaction_id = :id'
        );
        $stmt->execute(['id' => $sellTxId]);
        $row = $stmt->fetch();
        if ($row === false) {
            return null;
        }
        $eur = $row['realized_pnl_eur'] === null ? null : (float) $row['realized_pnl_eur'];
        return [
            'trade_date' => $row['trade_date'],
            'quantity' => (float) $row['quantity'],
            'currency' => $currency,
            'proceeds_native' => (float) $row['proceeds'],
            'cost_native' => (float) $row['cost'],
            'realized_pnl_native' => (float) $row['realized_pnl'],
            'realized_pnl_eur' => $eur,
            'realized_pnl_display' => RealizedGains::eurToDisplay($this->db, $eur, CurrencyRates::display($this->db)),
            'display_currency' => CurrencyRates::display($this->db),
        ];
    }

    /**
     * Rate for `$currency` on `$date`: 1.0 for EUR, else the injected source (cached per
     * request). Null when no source is configured or it has no rate.
     *
     * @return array{rate: float, as_of: string}|null
     */
    private function tradeFx(string $currency, string $date): ?array
    {
        $currency = strtoupper(trim($currency));
        if ($currency === 'EUR') {
            return ['rate' => 1.0, 'as_of' => $date];
        }
        if ($this->fx === null) {
            return null;
        }
        $key = $currency . '|' . $date;
        if (!array_key_exists($key, $this->fxCache)) {
            $this->fxCache[$key] = $this->fx->rateToEurOn($currency, $date);
        }
        return $this->fxCache[$key];
    }

    /**
     * Keep a transaction's locked rate unless its date or currency changed.
     *
     * @param array<string, mixed> $tx current row (needs trade_date, fx_to_eur, fx_as_of)
     * @return array{rate: float, as_of: string}|null
     */
    private function rateForEdit(array $tx, string $currency, string $tradeDate): ?array
    {
        $sameCurrency = !isset($tx['currency']) || strtoupper((string) $tx['currency']) === strtoupper($currency);
        if ($sameCurrency && $tx['trade_date'] === $tradeDate && ($tx['fx_to_eur'] ?? null) !== null) {
            return ['rate' => (float) $tx['fx_to_eur'], 'as_of' => (string) ($tx['fx_as_of'] ?? $tradeDate)];
        }
        return $this->tradeFx($currency, $tradeDate);
    }

    private function daysBetween(?string $from, ?string $to): ?int
    {
        if ($from === null || $to === null) {
            return null;
        }
        $a = strtotime($from);
        $b = strtotime($to);
        return ($a === false || $b === false) ? null : (int) floor(($b - $a) / 86400);
    }

    private function nullableString(mixed $value): ?string
    {
        if ($value === null) {
            return null;
        }
        $trimmed = trim((string) $value);
        return $trimmed === '' ? null : $trimmed;
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function mapHolding(array $row): array
    {
        $instrumentId = (int) $row['instrument_id'];
        $transactions = $this->transactionsForInstrument($instrumentId);
        $lotCount = count($transactions);
        $buys = array_values(array_filter($transactions, static fn (array $t): bool => $t['side'] === 'buy'));
        $latest = $buys === [] ? null : $buys[count($buys) - 1];
        $currency = (string) $row['currency'];
        $qty = (float) $row['quantity'];
        $totalCost = (float) $row['total_cost'];
        $avgCost = (float) $row['avg_cost'];
        $displayCurrency = CurrencyRates::display($this->db);

        $quote = $this->latestQuote($instrumentId);
        $fxToDisplay = CurrencyRates::rate($this->db, $currency, $displayCurrency);
        // Prefer quote currency for mark-to-market FX when vendors report a different currency.
        $quoteCurrency = isset($quote['currency']) ? (string) $quote['currency'] : $currency;
        $fxQuoteToDisplay = CurrencyRates::rate($this->db, $quoteCurrency, $displayCurrency);

        $costDisplay = $fxToDisplay === null ? null : $totalCost * $fxToDisplay;
        $price = $quote['price'] ?? null;
        $marketValueNative = $price === null ? null : $qty * $price;
        $marketValueDisplay = ($marketValueNative === null || $fxQuoteToDisplay === null)
            ? null
            : $marketValueNative * $fxQuoteToDisplay;
        $pnlNative = ($marketValueNative === null || strtoupper($quoteCurrency) !== strtoupper($currency))
            ? null
            : $marketValueNative - $totalCost;
        $pnlDisplay = ($marketValueDisplay === null || $costDisplay === null)
            ? null
            : $marketValueDisplay - $costDisplay;
        // 0031: realised from partial sells, locked in EUR at trade dates.
        $realizedDisplay = RealizedGains::display($this->db, $displayCurrency, null, $instrumentId);

        return [
            'id' => (int) $row['id'],
            'quantity' => $qty,
            'avg_cost' => $avgCost,
            'total_cost' => $totalCost,
            'notes' => $row['notes'],
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
            // Display rollup: one line per ticker; lot details live under transactions.
            'lot_count' => $lotCount,
            'trade_date' => $latest['trade_date'] ?? null,
            // 0013: FIFO-derived. `first_trade_date` is how long the position has actually
            // been held — a 12-year-old dead position and a fresh conviction add both show
            // a deep negative P&L, and nothing else distinguishes them.
            'first_trade_date' => $row['first_trade_date'] ?? null,
            'held_days' => $this->heldDays($row['first_trade_date'] ?? null),
            'open_lot_count' => $row['open_lot_count'] !== null ? (int) $row['open_lot_count'] : null,
            'realized_pnl_native' => $row['realized_pnl_native'] !== null
                ? (float) $row['realized_pnl_native']
                : null,
            'realized_pnl_display' => $realizedDisplay,
            'total_pnl_display' => ($pnlDisplay === null || $realizedDisplay === null)
                ? null
                : $pnlDisplay + $realizedDisplay,
            'unit_price' => $avgCost,
            'commission' => null,
            'cost_native' => $totalCost,
            'cost_display' => $costDisplay,
            'market_value_native' => $marketValueNative,
            'market_value_display' => $marketValueDisplay,
            'pnl_native' => $pnlNative,
            'pnl_display' => $pnlDisplay,
            'pnl_pct' => ($totalCost > 0 && $pnlNative !== null)
                ? ($pnlNative / $totalCost) * 100.0
                : null,
            'daily_change_pct' => $this->dailyChangePct($instrumentId, $quote),
            'fx_to_display' => $fxToDisplay,
            'display_currency' => $displayCurrency,
            'quote' => $quote,
            'technicals' => $this->technicalsForInstrument($instrumentId),
            'fundamentals' => $this->fundamentalsForInstrument($instrumentId),
            // Advisory sends the five most recent linked items. Keep the read model at the
            // same depth so the context preview is an honest picture of the next run.
            'news' => $this->newsForInstrument($instrumentId, 5),
            'instrument' => [
                'id' => $instrumentId,
                'isin' => $row['isin'],
                'symbol' => $row['symbol'],
                'mic' => $row['mic'],
                'currency' => $currency,
                'name' => $row['name'],
                'kind' => $row['kind'],
                'region' => $row['region'],
            ],
            'transactions' => $transactions,
        ];
    }

    private function heldDays(?string $firstTradeDate): ?int
    {
        if ($firstTradeDate === null || $firstTradeDate === '') {
            return null;
        }
        $start = strtotime($firstTradeDate);
        if ($start === false) {
            return null;
        }
        return (int) floor((time() - $start) / 86400);
    }

    /** @return array<string, mixed>|null */
    private function technicalsForInstrument(int $instrumentId): ?array
    {
        $stmt = $this->db->prepare(
            'SELECT as_of, features_json, source, updated_at
             FROM technicals WHERE instrument_id = :id'
        );
        $stmt->execute(['id' => $instrumentId]);
        $row = $stmt->fetch();
        if ($row === false) {
            return null;
        }
        $features = json_decode((string) $row['features_json'], true);
        if (!is_array($features)) {
            $features = [];
        }
        return [
            'as_of' => $row['as_of'],
            'source' => $row['source'],
            'updated_at' => $row['updated_at'],
            'features' => $features,
        ];
    }

    /** @return array<string, mixed>|null */
    private function fundamentalsForInstrument(int $instrumentId): ?array
    {
        $stmt = $this->db->prepare(
            'SELECT payload_json, source, as_of, completeness_state, coverage_score,
                    missing_fields_json, updated_at
             FROM fundamentals WHERE instrument_id = :id'
        );
        $stmt->execute(['id' => $instrumentId]);
        $row = $stmt->fetch();
        if ($row === false) {
            return null;
        }
        $payload = json_decode((string) $row['payload_json'], true);
        $missing = json_decode((string) $row['missing_fields_json'], true);
        return [
            'payload' => is_array($payload) ? $payload : [],
            'source' => $row['source'],
            'as_of' => $row['as_of'],
            'completeness_state' => $row['completeness_state'],
            'coverage_score' => (float) $row['coverage_score'],
            'missing_fields' => is_array($missing) ? $missing : [],
            'updated_at' => $row['updated_at'],
        ];
    }

    /** @return list<array<string, mixed>> */
    private function newsForInstrument(int $instrumentId, int $limit = 3): array
    {
        $stmt = $this->db->prepare(
            'SELECT n.id, n.title, n.snippet, n.url, n.source_name, n.published_at,
                    n.fetched_at, n.adapter_source
             FROM news_items n
             INNER JOIN news_item_instruments nii ON nii.news_item_id = n.id
             WHERE nii.instrument_id = :id
             ORDER BY COALESCE(n.published_at, n.fetched_at) DESC
             LIMIT :limit'
        );
        $stmt->bindValue('id', $instrumentId, PDO::PARAM_INT);
        $stmt->bindValue('limit', $limit, PDO::PARAM_INT);
        $stmt->execute();
        $rows = $stmt->fetchAll();
        $out = [];
        foreach ($rows as $row) {
            $out[] = [
                'id' => (int) $row['id'],
                'title' => $row['title'],
                'snippet' => $row['snippet'],
                'url' => $row['url'],
                'source_name' => $row['source_name'],
                'adapter_source' => $row['adapter_source'],
                'published_at' => $row['published_at'],
                'fetched_at' => $row['fetched_at'],
            ];
        }
        return $out;
    }

    /** @return array<string, mixed>|null */
    private function latestQuote(int $instrumentId): ?array
    {
        $stmt = $this->db->prepare(
            'SELECT price, currency, as_of, source, updated_at
             FROM quotes WHERE instrument_id = :id'
        );
        $stmt->execute(['id' => $instrumentId]);
        $row = $stmt->fetch();
        if ($row === false) {
            return null;
        }
        return [
            'price' => (float) $row['price'],
            'currency' => $row['currency'],
            'as_of' => $row['as_of'],
            'source' => $row['source'],
            'updated_at' => $row['updated_at'],
        ];
    }

    /** @param array<string, mixed>|null $quote */
    private function dailyChangePct(int $instrumentId, ?array $quote): ?float
    {
        if ($quote === null || !isset($quote['price'], $quote['as_of'])) {
            return null;
        }

        $quotePrice = (float) $quote['price'];
        $quoteDate = substr((string) $quote['as_of'], 0, 10);
        if (!is_finite($quotePrice) || preg_match('/^\d{4}-\d{2}-\d{2}$/', $quoteDate) !== 1) {
            return null;
        }

        [$year, $month, $day] = array_map('intval', explode('-', $quoteDate));
        if (!checkdate($month, $day, $year)) {
            return null;
        }

        $stmt = $this->db->prepare(
            'SELECT close FROM price_bars
             WHERE instrument_id = :id AND bar_date < :quote_date
             ORDER BY bar_date DESC LIMIT 1'
        );
        $stmt->execute(['id' => $instrumentId, 'quote_date' => $quoteDate]);
        $row = $stmt->fetch();
        if ($row === false) {
            return null;
        }

        $previousClose = (float) $row['close'];
        if (!is_finite($previousClose) || $previousClose <= 0.0) {
            return null;
        }

        return (($quotePrice - $previousClose) / $previousClose) * 100.0;
    }

    /** @return list<array<string, mixed>> */
    private function transactionsForInstrument(int $instrumentId): array
    {
        $stmt = $this->db->prepare(
            'SELECT id, side, trade_date, quantity, unit_price, commission, notes, fx_to_eur, fx_as_of,
                    created_at, updated_at
             FROM transactions
             WHERE instrument_id = :instrument_id
             ORDER BY trade_date ASC, id ASC'
        );
        $stmt->execute(['instrument_id' => $instrumentId]);
        $rows = $stmt->fetchAll();
        $out = [];
        foreach ($rows as $row) {
            $qty = (float) $row['quantity'];
            $price = (float) $row['unit_price'];
            $commission = (float) $row['commission'];
            $out[] = [
                'id' => (int) $row['id'],
                'side' => $row['side'],
                'trade_date' => $row['trade_date'],
                'quantity' => $qty,
                'unit_price' => $price,
                'commission' => $commission,
                // Buy: all-in cost. Sell: net proceeds (0031).
                'lot_cost' => $row['side'] === 'sell' ? $qty * $price - $commission : $qty * $price + $commission,
                'notes' => $row['notes'],
                'fx_to_eur' => $row['fx_to_eur'] !== null ? (float) $row['fx_to_eur'] : null,
                'fx_as_of' => $row['fx_as_of'],
                'created_at' => $row['created_at'],
                'updated_at' => $row['updated_at'],
            ];
        }
        return $out;
    }
}
