<?php

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use Tradai\Api\Infrastructure\HistoryRepository;

function check(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$pdo = new PDO('sqlite::memory:', null, null, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
]);
$pdo->exec(<<<'SQL'
    CREATE TABLE instruments (
        id INTEGER PRIMARY KEY, symbol TEXT, name TEXT, currency TEXT, region TEXT
    );
    CREATE TABLE historical_series (
        id INTEGER PRIMARY KEY, series_key TEXT UNIQUE, series_kind TEXT,
        instrument_id INTEGER, benchmark_region TEXT, symbol TEXT, native_currency TEXT,
        source TEXT, resolution TEXT, earliest_date TEXT, as_of TEXT, updated_at TEXT
    );
    CREATE TABLE historical_points (
        series_id INTEGER, point_date TEXT, adjusted_close REAL, resolution TEXT,
        PRIMARY KEY(series_id, point_date)
    );
    INSERT INTO instruments VALUES (1,'OLD','Older listing','GBP','eu');
    INSERT INTO instruments VALUES (2,'NEW','New listing','USD','us');
    INSERT INTO historical_series VALUES
        (1,'instrument:1','instrument',1,NULL,'OLD','GBP','yfinance','compact','2018-01-02','2026-01-02','2026-01-03T00:00:00+00:00'),
        (2,'benchmark:eu','benchmark',NULL,'eu','EXSA.DE','EUR','yfinance','compact','2020-01-02','2026-01-02','2026-01-03T00:00:00+00:00');
    INSERT INTO historical_points VALUES
        (1,'2018-01-02',50,'monthly'),
        (1,'2020-01-02',80,'monthly'),
        (1,'2025-01-02',100,'daily'),
        (1,'2026-01-02',125,'daily'),
        (2,'2020-01-02',200,'monthly'),
        (2,'2025-01-02',250,'daily'),
        (2,'2026-01-02',300,'daily');
SQL);

$repo = new HistoryRepository($pdo);
$max = $repo->forInstrument(1, 'max');
check($max['basis'] === 'adjusted_performance', 'basis');
check($max['comparison_start'] === '2020-01-02', 'comparison start');
check($max['stock']['native_currency'] === 'GBP', 'stock native currency');
check($max['benchmark']['native_currency'] === 'EUR', 'benchmark native currency');
check($max['stock']['points'][0]['normalized_value'] === 62.5, 'pre-benchmark stock retained');
check($max['stock']['endpoint_return_pct'] === 56.25, 'stock return');
check($max['benchmark']['endpoint_return_pct'] === 50.0, 'benchmark return');

$oneYear = $repo->forInstrument(1, '1y');
check($oneYear['stock']['coverage_start'] === '2025-01-02', '1y cutoff');
check($oneYear['comparison_start'] === '2025-01-02', '1y normalization');

$missing = $repo->forInstrument(2, '5y');
check($missing['stock']['points'] === [], 'missing stock points');
check(str_contains($missing['warning'], 'not available'), 'missing warning');

try {
    $repo->forInstrument(1, '10y');
    throw new RuntimeException('invalid range accepted');
} catch (InvalidArgumentException) {
}
try {
    $repo->forInstrument(999, '5y');
    throw new RuntimeException('unknown instrument accepted');
} catch (RuntimeException $e) {
    check($e->getCode() === 404, 'unknown instrument status');
}

echo "history api tests passed\n";
