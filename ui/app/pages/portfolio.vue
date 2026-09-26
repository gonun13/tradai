<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import type {
  Holding,
  HoldingInput,
  Transaction,
  AgentRun,
  Recommendation,
} from '~/composables/useHoldingsApi'

const api = useHoldingsApi()
const { money, moneyOrDash, pctOrDash } = useFormat()
const route = useRoute()
const message = ref('')
const error = ref('')
const editingId = ref<number | null>(null)
const editingTxId = ref<number | null>(null)
const formOpen = ref(false)
const lotPickerHolding = ref<Holding | null>(null)
const expandedHistoryId = ref<number | null>(null)
const PerformanceHistory = defineAsyncComponent(() => import('~/components/PerformanceHistory.vue'))

const today = () => new Date().toISOString().slice(0, 10)

const emptyForm = (): HoldingInput => ({
  symbol: '',
  isin: '',
  mic: '',
  currency: 'EUR',
  name: '',
  kind: 'equity',
  region: 'eu',
  trade_date: today(),
  quantity: 0,
  unit_price: 0,
  commission: 0,
  notes: '',
})

const form = reactive<HoldingInput>(emptyForm())

const { data, pending, refresh, error: loadError } = await useAsyncData('holdings', () => api.list())

const {
  data: contextPreviewData,
  pending: contextPending,
  refresh: refreshContext,
  error: contextLoadError,
} = await useAsyncData('portfolio-context-preview', () => api.contextPreview('portfolio'))

const refreshBook = () => Promise.all([refresh(), refreshContext()])

const holdings = computed(() => data.value?.holdings ?? [])
const portfolioValue = computed(() => data.value?.portfolio_market_value_display ?? null)
const displayCurrency = computed(() => data.value?.display_currency ?? 'EUR')
// Ingest / Run / Force are inline buttons in the layout header — one combined run covers both
// books (0019), so there is nothing per-tab to trigger from here.
const { advising } = useGlobalOps()
const agentRun = ref<AgentRun | null>(null)
const recommendations = ref<Recommendation[]>([])
const { data: advisoryData, refresh: refreshAdvisory } = await useAsyncData('advisory-latest', () =>
  api.latestRun().catch(() => ({ ok: true, run: null, recommendations: [] as Recommendation[] })),
)

watch(
  advisoryData,
  (a) => {
    agentRun.value = a?.run ?? null
    recommendations.value = a?.recommendations ?? []
  },
  { immediate: true },
)

// The Tracker module's "Bought" action links here with the symbol prefilled; recording the
// acquisition is what archives the tracker entry (0019, auto-promote).
onMounted(() => {
  const symbol = route.query.buy
  if (typeof symbol === 'string' && symbol.trim()) {
    startAdd()
    form.symbol = symbol.trim().toUpperCase()
  }
})

const previewLotCost = computed(
  () => Number(form.quantity || 0) * Number(form.unit_price || 0) + Number(form.commission || 0),
)

const previewAvg = computed(() => {
  const q = Number(form.quantity || 0)
  return q > 0 ? previewLotCost.value / q : 0
})

function resetForm() {
  editingId.value = null
  editingTxId.value = null
  formOpen.value = false
  lotPickerHolding.value = null
  Object.assign(form, emptyForm())
}

function startAdd() {
  editingId.value = null
  editingTxId.value = null
  lotPickerHolding.value = null
  Object.assign(form, emptyForm())
  formOpen.value = true
  message.value = ''
  error.value = ''
}

function onCurrencyChange() {
  if (form.currency === 'USD') {
    form.region = 'us'
  } else if (form.currency === 'EUR' || form.currency === 'GBP' || form.currency === 'CHF') {
    if (form.region === 'us' || form.region === '') {
      form.region = 'eu'
    }
  }
}

function startEdit(h: Holding) {
  message.value = ''
  error.value = ''
  const lots = h.transactions ?? []
  if (lots.length > 1) {
    lotPickerHolding.value = h
    formOpen.value = false
    return
  }
  openEditLot(h, lots[0] ?? null)
}

function dailyChangeClass(change: number | null) {
  if (change == null || Number.isNaN(Number(change)) || change === 0) {
    return 'daily-change--neutral'
  }
  return change > 0 ? 'daily-change--up' : 'daily-change--down'
}

function toggleHistory(instrumentId: number) {
  expandedHistoryId.value = expandedHistoryId.value === instrumentId ? null : instrumentId
}

function openEditLot(h: Holding, tx: Transaction | null) {
  lotPickerHolding.value = null
  editingId.value = h.id
  editingTxId.value = tx?.id ?? null
  formOpen.value = true
  message.value = ''
  error.value = ''
  Object.assign(form, {
    symbol: h.instrument.symbol,
    isin: h.instrument.isin ?? '',
    mic: h.instrument.mic ?? '',
    currency: h.instrument.currency,
    name: h.instrument.name ?? '',
    kind: h.instrument.kind,
    region: h.instrument.region ?? '',
    trade_date: tx?.trade_date ?? h.trade_date ?? today(),
    quantity: tx?.quantity ?? h.quantity,
    unit_price: tx?.unit_price ?? h.avg_cost,
    commission: tx?.commission ?? 0,
    notes: tx?.notes ?? '',
  })
}

async function submit() {
  error.value = ''
  message.value = ''
  try {
    const payload: HoldingInput = {
      ...form,
      region: form.region || undefined,
      isin: form.isin || undefined,
      mic: form.mic || undefined,
      name: form.name || undefined,
      notes: form.notes || undefined,
    }
    if (editingId.value === null) {
      await api.create(payload)
      message.value = 'Acquisition recorded (same ticker merges into one line).'
    } else {
      if (editingTxId.value != null) {
        payload.transaction_id = editingTxId.value
      }
      await api.update(editingId.value, payload)
      message.value = 'Acquisition lot updated.'
    }
    resetForm()
    await refreshBook()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Save failed'
  }
}

async function removeHolding(h: Holding) {
  const lots = h.lot_count ?? h.transactions?.length ?? 1
  const detail =
    lots > 1
      ? `${h.instrument.symbol} (${lots} lots, qty ${h.quantity})`
      : `${h.instrument.symbol} (qty ${h.quantity})`
  if (!window.confirm(`Delete holding ${detail}? This cannot be undone.`)) {
    return
  }
  error.value = ''
  message.value = ''
  try {
    await api.remove(h.id)
    message.value = 'Holding deleted.'
    if (editingId.value === h.id) {
      resetForm()
    }
    if (lotPickerHolding.value?.id === h.id) {
      lotPickerHolding.value = null
    }
    await refreshBook()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Delete failed'
  }
}

</script>

<template>
  <div class="module-page portfolio-page">
    <section class="module-hero" aria-labelledby="portfolio-title">
      <div>
        <p class="module-kicker">Owned capital · Portfolio mandate</p>
        <h1 id="portfolio-title" class="module-title">Portfolio</h1>
        <p class="module-description">
          The ledger for positions you own: acquisition cost, current value, conviction and
          long-horizon decisions.
        </p>
      </div>
      <div class="module-metrics" aria-label="Portfolio summary">
        <div class="module-metric">
          <span class="module-metric-label">Market value</span>
          <strong>{{ moneyOrDash(portfolioValue, displayCurrency) }}</strong>
        </div>
        <div class="module-metric">
          <span class="module-metric-label">Holdings</span>
          <strong>{{ holdings.length }}</strong>
        </div>
        <div class="module-metric">
          <span class="module-metric-label">Decision horizon</span>
          <strong>6 · 12 · 24m</strong>
        </div>
      </div>
    </section>

    <p v-if="message" class="ok">{{ message }}</p>
    <p v-if="error || loadError" class="bad">{{ error || loadError }}</p>

    <section v-if="lotPickerHolding" class="panel">
      <h1>Choose acquisition to edit — {{ lotPickerHolding.instrument.symbol }}</h1>
      <p class="mute">Position shows as one line; pick the lot you want to change.</p>
      <ul class="lots">
        <li v-for="tx in lotPickerHolding.transactions" :key="tx.id">
          <button type="button" class="ghost lot-btn" @click="openEditLot(lotPickerHolding!, tx)">
            {{ tx.trade_date }} · qty {{ tx.quantity }} @ {{ money(tx.unit_price, lotPickerHolding.instrument.currency) }}
            · fee {{ money(tx.commission, lotPickerHolding.instrument.currency) }}
            · lot {{ money(tx.lot_cost, lotPickerHolding.instrument.currency) }}
          </button>
        </li>
      </ul>
      <div class="actions">
        <button type="button" class="ghost" @click="lotPickerHolding = null">Cancel</button>
      </div>
    </section>

    <section v-if="formOpen" class="panel">
      <h1>
        {{
          editingId === null
            ? 'Add acquisition'
            : `Edit acquisition${editingTxId != null ? ` #${editingTxId}` : ''} — holding #${editingId}`
        }}
      </h1>
      <form class="form" @submit.prevent="submit">
        <label>
          Symbol
          <input v-model="form.symbol" required autocomplete="off" placeholder="AM.PA">
        </label>
        <label>
          Trade date
          <input v-model="form.trade_date" type="date" required>
        </label>
        <label>
          ISIN
          <input v-model="form.isin" autocomplete="off">
        </label>
        <label>
          MIC / exchange
          <input v-model="form.mic" placeholder="XPAR / XNAS" autocomplete="off">
        </label>
        <label>
          Name
          <input v-model="form.name" autocomplete="off">
        </label>
        <label>
          Kind
          <select v-model="form.kind">
            <option value="equity">equity</option>
            <option value="etf">etf</option>
          </select>
        </label>
        <label>
          Region
          <select v-model="form.region">
            <option value="eu">eu</option>
            <option value="us">us</option>
            <option value="">—</option>
          </select>
        </label>
        <label>
          Currency
          <input v-model="form.currency" required maxlength="3" @change="onCurrencyChange">
        </label>
        <label>
          Quantity
          <input v-model.number="form.quantity" type="number" min="0" step="any" required>
        </label>
        <label>
          Unit price
          <input v-model.number="form.unit_price" type="number" min="0" step="any" required>
        </label>
        <label>
          Commission
          <input v-model.number="form.commission" type="number" min="0" step="any">
        </label>
        <label class="wide">
          Notes
          <input v-model="form.notes">
        </label>
        <p class="mute wide">
          Lot cost preview: {{ money(previewLotCost, form.currency) }}
          · avg {{ money(previewAvg, form.currency) }}
        </p>
        <div class="actions wide">
          <button type="submit">{{ editingId === null ? 'Add' : 'Save' }}</button>
          <button type="button" class="ghost" @click="resetForm">Cancel</button>
        </div>
      </form>
    </section>

    <section class="panel">
      <div class="list-head">
        <h1>Holdings</h1>
        <div class="actions">
          <button type="button" @click="startAdd()">Add acquisition</button>
          <button type="button" class="ghost" :disabled="pending" @click="refreshBook()">Reload</button>
        </div>
      </div>
      <p v-if="portfolioValue != null" class="ok">
        Market value in {{ displayCurrency }}: {{ moneyOrDash(portfolioValue, displayCurrency) }}
      </p>
      <p v-else-if="holdings.length" class="mute">
        Market value pending FX or quotes.
      </p>
      <p v-if="pending">Loading…</p>
      <p v-else-if="holdings.length === 0" class="mute">No holdings yet.</p>
      <table v-else>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Acquired</th>
            <th>Qty</th>
            <th>Quote</th>
            <th>Cost</th>
            <th>Value ({{ displayCurrency }})</th>
            <th>P&amp;L ({{ displayCurrency }})</th>
            <th>P&amp;L %</th>
            <th class="daily-change-heading">Daily %</th>
            <th aria-label="Actions"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="h in holdings" :key="h.id">
          <tr>
            <td>
              <strong>{{ h.instrument.symbol }}</strong>
              <span class="sub">
                {{ h.instrument.name || h.instrument.isin || h.instrument.kind }}
                · {{ h.instrument.currency }}
              </span>
            </td>
            <td>
              <template v-if="(h.lot_count ?? h.transactions?.length ?? 0) > 1">
                {{ h.lot_count ?? h.transactions.length }} lots
                <span class="sub">last {{ h.trade_date || '—' }} · avg {{ money(h.avg_cost, h.instrument.currency) }}</span>
              </template>
              <template v-else>
                {{ h.trade_date || '—' }}
                <span class="sub">avg {{ money(h.avg_cost, h.instrument.currency) }}</span>
              </template>
            </td>
            <td>{{ h.quantity }}</td>
            <td>
              <template v-if="h.quote">
                {{ money(h.quote.price, h.quote.currency) }}
              </template>
              <span v-else class="bad">no quote</span>
            </td>
            <td>
              {{ money(h.cost_native, h.instrument.currency) }}
              <span v-if="h.instrument.currency.toUpperCase() !== displayCurrency" class="sub">
                {{ h.cost_display == null ? `${displayCurrency} pending FX` : moneyOrDash(h.cost_display, displayCurrency) }}
              </span>
            </td>
            <td>
              {{ h.market_value_display == null ? 'pending FX' : moneyOrDash(h.market_value_display, displayCurrency) }}
              <span
                v-if="h.market_value_native != null && (h.quote?.currency || h.instrument.currency).toUpperCase() !== displayCurrency"
                class="sub"
              >
                {{ money(h.market_value_native, h.quote?.currency || h.instrument.currency) }}
              </span>
            </td>
            <td :class="{ ok: (h.pnl_display ?? 0) > 0, bad: (h.pnl_display ?? 0) < 0 }">
              {{ h.pnl_display == null ? 'pending FX' : moneyOrDash(h.pnl_display, displayCurrency) }}
              <span
                v-if="h.pnl_native != null && h.instrument.currency.toUpperCase() !== displayCurrency"
                class="sub"
              >
                {{ money(h.pnl_native, h.instrument.currency) }}
              </span>
            </td>
            <td :class="{ ok: (h.pnl_pct ?? 0) > 0, bad: (h.pnl_pct ?? 0) < 0 }">
              {{ pctOrDash(h.pnl_pct) }}
            </td>
            <td class="daily-change" :class="dailyChangeClass(h.daily_change_pct)">
              {{ pctOrDash(h.daily_change_pct) }}
            </td>
            <td class="row-actions portfolio-actions">
              <button
                type="button"
                class="ghost portfolio-icon-btn"
                :aria-label="`${expandedHistoryId === h.instrument.id ? 'Close' : 'Show'} performance chart for ${h.instrument.symbol}`"
                :title="`${expandedHistoryId === h.instrument.id ? 'Close' : 'Show'} performance chart for ${h.instrument.symbol}`"
                :aria-expanded="expandedHistoryId === h.instrument.id"
                :aria-controls="`portfolio-history-${h.instrument.id}`"
                @click="toggleHistory(h.instrument.id)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 19h16v2H2V3h2v16Zm2-3 4-5 3 3 5-7 2 1.4-6.5 9.1-3.3-3.3L7.5 17 6 16Z" />
                </svg>
              </button>
              <button
                type="button"
                class="ghost portfolio-icon-btn"
                :aria-label="`Edit ${h.instrument.symbol}`"
                :title="`Edit ${h.instrument.symbol}`"
                @click="startEdit(h)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M4 20h4l11-11-4-4L4 16v4Zm12.2-16.2 4 4 1.1-1.1a1.4 1.4 0 0 0 0-2l-2-2a1.4 1.4 0 0 0-2 0l-1.1 1.1Z" />
                </svg>
              </button>
              <button
                type="button"
                class="danger portfolio-icon-btn"
                :aria-label="`Delete ${h.instrument.symbol}`"
                :title="`Delete ${h.instrument.symbol}`"
                @click="removeHolding(h)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-3 6h12l-1 12H7L6 9Zm4 2v8h2v-8h-2Zm4 0v8h2v-8h-2Z" />
                </svg>
              </button>
            </td>
          </tr>
          <tr v-if="expandedHistoryId === h.instrument.id" :id="`portfolio-history-${h.instrument.id}`" class="history-row">
            <td colspan="10">
              <PerformanceHistory :instrument-id="h.instrument.id" :symbol="h.instrument.symbol" />
            </td>
          </tr>
          </template>
        </tbody>
      </table>
    </section>

    <RecommendationsPanel
      book="portfolio"
      :run="agentRun"
      :recommendations="recommendations"
      :busy="advising"
      @reload="refreshAdvisory()"
    />

    <AgentContextPreview
      book="portfolio"
      :items="contextPreviewData?.instruments ?? []"
      :pending="contextPending"
      :error="contextLoadError?.message ?? null"
    />
  </div>
</template>

<style scoped>
.portfolio-icon-btn {
  display: inline-flex;
  width: 1.75rem;
  height: 1.75rem;
  padding: 0.3rem;
  align-items: center;
  justify-content: center;
}

.portfolio-actions {
  justify-content: flex-end;
}

.portfolio-icon-btn svg {
  width: 100%;
  height: 100%;
  fill: currentColor;
}

.daily-change-heading {
  background: var(--accent-soft);
  text-align: right;
}

.daily-change {
  min-width: 5.5rem;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

.daily-change--up {
  background: #dceade;
  color: #175b3a;
}

.daily-change--down {
  background: #f2dddd;
  color: #7a2323;
}

.daily-change--neutral {
  background: #ecebe5;
  color: var(--mute);
}

.history-row > td { padding: 0; }
</style>
