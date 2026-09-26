<?php

declare(strict_types=1);

namespace Tradai\Api\Domain;

/** Supported display currencies. Cached-rate lookups live in Infrastructure\CurrencyRates. */
final class Currency
{
    public const SUPPORTED = ['EUR', 'USD', 'GBP', 'CHF'];

    public static function validate(mixed $value): string
    {
        $currency = strtoupper(trim((string) $value));
        if (!in_array($currency, self::SUPPORTED, true)) {
            throw new \InvalidArgumentException('display_currency must be one of EUR, USD, GBP, CHF');
        }
        return $currency;
    }
}
