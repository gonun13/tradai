<?php

declare(strict_types=1);

namespace Tradai\Api;

use PDO;

/**
 * The one place an instrument row is minted. Extracted from HoldingRepository when the
 * tracker (0019) became a second book: a tracked name needs a real instrument for the
 * ingest loop to route it and for a recommendation to point at, and both books must agree
 * on what "the same ticker" means or they would drift into duplicate rows.
 */
final class InstrumentRepository
{
    public function __construct(private readonly PDO $db)
    {
    }

    /**
     * @param array<string, mixed> $data symbol, isin, mic, currency, name, kind, region
     */
    public function upsert(array $data, string $now): int
    {
        // One instrument (and thus one holding line) per ticker — MIC/venue is metadata.
        $find = $this->db->prepare(
            'SELECT id FROM instruments WHERE symbol = :symbol COLLATE NOCASE ORDER BY id ASC LIMIT 1'
        );
        $find->execute(['symbol' => $data['symbol']]);
        $row = $find->fetch();

        if ($row !== false) {
            $id = (int) $row['id'];
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
                'id' => $id,
                'isin' => $data['isin'] ?? null,
                'mic' => $data['mic'] ?? null,
                'currency' => $data['currency'],
                'name' => $data['name'] ?? null,
                'kind' => $data['kind'],
                'region' => $data['region'] ?? null,
                'updated_at' => $now,
            ]);
            return $id;
        }

        $ins = $this->db->prepare(
            'INSERT INTO instruments (isin, symbol, mic, currency, name, kind, region, created_at, updated_at)
             VALUES (:isin, :symbol, :mic, :currency, :name, :kind, :region, :created_at, :updated_at)'
        );
        $ins->execute([
            'isin' => $data['isin'] ?? null,
            'symbol' => $data['symbol'],
            'mic' => $data['mic'] ?? null,
            'currency' => $data['currency'],
            'name' => $data['name'] ?? null,
            'kind' => $data['kind'],
            'region' => $data['region'] ?? null,
            'created_at' => $now,
            'updated_at' => $now,
        ]);

        return (int) $this->db->lastInsertId();
    }

    /**
     * Case-insensitive symbol lookup. Null when the ticker has never been seen.
     *
     * @return array<string, mixed>|null
     */
    public function findBySymbol(string $symbol): ?array
    {
        $stmt = $this->db->prepare(
            'SELECT id, isin, symbol, mic, currency, name, kind, region
             FROM instruments WHERE symbol = :symbol COLLATE NOCASE ORDER BY id ASC LIMIT 1'
        );
        $stmt->execute(['symbol' => strtoupper(trim($symbol))]);
        $row = $stmt->fetch();
        return $row === false ? null : $row;
    }

    /**
     * Local-first half of instrument search (0019): tickers the book already knows rank
     * above anything a vendor returns, because they carry the operator's own metadata.
     *
     * @return list<array<string, mixed>>
     */
    public function search(string $query, int $limit = 10): array
    {
        $q = trim($query);
        if ($q === '') {
            return [];
        }

        $stmt = $this->db->prepare(
            'SELECT symbol, name, mic, currency, kind, region, isin
             FROM instruments
             WHERE symbol LIKE :like COLLATE NOCASE OR name LIKE :like COLLATE NOCASE
             ORDER BY
                CASE WHEN symbol = :exact COLLATE NOCASE THEN 0 ELSE 1 END,
                symbol COLLATE NOCASE ASC
             LIMIT :limit'
        );
        $stmt->bindValue('like', '%' . $q . '%');
        $stmt->bindValue('exact', $q);
        $stmt->bindValue('limit', $limit, PDO::PARAM_INT);
        $stmt->execute();

        return array_map(static fn (array $r): array => [
            'symbol' => (string) $r['symbol'],
            'name' => $r['name'],
            'mic' => $r['mic'],
            'currency' => $r['currency'],
            'kind' => $r['kind'],
            'region' => $r['region'],
            'isin' => $r['isin'],
            'source' => 'local',
            'known' => true,
        ], $stmt->fetchAll());
    }
}
