<?php

declare(strict_types=1);

namespace Tradai\Api;

/**
 * FIFO cost-basis ledger over a single instrument's transactions.
 *
 * Portugal taxes securities disposals on a FIFO basis (0013 / 0012), so the open
 * position's cost basis and every realised gain are derived by consuming the oldest
 * lots first. Commission is capitalised into buy cost and netted out of sell proceeds
 * (0007 already treats cost basis as including commission).
 *
 * Amounts are in the instrument's native currency; FX to EUR happens at read time.
 */
final class FifoLedger
{
    private const EPS = 1e-9;

    /**
     * @param list<array<string, mixed>> $transactions Ordered or unordered; sorted here by trade_date then id.
     * @return array{
     *     quantity: float,
     *     total_cost: float,
     *     avg_cost: float,
     *     lot_count: int,
     *     first_trade_date: ?string,
     *     last_trade_date: ?string,
     *     oversold_quantity: float,
     *     disposals: list<array<string, mixed>>
     * }
     */
    public static function walk(array $transactions): array
    {
        usort($transactions, static function (array $a, array $b): int {
            $d = strcmp((string) ($a['trade_date'] ?? ''), (string) ($b['trade_date'] ?? ''));
            return $d !== 0 ? $d : ((int) ($a['id'] ?? 0) <=> (int) ($b['id'] ?? 0));
        });

        /** @var list<array{qty: float, unit_cost: float}> $lots */
        $lots = [];
        $disposals = [];
        $oversold = 0.0;
        $firstTradeDate = null;
        $lastTradeDate = null;

        foreach ($transactions as $txn) {
            $qty = (float) ($txn['quantity'] ?? 0);
            $price = (float) ($txn['unit_price'] ?? 0);
            $commission = (float) ($txn['commission'] ?? 0);
            $side = (string) ($txn['side'] ?? 'buy');
            $tradeDate = (string) ($txn['trade_date'] ?? '');

            if ($qty <= self::EPS) {
                continue;
            }
            if ($firstTradeDate === null || $tradeDate < $firstTradeDate) {
                $firstTradeDate = $tradeDate;
            }
            if ($lastTradeDate === null || $tradeDate > $lastTradeDate) {
                $lastTradeDate = $tradeDate;
            }

            if ($side === 'buy') {
                $lots[] = ['qty' => $qty, 'unit_cost' => ($qty * $price + $commission) / $qty];
                continue;
            }

            // sell — consume oldest lots first
            $remaining = $qty;
            $cost = 0.0;
            while ($remaining > self::EPS && $lots !== []) {
                $lot = &$lots[0];
                $take = min($lot['qty'], $remaining);
                $cost += $take * $lot['unit_cost'];
                $lot['qty'] -= $take;
                $remaining -= $take;
                if ($lot['qty'] <= self::EPS) {
                    array_shift($lots);
                }
                unset($lot);
            }
            if ($remaining > self::EPS) {
                // More sold than ever bought — book the uncovered part at zero cost and flag it.
                $oversold += $remaining;
            }

            $proceeds = $qty * $price - $commission;
            $disposals[] = [
                'sell_transaction_id' => (int) ($txn['id'] ?? 0),
                'trade_date' => $tradeDate,
                'quantity' => $qty,
                'proceeds' => $proceeds,
                'cost' => $cost,
                'realized_pnl' => $proceeds - $cost,
            ];
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
            'first_trade_date' => $firstTradeDate,
            'last_trade_date' => $lastTradeDate,
            'oversold_quantity' => $oversold,
            'disposals' => $disposals,
        ];
    }
}
