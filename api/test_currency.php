<?php

declare(strict_types=1);

use Tradai\Api\Database;
use Tradai\Api\HoldingRepository;
use Tradai\Api\SettingsRepository;
use Tradai\Api\TrackerRepository;

require __DIR__ . '/vendor/autoload.php';

function expect(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

function near(?float $actual, float $expected): bool
{
    return $actual !== null && abs($actual - $expected) < 0.0001;
}

$dataDir = sys_get_temp_dir() . '/tradai-currency-test-' . bin2hex(random_bytes(6));
$pdo = Database::connection($dataDir);
$now = '2026-09-25T12:00:00+00:00';

try {
    $pdo->prepare("INSERT INTO settings (key, value, updated_at) VALUES ('cash_eur', '42.5', ?)")
        ->execute([$now]);
    $pdo->prepare("INSERT INTO settings (key, value, updated_at) VALUES ('realized_gains_ytd_override_eur', '5', ?)")
        ->execute([$now]);

    $settings = new SettingsRepository($pdo);
    $initial = $settings->portfolio();
    expect($initial['display_currency'] === 'EUR', 'EUR must be the default');
    expect($initial['cash']['amount'] === 42.5 && $initial['cash']['currency'] === 'EUR', 'legacy cash must migrate once');
    expect($initial['realized_gains_ytd_override']['currency'] === 'EUR', 'legacy override must migrate once');

    foreach (['USD' => 0.8, 'GBP' => 1.2, 'CHF' => 1.05] as $base => $rate) {
        $pdo->prepare(
            "INSERT INTO fx_rates (base_currency, quote_currency, rate, as_of, source, updated_at)
             VALUES (?, 'EUR', ?, '2026-09-25', 'test', ?)"
        )->execute([$base, $rate, $now]);
    }

    $updated = $settings->update([
        'display_currency' => 'USD',
        'cash' => ['amount' => 20, 'currency' => 'GBP'],
        'realized_gains_ytd_override' => ['amount' => 10, 'currency' => 'CHF'],
    ]);
    expect($updated['display_currency'] === 'USD', 'USD display setting must persist');
    expect(near($updated['cash']['display_amount'], 30.0), 'GBP cash must cross-convert through EUR');
    expect(near($updated['realized_gains_ytd_override']['display_amount'], 13.13), 'CHF override must cross-convert');

    $partial = $settings->update(['portfolio_profile_text' => 'Keep quality.']);
    expect($partial['cash']['amount'] === 20.0 && $partial['cash']['currency'] === 'GBP', 'partial updates must preserve money');
    try {
        $settings->update(['display_currency' => 'CAD']);
        throw new RuntimeException('unsupported display currency was accepted');
    } catch (InvalidArgumentException) {
        // expected
    }

    $pdo->prepare(
        "INSERT INTO instruments (symbol, currency, kind, region, created_at, updated_at)
         VALUES ('CROSS', 'GBP', 'equity', 'eu', ?, ?)"
    )->execute([$now, $now]);
    $instrumentId = (int) $pdo->lastInsertId();
    $pdo->prepare(
        'INSERT INTO holdings (instrument_id, quantity, avg_cost, total_cost, created_at, updated_at)
         VALUES (?, 10, 10, 100, ?, ?)'
    )->execute([$instrumentId, $now, $now]);
    $pdo->prepare(
        "INSERT INTO quotes (instrument_id, price, currency, as_of, source, updated_at)
         VALUES (?, 10, 'CHF', '2026-09-25', 'test', ?)"
    )->execute([$instrumentId, $now]);
    $pdo->prepare(
        'INSERT INTO tracker (symbol, instrument_id, name, added_at, updated_at) VALUES (?, ?, ?, ?, ?)'
    )->execute(['CROSS', $instrumentId, 'Cross currency', $now, $now]);

    $holding = (new HoldingRepository($pdo))->all()[0];
    expect(near($holding['cost_display'], 150.0), 'instrument currency must convert cost');
    expect(near($holding['market_value_display'], 131.25), 'quote currency must convert market value');
    expect(near($holding['fx_to_display'], 1.5), 'base-to-display cross rate must be exposed');
    expect($holding['display_currency'] === 'USD', 'holding must carry selected currency');

    $tracked = (new TrackerRepository($pdo))->all()[0];
    expect(near($tracked['price_display'], 13.125), 'tracker quote must use quote currency');
    expect($tracked['display_currency'] === 'USD', 'tracker row must carry selected currency');

    $pdo->prepare(
        "INSERT INTO instruments (symbol, currency, kind, region, created_at, updated_at)
         VALUES ('NOFX', 'CAD', 'equity', 'us', ?, ?)"
    )->execute([$now, $now]);
    $missingId = (int) $pdo->lastInsertId();
    $pdo->prepare(
        'INSERT INTO holdings (instrument_id, quantity, avg_cost, total_cost, created_at, updated_at)
         VALUES (?, 1, 10, 10, ?, ?)'
    )->execute([$missingId, $now, $now]);
    $pdo->prepare(
        "INSERT INTO quotes (instrument_id, price, currency, as_of, source, updated_at)
         VALUES (?, 12, 'CAD', '2026-09-25', 'test', ?)"
    )->execute([$missingId, $now]);
    $missing = (new HoldingRepository($pdo))->all()[1];
    expect($missing['market_value_display'] === null, 'missing FX must expose null, never relabel');

    echo "configurable display currency tests passed\n";
} finally {
    $databasePath = $dataDir . '/tradai.sqlite';
    if (is_file($databasePath)) unlink($databasePath);
    if (is_dir($dataDir)) rmdir($dataDir);
}
