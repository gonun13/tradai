<?php

declare(strict_types=1);

use Tradai\Api\Database;
use Tradai\Api\HoldingRepository;
use Tradai\Api\TrackerRepository;

require __DIR__ . '/vendor/autoload.php';

$dataDir = sys_get_temp_dir() . '/tradai-holding-test-' . bin2hex(random_bytes(6));
$pdo = Database::connection($dataDir);
$now = '2026-09-25T12:00:00+00:00';

$cases = [
    'UP' => ['quote' => 110.0, 'prior' => 100.0, 'same_day' => 109.0, 'expected' => 10.0],
    'DOWN' => ['quote' => 90.0, 'prior' => 100.0, 'expected' => -10.0],
    'FLAT' => ['quote' => 100.0, 'prior' => 100.0, 'expected' => 0.0],
    'NO_BAR' => ['quote' => 100.0, 'expected' => null],
    'NO_QUOTE' => ['prior' => 100.0, 'expected' => null],
    'ZERO_CLOSE' => ['quote' => 100.0, 'prior' => 0.0, 'expected' => null],
    'BAD_DATE' => ['quote' => 100.0, 'prior' => 90.0, 'as_of' => '2026-99-25', 'expected' => null],
];

try {
    $insertInstrument = $pdo->prepare(
        'INSERT INTO instruments
         (symbol, currency, kind, region, created_at, updated_at)
         VALUES (:symbol, \'EUR\', \'equity\', \'eu\', :created_at, :updated_at)'
    );
    $insertHolding = $pdo->prepare(
        'INSERT INTO holdings
         (instrument_id, quantity, avg_cost, total_cost, created_at, updated_at)
         VALUES (:instrument_id, 1, 100, 100, :created_at, :updated_at)'
    );
    $insertTracker = $pdo->prepare(
        'INSERT INTO tracker
         (symbol, instrument_id, name, note, added_at, updated_at)
         VALUES (:symbol, :instrument_id, :name, :note, :added_at, :updated_at)'
    );
    $insertQuote = $pdo->prepare(
        'INSERT INTO quotes
         (instrument_id, price, currency, as_of, source, updated_at)
         VALUES (:instrument_id, :price, \'EUR\', :as_of, \'test\', :updated_at)'
    );
    $insertBar = $pdo->prepare(
        'INSERT INTO price_bars (instrument_id, bar_date, close, source)
         VALUES (:instrument_id, :bar_date, :close, \'test\')'
    );

    foreach ($cases as $symbol => $case) {
        $insertInstrument->execute(['symbol' => $symbol, 'created_at' => $now, 'updated_at' => $now]);
        $instrumentId = (int) $pdo->lastInsertId();
        $insertHolding->execute([
            'instrument_id' => $instrumentId,
            'created_at' => $now,
            'updated_at' => $now,
        ]);
        $insertTracker->execute([
            'symbol' => $symbol,
            'instrument_id' => $instrumentId,
            'name' => $symbol . ' test',
            'note' => 'legacy note must not be exposed',
            'added_at' => $now,
            'updated_at' => $now,
        ]);

        if (array_key_exists('quote', $case)) {
            $insertQuote->execute([
                'instrument_id' => $instrumentId,
                'price' => $case['quote'],
                'as_of' => $case['as_of'] ?? '2026-09-25T12:00:00+00:00',
                'updated_at' => $now,
            ]);
        }
        if (array_key_exists('prior', $case)) {
            $insertBar->execute([
                'instrument_id' => $instrumentId,
                'bar_date' => '2026-09-24',
                'close' => $case['prior'],
            ]);
        }
        if (array_key_exists('same_day', $case)) {
            $insertBar->execute([
                'instrument_id' => $instrumentId,
                'bar_date' => '2026-09-25',
                'close' => $case['same_day'],
            ]);
        }
    }

    $books = [
        'holding' => array_map(
            static fn (array $row): array => [
                'symbol' => $row['instrument']['symbol'],
                'daily_change_pct' => $row['daily_change_pct'],
            ],
            (new HoldingRepository($pdo))->all()
        ),
        'tracker' => (new TrackerRepository($pdo))->all(),
    ];

    foreach ($books['tracker'] as $row) {
        if (array_key_exists('note', $row)) {
            throw new RuntimeException('tracker read model must not expose the legacy note column');
        }
    }

    $created = (new TrackerRepository($pdo))->create([
        'symbol' => 'NO_NOTE_INPUT',
        'name' => 'No note input',
        'note' => 'must be ignored',
    ]);
    if (array_key_exists('note', $created)) {
        throw new RuntimeException('tracker create response must not expose a note');
    }
    $storedNote = $pdo->query(
        "SELECT note FROM tracker WHERE symbol = 'NO_NOTE_INPUT'"
    )->fetchColumn();
    if ($storedNote !== null) {
        throw new RuntimeException('tracker create must ignore note input');
    }

    foreach ($books as $book => $rows) {
        $bySymbol = [];
        foreach ($rows as $row) {
            $bySymbol[$row['symbol']] = $row['daily_change_pct'];
        }

        foreach ($cases as $symbol => $case) {
            $actual = $bySymbol[$symbol] ?? null;
            $expected = $case['expected'];
            if ($expected === null) {
                if ($actual !== null) {
                    throw new RuntimeException("{$book} {$symbol}: expected null, got {$actual}");
                }
                continue;
            }
            if ($actual === null || abs((float) $actual - (float) $expected) > 0.000001) {
                throw new RuntimeException(
                    "{$book} {$symbol}: expected {$expected}, got " . var_export($actual, true)
                );
            }
        }
    }

    echo "portfolio and tracker daily change tests passed\n";
} finally {
    $databasePath = $dataDir . '/tradai.sqlite';
    if (is_file($databasePath)) {
        unlink($databasePath);
    }
    if (is_dir($dataDir)) {
        rmdir($dataDir);
    }
}
