<?php

declare(strict_types=1);

namespace Tradai\Api\Infrastructure;

use PDO;
use Tradai\Api\Domain\Currency;

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
    private const DISPLAY_CURRENCY = 'display_currency';
    private const CASH_AMOUNT = 'cash_amount';
    private const CASH_CURRENCY = 'cash_currency';
    private const REALIZED_OVERRIDE_AMOUNT = 'realized_gains_ytd_override_amount';
    private const REALIZED_OVERRIDE_CURRENCY = 'realized_gains_ytd_override_currency';
    private const CURRENCY_MIGRATION = 'currency_settings_migration_v1';
    private const INVESTOR_PROFILE_TEXT = 'investor_profile_text';
    private const PORTFOLIO_PROFILE_TEXT = 'portfolio_profile_text';

    public function __construct(private readonly PDO $db)
    {
        $this->migrateLegacyMoneySettings();
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
        $display = CurrencyRates::display($this->db);
        $fromDisposals = $this->realizedYtdFromDisposals($display);
        $cash = $this->money(self::CASH_AMOUNT, self::CASH_CURRENCY, $display);
        $override = $this->money(
            self::REALIZED_OVERRIDE_AMOUNT,
            self::REALIZED_OVERRIDE_CURRENCY,
            $display
        );
        $realized = $fromDisposals === null || ($override['amount'] !== null && $override['display_amount'] === null)
            ? null
            : round($fromDisposals + ($override['display_amount'] ?? 0.0), 2);

        return [
            'display_currency' => $display,
            'display_currency_options' => Currency::SUPPORTED,
            'investor_profile_text' => $this->get(self::INVESTOR_PROFILE_TEXT),
            'portfolio_profile_text' => $this->get(self::PORTFOLIO_PROFILE_TEXT),
            'cash' => $cash,
            'realized_gains_ytd_from_disposals_display' => $fromDisposals,
            'realized_gains_ytd_override' => $override,
            'realized_gains_ytd_display' => $realized,
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

        if (array_key_exists(self::DISPLAY_CURRENCY, $input)) {
            $this->set(self::DISPLAY_CURRENCY, Currency::validate($input[self::DISPLAY_CURRENCY]));
        }

        $this->updateMoney($input, 'cash', self::CASH_AMOUNT, self::CASH_CURRENCY);
        $this->updateMoney(
            $input,
            'realized_gains_ytd_override',
            self::REALIZED_OVERRIDE_AMOUNT,
            self::REALIZED_OVERRIDE_CURRENCY
        );

        return $this->portfolio();
    }

    /** Sum of this calendar year's FIFO disposals in the selected display currency. */
    private function realizedYtdFromDisposals(string $display): ?float
    {
        $year = gmdate('Y');
        $rows = $this->db->prepare(
            "SELECT d.realized_pnl, d.currency
             FROM realized_disposals d
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
            $rate = CurrencyRates::rate($this->db, $currency, $display);
            if ($rate === null) {
                return null;
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

    /** @return array{amount: ?float, currency: ?string, display_amount: ?float} */
    private function money(string $amountKey, string $currencyKey, string $display): array
    {
        $amount = $this->getFloat($amountKey);
        $currency = $this->get($currencyKey);
        if ($amount === null) {
            return ['amount' => null, 'currency' => $currency, 'display_amount' => null];
        }
        $currency = strtoupper(trim((string) ($currency ?: $display)));
        return [
            'amount' => $amount,
            'currency' => $currency,
            'display_amount' => ($converted = CurrencyRates::convert($this->db, $amount, $currency, $display)) === null
                ? null
                : round($converted, 2),
        ];
    }

    /** @param array<string, mixed> $input */
    private function updateMoney(array $input, string $field, string $amountKey, string $currencyKey): void
    {
        if (!array_key_exists($field, $input)) {
            return;
        }
        $raw = $input[$field];
        if ($raw === null || $raw === '') {
            $this->set($amountKey, null);
            $this->set($currencyKey, null);
            return;
        }
        if (!is_array($raw)) {
            throw new \InvalidArgumentException($field . ' must be an amount/currency object or null');
        }
        $amount = $raw['amount'] ?? null;
        if ($amount === null || $amount === '') {
            $this->set($amountKey, null);
            $this->set($currencyKey, null);
            return;
        }
        if (!is_numeric($amount)) {
            throw new \InvalidArgumentException($field . '.amount must be numeric');
        }
        $currency = Currency::validate($raw['currency'] ?? '');
        $this->set($amountKey, (string) (float) $amount);
        $this->set($currencyKey, $currency);
    }

    private function migrateLegacyMoneySettings(): void
    {
        if ($this->get(self::DISPLAY_CURRENCY) === null) {
            $this->set(self::DISPLAY_CURRENCY, 'EUR');
        }
        if ($this->get(self::CURRENCY_MIGRATION) === '1') {
            return;
        }
        $legacy = [
            ['cash_eur', self::CASH_AMOUNT, self::CASH_CURRENCY],
            ['realized_gains_ytd_override_eur', self::REALIZED_OVERRIDE_AMOUNT, self::REALIZED_OVERRIDE_CURRENCY],
        ];
        foreach ($legacy as [$oldKey, $amountKey, $currencyKey]) {
            if ($this->get($amountKey) === null) {
                $value = $this->get($oldKey);
                if ($value !== null && $value !== '') {
                    $this->set($amountKey, $value);
                    $this->set($currencyKey, 'EUR');
                }
            }
        }
        $this->set(self::CURRENCY_MIGRATION, '1');
    }
}
