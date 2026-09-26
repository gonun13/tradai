<?php

declare(strict_types=1);

use DI\Container;
use Psr\Http\Message\ResponseInterface as Response;
use Psr\Http\Message\ServerRequestInterface as Request;
use Slim\Exception\HttpException;
use Slim\Factory\AppFactory;
use Tradai\Api\Infrastructure\AgentRepository;
use Tradai\Api\Infrastructure\AlertRepository;
use Tradai\Api\Infrastructure\Database;
use Tradai\Api\Infrastructure\HoldingRepository;
use Tradai\Api\Infrastructure\HistoryRepository;
use Tradai\Api\Infrastructure\IngestRunRepository;
use Tradai\Api\Infrastructure\OutcomeRepository;
use Tradai\Api\Infrastructure\SettingsRepository;
use Tradai\Api\Infrastructure\InstrumentRepository;
use Tradai\Api\Infrastructure\ThesisRepository;
use Tradai\Api\Infrastructure\TrackerRepository;
use Tradai\Api\Infrastructure\WorkerClient;

require __DIR__ . '/../vendor/autoload.php';

$container = new Container();
AppFactory::setContainer($container);
$app = AppFactory::create();

$dataDir = getenv('TRADAI_DATA_DIR') ?: '/data';
$workerBase = getenv('WORKER_BASE_URL') ?: 'http://worker:8090';
$pdo = Database::connection($dataDir);
$holdings = new HoldingRepository($pdo);
$history = new HistoryRepository($pdo);
$ingestRuns = new IngestRunRepository($pdo);
$agents = new AgentRepository($pdo);
$alerts = new AlertRepository($pdo);
$outcomes = new OutcomeRepository($pdo);
$settings = new SettingsRepository($pdo);
$theses = new ThesisRepository($pdo);
$tracker = new TrackerRepository($pdo);
$instruments = new InstrumentRepository($pdo);
$worker = new WorkerClient($workerBase);

$json = static function (Response $response, mixed $payload, int $status = 200): Response {
    $response->getBody()->write(json_encode($payload, JSON_THROW_ON_ERROR));
    return $response
        ->withHeader('Content-Type', 'application/json')
        ->withStatus($status);
};

$app->add(function (Request $request, $handler) {
    $response = $handler->handle($request);
    return $response
        ->withHeader('Access-Control-Allow-Origin', '*')
        ->withHeader('Access-Control-Allow-Headers', 'Content-Type')
        ->withHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS');
});

$app->addBodyParsingMiddleware();
$app->addRoutingMiddleware();
$errorMiddleware = $app->addErrorMiddleware(true, true, true);
$errorMiddleware->setDefaultErrorHandler(
    function (Request $request, \Throwable $exception, bool $displayErrorDetails) use ($app, $json) {
        $status = 500;
        $message = $exception->getMessage();
        if ($exception instanceof \InvalidArgumentException) {
            $status = 400;
        } elseif ($exception instanceof \RuntimeException && $exception->getCode() === 404) {
            $status = 404;
        } elseif ($exception instanceof \RuntimeException && $exception->getCode() >= 400 && $exception->getCode() < 600) {
            $status = (int) $exception->getCode();
        } elseif ($exception instanceof HttpException) {
            $status = $exception->getCode() ?: 500;
            $message = $exception->getMessage();
        }

        $response = $app->getResponseFactory()->createResponse();
        return $json($response, [
            'ok' => false,
            'error' => $message,
        ], $status);
    }
);

$app->options('/{routes:.+}', function (Request $request, Response $response): Response {
    return $response;
});

$app->get('/', function (Request $request, Response $response) use ($json): Response {
    return $json($response, [
        'ok' => true,
        'service' => 'tradai-api',
        'stage' => '7',
        'message' => 'Slim API is up. Open the UI at http://127.0.0.1:3000',
        'endpoints' => [
            'GET /',
            'GET /health',
            'GET /setup',
            'GET /holdings',
            'GET /instruments/{id}/history?range=1y|2y|5y|max',
            'POST /holdings',
            'PUT /holdings/{id}',
            'DELETE /holdings/{id}',
            'GET /context/preview?book=portfolio|tracker',
            'GET /ingest/report',
            'GET /ingest/runs',
            'GET /ingest/runs/{id}',
            'POST /refresh/market',
            'POST /agent/run',
            'GET /agent/runs',
            'GET /agent/runs/latest',
            'GET /agent/runs/{id}',
            'GET /agent/runs/{id}/log',
            'GET /recommendations',
            'GET /alerts',
            'POST /alerts/{id}/ack',
        ],
    ]);
});

$app->get('/health', function (Request $request, Response $response) use ($json, $dataDir, $pdo, $alerts): Response {
    $dbPath = rtrim($dataDir, '/') . '/tradai.sqlite';
    $count = (int) $pdo->query('SELECT COUNT(*) FROM holdings')->fetchColumn();
    $quotes = (int) $pdo->query('SELECT COUNT(*) FROM quotes')->fetchColumn();
    $news = (int) $pdo->query('SELECT COUNT(*) FROM news_items')->fetchColumn();
    $technicals = (int) $pdo->query('SELECT COUNT(*) FROM technicals')->fetchColumn();
    $runs = (int) $pdo->query('SELECT COUNT(*) FROM agent_runs')->fetchColumn();
    $recs = (int) $pdo->query('SELECT COUNT(*) FROM recommendations')->fetchColumn();

    return $json($response, [
        'ok' => true,
        'service' => 'tradai-api',
        'stage' => '7',
        'data_dir' => $dataDir,
        'sqlite_exists' => file_exists($dbPath),
        'holdings_count' => $count,
        'quotes_count' => $quotes,
        'news_count' => $news,
        'technicals_count' => $technicals,
        'agent_runs_count' => $runs,
        'recommendations_count' => $recs,
        'alerts_unread' => $alerts->unreadCount(),
        'time' => gmdate('c'),
    ]);
});

$app->get('/setup', function (Request $request, Response $response) use ($json): Response {
    $present = static function (string $name): bool {
        $v = getenv($name);
        return is_string($v) && trim($v) !== '';
    };

    $afterClose = getenv('ADVISORY_AFTER_US_CLOSE_MINUTES');
    $afterCloseMinutes = is_string($afterClose) && ctype_digit(trim($afterClose))
        ? (int) trim($afterClose)
        : 30;
    $fireTotal = 16 * 60 + $afterCloseMinutes;
    $fireAtEt = sprintf('%02d:%02d', intdiv($fireTotal, 60) % 24, $fireTotal % 60);

    return $json($response, [
        'ok' => true,
        'stage' => '7',
        'keys' => [
            'finnhub' => $present('FINNHUB_API_KEY'),
            'marketaux' => $present('MARKETAUX_API_TOKEN'),
            'alpha_vantage' => $present('ALPHA_VANTAGE_API_KEY'),
            'typesafe' => $present('TYPESAFE_API_KEY'),
            'claude_oauth' => $present('CLAUDE_CODE_OAUTH_TOKEN'),
            'anthropic_api_key_set' => $present('ANTHROPIC_API_KEY'),
        ],
        'schedule' => [
            'timezone' => 'America/New_York',
            'us_close' => '16:00',
            'after_us_close_minutes' => $afterCloseMinutes,
            'fire_at_et' => $fireAtEt,
            'interval_seconds' => (int) (getenv('ADVISORY_INTERVAL_SECONDS') ?: 86400),
            'max_scenario_rounds' => (int) (getenv('ADVISORY_MAX_SCENARIO_ROUNDS') ?: 0),
        ],
        'docs' => [
            'finnhub' => 'https://finnhub.io — US quotes and equity fundamentals',
            'marketaux' => 'https://www.marketaux.com — news',
            'alpha_vantage' => 'https://www.alphavantage.co — EU equities and ETF fundamentals; US equity fallback',
            'typesafe' => 'https://console.typesafe.ai — Jev / System One',
            'claude' => 'Host: `claude setup-token` → CLAUDE_CODE_OAUTH_TOKEN (do not set ANTHROPIC_API_KEY)',
            'env_file' => 'Copy `.env.example` → `.env` on the host; restart with `bin/up -d`',
        ],
        'warnings' => $present('ANTHROPIC_API_KEY')
            ? ['ANTHROPIC_API_KEY is set — unset it to stay on Claude subscription billing.']
            : [],
    ]);
});

$app->get('/context/preview', function (Request $request, Response $response) use ($json, $holdings, $tracker, $settings): Response {
    $book = strtolower(trim((string) ($request->getQueryParams()['book'] ?? 'portfolio')));
    if (!in_array($book, ['portfolio', 'tracker'], true)) {
        throw new \InvalidArgumentException('book must be portfolio or tracker');
    }

    $rows = $book === 'portfolio' ? $holdings->all() : $tracker->all();
    $preview = [];
    foreach ($rows as $row) {
        $features = $row['technicals']['features'] ?? null;
        $quote = $row['quote'] ?? null;
        $instrument = $book === 'portfolio' ? $row['instrument'] : ($row['instrument'] ?? []);
        $preview[] = [
            'book' => $book,
            'symbol' => $book === 'portfolio' ? $instrument['symbol'] : $row['symbol'],
            'name' => $book === 'portfolio' ? $instrument['name'] : $row['name'],
            'historical' => [
                'quote' => $quote,
                'bars' => is_array($features) ? [
                    'count' => isset($features['bar_count']) ? (int) $features['bar_count'] : null,
                    'last_close' => isset($features['last_close']) ? (float) $features['last_close'] : null,
                    'as_of' => $features['as_of_bar'] ?? ($row['technicals']['as_of'] ?? null),
                ] : null,
            ],
            'fundamentals' => $row['fundamentals'] ?? null,
            'technicals' => $row['technicals'] ?? null,
            'news' => $row['news'] ?? [],
        ];
    }

    return $json($response, [
        'ok' => true,
        'stage' => '7',
        'book' => $book,
        'display_currency' => $settings->portfolio()['display_currency'],
        'instruments' => $preview,
    ]);
});

$app->get('/holdings', function (Request $request, Response $response) use ($json, $holdings, $settings): Response {
    $rows = $holdings->all();
    $totalDisplay = 0.0;
    $complete = $rows !== [];
    foreach ($rows as $h) {
        if (($h['market_value_display'] ?? null) === null) {
            $complete = false;
        } else {
            $totalDisplay += (float) $h['market_value_display'];
        }
    }
    $displayCurrency = $settings->portfolio()['display_currency'];

    return $json($response, [
        'ok' => true,
        'holdings' => $rows,
        'display_currency' => $displayCurrency,
        'portfolio_market_value_display' => $complete ? round($totalDisplay, 2) : null,
    ]);
});

$app->get('/instruments/{id}/history', function (Request $request, Response $response, array $args) use ($json, $history): Response {
    $range = (string) ($request->getQueryParams()['range'] ?? '5y');
    return $json($response, $history->forInstrument((int) $args['id'], $range));
});

$app->post('/holdings', function (Request $request, Response $response) use ($json, $holdings, $tracker): Response {
    /** @var array<string, mixed> $body */
    $body = (array) $request->getParsedBody();
    $created = $holdings->create($body);
    // Auto-promote (0019, revised by 0024): a name you now own leaves the tracker.
    $tracker->archiveBySymbol((string) $created['instrument']['symbol']);
    return $json($response, ['ok' => true, 'holding' => $created], 201);
});

$app->put('/holdings/{id}', function (Request $request, Response $response, array $args) use ($json, $holdings): Response {
    $id = (int) $args['id'];
    /** @var array<string, mixed> $body */
    $body = (array) $request->getParsedBody();
    $updated = $holdings->update($id, $body);
    return $json($response, ['ok' => true, 'holding' => $updated]);
});

$app->delete('/holdings/{id}', function (Request $request, Response $response, array $args) use ($json, $holdings, $tracker): Response {
    $id = (int) $args['id'];
    $symbol = $holdings->delete($id);
    // A full exit drops the name back onto the tracker it was promoted from (0019).
    $tracker->unarchiveBySymbol($symbol);
    return $json($response, ['ok' => true]);
});

$app->get('/ingest/report', function (Request $request, Response $response) use ($json, $pdo): Response {
    $row = $pdo->query('SELECT payload_json, updated_at FROM ingest_reports WHERE id = 1')->fetch();
    if ($row === false) {
        return $json($response, [
            'ok' => true,
            'report' => null,
            'message' => 'No ingest yet — run Ingest now.',
        ]);
    }
    $payload = json_decode((string) $row['payload_json'], true);
    if (!is_array($payload)) {
        $payload = [];
    }
    return $json($response, [
        'ok' => true,
        'updated_at' => $row['updated_at'],
        'symbol_not_found' => $payload['symbol_not_found'] ?? [],
        'errors' => $payload['errors'] ?? [],
        'report' => $payload,
    ]);
});

$app->get('/ingest/runs', function (Request $request, Response $response) use ($json, $ingestRuns): Response {
    return $json($response, ['ok' => true, 'runs' => $ingestRuns->list()]);
});

$app->get('/ingest/runs/{id}', function (Request $request, Response $response, array $args) use ($json, $ingestRuns): Response {
    $run = $ingestRuns->find((int) $args['id']);
    if ($run === null) {
        throw new \RuntimeException('Ingest run not found.', 404);
    }
    return $json($response, ['ok' => true, 'run' => $run]);
});

$app->post('/refresh/market', function (Request $request, Response $response) use ($json, $worker, $ingestRuns): Response {
    $force = in_array(strtolower((string) ($request->getQueryParams()['force_news'] ?? '')), ['1', 'true', 'yes'], true);
    $runId = $ingestRuns->start();
    try {
        $result = $worker->refreshMarket($force);
        $ingestRuns->succeed($runId, $result);
        return $json($response, ['ok' => true, 'run_id' => $runId, 'refresh' => $result]);
    } catch (\Throwable $exception) {
        $ingestRuns->fail($runId, $exception->getMessage());
        throw $exception;
    }
});

$app->post('/agent/run', function (Request $request, Response $response) use ($json, $worker, $agents): Response {
    $force = in_array(strtolower((string) ($request->getQueryParams()['force'] ?? '')), ['1', 'true', 'yes'], true);
    $started = $worker->startAdvisory($force);
    $runId = isset($started['run_id']) ? (int) $started['run_id'] : null;
    $run = $runId !== null ? $agents->findRun($runId) : null;
    $fromCache = (bool) ($started['from_cache'] ?? false);
    $status = 200;
    if (!$fromCache) {
        $status = ($started['ok'] ?? false) ? 202 : 409;
    }
    return $json($response, [
        'ok' => (bool) ($started['ok'] ?? false),
        'from_cache' => $fromCache,
        'run_id' => $runId,
        'run' => $run,
        'recommendations' => $fromCache && $runId !== null ? $agents->recommendations($runId) : [],
        'worker' => $started,
    ], $status);
});

$app->post('/agent/run/symbol', function (Request $request, Response $response) use ($json, $worker, $agents): Response {
    $params = $request->getQueryParams();
    $symbol = $params['symbol'] ?? '';
    $force = in_array(strtolower((string) ($params['force'] ?? '')), ['1', 'true', 'yes'], true);
    
    if (empty($symbol)) {
        return $json($response, ['ok' => false, 'error' => 'symbol is required'], 400);
    }
    
    $started = $worker->startAdvisorySingle($symbol, $force);
    $runId = isset($started['run_id']) ? (int) $started['run_id'] : null;
    $fromCache = (bool) ($started['from_cache'] ?? false);
    $status = 200;
    if (!$fromCache) {
        $status = ($started['ok'] ?? false) ? 202 : 409;
    }
    return $json($response, [
        'ok' => (bool) ($started['ok'] ?? false),
        'from_cache' => $fromCache,
        'run_id' => $runId,
        'run' => null,  // Single-symbol runs don't return full run immediately
        'recommendations' => [],
        'worker' => $started,
    ], $status);
});

$app->get('/agent/runs', function (Request $request, Response $response) use ($json, $agents): Response {
    return $json($response, [
        'ok' => true,
        'runs' => $agents->listRuns(),
    ]);
});

$app->get('/agent/runs/latest', function (Request $request, Response $response) use ($json, $agents): Response {
    $run = $agents->latestRun();
    return $json($response, [
        'ok' => true,
        'run' => $run,
        'recommendations' => $run !== null ? $agents->recommendations((int) $run['id']) : [],
    ]);
});

$app->get('/agent/runs/{id}', function (Request $request, Response $response, array $args) use ($json, $agents): Response {
    $id = (int) $args['id'];
    $run = $agents->findRun($id);
    if ($run === null) {
        throw new \RuntimeException('Agent run not found.', 404);
    }
    return $json($response, [
        'ok' => true,
        'run' => $run,
        'recommendations' => $agents->recommendations($id),
    ]);
});

$app->get('/agent/runs/{id}/log', function (Request $request, Response $response, array $args) use ($json, $agents): Response {
    $run = $agents->runLog((int) $args['id']);
    if ($run === null) {
        throw new \RuntimeException('Agent run not found.', 404);
    }
    return $json($response, ['ok' => true, 'run' => $run]);
});

$app->get('/recommendations', function (Request $request, Response $response) use ($json, $agents): Response {
    $params = $request->getQueryParams();
    $runId = isset($params['run_id']) ? (int) $params['run_id'] : null;
    $recs = $agents->recommendations($runId);
    $latest = $agents->latestRun();
    return $json($response, [
        'ok' => true,
        'run_id' => $runId ?? ($latest['id'] ?? null),
        'run' => $runId !== null ? $agents->findRun($runId) : $latest,
        'recommendations' => $recs,
    ]);
});

$app->get('/alerts', function (Request $request, Response $response) use ($json, $alerts): Response {
    $params = $request->getQueryParams();
    $unreadOnly = null;
    if (isset($params['unread'])) {
        $flag = strtolower((string) $params['unread']);
        $unreadOnly = in_array($flag, ['1', 'true', 'yes'], true)
            ? true
            : (in_array($flag, ['0', 'false', 'no'], true) ? false : null);
    }
    return $json($response, [
        'ok' => true,
        'unread_count' => $alerts->unreadCount(),
        'alerts' => $alerts->list($unreadOnly),
    ]);
});

$app->post('/alerts/{id}/ack', function (Request $request, Response $response, array $args) use ($json, $alerts): Response {
    $id = (int) $args['id'];
    $acked = $alerts->acknowledge($id);
    return $json($response, [
        'ok' => true,
        'alert' => $acked,
        'unread_count' => $alerts->unreadCount(),
    ]);
});

// --- Stage F: did any of this advice work? -------------------------------------------

$app->get('/agent/outcomes', function (Request $request, Response $response) use ($json, $outcomes): Response {
    return $json($response, ['ok' => true, ...$outcomes->report()]);
});

// --- 0012: cash reserve + realised-gain inputs to the doctrine gate -------------------

$app->get('/portfolio/settings', function (Request $request, Response $response) use ($json, $settings): Response {
    return $json($response, ['ok' => true, 'settings' => $settings->portfolio()]);
});

$app->put('/portfolio/settings', function (Request $request, Response $response) use ($json, $settings): Response {
    /** @var array<string, mixed> $body */
    $body = (array) $request->getParsedBody();
    return $json($response, ['ok' => true, 'settings' => $settings->update($body)]);
});

// --- 0015: thesis of record ----------------------------------------------------------

$app->get('/theses', function (Request $request, Response $response) use ($json, $theses): Response {
    $params = $request->getQueryParams();
    $status = isset($params['status']) ? (string) $params['status'] : null;
    return $json($response, [
        'ok' => true,
        'theses' => $theses->all($status),
        'coverage' => $theses->coverage(),
    ]);
});

$app->post('/theses', function (Request $request, Response $response) use ($json, $theses): Response {
    /** @var array<string, mixed> $body */
    $body = (array) $request->getParsedBody();
    return $json($response, ['ok' => true, 'thesis' => $theses->create($body)], 201);
});

$app->put('/theses/{id}', function (Request $request, Response $response, array $args) use ($json, $theses): Response {
    /** @var array<string, mixed> $body */
    $body = (array) $request->getParsedBody();
    return $json($response, ['ok' => true, 'thesis' => $theses->update((int) $args['id'], $body)]);
});

$app->delete('/theses/{id}', function (Request $request, Response $response, array $args) use ($json, $theses): Response {
    $theses->delete((int) $args['id']);
    return $json($response, ['ok' => true]);
});

// --- 0019: the tracker — a second book, ingested and researched like the portfolio ----

$app->get('/tracker', function (Request $request, Response $response) use ($json, $tracker, $settings): Response {
    return $json($response, [
        'ok' => true,
        'tracker' => $tracker->all(),
        'display_currency' => $settings->portfolio()['display_currency'],
    ]);
});

$app->post('/tracker', function (Request $request, Response $response) use ($json, $tracker): Response {
    /** @var array<string, mixed> $body */
    $body = (array) $request->getParsedBody();
    return $json($response, ['ok' => true, 'entry' => $tracker->create($body)], 201);
});

$app->put('/tracker/{id}', function (Request $request, Response $response, array $args) use ($json, $tracker): Response {
    /** @var array<string, mixed> $body */
    $body = (array) $request->getParsedBody();
    return $json($response, ['ok' => true, 'entry' => $tracker->update((int) $args['id'], $body)]);
});

$app->delete('/tracker/{id}', function (Request $request, Response $response, array $args) use ($json, $tracker): Response {
    $tracker->delete((int) $args['id']);
    return $json($response, ['ok' => true]);
});

/**
 * Name-or-ticker search (0019). Instruments the book already knows come first and are
 * flagged `known`; the worker then merges Yahoo and Finnhub behind them. A vendor being
 * down degrades the result rather than failing it — manual symbol entry is always open.
 */
$app->get('/instruments/search', function (Request $request, Response $response) use ($json, $instruments, $worker): Response {
    $params = $request->getQueryParams();
    $query = trim((string) ($params['q'] ?? ''));
    if ($query === '') {
        return $json($response, ['ok' => true, 'query' => '', 'results' => [], 'warnings' => []]);
    }

    $results = $instruments->search($query);
    $seen = [];
    foreach ($results as $row) {
        $seen[strtoupper((string) $row['symbol'])] = true;
    }

    $warnings = [];
    try {
        $remote = $worker->searchSymbols($query);
        foreach (($remote['results'] ?? []) as $hit) {
            $symbol = strtoupper(trim((string) ($hit['symbol'] ?? '')));
            if ($symbol === '' || isset($seen[$symbol])) {
                continue;
            }
            $seen[$symbol] = true;
            $results[] = [
                'symbol' => $symbol,
                'name' => $hit['name'] ?? null,
                'mic' => $hit['mic'] ?? null,
                'currency' => $hit['currency'] ?? null,
                'kind' => $hit['kind'] ?? 'equity',
                'region' => $hit['region'] ?? null,
                'isin' => $hit['isin'] ?? null,
                'exchange' => $hit['exchange'] ?? null,
                'source' => $hit['source'] ?? 'vendor',
                'known' => false,
            ];
        }
        foreach (($remote['warnings'] ?? []) as $warning) {
            $warnings[] = (string) $warning;
        }
    } catch (\Throwable $e) {
        // Local hits still stand, and the operator can always type a ticker by hand.
        $warnings[] = 'Symbol search unavailable: ' . $e->getMessage();
    }

    return $json($response, [
        'ok' => true,
        'query' => $query,
        'results' => $results,
        'warnings' => $warnings,
    ]);
});

$app->run();
