<?php

declare(strict_types=1);

namespace Tradai\Api;

use PDO;

final class IngestRunRepository
{
    public function __construct(private readonly PDO $db)
    {
    }

    public function start(string $trigger = 'manual'): int
    {
        $stmt = $this->db->prepare(
            "INSERT INTO ingest_runs (status, trigger_kind, started_at) VALUES ('running', :trigger, :started_at)"
        );
        $stmt->execute(['trigger' => $trigger, 'started_at' => gmdate('c')]);
        return (int) $this->db->lastInsertId();
    }

    /** @param array<string, mixed> $report */
    public function succeed(int $id, array $report): void
    {
        $stmt = $this->db->prepare(<<<'SQL'
            UPDATE ingest_runs
            SET status = 'succeeded', finished_at = :finished_at, report_json = :report, error_text = NULL
            WHERE id = :id
            SQL);
        $stmt->execute([
            'id' => $id,
            'finished_at' => gmdate('c'),
            'report' => json_encode($report, JSON_THROW_ON_ERROR),
        ]);
        $this->prune();
    }

    public function fail(int $id, string $error): void
    {
        $stmt = $this->db->prepare(<<<'SQL'
            UPDATE ingest_runs
            SET status = 'failed', finished_at = :finished_at, error_text = :error
            WHERE id = :id
            SQL);
        $stmt->execute(['id' => $id, 'finished_at' => gmdate('c'), 'error' => $error]);
        $this->prune();
    }

    /** @return list<array<string, mixed>> */
    public function list(int $limit = 20): array
    {
        $stmt = $this->db->prepare(<<<'SQL'
            SELECT id, status, trigger_kind, started_at, finished_at
            FROM ingest_runs ORDER BY id DESC LIMIT :limit
            SQL);
        $stmt->bindValue('limit', $limit, PDO::PARAM_INT);
        $stmt->execute();
        return array_map(static fn (array $row): array => [
            'id' => (int) $row['id'],
            'status' => $row['status'],
            'trigger' => $row['trigger_kind'],
            'started_at' => $row['started_at'],
            'finished_at' => $row['finished_at'],
        ], $stmt->fetchAll());
    }

    /** @return array<string, mixed>|null */
    public function find(int $id): ?array
    {
        $stmt = $this->db->prepare('SELECT * FROM ingest_runs WHERE id = :id');
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();
        if ($row === false) {
            return null;
        }
        $report = null;
        if (!empty($row['report_json'])) {
            $decoded = json_decode((string) $row['report_json'], true);
            $report = is_array($decoded) ? $decoded : null;
        }
        return [
            'id' => (int) $row['id'],
            'status' => $row['status'],
            'trigger' => $row['trigger_kind'],
            'started_at' => $row['started_at'],
            'finished_at' => $row['finished_at'],
            'report' => $report,
            'error' => $row['error_text'],
        ];
    }

    private function prune(): void
    {
        $this->db->exec(<<<'SQL'
            DELETE FROM ingest_runs
            WHERE id NOT IN (SELECT id FROM ingest_runs ORDER BY id DESC LIMIT 20)
            SQL);
    }
}
