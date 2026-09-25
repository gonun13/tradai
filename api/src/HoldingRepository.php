<?php

declare(strict_types=1);

namespace Tradai\Api;

use InvalidArgumentException;
use PDO;
use RuntimeException;

final class HoldingRepository
{
    private readonly InstrumentRepository $instruments;

    public function __construct(private readonly PDO $db)
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

        $this->db->beginTransaction();
        try {
            // One holding line per ticker: same symbol appends a lot and re-averages.
            $existingHolding = $this->findHoldingIdBySymbol($data['symbol']);
            if ($existingHolding !== null) {
                $holdingId = $existingHolding['holding_id'];
                $instrumentId = $existingHolding['instrument_id'];
                $this->touchInstrument($instrumentId, $data, $now);
                $this->insertBuyTransaction($instrumentId, $data, $now);
                $this->recomputeHoldingRollup($holdingId, $instrumentId, $data['notes'], $now, mergeNotes: true);
            } else {
                $instrumentId = $this->instruments->upsert($data, $now);
                $this->insertBuyTransaction($instrumentId, $data, $now);
                $holdingId = $this->insertHoldingRollup($instrumentId, $data, $now);
            }
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        $created = $this->find($holdingId);
        if ($created === null) {
            throw new RuntimeException('Failed to load created holding.');
        }
        return $created;
    }

    /**
     * @param array<string, mixed> $input
     * @return array<string, mixed>
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

        $this->db->beginTransaction();
        try {
            $this->touchInstrument($instrumentId, $data, $now);
            $this->updateBuyTransaction($txId, $data, $now);
            $this->recomputeHoldingRollup($id, $instrumentId, $data['notes'], $now, mergeNotes: false);
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        $updated = $this->find($id);
        if ($updated === null) {
            throw new RuntimeException('Failed to load updated holding.');
        }
        return $updated;
    }

    /**
     * Returns the deleted holding's symbol so the caller can drop the name back onto the
     * tracker it was promoted from (0019). find() is already called here anyway.
     */
    public function delete(int $id): string
    {
        $current = $this->find($id);
        if ($current === null) {
            throw new RuntimeException('Holding not found.', 404);
        }

        $instrumentId = (int) $current['instrument']['id'];
        $this->db->beginTransaction();
        try {
            $this->db->prepare('DELETE FROM transactions WHERE instrument_id = :id')->execute(['id' => $instrumentId]);
            $this->db->prepare('DELETE FROM holdings WHERE id = :id')->execute(['id' => $id]);
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }

        return (string) $current['instrument']['symbol'];
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

        $tradeDate = trim((string) ($input['trade_date'] ?? ''));
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $tradeDate)) {
            throw new InvalidArgumentException('trade_date must be YYYY-MM-DD');
        }

        $quantity = $input['quantity'] ?? null;
        $unitPrice = $input['unit_price'] ?? $input['avg_cost'] ?? null;
        $commission = $input['commission'] ?? 0;

        if (!is_numeric($quantity) || (float) $quantity <= 0) {
            throw new InvalidArgumentException('quantity must be a positive number');
        }
        if (!is_numeric($unitPrice) || (float) $unitPrice < 0) {
            throw new InvalidArgumentException('unit_price must be a non-negative number');
        }
        if (!is_numeric($commission) || (float) $commission < 0) {
            throw new InvalidArgumentException('commission must be a non-negative number');
        }

        $isin = $this->nullableString($input['isin'] ?? null);
        $mic = $this->nullableString($input['mic'] ?? null);
        $name = $this->nullableString($input['name'] ?? null);
        $notes = $this->nullableString($input['notes'] ?? null);

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
            'notes' => $notes,
            'trade_date' => $tradeDate,
            'quantity' => (float) $quantity,
            'unit_price' => (float) $unitPrice,
            'commission' => (float) $commission,
        ];
    }

    /**
     * @param array<string, mixed> $data
     */
    private function insertBuyTransaction(int $instrumentId, array $data, string $now): void
    {
        $stmt = $this->db->prepare(
            'INSERT INTO transactions
                (instrument_id, side, trade_date, quantity, unit_price, commission, notes, created_at, updated_at)
             VALUES
                (:instrument_id, \'buy\', :trade_date, :quantity, :unit_price, :commission, :notes, :created_at, :updated_at)'
        );
        $stmt->execute([
            'instrument_id' => $instrumentId,
            'trade_date' => $data['trade_date'],
            'quantity' => $data['quantity'],
            'unit_price' => $data['unit_price'],
            'commission' => $data['commission'],
            'notes' => $data['notes'],
            'created_at' => $now,
            'updated_at' => $now,
        ]);
    }

    /**
     * @param array<string, mixed> $data
     */
    private function updateBuyTransaction(int $transactionId, array $data, string $now): void
    {
        $stmt = $this->db->prepare(
            'UPDATE transactions
             SET trade_date = :trade_date, quantity = :quantity, unit_price = :unit_price,
                 commission = :commission, notes = :notes, updated_at = :updated_at
             WHERE id = :id AND side = \'buy\''
        );
        $stmt->execute([
            'id' => $transactionId,
            'trade_date' => $data['trade_date'],
            'quantity' => $data['quantity'],
            'unit_price' => $data['unit_price'],
            'commission' => $data['commission'],
            'notes' => $data['notes'],
            'updated_at' => $now,
        ]);
        if ($stmt->rowCount() === 0) {
            throw new RuntimeException('Acquisition lot not found.', 404);
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

    private function recomputeHoldingRollup(
        int $holdingId,
        int $instrumentId,
        ?string $notes,
        string $now,
        bool $mergeNotes = false,
    ): void {
        // FIFO over *all* sides. The previous aggregate filtered `side = 'buy'`, so sells
        // never reduced quantity or cost basis and realised P&L was unknowable (0013).
        $ledger = FifoLedger::walk($this->rawTransactions($instrumentId));
        $qty = $ledger['quantity'];
        $totalCost = $ledger['total_cost'];
        $avg = $ledger['avg_cost'];

        $this->persistDisposals($instrumentId, $ledger['disposals'], $now);

        if ($mergeNotes && $notes !== null && $notes !== '') {
            $cur = $this->db->prepare('SELECT notes FROM holdings WHERE id = :id');
            $cur->execute(['id' => $holdingId]);
            $existing = $cur->fetch();
            $prev = is_array($existing) ? trim((string) ($existing['notes'] ?? '')) : '';
            if ($prev !== '' && $prev !== $notes) {
                $notes = $prev . "\n" . $notes;
            } elseif ($prev !== '' && ($notes === null || $notes === '')) {
                $notes = $prev;
            }
        }

        $realized = 0.0;
        foreach ($ledger['disposals'] as $d) {
            $realized += (float) $d['realized_pnl'];
        }

        $h = $this->db->prepare(
            'UPDATE holdings
             SET quantity = :quantity, avg_cost = :avg_cost, total_cost = :total_cost,
                 first_trade_date = :first_trade_date, open_lot_count = :open_lot_count,
                 realized_pnl_native = :realized_pnl_native,
                 notes = :notes, updated_at = :updated_at
             WHERE id = :id'
        );
        $h->execute([
            'id' => $holdingId,
            'quantity' => $qty,
            'avg_cost' => $avg,
            'total_cost' => $totalCost,
            'first_trade_date' => $ledger['first_trade_date'],
            'open_lot_count' => $ledger['lot_count'],
            'realized_pnl_native' => $realized,
            'notes' => $notes,
            'updated_at' => $now,
        ]);
    }

    /** @return list<array<string, mixed>> */
    private function rawTransactions(int $instrumentId): array
    {
        $stmt = $this->db->prepare(
            'SELECT id, side, trade_date, quantity, unit_price, commission
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
                 proceeds, cost, realized_pnl, currency, created_at)
             VALUES
                (:instrument_id, :sell_transaction_id, :trade_date, :quantity,
                 :proceeds, :cost, :realized_pnl, :currency, :created_at)'
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
                'created_at' => $now,
            ]);
        }
    }

    /**
     * @param array<string, mixed> $data
     */
    private function insertHoldingRollup(int $instrumentId, array $data, string $now): int
    {
        $lotCost = $data['quantity'] * $data['unit_price'] + $data['commission'];
        $avg = $lotCost / $data['quantity'];
        $stmt = $this->db->prepare(
            'INSERT INTO holdings (instrument_id, quantity, avg_cost, total_cost, notes, created_at, updated_at)
             VALUES (:instrument_id, :quantity, :avg_cost, :total_cost, :notes, :created_at, :updated_at)'
        );
        $stmt->execute([
            'instrument_id' => $instrumentId,
            'quantity' => $data['quantity'],
            'avg_cost' => $avg,
            'total_cost' => $lotCost,
            'notes' => $data['notes'],
            'created_at' => $now,
            'updated_at' => $now,
        ]);
        return (int) $this->db->lastInsertId();
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
        $latest = $lotCount > 0 ? $transactions[$lotCount - 1] : null;
        $currency = (string) $row['currency'];
        $qty = (float) $row['quantity'];
        $totalCost = (float) $row['total_cost'];
        $avgCost = (float) $row['avg_cost'];

        $quote = $this->latestQuote($instrumentId);
        $fxToEur = $this->fxRateToEur($currency);
        // Prefer quote currency for mark-to-market FX when vendors report a different currency.
        $quoteCurrency = isset($quote['currency']) ? (string) $quote['currency'] : $currency;
        $fxQuoteToEur = $quoteCurrency === $currency ? $fxToEur : $this->fxRateToEur($quoteCurrency);

        $costEur = $fxToEur === null ? null : $totalCost * $fxToEur;
        $price = $quote['price'] ?? null;
        $marketValueNative = $price === null ? null : $qty * $price;
        $marketValueEur = ($marketValueNative === null || $fxQuoteToEur === null)
            ? null
            : $marketValueNative * $fxQuoteToEur;
        $pnlNative = ($marketValueNative === null) ? null : $marketValueNative - $totalCost;
        $pnlEur = ($marketValueEur === null || $costEur === null) ? null : $marketValueEur - $costEur;

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
            'unit_price' => $avgCost,
            'commission' => null,
            'cost_native' => $totalCost,
            'cost_eur' => $costEur,
            'market_value_native' => $marketValueNative,
            'market_value_eur' => $marketValueEur,
            'pnl_native' => $pnlNative,
            'pnl_eur' => $pnlEur,
            'pnl_pct' => ($totalCost > 0 && $pnlNative !== null)
                ? ($pnlNative / $totalCost) * 100.0
                : null,
            'daily_change_pct' => $this->dailyChangePct($instrumentId, $quote),
            'fx_to_eur' => $fxToEur,
            'display_currency' => 'EUR',
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

    private function fxRateToEur(string $currency): ?float
    {
        $currency = strtoupper($currency);
        if ($currency === 'EUR') {
            return 1.0;
        }
        $stmt = $this->db->prepare(
            'SELECT rate FROM fx_rates
             WHERE base_currency = :base AND quote_currency = \'EUR\''
        );
        $stmt->execute(['base' => $currency]);
        $row = $stmt->fetch();
        return $row === false ? null : (float) $row['rate'];
    }

    /** @return list<array<string, mixed>> */
    private function transactionsForInstrument(int $instrumentId): array
    {
        $stmt = $this->db->prepare(
            'SELECT id, side, trade_date, quantity, unit_price, commission, notes, created_at, updated_at
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
                'lot_cost' => $qty * $price + $commission,
                'notes' => $row['notes'],
                'created_at' => $row['created_at'],
                'updated_at' => $row['updated_at'],
            ];
        }
        return $out;
    }
}
