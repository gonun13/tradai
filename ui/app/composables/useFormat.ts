/**
 * Display helpers shared by the Portfolio and Tracker modules (0019, 0021). Extracted from
 * index.vue when the tracker gained a table of its own — both books show money the same
 * way, and every figure the UI renders is EUR unless it names another currency
 * (spec/domain.md invariant 10).
 */
export function useFormat() {
  function fmtNum(n: number | null | undefined, digits = 2) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) {
      return '—'
    }
    return Number(n).toFixed(digits)
  }

  function money(amount: number, currency: string) {
    const code = (currency || 'EUR').trim().toUpperCase()
    if (!/^[A-Z]{3}$/.test(code)) {
      return `${Number(amount).toFixed(2)} ${currency || ''}`.trim()
    }
    try {
      return new Intl.NumberFormat('en-IE', {
        style: 'currency',
        currency: code,
        maximumFractionDigits: 2,
      }).format(amount)
    } catch {
      return `${Number(amount).toFixed(2)} ${code}`
    }
  }

  function moneyOrDash(amount: number | null | undefined, currency = 'EUR') {
    if (amount === null || amount === undefined || Number.isNaN(Number(amount))) {
      return '—'
    }
    return money(Number(amount), currency)
  }

  function pctOrDash(pct: number | null | undefined) {
    if (pct === null || pct === undefined || Number.isNaN(Number(pct))) {
      return '—'
    }
    const n = Number(pct)
    const sign = n > 0 ? '+' : ''
    return `${sign}${n.toFixed(2)}%`
  }

  return { fmtNum, money, moneyOrDash, pctOrDash }
}
