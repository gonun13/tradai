<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use PDO;

final class AlertRepository
{
    public function __construct(private readonly PDO $db)
    {
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function list(?bool $unreadOnly = null, int $limit = 100): array
    {
        $sql = <<<'SQL'
            SELECT a.*,
                   r.action, r.horizon, r.agent_run_id, r.rationale, r.book,
                   i.symbol, i.name AS instrument_name
            FROM alerts a
            INNER JOIN recommendations r ON r.id = a.recommendation_id
            INNER JOIN instruments i ON i.id = r.instrument_id
        SQL;
        if ($unreadOnly === true) {
            $sql .= ' WHERE a.unread = 1';
        } elseif ($unreadOnly === false) {
            $sql .= ' WHERE a.unread = 0';
        }
        $sql .= ' ORDER BY a.raised_at DESC, a.id DESC LIMIT :limit';
        $stmt = $this->db->prepare($sql);
        $stmt->bindValue('limit', $limit, PDO::PARAM_INT);
        $stmt->execute();
        return array_map(fn (array $row): array => $this->map($row), $stmt->fetchAll());
    }

    public function unreadCount(): int
    {
        return (int) $this->db->query('SELECT COUNT(*) FROM alerts WHERE unread = 1')->fetchColumn();
    }

    /** @return array<string, mixed>|null */
    public function find(int $id): ?array
    {
        $stmt = $this->db->prepare(
            <<<'SQL'
            SELECT a.*,
                   r.action, r.horizon, r.agent_run_id, r.rationale, r.book,
                   i.symbol, i.name AS instrument_name
            FROM alerts a
            INNER JOIN recommendations r ON r.id = a.recommendation_id
            INNER JOIN instruments i ON i.id = r.instrument_id
            WHERE a.id = :id
            SQL
        );
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();
        return $row === false ? null : $this->map($row);
    }

    /** @return array<string, mixed> */
    public function acknowledge(int $id): array
    {
        $existing = $this->find($id);
        if ($existing === null) {
            throw new \RuntimeException('Alert not found.', 404);
        }
        $stmt = $this->db->prepare(
            'UPDATE alerts SET unread = 0, acked_at = :acked_at WHERE id = :id'
        );
        $stmt->execute([
            'id' => $id,
            'acked_at' => gmdate('c'),
        ]);
        $updated = $this->find($id);
        if ($updated === null) {
            throw new \RuntimeException('Alert not found after ack.', 404);
        }
        return $updated;
    }

    /** @param array<string, mixed> $row */
    private function map(array $row): array
    {
        return [
            'id' => (int) $row['id'],
            'recommendation_id' => (int) $row['recommendation_id'],
            'agent_run_id' => (int) $row['agent_run_id'],
            'unread' => ((int) $row['unread']) === 1,
            'severity' => $row['severity'],
            'raised_at' => $row['raised_at'],
            'acked_at' => $row['acked_at'] ?? null,
            'action' => $row['action'],
            'book' => $row['book'] ?? 'portfolio',
            'horizon' => $row['horizon'],
            'symbol' => $row['symbol'],
            'instrument_name' => $row['instrument_name'],
            'rationale' => $row['rationale'],
        ];
    }
}
