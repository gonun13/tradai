<?php

declare(strict_types=1);

use Tradai\Api\Domain\FifoLedger;

require __DIR__ . '/vendor/autoload.php';

/** FIFO ledger (0013) with sells and trade-date EUR locking (0031). Pure — no database. */

function near(mixed $actual, ?float $expected, string $label): void
{
    if ($expected === null) {
        if ($actual !== null) {
            throw new RuntimeException("{$label}: expected null, got " . var_export($actual, true));
        }
        return;
    }
    if ($actual === null || abs((float) $actual - $expected) > 1e-6) {
        throw new RuntimeException("{$label}: expected {$expected}, got " . var_export($actual, true));
    }
}

function same(mixed $actual, mixed $expected, string $label): void
{
    if ($actual !== $expected) {
        throw new RuntimeException("{$label}: expected " . var_export($expected, true) . ', got ' . var_export($actual, true));
    }
}

$buy = static fn (int $id, string $date, float $qty, float $price, float $fee = 0.0, ?float $fx = 1.0): array =>
    ['id' => $id, 'side' => 'buy', 'trade_date' => $date, 'quantity' => $qty, 'unit_price' => $price, 'commission' => $fee, 'fx_to_eur' => $fx];
$sell = static fn (int $id, string $date, float $qty, float $price, float $fee = 0.0, ?float $fx = 1.0): array =>
    ['id' => $id, 'side' => 'sell', 'trade_date' => $date, 'quantity' => $qty, 'unit_price' => $price, 'commission' => $fee, 'fx_to_eur' => $fx];

// Partial sell consumes the oldest lot first; commission is capitalised on buys and netted on sells.
$l = FifoLedger::walk([
    $buy(1, '2024-01-10', 10, 100, 10),   // unit cost 101
    $buy(2, '2025-02-01', 10, 120),       // unit cost 120
    $sell(3, '2025-06-01', 12, 150, 6),   // proceeds 1794; cost 10*101 + 2*120 = 1250
]);
near($l['quantity'], 8.0, 'partial: open qty');
near($l['total_cost'], 960.0, 'partial: open cost');
near($l['avg_cost'], 120.0, 'partial: avg cost');
same($l['first_trade_date'], '2025-02-01', 'partial: held since = oldest open lot');
same($l['opened_at'], '2024-01-10', 'partial: opened_at');
same($l['closed_at'], null, 'partial: still open');
same(count($l['disposals']), 1, 'partial: one disposal');
near($l['disposals'][0]['proceeds'], 1794.0, 'partial: proceeds');
near($l['disposals'][0]['cost'], 1250.0, 'partial: cost');
near($l['disposals'][0]['realized_pnl'], 544.0, 'partial: realised');
near($l['disposals'][0]['realized_pnl_eur'], 544.0, 'partial: EUR realised for EUR listing');

// Full close, out-of-order input, then a re-entry that ages from its own date.
$l = FifoLedger::walk([
    $sell(3, '2025-06-01', 20, 130),
    $buy(1, '2024-01-10', 10, 100),
    $buy(2, '2025-02-01', 10, 120),
]);
near($l['quantity'], 0.0, 'closed: open qty');
same($l['first_trade_date'], null, 'closed: no held-since');
same($l['closed_at'], '2025-06-01', 'closed: closed_at');
near($l['disposals'][0]['realized_pnl'], 400.0, 'closed: realised');

$l = FifoLedger::walk([
    $buy(1, '2024-01-10', 10, 100),
    $sell(2, '2024-06-01', 10, 110),
    $buy(3, '2026-03-01', 5, 90),
]);
near($l['quantity'], 5.0, 're-entry: open qty');
same($l['first_trade_date'], '2026-03-01', 're-entry: held since the new lot');
same($l['closed_at'], null, 're-entry: open again');

// EUR locking: buy at 0.90 EUR/USD, sell at 0.80. Native gain, EUR loss.
$l = FifoLedger::walk([
    $buy(1, '2024-01-10', 10, 100, 0, 0.90),  // cost 1000 USD = 900 EUR
    $sell(2, '2025-01-10', 10, 105, 0, 0.80), // proceeds 1050 USD = 840 EUR
]);
near($l['disposals'][0]['realized_pnl'], 50.0, 'fx: native gain');
near($l['disposals'][0]['cost_eur'], 900.0, 'fx: EUR cost at buy date');
near($l['disposals'][0]['proceeds_eur'], 840.0, 'fx: EUR proceeds at sell date');
near($l['disposals'][0]['realized_pnl_eur'], -60.0, 'fx: EUR loss');

// Pending FX on a consumed lot leaves every EUR figure null; native figures are unaffected.
$l = FifoLedger::walk([
    $buy(1, '2024-01-10', 10, 100, 0, null),
    $sell(2, '2025-01-10', 10, 105, 0, 0.80),
]);
near($l['disposals'][0]['realized_pnl'], 50.0, 'pending: native realised');
near($l['disposals'][0]['realized_pnl_eur'], null, 'pending: EUR realised');
near($l['disposals'][0]['cost_eur'], null, 'pending: EUR cost');

// Oversold is flagged with the date it first happened (callers reject it).
$l = FifoLedger::walk([
    $sell(1, '2024-01-01', 5, 100),
    $buy(2, '2024-02-01', 10, 100),
]);
near($l['oversold_quantity'], 5.0, 'oversold: quantity');
same($l['oversold_on'], '2024-01-01', 'oversold: date');

echo "fifo ledger tests passed\n";
