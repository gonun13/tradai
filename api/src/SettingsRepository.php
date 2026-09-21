<?php

declare(strict_types=1);

namespace Tradai\Api;

use PDO;

/**
 * The two operator profiles (0019) plus the cash reserve and realised gains the agents need
 * as context. Claude and Jev read both texts verbatim and reason with them; nothing here
 * validates or gates their decisions.
 *
 *  - investor profile  — who the operator is and what makes a name worth buying. Applies to
 *                        the tracker, and travels as global context on every call.
 *  - portfolio profile — the rules for names already owned: sizing, trimming, when to sell,
 *                        tax. Applies to holdings.
 */
final class SettingsRepository
{
    private const CASH_EUR = 'cash_eur';
    private const REALIZED_OVERRIDE_EUR = 'realized_gains_ytd_override_eur';
    private const INVESTOR_PROFILE_TEXT = 'investor_profile_text';
    private const PORTFOLIO_PROFILE_TEXT = 'portfolio_profile_text';

    public function __construct(private readonly PDO $db)
    {
    }

    public function get(string $key): ?string
    {
        $stmt = $this->db->prepare('SELECT value FROM settings WHERE key = :key');
        $stmt->execute(['key' => $key]);
        $value = $stmt->fetchColumn();
        return $value === false ? null : (string) $value;
    }

    public function set(string $key, ?string $value): void
    {
        $this->db->prepare(
            'INSERT INTO settings (key, value, updated_at) VALUES (:key, :value, :updated_at)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at'
        )->execute(['key' => $key, 'value' => $value, 'updated_at' => gmdate('c')]);
    }

    /** @return array<string, mixed> */
    public function portfolio(): array
    {
        $fromDisposals = $this->realizedYtdFromDisposals();
        $override = $this->getFloat(self::REALIZED_OVERRIDE_EUR);

        return [
            'investor_profile_text' => $this->get(self::INVESTOR_PROFILE_TEXT),
            'portfolio_profile_text' => $this->get(self::PORTFOLIO_PROFILE_TEXT),
            'cash_eur' => $this->getFloat(self::CASH_EUR),
            'realized_gains_ytd_from_disposals_eur' => $fromDisposals,
            'realized_gains_ytd_override_eur' => $override,
            'realized_gains_ytd_eur' => round(($fromDisposals ?? 0.0) + ($override ?? 0.0), 2),
            'calendar_year' => (int) gmdate('Y'),
        ];
    }

    /**
     * @param array<string, mixed> $input
     * @return array<string, mixed>
     */
    public function update(array $input): array
    {
        foreach ([self::INVESTOR_PROFILE_TEXT, self::PORTFOLIO_PROFILE_TEXT] as $key) {
            if (!array_key_exists($key, $input)) {
                continue;
            }
            $text = $input[$key];
            $trimmed = $text === null ? null : trim((string) $text);
            $this->set($key, $trimmed === '' ? null : $trimmed);
        }

        foreach ([self::CASH_EUR, self::REALIZED_OVERRIDE_EUR] as $key) {
            if (!array_key_exists($key, $input)) {
                continue;
            }
            $raw = $input[$key];
            if ($raw === null || $raw === '') {
                $this->set($key, null);
                continue;
            }
            if (!is_numeric($raw)) {
                throw new \InvalidArgumentException($key . ' must be numeric');
            }
            $this->set($key, (string) (float) $raw);
        }

        return $this->portfolio();
    }

    /** Sum of this calendar year's FIFO disposals, converted to EUR. */
    private function realizedYtdFromDisposals(): ?float
    {
        $year = gmdate('Y');
        $rows = $this->db->prepare(
            "SELECT d.realized_pnl, d.currency, fx.rate
             FROM realized_disposals d
             LEFT JOIN fx_rates fx
               ON fx.base_currency = d.currency AND fx.quote_currency = 'EUR'
             WHERE substr(d.trade_date, 1, 4) = :year"
        );
        $rows->execute(['year' => $year]);
        $all = $rows->fetchAll();
        if ($all === []) {
            return 0.0;
        }

        $total = 0.0;
        foreach ($all as $row) {
            $currency = strtoupper((string) $row['currency']);
            $rate = $currency === 'EUR' ? 1.0 : ($row['rate'] !== null ? (float) $row['rate'] : null);
            if ($rate === null) {
                // No FX on hand — skip rather than silently mis-state the gate input.
                continue;
            }
            $total += ((float) $row['realized_pnl']) * $rate;
        }
        return round($total, 2);
    }

    private function getFloat(string $key): ?float
    {
        $raw = $this->get($key);
        return $raw === null || $raw === '' ? null : (float) $raw;
    }
}
