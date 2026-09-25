<?php

declare(strict_types=1);

namespace Tradai\Api;

use RuntimeException;

final class WorkerClient
{
    public function __construct(private readonly string $baseUrl)
    {
    }

    /** @return array<string, mixed> */
    public function refreshMarket(bool $forceNews = false): array
    {
        $path = '/refresh?manual_gap_retry=1';
        if ($forceNews) {
            $path .= '&force_news=1';
        }
        return $this->postJson($path, 120);
    }

    /** @return array<string, mixed> */
    public function startAdvisory(bool $force = false): array
    {
        $path = '/advisory/run';
        if ($force) {
            $path .= '?force=1';
        }
        return $this->postJson($path, 30, [200, 202, 409]);
    }

    /** @return array<string, mixed> */
    public function startAdvisorySingle(string $symbol, bool $force = false): array
    {
        $path = '/advisory/run/symbol?symbol=' . rawurlencode($symbol);
        if ($force) {
            $path .= '&force=1';
        }
        return $this->postJson($path, 30, [200, 202, 409]);
    }

    /**
     * Instrument search (0019). The vendor calls live in the worker because that is where
     * the adapters and the API keys are; the API stays the single BFF the UI talks to.
     *
     * @return array<string, mixed>
     */
    public function searchSymbols(string $query): array
    {
        return $this->getJson('/search?q=' . rawurlencode($query), 15);
    }

    /** @return array<string, mixed> */
    private function getJson(string $path, int $timeout): array
    {
        $url = rtrim($this->baseUrl, '/') . $path;
        $ctx = stream_context_create([
            'http' => [
                'method' => 'GET',
                'timeout' => $timeout,
                'ignore_errors' => true,
            ],
        ]);
        $body = @file_get_contents($url, false, $ctx);
        if ($body === false) {
            throw new RuntimeException('Worker unreachable at ' . $url);
        }
        /** @var array<string, mixed>|null $decoded */
        $decoded = json_decode($body, true);
        if (!is_array($decoded)) {
            throw new RuntimeException('Worker returned non-JSON for ' . $path);
        }
        return $decoded;
    }

    /**
     * @param list<int> $okStatuses
     * @return array<string, mixed>
     */
    private function postJson(string $path, int $timeout, array $okStatuses = [200]): array
    {
        $url = rtrim($this->baseUrl, '/') . $path;
        $ctx = stream_context_create([
            'http' => [
                'method' => 'POST',
                'header' => "Content-Type: application/json\r\n",
                'content' => '{}',
                'timeout' => $timeout,
                'ignore_errors' => true,
            ],
        ]);
        $body = @file_get_contents($url, false, $ctx);
        if ($body === false) {
            throw new RuntimeException('Worker unreachable at ' . $url);
        }

        $status = 500;
        if (isset($http_response_header[0]) && preg_match('/\s(\d{3})\s/', $http_response_header[0], $m)) {
            $status = (int) $m[1];
        }

        /** @var array<string, mixed>|null $decoded */
        $decoded = json_decode($body, true);
        if (!is_array($decoded)) {
            throw new RuntimeException('Worker returned non-JSON (HTTP ' . $status . ')');
        }
        if (!in_array($status, $okStatuses, true) && $status >= 400) {
            $msg = isset($decoded['error']) ? (string) $decoded['error'] : 'Worker request failed';
            throw new RuntimeException($msg, $status);
        }
        return $decoded;
    }
}
