<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use PDO;

/**
 * Realised P&L totals from `realized_disposals` (0013). Amounts are locked in EUR at trade
 * dates (0031); only the EUR -> display step uses today's rate.
 */
final class RealizedGains
{
    /**
     * Null while any matching disposal still lacks a trade-date rate, or EUR -> display is missing.
     * No disposals is a real zero.
     */
    public static function display(PDO $db, string $display, ?string $year = null, ?int $instrumentId = null): ?float
    {
        $where = [];
        $params = [];
        if ($year !== null) {
            $where[] = 'substr(trade_date, 1, 4) = :year';
            $params['year'] = $year;
        }
        if ($instrumentId !== null) {
            $where[] = 'instrument_id = :instrument_id';
            $params['instrument_id'] = $instrumentId;
        }
        $stmt = $db->prepare(
            'SELECT COUNT(*) AS n, COUNT(realized_pnl_eur) AS locked, COALESCE(SUM(realized_pnl_eur), 0) AS eur
             FROM realized_disposals'
            . ($where === [] ? '' : ' WHERE ' . implode(' AND ', $where))
        );
        $stmt->execute($params);
        $row = $stmt->fetch();
        if ((int) $row['n'] === 0) {
            return 0.0;
        }
        if ((int) $row['locked'] !== (int) $row['n']) {
            return null;
        }
        return self::eurToDisplay($db, (float) $row['eur'], $display);
    }

    public static function eurToDisplay(PDO $db, ?float $eur, string $display): ?float
    {
        $converted = CurrencyRates::convert($db, $eur, 'EUR', $display);
        return $converted === null ? null : round($converted, 2);
    }
}
