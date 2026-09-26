<?php

declare(strict_types=1);

use Tradai\Api\Infrastructure\Database;
use Tradai\Api\Infrastructure\IngestRunRepository;

require __DIR__ . '/vendor/autoload.php';

function check(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$dir = sys_get_temp_dir() . '/tradai-operation-history-' . bin2hex(random_bytes(6));
mkdir($dir, 0775, true);

try {
    $db = Database::connection($dir);
    $runs = new IngestRunRepository($db);

    for ($i = 1; $i <= 21; $i++) {
        $id = $runs->start();
        $runs->succeed($id, [
            'statistics' => ['layers' => ['historical' => [
                'ingested' => $i, 'from_cache' => 0, 'missing' => 0,
            ]]],
            'errors' => [],
            'symbol_not_found' => [],
        ]);
    }
    $failedId = $runs->start();
    $runs->fail($failedId, 'fixture worker failure');

    $history = $runs->list();
    check(count($history) === 20, 'history is limited to 20');
    check($history[0]['id'] === $failedId, 'newest failure is first');
    check($history[19]['id'] === 3, 'oldest retained row is #3');

    $failed = $runs->find($failedId);
    check($failed !== null && $failed['status'] === 'failed', 'failed status is stored');
    check($failed['started_at'] !== null && $failed['finished_at'] !== null, 'failure timestamps are complete');
    check($failed['error'] === 'fixture worker failure', 'failure detail is stored');

    $succeeded = $runs->find($failedId - 1);
    check($succeeded !== null && $succeeded['status'] === 'succeeded', 'success status is stored');
    check($succeeded['finished_at'] !== null && is_array($succeeded['report']), 'success report and timestamp are stored');
    check($runs->find(1) === null && $runs->find(2) === null, 'oldest rows are pruned');

    echo "operation history tests passed\n";
} finally {
    $dbFile = $dir . '/tradai.sqlite';
    if (is_file($dbFile)) unlink($dbFile);
    rmdir($dir);
}
