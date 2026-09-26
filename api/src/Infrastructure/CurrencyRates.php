<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use PDO;
use Tradai\Api\Domain\Currency;

/** Currency presentation helpers. Cached rates are always base -> EUR. */
final class CurrencyRates
{
    public static function display(PDO $db): string
    {
        $stmt = $db->prepare("SELECT value FROM settings WHERE key = 'display_currency'");
        $stmt->execute();
        $value = strtoupper(trim((string) ($stmt->fetchColumn() ?: 'EUR')));
        return in_array($value, Currency::SUPPORTED, true) ? $value : 'EUR';
    }

    public static function rateToEur(PDO $db, string $currency): ?float
    {
        $currency = strtoupper(trim($currency));
        if ($currency === 'EUR') {
            return 1.0;
        }
        $stmt = $db->prepare(
            "SELECT rate FROM fx_rates WHERE base_currency = :base AND quote_currency = 'EUR'"
        );
        $stmt->execute(['base' => $currency]);
        $rate = $stmt->fetchColumn();
        return $rate === false ? null : (float) $rate;
    }

    public static function rate(PDO $db, string $base, string $display): ?float
    {
        $base = strtoupper(trim($base));
        $display = strtoupper(trim($display));
        if ($base === $display) {
            return 1.0;
        }
        $baseToEur = self::rateToEur($db, $base);
        $displayToEur = self::rateToEur($db, $display);
        if ($baseToEur === null || $displayToEur === null || $displayToEur == 0.0) {
            return null;
        }
        return $baseToEur / $displayToEur;
    }

    public static function convert(PDO $db, ?float $amount, string $base, string $display): ?float
    {
        if ($amount === null) {
            return null;
        }
        $rate = self::rate($db, $base, $display);
        return $rate === null ? null : $amount * $rate;
    }
}
