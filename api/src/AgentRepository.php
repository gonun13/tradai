<?php

declare(strict_types=1);

namespace Tradai\Api;

use PDO;

final class AgentRepository
{
    public function __construct(private readonly PDO $db)
    {
    }

    /** @return array<string, mixed>|null */
    public function findRun(int $id): ?array
    {
        $stmt = $this->db->prepare('SELECT * FROM agent_runs WHERE id = :id');
        $stmt->execute(['id' => $id]);
        $row = $stmt->fetch();
        return $row === false ? null : $this->mapRun($row);
    }

    /** @return array<string, mixed>|null */
    public function latestRun(): ?array
    {
        $row = $this->db->query(
            'SELECT * FROM agent_runs ORDER BY id DESC LIMIT 1'
        )->fetch();
        return $row === false ? null : $this->mapRun($row);
    }

    /**
     * @return list<array<string, mixed>>
     */
    public function listRuns(int $limit = 20): array
    {
        $stmt = $this->db->prepare(
            'SELECT * FROM agent_runs ORDER BY id DESC LIMIT :limit'
        );
        $stmt->bindValue('limit', $limit, PDO::PARAM_INT);
        $stmt->execute();
        return array_map(fn (array $row): array => $this->mapRun($row), $stmt->fetchAll());
    }

    /**
     * Recommendations for a run, or latest succeeded/partial run when $runId is null.
     *
     * @return list<array<string, mixed>>
     */
    public function recommendations(?int $runId = null): array
    {
        if ($runId === null) {
            $latest = $this->db->query(
                "SELECT id FROM agent_runs
                 WHERE status IN ('succeeded', 'partial')
                 ORDER BY id DESC LIMIT 1"
            )->fetch();
            if ($latest === false) {
                return [];
            }
            $runId = (int) $latest['id'];
        }

        $stmt = $this->db->prepare(<<<'SQL'
            SELECT r.*, i.symbol, i.name AS instrument_name, i.kind, i.region, i.currency
            FROM recommendations r
            INNER JOIN instruments i ON i.id = r.instrument_id
            WHERE r.agent_run_id = :run_id
            ORDER BY i.symbol COLLATE NOCASE ASC,
                     CASE r.horizon WHEN '1m' THEN 0 WHEN '3m' THEN 1 WHEN '6m' THEN 2
                                    WHEN '12m' THEN 3 ELSE 4 END ASC
            SQL);
        $stmt->execute(['run_id' => $runId]);
        return array_map(fn (array $row): array => $this->mapRecommendation($row), $stmt->fetchAll());
    }

    /** @param array<string, mixed> $row */
    private function mapRun(array $row): array
    {
        $models = null;
        if (!empty($row['model_refs_json'])) {
            $decoded = json_decode((string) $row['model_refs_json'], true);
            $models = is_array($decoded) ? $decoded : null;
        }

        $research = null;
        if (!empty($row['research_json'])) {
            $decoded = json_decode((string) $row['research_json'], true);
            $research = is_array($decoded) ? $decoded : null;
        }

        $infoNeeds = null;
        if (!empty($row['info_needs_json'])) {
            $decoded = json_decode((string) $row['info_needs_json'], true);
            $infoNeeds = is_array($decoded) ? $decoded : null;
        }

        $context = null;
        if (!empty($row['context_json'])) {
            $decoded = json_decode((string) $row['context_json'], true);
            $context = is_array($decoded) ? $decoded : null;
        }

        return [
            'id' => (int) $row['id'],
            'trigger' => $row['trigger_kind'],
            'status' => $row['status'],
            'started_at' => $row['started_at'],
            'finished_at' => $row['finished_at'],
            'models' => $models,
            'log' => $row['log_text'],
            'error' => $row['error_text'],
            'research' => $research,
            'info_needs' => $infoNeeds,
            'context' => $context,
        ];
    }

    /** @param array<string, mixed> $row */
    private function mapRecommendation(array $row): array
    {
        $jev = null;
        if (!empty($row['jev_payload_json'])) {
            $decoded = json_decode((string) $row['jev_payload_json'], true);
            $jev = is_array($decoded) ? $decoded : null;
        }

        $lenses = null;
        if (!empty($row['jev_lenses_json'])) {
            $decoded = json_decode((string) $row['jev_lenses_json'], true);
            $lenses = is_array($decoded) ? $decoded : null;
        }

        $conversation = null;
        if (!empty($row['conversation_json'])) {
            $decoded = json_decode((string) $row['conversation_json'], true);
            $conversation = is_array($decoded) ? $decoded : null;
        }

        return [
            'id' => (int) $row['id'],
            'agent_run_id' => (int) $row['agent_run_id'],
            'instrument_id' => (int) $row['instrument_id'],
            // 0019: which book this was decided about — the UI splits its panels on it.
            'book' => $row['book'] ?? 'portfolio',
            'symbol' => $row['symbol'],
            'instrument_name' => $row['instrument_name'],
            'kind' => $row['kind'],
            'region' => $row['region'],
            'currency' => $row['currency'],
            'action' => $row['action'],
            'horizon' => $row['horizon'],
            // 0013 — why, and what the doctrine gate did about it.
            'reason' => $row['reason'] ?? null,
            'loss_gate' => $row['loss_gate'] ?? null,
            'pair_symbol' => $row['pair_symbol'] ?? null,
            'confidence' => isset($row['confidence']) && $row['confidence'] !== null
                ? (float) $row['confidence']
                : null,
            'suppressed' => (bool) ($row['suppressed'] ?? false),
            'suppressed_reason' => $row['suppressed_reason'] ?? null,
            'proposed_action' => $row['proposed_action'] ?? null,
            'price_at_rec' => isset($row['price_at_rec']) && $row['price_at_rec'] !== null
                ? (float) $row['price_at_rec']
                : null,
            'rationale' => $row['rationale'],
            'jev' => $jev,
            'jev_lenses' => $lenses,
            'conversation' => $conversation,
            'created_at' => $row['created_at'],
            'updated_at' => $row['updated_at'],
        ];
    }
}
