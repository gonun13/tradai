<?php

declare(strict_types=1);

namespace Tradai\Api;

use DateTimeImmutable;
use DateTimeZone;
use PDO;

final class HistoryRepository
{
    private const RANGES = ['1y' => 1, '2y' => 2, '5y' => 5, 'max' => null];

    public function __construct(private PDO $pdo)
    {
    }

    /** @return array<string, mixed> */
    public function forInstrument(int $instrumentId, string $range): array
    {
        $range = strtolower(trim($range));
        if (!array_key_exists($range, self::RANGES)) {
            throw new \InvalidArgumentException('range must be 1y, 2y, 5y, or max');
        }
        $instrument = $this->pdo->prepare(
            'SELECT id,symbol,name,currency,region FROM instruments WHERE id=?'
        );
        $instrument->execute([$instrumentId]);
        $instrumentRow = $instrument->fetch();
        if ($instrumentRow === false) {
            throw new \RuntimeException('Instrument not found.', 404);
        }

        $stockSeries = $this->series('instrument:' . $instrumentId);
        if ($stockSeries === null) {
            return $this->emptyResponse($instrumentRow, $range);
        }
        $cutoff = $this->cutoff((string) $stockSeries['as_of'], self::RANGES[$range]);
        $stock = $this->points((int) $stockSeries['id'], $cutoff);
        if ($stock === []) {
            return $this->emptyResponse($instrumentRow, $range, $stockSeries);
        }

        $region = in_array($instrumentRow['region'], ['eu', 'us'], true)
            ? (string) $instrumentRow['region']
            : (str_contains((string) $instrumentRow['symbol'], '.') ? 'eu' : 'us');
        $benchmarkSeries = $this->series('benchmark:' . $region);
        $benchmark = $benchmarkSeries === null
            ? []
            : $this->points((int) $benchmarkSeries['id'], $cutoff);

        $comparisonStart = $this->firstCommonDate($stock, $benchmark);
        $warning = null;
        if ($comparisonStart === null) {
            $warning = $benchmarkSeries === null
                ? 'Regional benchmark history is not available yet.'
                : 'Regional benchmark has no overlapping observations for this range.';
            $stockBaseDate = (string) $stock[0]['date'];
            $stockBase = (float) $stock[0]['adjusted_close'];
            $benchmark = [];
        } else {
            $stockBaseDate = $comparisonStart;
            $stockBase = $this->valueAt($stock, $comparisonStart);
            $benchmarkBase = $this->valueAt($benchmark, $comparisonStart);
            // Comparison is only meaningful once both calendars share an observation.
            $benchmark = array_values(array_filter(
                $benchmark,
                static fn (array $point): bool => $point['date'] >= $comparisonStart
            ));
            $benchmark = $this->normalize($benchmark, $benchmarkBase);
        }
        $stock = $this->normalize($stock, $stockBase);

        $stale = $this->isStale((string) $stockSeries['updated_at']);
        $benchmarkStale = $benchmarkSeries !== null
            ? $this->isStale((string) $benchmarkSeries['updated_at'])
            : null;

        return [
            'ok' => true,
            'range' => $range,
            'basis' => 'adjusted_performance',
            'normalization_base' => 100,
            'comparison_start' => $comparisonStart,
            'warning' => $warning,
            'stock' => [
                'instrument_id' => $instrumentId,
                'symbol' => $instrumentRow['symbol'],
                'name' => $instrumentRow['name'],
                'native_currency' => $stockSeries['native_currency'],
                'source' => $stockSeries['source'],
                'as_of' => $stockSeries['as_of'],
                'earliest_date' => $stockSeries['earliest_date'],
                'coverage_start' => $stock[0]['date'] ?? null,
                'normalization_date' => $stockBaseDate,
                'stale' => $stale,
                'endpoint_return_pct' => $this->endpointReturn($stock),
                'points' => $stock,
            ],
            'benchmark' => $benchmarkSeries === null ? null : [
                'region' => $region,
                'symbol' => $benchmarkSeries['symbol'],
                'name' => $region === 'us' ? 'S&P 500 proxy' : 'STOXX Europe 600 proxy',
                'native_currency' => $benchmarkSeries['native_currency'],
                'source' => $benchmarkSeries['source'],
                'as_of' => $benchmarkSeries['as_of'],
                'earliest_date' => $benchmarkSeries['earliest_date'],
                'coverage_start' => $benchmark[0]['date'] ?? null,
                'normalization_date' => $comparisonStart,
                'stale' => $benchmarkStale,
                'endpoint_return_pct' => $this->endpointReturn($benchmark),
                'points' => $benchmark,
            ],
        ];
    }

    /** @return array<string, mixed>|null */
    private function series(string $key): ?array
    {
        $stmt = $this->pdo->prepare('SELECT * FROM historical_series WHERE series_key=?');
        $stmt->execute([$key]);
        $row = $stmt->fetch();
        return $row === false ? null : $row;
    }

    /** @return list<array{date:string,adjusted_close:float,resolution:string}> */
    private function points(int $seriesId, ?string $cutoff): array
    {
        $sql = 'SELECT point_date AS date,adjusted_close,resolution '
            . 'FROM historical_points WHERE series_id=?';
        $params = [$seriesId];
        if ($cutoff !== null) {
            $sql .= ' AND point_date>=?';
            $params[] = $cutoff;
        }
        $sql .= ' ORDER BY point_date';
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return array_map(static fn (array $row): array => [
            'date' => (string) $row['date'],
            'adjusted_close' => (float) $row['adjusted_close'],
            'resolution' => (string) $row['resolution'],
        ], $stmt->fetchAll());
    }

    private function cutoff(string $asOf, ?int $years): ?string
    {
        if ($years === null) {
            return null;
        }
        $date = new DateTimeImmutable($asOf, new DateTimeZone('UTC'));
        $year = (int) $date->format('Y') - $years;
        $month = (int) $date->format('n');
        $monthStart = new DateTimeImmutable(
            sprintf('%04d-%02d-01', $year, $month),
            new DateTimeZone('UTC')
        );
        $day = min((int) $date->format('j'), (int) $monthStart->format('t'));
        return $date->setDate($year, $month, $day)->format('Y-m-d');
    }

    /** @param list<array<string,mixed>> $left @param list<array<string,mixed>> $right */
    private function firstCommonDate(array $left, array $right): ?string
    {
        $dates = array_fill_keys(array_column($right, 'date'), true);
        foreach ($left as $point) {
            if (isset($dates[$point['date']])) {
                return (string) $point['date'];
            }
        }
        return null;
    }

    /** @param list<array<string,mixed>> $points */
    private function valueAt(array $points, string $date): float
    {
        foreach ($points as $point) {
            if ($point['date'] === $date) {
                return (float) $point['adjusted_close'];
            }
        }
        throw new \LogicException('Normalization date is absent from series.');
    }

    /** @param list<array<string,mixed>> $points @return list<array<string,mixed>> */
    private function normalize(array $points, float $base): array
    {
        return array_map(static function (array $point) use ($base): array {
            $point['normalized_value'] = round(((float) $point['adjusted_close'] / $base) * 100, 4);
            return $point;
        }, $points);
    }

    /** @param list<array<string,mixed>> $points */
    private function endpointReturn(array $points): ?float
    {
        if ($points === []) {
            return null;
        }
        return round((float) $points[array_key_last($points)]['normalized_value'] - 100, 2);
    }

    private function isStale(string $updatedAt): bool
    {
        return (new DateTimeImmutable($updatedAt))->getTimestamp() < time() - (8 * 86400);
    }

    /** @param array<string,mixed> $instrument @param array<string,mixed>|null $series */
    private function emptyResponse(array $instrument, string $range, ?array $series = null): array
    {
        return [
            'ok' => true,
            'range' => $range,
            'basis' => 'adjusted_performance',
            'normalization_base' => 100,
            'comparison_start' => null,
            'warning' => 'Long history is not available yet. Run market ingestion to populate it.',
            'stock' => [
                'instrument_id' => (int) $instrument['id'],
                'symbol' => $instrument['symbol'],
                'name' => $instrument['name'],
                'native_currency' => $series['native_currency'] ?? $instrument['currency'],
                'source' => $series['source'] ?? null,
                'as_of' => $series['as_of'] ?? null,
                'earliest_date' => $series['earliest_date'] ?? null,
                'coverage_start' => null,
                'normalization_date' => null,
                'stale' => $series ? $this->isStale((string) $series['updated_at']) : null,
                'endpoint_return_pct' => null,
                'points' => [],
            ],
            'benchmark' => null,
        ];
    }
}
