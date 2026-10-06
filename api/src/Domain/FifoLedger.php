<?php

declare(strict_types=1);

namespace Tradai\Api\Domain;

/**
 * FIFO cost-basis ledger over a single instrument's transactions.
 *
 * Portugal taxes securities disposals on a FIFO basis (0013 / 0012), so the open
 * position's cost basis and every realised gain are derived by consuming the oldest
 * lots first. Commission is capitalised into buy cost and netted out of sell proceeds
 * (0007 already treats cost basis as including commission).
 *
 * Amounts are in the instrument's native currency; display conversion happens at read time.
 * When transactions carry `fx_to_eur`, disposals also lock EUR amounts at trade-date rates
 * (buy lots at their own date, 0031); any missing rate leaves those EUR amounts null.
 */
final class FifoLedger
{
    private const EPS = 1e-9;

    /**
     * @param list<array<string, mixed>> $transactions Ordered or unordered; sorted here by trade_date then id.
     *     Optional `fx_to_eur` (instrument currency -> EUR on the trade date, 0031) locks EUR amounts.
     * @return array{
     *     quantity: float,
     *     total_cost: float,
     *     avg_cost: float,
     *     lot_count: int,
     *     first_trade_date: ?string,
     *     last_trade_date: ?string,
     *     opened_at: ?string,
     *     closed_at: ?string,
     *     oversold_quantity: float,
     *     oversold_on: ?string,
     *     disposals: list<array<string, mixed>>
     * }
     */
    public static function walk(array $transactions): array
    {
        usort($transactions, static function (array $a, array $b): int {
            $d = strcmp((string) ($a['trade_date'] ?? ''), (string) ($b['trade_date'] ?? ''));
            return $d !== 0 ? $d : ((int) ($a['id'] ?? 0) <=> (int) ($b['id'] ?? 0));
        });

        /** @var list<array{qty: float, unit_cost: float, unit_cost_eur: ?float, trade_date: string}> $lots */
        $lots = [];
        $disposals = [];
        $oversold = 0.0;
        $oversoldOn = null;
        $openedAt = null;
        $lastTradeDate = null;
        $closedAt = null;

        foreach ($transactions as $txn) {
            $qty = (float) ($txn['quantity'] ?? 0);
            $price = (float) ($txn['unit_price'] ?? 0);
            $commission = (float) ($txn['commission'] ?? 0);
            $side = (string) ($txn['side'] ?? 'buy');
            $tradeDate = (string) ($txn['trade_date'] ?? '');
            $fx = isset($txn['fx_to_eur']) && $txn['fx_to_eur'] !== null ? (float) $txn['fx_to_eur'] : null;

            if ($qty <= self::EPS) {
                continue;
            }
            if ($openedAt === null || $tradeDate < $openedAt) {
                $openedAt = $tradeDate;
            }
            if ($lastTradeDate === null || $tradeDate > $lastTradeDate) {
                $lastTradeDate = $tradeDate;
            }

            if ($side === 'buy') {
                $unitCost = ($qty * $price + $commission) / $qty;
                $lots[] = [
                    'qty' => $qty,
                    'unit_cost' => $unitCost,
                    'unit_cost_eur' => $fx === null ? null : $unitCost * $fx,
                    'trade_date' => $tradeDate,
                ];
                $closedAt = null;
                continue;
            }

            // sell — consume oldest lots first
            $remaining = $qty;
            $cost = 0.0;
            $costEur = 0.0;
            while ($remaining > self::EPS && $lots !== []) {
                $lot = &$lots[0];
                $take = min($lot['qty'], $remaining);
                $cost += $take * $lot['unit_cost'];
                $costEur = ($costEur === null || $lot['unit_cost_eur'] === null)
                    ? null
                    : $costEur + $take * $lot['unit_cost_eur'];
                $lot['qty'] -= $take;
                $remaining -= $take;
                if ($lot['qty'] <= self::EPS) {
                    array_shift($lots);
                }
                unset($lot);
            }
            if ($remaining > self::EPS) {
                // More sold than open at this date — book the uncovered part at zero cost and flag it.
                $oversold += $remaining;
                $oversoldOn ??= $tradeDate;
            }

            $proceeds = $qty * $price - $commission;
            $proceedsEur = $fx === null ? null : $proceeds * $fx;
            $realizedEur = ($proceedsEur === null || $costEur === null) ? null : $proceedsEur - $costEur;
            $disposals[] = [
                'sell_transaction_id' => (int) ($txn['id'] ?? 0),
                'trade_date' => $tradeDate,
                'quantity' => $qty,
                'proceeds' => $proceeds,
                'cost' => $cost,
                'realized_pnl' => $proceeds - $cost,
                'proceeds_eur' => $proceedsEur,
                'cost_eur' => $realizedEur === null ? null : $costEur,
                'realized_pnl_eur' => $realizedEur,
            ];
            if ($lots === []) {
                $closedAt = $tradeDate;
            }
        }

        $openQty = 0.0;
        $openCost = 0.0;
        foreach ($lots as $lot) {
            $openQty += $lot['qty'];
            $openCost += $lot['qty'] * $lot['unit_cost'];
        }

        return [
            'quantity' => $openQty,
            'total_cost' => $openCost,
            'avg_cost' => $openQty > self::EPS ? $openCost / $openQty : 0.0,
            'lot_count' => count($lots),
            // Held since: the oldest lot still open, so partial sells and re-entries age correctly.
            'first_trade_date' => $lots === [] ? null : $lots[0]['trade_date'],
            'last_trade_date' => $lastTradeDate,
            'opened_at' => $openedAt,
            'closed_at' => $lots === [] ? $closedAt : null,
            'oversold_quantity' => $oversold,
            'oversold_on' => $oversoldOn,
            'disposals' => $disposals,
        ];
    }
}
