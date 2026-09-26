<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use PDO;

/**
 * The recorded reason a holding is owned, plus named falsifiers (0015).
 *
 * Claude drafts; the operator approves. A `thesis_broken` sell is only reachable for a
 * holding with an `approved` thesis — the doctrine gate fails closed without one (0013).
 * Editing an approved thesis supersedes it rather than overwriting, because a thesis
 * rewritten after the price moved is itself worth being able to see.
 */
final class ThesisRepository
{
    public function __construct(private readonly PDO $db)
    {
    }

    /** @return list<array<string, mixed>> */
    public function all(?string $status = null): array
    {
        $sql =
            'SELECT t.*, i.symbol, i.name AS instrument_name
             FROM theses t
             INNER JOIN instruments i ON i.id = t.instrument_id';
        $params = [];
        if ($status !== null) {
            $sql .= ' WHERE t.status = :status';
            $params['status'] = $status;
        }
        $sql .= ' ORDER BY i.symbol COLLATE NOCASE ASC, t.version DESC';

        $stmt = $this->db->prepare($sql);
        $stmt->execute($params);
        return array_map([$this, 'map'], $stmt->fetchAll());
    }

    /**
     * Holdings and their current thesis state — the read model behind the approval screen
     * and the "needs a thesis" nudge.
     *
     * @return list<array<string, mixed>>
     */
    public function coverage(): array
    {
        $rows = $this->db->query(
            "SELECT i.id AS instrument_id, i.symbol, i.name,
                    (SELECT COUNT(*) FROM theses t
                      WHERE t.instrument_id = i.id AND t.status = 'approved') AS approved_count,
                    (SELECT COUNT(*) FROM theses t
                      WHERE t.instrument_id = i.id AND t.status = 'draft') AS draft_count
             FROM holdings h
             INNER JOIN instruments i ON i.id = h.instrument_id
             ORDER BY i.symbol COLLATE NOCASE ASC"
        )->fetchAll();

        return array_map(static fn (array $r): array => [
            'instrument_id' => (int) $r['instrument_id'],
            'symbol' => (string) $r['symbol'],
            'name' => $r['name'],
            'has_approved' => ((int) $r['approved_count']) > 0,
            'pending_drafts' => (int) $r['draft_count'],
        ], $rows);
    }

    /**
     * @param array<string, mixed> $input
     * @return array<string, mixed>
     */
    public function create(array $input): array
    {
        $instrumentId = (int) ($input['instrument_id'] ?? 0);
        if ($instrumentId <= 0) {
            throw new \InvalidArgumentException('instrument_id is required');
        }
        $thesis = trim((string) ($input['thesis'] ?? ''));
        if ($thesis === '') {
            throw new \InvalidArgumentException('thesis is required');
        }
        $status = (string) ($input['status'] ?? 'approved');
        if (!in_array($status, ['draft', 'approved'], true)) {
            throw new \InvalidArgumentException('status must be draft or approved');
        }

        $now = gmdate('c');
        $id = $this->insert(
            $instrumentId,
            $thesis,
            $this->normalizeFalsifiers($input['falsifiers'] ?? []),
            $status,
            (string) ($input['source'] ?? 'operator'),
            $now
        );
        if ($status === 'approved') {
            $this->supersedeOthers($instrumentId, $id, $now);
        }
        return $this->find($id);
    }

    /**
     * Approve a draft, or revise an approved thesis into a new version.
     *
     * @param array<string, mixed> $input
     * @return array<string, mixed>
     */
    public function update(int $id, array $input): array
    {
        $current = $this->find($id);
        $now = gmdate('c');

        $thesis = array_key_exists('thesis', $input)
            ? trim((string) $input['thesis'])
            : (string) $current['thesis'];
        if ($thesis === '') {
            throw new \InvalidArgumentException('thesis cannot be empty');
        }
        $falsifiers = array_key_exists('falsifiers', $input)
            ? $this->normalizeFalsifiers($input['falsifiers'])
            : $current['falsifiers'];
        $status = (string) ($input['status'] ?? $current['status']);
        if (!in_array($status, ['draft', 'approved', 'superseded'], true)) {
            throw new \InvalidArgumentException('status must be draft, approved or superseded');
        }

        $contentChanged = $thesis !== (string) $current['thesis']
            || $falsifiers !== $current['falsifiers'];

        // Revising something already approved creates a new version; history is kept.
        if ($current['status'] === 'approved' && $contentChanged) {
            $newId = $this->insert(
                (int) $current['instrument_id'],
                $thesis,
                $falsifiers,
                'approved',
                'operator',
                $now
            );
            $this->supersedeOthers((int) $current['instrument_id'], $newId, $now);
            return $this->find($newId);
        }

        $this->db->prepare(
            'UPDATE theses
             SET thesis = :thesis, falsifiers_json = :falsifiers_json, status = :status,
                 source = CASE WHEN :changed = 1 THEN \'operator\' ELSE source END,
                 approved_at = CASE WHEN :status2 = \'approved\' THEN COALESCE(approved_at, :now) ELSE approved_at END
             WHERE id = :id'
        )->execute([
            'id' => $id,
            'thesis' => $thesis,
            'falsifiers_json' => json_encode($falsifiers, JSON_THROW_ON_ERROR),
            'status' => $status,
            'status2' => $status,
            'changed' => $contentChanged ? 1 : 0,
            'now' => $now,
        ]);

        if ($status === 'approved') {
            $this->supersedeOthers((int) $current['instrument_id'], $id, $now);
        }
        return $this->find($id);
    }

    public function delete(int $id): void
    {
        $stmt = $this->db->prepare('DELETE FROM theses WHERE id = :id');
        $stmt->execute(['id' => $id]);
        if ($stmt->rowCount() === 0) {
            throw new \RuntimeException('thesis not found', 404);
        }
    }

    /** @return array<string, mixed> */
    public function find(int $id): array
    {
        $stmt = $this->db->prepare(
            'SELECT t.*, i.symbol, i.name AS instrument_name
             FROM theses t
             INNER JOIN instruments i ON i.id = t.instrument_id
             WHERE t.id = :id'
        );
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();
        if ($row === false) {
            throw new \RuntimeException('thesis not found', 404);
        }
        return $this->map($row);
    }

    /** @param list<string> $falsifiers */
    private function insert(
        int $instrumentId,
        string $thesis,
        array $falsifiers,
        string $status,
        string $source,
        string $now,
    ): int {
        $version = $this->db->prepare(
            'SELECT COALESCE(MAX(version), 0) + 1 FROM theses WHERE instrument_id = :id'
        );
        $version->execute(['id' => $instrumentId]);

        $this->db->prepare(
            'INSERT INTO theses
                (instrument_id, thesis, falsifiers_json, status, version, source, created_at, approved_at)
             VALUES
                (:instrument_id, :thesis, :falsifiers_json, :status, :version, :source, :created_at, :approved_at)'
        )->execute([
            'instrument_id' => $instrumentId,
            'thesis' => $thesis,
            'falsifiers_json' => json_encode($falsifiers, JSON_THROW_ON_ERROR),
            'status' => $status,
            'version' => (int) $version->fetchColumn(),
            'source' => in_array($source, ['claude', 'operator'], true) ? $source : 'operator',
            'created_at' => $now,
            'approved_at' => $status === 'approved' ? $now : null,
        ]);

        return (int) $this->db->lastInsertId();
    }

    private function supersedeOthers(int $instrumentId, int $keepId, string $now): void
    {
        $this->db->prepare(
            "UPDATE theses
             SET status = 'superseded', superseded_at = :now
             WHERE instrument_id = :instrument_id AND id != :keep AND status = 'approved'"
        )->execute(['instrument_id' => $instrumentId, 'keep' => $keepId, 'now' => $now]);
    }

    /** @return list<string> */
    private function normalizeFalsifiers(mixed $raw): array
    {
        if (is_string($raw)) {
            $decoded = json_decode($raw, true);
            $raw = is_array($decoded) ? $decoded : array_filter(array_map('trim', explode("\n", $raw)));
        }
        if (!is_array($raw)) {
            return [];
        }
        $out = [];
        foreach ($raw as $item) {
            $s = trim((string) (is_array($item) ? ($item['condition'] ?? '') : $item));
            if ($s !== '') {
                $out[] = $s;
            }
        }
        return array_values($out);
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function map(array $row): array
    {
        $falsifiers = json_decode((string) ($row['falsifiers_json'] ?? '[]'), true);
        return [
            'id' => (int) $row['id'],
            'instrument_id' => (int) $row['instrument_id'],
            'symbol' => $row['symbol'] ?? null,
            'instrument_name' => $row['instrument_name'] ?? null,
            'thesis' => (string) $row['thesis'],
            'falsifiers' => is_array($falsifiers) ? $falsifiers : [],
            'status' => (string) $row['status'],
            'version' => (int) $row['version'],
            'source' => (string) $row['source'],
            'agent_run_id' => $row['agent_run_id'] !== null ? (int) $row['agent_run_id'] : null,
            'created_at' => $row['created_at'],
            'approved_at' => $row['approved_at'],
            'superseded_at' => $row['superseded_at'],
        ];
    }
}
