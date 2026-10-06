<?php

declare(strict_types=1);

use Tradai\Api\Domain\TradeFxSource;
use Tradai\Api\Infrastructure\Database;
use Tradai\Api\Infrastructure\HoldingRepository;
use Tradai\Api\Infrastructure\SettingsRepository;

require __DIR__ . '/vendor/autoload.php';

/** Sells, closed positions and trade-date EUR realised P&L (0031) against a temp SQLite. */

final class StubFx implements TradeFxSource
{
    /** @var array<string, float> "CCY|date" => rate */
    public array $rates = [];
    public int $calls = 0;

    public function rateToEurOn(string $base, string $date): ?array
    {
        $this->calls++;
        $rate = $this->rates[$base . '|' . $date] ?? null;
        return $rate === null ? null : ['rate' => $rate, 'as_of' => $date];
    }
}

function check(bool $ok, string $label): void
{
    if (!$ok) {
        throw new RuntimeException('FAILED: ' . $label);
    }
}

function close(mixed $actual, float $expected, string $label): void
{
    check($actual !== null && abs((float) $actual - $expected) < 1e-6, $label . ' (got ' . var_export($actual, true) . ", expected {$expected})");
}

/** @param callable(): mixed $fn */
function rejects(callable $fn, string $exceptionClass, string $label): void
{
    try {
        $fn();
    } catch (Throwable $e) {
        check($e instanceof $exceptionClass, $label . ' threw ' . $e::class . ': ' . $e->getMessage());
        return;
    }
    throw new RuntimeException('FAILED: ' . $label . ' did not throw');
}

$dataDir = sys_get_temp_dir() . '/tradai-sell-test-' . bin2hex(random_bytes(6));
$pdo = Database::connection($dataDir);
$fx = new StubFx();
$fx->rates = [
    'USD|2024-01-10' => 0.90,
    'USD|2025-01-10' => 0.80,
    'USD|2025-03-01' => 0.85,
];
$repo = new HoldingRepository($pdo, $fx);
$settings = new SettingsRepository($pdo);
$count = static fn (string $sql): int => (int) $pdo->query($sql)->fetchColumn();

try {
    $buy = $repo->create([
        'symbol' => 'AAPL', 'currency' => 'USD', 'kind' => 'equity', 'region' => 'us',
        'trade_date' => '2024-01-10', 'quantity' => 10, 'unit_price' => 100, 'commission' => 0,
    ]);
    $holdingId = (int) $buy['id'];
    $instrumentId = (int) $buy['instrument']['id'];
    close($buy['transactions'][0]['fx_to_eur'], 0.90, 'buy locks its trade-date rate');

    // Partial sell: 4 @ 105. Native +20; EUR 336 - 360 = -24.
    $partial = $repo->recordSell($holdingId, ['trade_date' => '2025-01-10', 'quantity' => 4, 'unit_price' => 105]);
    check($partial['is_open'] && $partial['was_open'], 'partial sell keeps the position open');
    close($partial['holding']['quantity'], 6.0, 'partial sell reduces quantity');
    close($partial['disposal']['realized_pnl_native'], 20.0, 'partial sell native realised');
    close($partial['disposal']['realized_pnl_eur'], -24.0, 'partial sell EUR realised at trade-date rates');
    close($partial['holding']['realized_pnl_native'], 20.0, 'holding carries realised native');
    close($partial['holding']['realized_pnl_display'], -24.0, 'holding carries realised display (EUR)');
    check($partial['holding']['trade_date'] === '2024-01-10', 'Acquired date is the last buy, not the sell');

    // Overselling is rejected and nothing is written.
    $before = $count('SELECT COUNT(*) FROM transactions');
    rejects(fn () => $repo->recordSell($holdingId, ['trade_date' => '2025-02-01', 'quantity' => 7, 'unit_price' => 100]), InvalidArgumentException::class, 'oversell');
    check($count('SELECT COUNT(*) FROM transactions') === $before, 'rejected oversell writes nothing');
    rejects(fn () => $repo->recordSell($holdingId, ['trade_date' => '2023-12-31', 'quantity' => 1, 'unit_price' => 100]), InvalidArgumentException::class, 'sell before any buy');
    rejects(fn () => $repo->recordSell($holdingId, ['trade_date' => '2025-02-30', 'quantity' => 1, 'unit_price' => 100]), InvalidArgumentException::class, 'impossible date');

    // Full exit: 6 @ 110. Native +60; EUR 561 - 540 = +21.
    $exit = $repo->recordSell($holdingId, ['trade_date' => '2025-03-01', 'quantity' => 6, 'unit_price' => 110, 'notes' => 'done']);
    check(!$exit['is_open'] && $exit['was_open'] && $exit['holding'] === null, 'full sell closes the position');
    check($count('SELECT COUNT(*) FROM holdings') === 0, 'closed position has no holdings row');
    check($count('SELECT COUNT(*) FROM transactions') === 3, 'closed position keeps its transactions');
    check($repo->all() === [], 'holdings list excludes closed positions');

    $closed = $repo->closedPositions();
    check(count($closed) === 1 && $closed[0]['instrument']['symbol'] === 'AAPL', 'closed positions lists AAPL');
    $c = $closed[0];
    check($c['opened_at'] === '2024-01-10' && $c['closed_at'] === '2025-03-01', 'closed position dates');
    close($c['quantity_sold'], 10.0, 'closed quantity sold');
    close($c['cost_native'], 1000.0, 'closed cost');
    close($c['proceeds_native'], 1080.0, 'closed proceeds');
    close($c['realized_pnl_native'], 80.0, 'closed realised native');
    close($c['realized_pnl_eur'], -3.0, 'closed realised EUR');
    close($c['realized_pct'], 8.0, 'closed realised %');
    check(count($c['transactions']) === 3, 'closed position lists its transactions');

    $summary = $repo->summary([]);
    close($summary['realized_all_time_display'], -3.0, 'summary all-time realised');
    close($summary['realized_ytd_display'], 0.0, 'summary YTD realised excludes earlier years');
    close($summary['total_pnl_display'], -3.0, 'summary total with no open positions');
    close($settings->portfolio()['realized_gains_all_time_from_disposals_display'], -3.0, 'settings all-time realised');

    // Removing the closing sell reopens the position on the same history.
    $closingSellId = (int) $pdo->query("SELECT id FROM transactions WHERE side = 'sell' ORDER BY trade_date DESC LIMIT 1")->fetchColumn();
    $reopen = $repo->deleteTransaction($closingSellId);
    check($reopen['is_open'] && !$reopen['was_open'], 'deleting the closing sell reopens');
    close($reopen['holding']['quantity'], 6.0, 'reopened quantity');
    check($reopen['holding']['first_trade_date'] === '2024-01-10', 'reopened held-since');
    check($repo->closedPositions() === [], 'reopened position leaves the closed list');

    // A buy that a later sell depends on cannot be deleted.
    $buyId = (int) $pdo->query("SELECT id FROM transactions WHERE side = 'buy' LIMIT 1")->fetchColumn();
    rejects(fn () => $repo->deleteTransaction($buyId), InvalidArgumentException::class, 'delete funding buy');
    check($count("SELECT COUNT(*) FROM transactions WHERE side = 'buy'") === 1, 'funding buy kept');

    // Editing a sell re-walks FIFO; an edit that oversells is rejected.
    $sellId = (int) $pdo->query("SELECT id FROM transactions WHERE side = 'sell' LIMIT 1")->fetchColumn();
    $edited = $repo->updateTransaction($sellId, ['unit_price' => 110]);
    close($edited['holding']['realized_pnl_native'], 40.0, 'edited sell realised');
    rejects(fn () => $repo->updateTransaction($sellId, ['quantity' => 11]), InvalidArgumentException::class, 'edit to oversell');

    // Legacy acquisition-form edit still works and returns the mutation shape.
    $viaForm = $repo->update((int) $reopen['holding']['id'], ['transaction_id' => $buyId, 'quantity' => 12]);
    close($viaForm['holding']['quantity'], 8.0, 'acquisition edit re-walks FIFO');

    // Erasing a closed position needs it closed; erasing an open one goes through delete().
    rejects(fn () => $repo->eraseClosed($instrumentId), RuntimeException::class, 'erase open as closed');

    // Pending FX: no rate at write time, filled later by the ingest sweep.
    $msft = $repo->create([
        'symbol' => 'MSFT', 'currency' => 'USD', 'trade_date' => '2025-04-01', 'quantity' => 2, 'unit_price' => 50,
    ]);
    check($msft['transactions'][0]['fx_to_eur'] === null, 'missing rate stays pending, write succeeds');
    $msftExit = $repo->recordSell((int) $msft['id'], ['trade_date' => '2025-05-01', 'quantity' => 2, 'unit_price' => 60]);
    check($msftExit['disposal']['realized_pnl_eur'] === null, 'disposal EUR pending');
    check($repo->summary([])['realized_all_time_display'] === null, 'pending disposal makes all-time unavailable');
    $fx->rates['USD|2025-04-01'] = 0.90;
    $fx->rates['USD|2025-05-01'] = 0.90;
    $retry = new HoldingRepository($pdo, $fx); // fresh per-request cache, as a new request would have
    check($retry->fillMissingTradeFx() === 2, 'sweep fills both MSFT rates');
    $msftClosed = array_values(array_filter($retry->closedPositions(), static fn (array $r): bool => $r['instrument']['symbol'] === 'MSFT'));
    close($msftClosed[0]['realized_pnl_eur'], 18.0, 'swept disposal EUR realised');

    // Display currency USD: locked EUR converts at today's EUR -> USD.
    $pdo->exec("INSERT INTO fx_rates (base_currency, quote_currency, rate, as_of, source, updated_at)
                VALUES ('USD', 'EUR', 0.8, '2026-10-05', 'test', '2026-10-05T00:00:00+00:00')");
    $settings->set('display_currency', 'USD');
    close($retry->closedPositions()[0]['realized_pnl_display'], 22.5, 'EUR realised shown in USD');

    // Erase a closed position: history and disposals go.
    $msftInstrument = (int) $msftClosed[0]['instrument']['id'];
    $retry->eraseClosed($msftInstrument);
    check($count("SELECT COUNT(*) FROM transactions WHERE instrument_id = {$msftInstrument}") === 0, 'erase removes transactions');
    check($count("SELECT COUNT(*) FROM realized_disposals WHERE instrument_id = {$msftInstrument}") === 0, 'erase removes disposals');

    // Erasing an open holding also clears its disposals (no stale realised totals).
    $retry->delete((int) $viaForm['holding']['id']);
    check($count('SELECT COUNT(*) FROM realized_disposals') === 0, 'delete clears disposals');

    echo "sell flow tests passed\n";
} finally {
    $databasePath = $dataDir . '/tradai.sqlite';
    foreach ([$databasePath, $databasePath . '-wal', $databasePath . '-shm'] as $file) {
        if (is_file($file)) {
            unlink($file);
        }
    }
    if (is_dir($dataDir)) {
        rmdir($dataDir);
    }
}
