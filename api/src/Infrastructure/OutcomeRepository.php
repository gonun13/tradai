<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use PDO;

/**
 * Scores matured recommendations against what the price actually did.
 *
 * 96 recommendations were stored before this existed and none was ever compared to a
 * subsequent price, so there was no way to know whether any of the advice helped. This is
 * deliberately not a backtest (`PROJECT.md` defers that): it reads `price_at_rec` and the
 * stored daily bars, nothing more.
 *
 * Note the horizons are 3m/6m/12m for holdings (`0030`; 6m/12m/24m before it) and 1m/3m/6m
 * for the tracker (`0020`), and
 * `price_bars` holds roughly seven months of history, so early on most rows — especially the
 * holdings' longer ones — will be `pending`; that is expected, not a failure.
 */
final class OutcomeRepository
{
    private const HORIZON_MONTHS = ['1m' => 1, '3m' => 3, '6m' => 6, '12m' => 12, '24m' => 24];

    public function __construct(private readonly PDO $db)
    {
    }

    /** @return array<string, mixed> */
    public function report(): array
    {
        $rows = $this->db->query(
            "SELECT r.id, r.instrument_id, r.book, r.action, r.horizon, r.reason, r.suppressed,
                    r.price_at_rec, r.created_at, i.symbol
             FROM recommendations r
             INNER JOIN instruments i ON i.id = r.instrument_id
             WHERE r.price_at_rec IS NOT NULL
             ORDER BY r.created_at ASC"
        )->fetchAll();

        $scored = [];
        $pending = 0;
        foreach ($rows as $row) {
            $months = self::HORIZON_MONTHS[$row['horizon']] ?? null;
            if ($months === null) {
                continue;
            }
            $matures = gmdate('Y-m-d', strtotime((string) $row['created_at'] . " +{$months} months"));
            if ($matures > gmdate('Y-m-d')) {
                $pending++;
                continue;
            }

            $bar = $this->db->prepare(
                'SELECT close, bar_date FROM price_bars
                 WHERE instrument_id = :id AND bar_date >= :matures
                 ORDER BY bar_date ASC LIMIT 1'
            );
            $bar->execute(['id' => (int) $row['instrument_id'], 'matures' => $matures]);
            $found = $bar->fetch();
            if ($found === false) {
                $pending++;
                continue;
            }

            $from = (float) $row['price_at_rec'];
            $to = (float) $found['close'];
            if ($from <= 0.0) {
                continue;
            }
            $ret = ($to - $from) / $from * 100.0;

            // A sell is right when the price fell; buy and hold when it did not.
            // Watch makes no directional claim, so it is recorded but not judged, and
            // neither does `drop` (0019) — declining to track a name says nothing about
            // where its price went.
            $correct = match ($row['action']) {
                'sell' => $ret < 0,
                'buy', 'hold' => $ret >= 0,
                default => null,
            };

            $scored[] = [
                'recommendation_id' => (int) $row['id'],
                'symbol' => $row['symbol'],
                'book' => $row['book'] ?? 'portfolio',
                'action' => $row['action'],
                'reason' => $row['reason'],
                'horizon' => $row['horizon'],
                'suppressed' => (bool) $row['suppressed'],
                'price_at_rec' => $from,
                'price_at_maturity' => $to,
                'matured_on' => $found['bar_date'],
                'return_pct' => round($ret, 2),
                'correct' => $correct,
            ];
        }

        return [
            'scored' => $scored,
            'pending' => $pending,
            'summary' => $this->summarize($scored),
        ];
    }

    /**
     * @param list<array<string, mixed>> $scored
     * @return array<string, mixed>
     */
    private function summarize(array $scored): array
    {
        $byAction = [];
        foreach ($scored as $s) {
            if ($s['correct'] === null) {
                continue;
            }
            $key = (string) $s['action'];
            $byAction[$key] ??= ['n' => 0, 'correct' => 0, 'mean_return_pct' => 0.0];
            $byAction[$key]['n']++;
            $byAction[$key]['correct'] += $s['correct'] ? 1 : 0;
            $byAction[$key]['mean_return_pct'] += (float) $s['return_pct'];
        }
        foreach ($byAction as $k => $v) {
            $byAction[$k]['mean_return_pct'] = round($v['mean_return_pct'] / max(1, $v['n']), 2);
            $byAction[$k]['hit_rate'] = round($v['correct'] / max(1, $v['n']), 3);
        }
        return ['by_action' => $byAction, 'judged' => count(array_filter($scored, static fn ($s) => $s['correct'] !== null))];
    }
}
