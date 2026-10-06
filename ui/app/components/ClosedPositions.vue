<script setup lang="ts">
import type { ClosedPosition, DisplayCurrency, Transaction } from '~/types/api'

/**
 * Positions sold out in full (0031). Their transactions and realised P&L stay; a later buy of
 * the same ticker reopens the line on the same history. Realised display amounts are locked in
 * EUR at trade dates and converted at today's rate.
 */
defineProps<{
  positions: ClosedPosition[]
  displayCurrency: DisplayCurrency
  pending?: boolean
}>()

const emit = defineEmits<{
  'remove-transaction': [tx: Transaction, symbol: string]
  erase: [position: ClosedPosition]
}>()

const { money, moneyOrDash, pctOrDash } = useFormat()
const expanded = ref<number | null>(null)

function toggle(instrumentId: number) {
  expanded.value = expanded.value === instrumentId ? null : instrumentId
}

function pnlClass(n: number | null | undefined) {
  return { ok: (n ?? 0) > 0, bad: (n ?? 0) < 0 }
}
</script>

<template>
  <section class="panel" aria-labelledby="closed-positions-title">
    <div class="list-head">
      <h1 id="closed-positions-title">Closed positions</h1>
    </div>
    <p class="mute">
      Fully sold positions keep their transactions and realised P&amp;L. Realised amounts in
      {{ displayCurrency }} use FX locked at each trade date.
    </p>
    <p v-if="pending">Loading…</p>
    <p v-else-if="positions.length === 0" class="mute">No closed positions yet.</p>
    <table v-else>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Held</th>
          <th>Qty</th>
          <th>Cost</th>
          <th>Proceeds</th>
          <th>Realised ({{ displayCurrency }})</th>
          <th>Realised %</th>
          <th aria-label="Actions"></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="c in positions" :key="c.instrument.id">
          <tr>
            <td>
              <strong>{{ c.instrument.symbol }}</strong>
              <span class="sub">
                {{ c.instrument.name || c.instrument.isin || c.instrument.kind }}
                · {{ c.instrument.currency }}
              </span>
            </td>
            <td>
              {{ c.opened_at || '—' }} → {{ c.closed_at || '—' }}
              <span v-if="c.held_days != null" class="sub">{{ c.held_days }} days</span>
            </td>
            <td>{{ c.quantity_sold }}</td>
            <td>{{ money(c.cost_native, c.instrument.currency) }}</td>
            <td>{{ money(c.proceeds_native, c.instrument.currency) }}</td>
            <td :class="pnlClass(c.realized_pnl_display ?? c.realized_pnl_native)">
              {{ c.realized_pnl_display == null ? 'pending FX' : moneyOrDash(c.realized_pnl_display, displayCurrency) }}
              <span v-if="c.instrument.currency.toUpperCase() !== displayCurrency" class="sub">
                {{ money(c.realized_pnl_native, c.instrument.currency) }}
              </span>
            </td>
            <td :class="pnlClass(c.realized_pct)">{{ pctOrDash(c.realized_pct) }}</td>
            <td class="row-actions closed-actions">
              <button
                type="button"
                class="ghost"
                :aria-expanded="expanded === c.instrument.id"
                :aria-controls="`closed-history-${c.instrument.id}`"
                @click="toggle(c.instrument.id)"
              >
                {{ expanded === c.instrument.id ? 'Hide' : 'History' }}
              </button>
              <button
                type="button"
                class="danger"
                :aria-label="`Erase ${c.instrument.symbol} and its history`"
                :title="`Erase ${c.instrument.symbol} and its history`"
                @click="emit('erase', c)"
              >
                Erase
              </button>
            </td>
          </tr>
          <tr v-if="expanded === c.instrument.id" :id="`closed-history-${c.instrument.id}`">
            <td colspan="8">
              <ul class="lots">
                <li v-for="tx in c.transactions" :key="tx.id" class="closed-lot">
                  <span class="pill" :data-action="tx.side">{{ tx.side }}</span>
                  {{ tx.trade_date }} · qty {{ tx.quantity }} @ {{ money(tx.unit_price, c.instrument.currency) }}
                  · fee {{ money(tx.commission, c.instrument.currency) }}
                  · {{ tx.side === 'sell' ? 'proceeds' : 'cost' }} {{ money(tx.lot_cost, c.instrument.currency) }}
                  <span v-if="tx.fx_to_eur == null" class="mute">· FX pending</span>
                  <button
                    type="button"
                    class="ghost"
                    :aria-label="`Delete ${tx.side} of ${tx.quantity} on ${tx.trade_date}`"
                    @click="emit('remove-transaction', tx, c.instrument.symbol)"
                  >
                    Delete
                  </button>
                </li>
              </ul>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.closed-actions {
  justify-content: flex-end;
}

.closed-lot {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  align-items: center;
}
</style>
