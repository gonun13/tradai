<?php

declare(strict_types=1);

namespace Tradai\Api\Domain;

/**
 * Trade-date FX that locks realised P&L in EUR (0031). Implementations must not throw:
 * a missing rate is `null`, never a failed write.
 */
interface TradeFxSource
{
    /** @return array{rate: float, as_of: string}|null base -> EUR on (or nearest before) `$date` */
    public function rateToEurOn(string $base, string $date): ?array;
}
