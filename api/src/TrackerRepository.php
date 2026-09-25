<?php

declare(strict_types=1);

namespace Tradai\Api;

use PDO;

/**
 * The tracker (0019): names the operator is interested in but does not own. A second book,
 * not a note — tracked names are ingested and researched on the same daily cadence as the
 * portfolio, and they remain the universe a `better_use` sell can point at (0016).
 *
 * Every entry carries a real instrument, because the ingest loop routes on `region` and a
 * recommendation FKs to `instruments`. Buying a tracked name archives its row rather than
 * deleting it, so its tracking history survives the promotion.
 */
final class TrackerRepository
{
    private readonly InstrumentRepository $instruments;

    public function __construct(private readonly PDO $db)
    {
        $this->instruments = new InstrumentRepository($db);
    }

    /** @return list<array<string, mixed>> */
    public function all(bool $includeArchived = false): array
    {
        $sql =
            'SELECT t.id, t.symbol, t.instrument_id, t.name, t.added_at, t.updated_at,
                    t.archived_at,
                    i.currency, i.kind, i.region, i.mic, i.isin, i.name AS instrument_name,
                    q.price AS quote_price, q.currency AS quote_currency, q.as_of AS quote_as_of,
                    q.source AS quote_source, q.updated_at AS quote_updated_at,
                    tech.features_json, tech.as_of AS technicals_as_of,
                    tech.source AS technicals_source, tech.updated_at AS technicals_updated_at,
                    f.payload_json AS fundamentals_json, f.source AS fundamentals_source,
                    f.as_of AS fundamentals_as_of,
                    f.completeness_state AS fundamentals_state,
                    f.coverage_score AS fundamentals_score,
                    f.missing_fields_json AS fundamentals_missing,
                    fx.rate AS fx_rate
             FROM tracker t
             INNER JOIN instruments i ON i.id = t.instrument_id
             LEFT JOIN quotes q ON q.instrument_id = t.instrument_id
             LEFT JOIN technicals tech ON tech.instrument_id = t.instrument_id
             LEFT JOIN fundamentals f ON f.instrument_id = t.instrument_id
             LEFT JOIN fx_rates fx
               ON fx.base_currency = q.currency AND fx.quote_currency = \'EUR\'';
        if (!$includeArchived) {
            $sql .= ' WHERE t.archived_at IS NULL';
        }
        $sql .= ' ORDER BY t.symbol COLLATE NOCASE ASC';

        return array_map(
            fn (array $r): array => $this->mapEntry($r),
            $this->db->query($sql)->fetchAll()
        );
    }

    /**
     * @param array<string, mixed> $input
     * @return array<string, mixed>
     */
    public function create(array $input): array
    {
        $symbol = strtoupper(trim((string) ($input['symbol'] ?? '')));
        if ($symbol === '') {
            throw new \InvalidArgumentException('symbol is required');
        }

        // A name you own belongs in the portfolio, not the tracker — the two books are
        // mutually exclusive by design (auto-promote, 0019).
        $owned = $this->db->prepare(
            'SELECT 1 FROM holdings h
             INNER JOIN instruments i ON i.id = h.instrument_id
             WHERE i.symbol = :symbol COLLATE NOCASE'
        );
        $owned->execute(['symbol' => $symbol]);
        if ($owned->fetchColumn() !== false) {
            throw new \RuntimeException($symbol . ' is already a holding — it shows in the Portfolio tab', 409);
        }

        $now = gmdate('c');

        // A tracked name may resolve to an instrument the book already knows — an old
        // position, or a symbol another entry minted. `upsert()` writes currency and kind
        // unconditionally (the holdings form always supplies them), but here they may be
        // *inferred* from the ticker, so an existing instrument's real values win over a
        // guess. Only what the operator actually typed overrides them.
        $existing = $this->instruments->findBySymbol($symbol);
        $region = $this->nullableString($input['region'] ?? null)
            ?? ($existing['region'] ?? null)
            ?? self::inferRegion($symbol);
        $currency = $this->nullableString($input['currency'] ?? null)
            ?? ($existing['currency'] ?? null)
            ?? ($region === 'us' ? 'USD' : 'EUR');
        $kind = $this->nullableString($input['kind'] ?? null)
            ?? ($existing['kind'] ?? null)
            ?? 'equity';

        $instrumentId = $this->instruments->upsert([
            'symbol' => $symbol,
            'isin' => $this->nullableString($input['isin'] ?? null),
            'mic' => $this->nullableString($input['mic'] ?? null),
            'currency' => strtoupper($currency),
            'name' => $this->nullableString($input['name'] ?? null),
            'kind' => $kind,
            'region' => $region,
        ], $now);

        $this->db->prepare(
            'INSERT INTO tracker (symbol, instrument_id, name, added_at, updated_at, archived_at)
             VALUES (:symbol, :instrument_id, :name, :added_at, :updated_at, NULL)
             ON CONFLICT(symbol) DO UPDATE SET
                instrument_id = excluded.instrument_id,
                name = COALESCE(excluded.name, tracker.name),
                updated_at = excluded.updated_at,
                archived_at = NULL'
        )->execute([
            'symbol' => $symbol,
            'instrument_id' => $instrumentId,
            'name' => $this->nullableString($input['name'] ?? null),
            'added_at' => $now,
            'updated_at' => $now,
        ]);

        $entry = $this->findBySymbol($symbol);
        if ($entry === null) {
            throw new \RuntimeException('tracker entry not found after insert', 500);
        }
        return $entry;
    }

    /**
     * @param array<string, mixed> $input
     * @return array<string, mixed>
     */
    public function update(int $id, array $input): array
    {
        $sets = [];
        $params = ['id' => $id, 'updated_at' => gmdate('c')];
        foreach (['name'] as $field) {
            if (array_key_exists($field, $input)) {
                $sets[] = $field . ' = :' . $field;
                $params[$field] = $this->nullableString($input[$field]);
            }
        }
        if ($sets === []) {
            $entry = $this->find($id);
            if ($entry === null) {
                throw new \RuntimeException('tracker entry not found', 404);
            }
            return $entry;
        }

        $stmt = $this->db->prepare(
            'UPDATE tracker SET ' . implode(', ', $sets) . ', updated_at = :updated_at WHERE id = :id'
        );
        $stmt->execute($params);
        if ($stmt->rowCount() === 0) {
            throw new \RuntimeException('tracker entry not found', 404);
        }

        $entry = $this->find($id);
        if ($entry === null) {
            throw new \RuntimeException('tracker entry not found', 404);
        }
        return $entry;
    }

    public function delete(int $id): void
    {
        $stmt = $this->db->prepare('DELETE FROM tracker WHERE id = :id');
        $stmt->execute(['id' => $id]);
        if ($stmt->rowCount() === 0) {
            throw new \RuntimeException('tracker entry not found', 404);
        }
    }

    /**
     * Auto-promote (0019): the operator bought a tracked name, so it leaves the Tracker
     * view and the agent context but keeps its added date. No-op when untracked.
     */
    public function archiveBySymbol(string $symbol): void
    {
        $this->db->prepare(
            'UPDATE tracker SET archived_at = :now, updated_at = :now
             WHERE symbol = :symbol COLLATE NOCASE AND archived_at IS NULL'
        )->execute(['symbol' => strtoupper(trim($symbol)), 'now' => gmdate('c')]);
    }

    /** The reverse: a full exit drops the name back onto the tracker it came from. */
    public function unarchiveBySymbol(string $symbol): void
    {
        $this->db->prepare(
            'UPDATE tracker SET archived_at = NULL, updated_at = :now
             WHERE symbol = :symbol COLLATE NOCASE AND archived_at IS NOT NULL'
        )->execute(['symbol' => strtoupper(trim($symbol)), 'now' => gmdate('c')]);
    }

    /** @return array<string, mixed>|null */
    public function find(int $id): ?array
    {
        foreach ($this->all(includeArchived: true) as $row) {
            if ($row['id'] === $id) {
                return $row;
            }
        }
        return null;
    }

    /** @return array<string, mixed>|null */
    public function findBySymbol(string $symbol): ?array
    {
        $needle = strtoupper(trim($symbol));
        foreach ($this->all(includeArchived: true) as $row) {
            if (strtoupper($row['symbol']) === $needle) {
                return $row;
            }
        }
        return null;
    }

    /** Same rule the worker applies on every ingest — a venue suffix means a EU listing. */
    public static function inferRegion(string $symbol): string
    {
        return str_contains($symbol, '.') ? 'eu' : 'us';
    }

    /**
     * @param array<string, mixed> $r
     * @return array<string, mixed>
     */
    private function mapEntry(array $r): array
    {
        $price = $r['quote_price'] !== null ? (float) $r['quote_price'] : null;
        $currency = $r['quote_currency'] !== null ? (string) $r['quote_currency'] : ($r['currency'] ?? 'EUR');
        $rate = strtoupper((string) $currency) === 'EUR'
            ? 1.0
            : ($r['fx_rate'] !== null ? (float) $r['fx_rate'] : null);

        $features = [];
        if (is_string($r['features_json']) && $r['features_json'] !== '') {
            $decoded = json_decode($r['features_json'], true);
            if (is_array($decoded)) {
                $features = $decoded;
            }
        }

        return [
            'id' => (int) $r['id'],
            'symbol' => (string) $r['symbol'],
            'instrument_id' => (int) $r['instrument_id'],
            'name' => $r['name'] ?? $r['instrument_name'],
            'added_at' => $r['added_at'],
            'updated_at' => $r['updated_at'],
            'archived_at' => $r['archived_at'],
            'instrument' => [
                'currency' => $r['currency'],
                'kind' => $r['kind'],
                'region' => $r['region'],
                'mic' => $r['mic'],
                'isin' => $r['isin'],
            ],
            'quote' => $price !== null ? [
                'price' => $price,
                'currency' => $currency,
                'as_of' => $r['quote_as_of'],
                'source' => $r['quote_source'],
                'updated_at' => $r['quote_updated_at'],
            ] : null,
            // There is no cost basis to show for an unowned name, so the EUR figure the
            // Tracker table renders is just the quote converted (0010: UI is always EUR).
            'price_eur' => $price !== null && $rate !== null ? round($price * $rate, 4) : null,
            'daily_change_pct' => $this->dailyChangePct((int) $r['instrument_id'], $price, $r['quote_as_of']),
            'technicals' => $features !== [] ? [
                'as_of' => $r['technicals_as_of'],
                'source' => $r['technicals_source'],
                'updated_at' => $r['technicals_updated_at'],
                'features' => $features,
            ] : null,
            'fundamentals' => $this->mapFundamentals($r),
            'news' => $this->newsForInstrument((int) $r['instrument_id']),
        ];
    }

    private function dailyChangePct(int $instrumentId, ?float $quotePrice, mixed $quoteAsOf): ?float
    {
        if ($quotePrice === null || $quoteAsOf === null) {
            return null;
        }

        $quoteDate = substr((string) $quoteAsOf, 0, 10);
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

    /** @param array<string, mixed> $row */
    private function mapFundamentals(array $row): ?array
    {
        if (!is_string($row['fundamentals_json']) || $row['fundamentals_json'] === '') {
            return null;
        }
        $payload = json_decode($row['fundamentals_json'], true);
        $missing = json_decode((string) ($row['fundamentals_missing'] ?? '[]'), true);
        return [
            'payload' => is_array($payload) ? $payload : [],
            'source' => $row['fundamentals_source'],
            'as_of' => $row['fundamentals_as_of'],
            'completeness_state' => $row['fundamentals_state'],
            'coverage_score' => (float) $row['fundamentals_score'],
            'missing_fields' => is_array($missing) ? $missing : [],
        ];
    }

    /** @return list<array<string, mixed>> */
    private function newsForInstrument(int $instrumentId): array
    {
        $stmt = $this->db->prepare(
            'SELECT n.id, n.title, n.snippet, n.url, n.source_name, n.adapter_source,
                    n.published_at, n.fetched_at
             FROM news_items n
             INNER JOIN news_item_instruments nii ON nii.news_item_id = n.id
             WHERE nii.instrument_id = :id
             ORDER BY COALESCE(n.published_at, n.fetched_at) DESC
             LIMIT 5'
        );
        $stmt->execute(['id' => $instrumentId]);
        return array_map(static fn (array $row): array => [
            'id' => (int) $row['id'],
            'title' => $row['title'],
            'snippet' => $row['snippet'],
            'url' => $row['url'],
            'source_name' => $row['source_name'],
            'adapter_source' => $row['adapter_source'],
            'published_at' => $row['published_at'],
            'fetched_at' => $row['fetched_at'],
        ], $stmt->fetchAll());
    }

    private function nullableString(mixed $value): ?string
    {
        if ($value === null) {
            return null;
        }
        $s = trim((string) $value);
        return $s === '' ? null : $s;
    }
}
