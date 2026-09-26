<script setup lang="ts">
import type { HistoryPoint, HistoryResponse } from '~/types/api'

const props = defineProps<{
  instrumentId: number
  symbol: string
}>()

const api = useHoldingsApi()
const ranges = ['1y', '2y', '5y', 'max'] as const
type Range = (typeof ranges)[number]
const selected = ref<Range>('5y')
const loading = ref(false)
const error = ref('')
const history = ref<HistoryResponse | null>(null)
const cache = new Map<Range, HistoryResponse>()
const chartId = `performance-chart-${props.instrumentId}`

const width = 800
const height = 300
const padding = { left: 52, right: 22, top: 24, bottom: 38 }

async function load(range: Range) {
  selected.value = range
  error.value = ''
  if (cache.has(range)) {
    history.value = cache.get(range)!
    return
  }
  loading.value = true
  try {
    const result = await api.instrumentHistory(props.instrumentId, range)
    cache.set(range, result)
    history.value = result
  } catch (cause: unknown) {
    const data = (cause as { data?: { error?: string } })?.data
    error.value = data?.error ?? (cause instanceof Error ? cause.message : 'History could not be loaded.')
  } finally {
    loading.value = false
  }
}

onMounted(() => load('5y'))

const allPoints = computed(() => [
  ...(history.value?.stock.points ?? []),
  ...(history.value?.benchmark?.points ?? []),
])

const bounds = computed(() => {
  const points = allPoints.value
  if (!points.length) return null
  const times = points.map(point => Date.parse(`${point.date}T00:00:00Z`))
  const values = points.map(point => point.normalized_value)
  const minValue = Math.min(...values)
  const maxValue = Math.max(...values)
  const logarithmic = minValue > 0 && maxValue / minValue >= 20
  const valuePad = Math.max(4, (maxValue - minValue) * 0.08)
  return {
    minTime: Math.min(...times),
    maxTime: Math.max(...times),
    minValue: logarithmic
      ? minValue * 0.9
      : Math.max(0, Math.floor(minValue - valuePad)),
    // Anchor the top of the visible Y scale to the actual peak in the selected range.
    maxValue,
    logarithmic,
  }
})

function x(point: HistoryPoint) {
  const b = bounds.value
  if (!b) return padding.left
  const span = Math.max(1, b.maxTime - b.minTime)
  return padding.left + ((Date.parse(`${point.date}T00:00:00Z`) - b.minTime) / span)
    * (width - padding.left - padding.right)
}

function y(point: HistoryPoint) {
  const b = bounds.value
  if (!b) return padding.top
  const ratio = b.logarithmic
    ? (Math.log(b.maxValue) - Math.log(point.normalized_value))
      / Math.max(Number.EPSILON, Math.log(b.maxValue) - Math.log(b.minValue))
    : (b.maxValue - point.normalized_value) / Math.max(1, b.maxValue - b.minValue)
  return padding.top + ratio
    * (height - padding.top - padding.bottom)
}

function path(points: HistoryPoint[]) {
  return points.map((point, index) => `${index ? 'L' : 'M'} ${x(point).toFixed(2)} ${y(point).toFixed(2)}`).join(' ')
}

const stockPath = computed(() => path(history.value?.stock.points ?? []))
const benchmarkPath = computed(() => path(history.value?.benchmark?.points ?? []))
const gridValues = computed(() => {
  const b = bounds.value
  if (!b) return []
  if (b.logarithmic) {
    const logMax = Math.log(b.maxValue)
    const logSpan = logMax - Math.log(b.minValue)
    return Array.from({ length: 5 }, (_, index) => Math.exp(logMax - (logSpan * index) / 4))
  }
  const step = (b.maxValue - b.minValue) / 4
  return Array.from({ length: 5 }, (_, index) => b.maxValue - step * index)
})

function gridY(value: number) {
  const b = bounds.value!
  const ratio = b.logarithmic
    ? (Math.log(b.maxValue) - Math.log(value))
      / Math.max(Number.EPSILON, Math.log(b.maxValue) - Math.log(b.minValue))
    : (b.maxValue - value) / Math.max(1, b.maxValue - b.minValue)
  return padding.top + ratio
    * (height - padding.top - padding.bottom)
}

function axisLabel(value: number) {
  if (value < 1) return value.toFixed(2)
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)
}

const logarithmicScale = computed(() => bounds.value?.logarithmic ?? false)

function returnLabel(value: number | null | undefined) {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
}

const description = computed(() => {
  const current = history.value
  if (!current?.stock.points.length) return `No long history available for ${props.symbol}.`
  const benchmark = current.benchmark?.points.length
    ? ` versus ${current.benchmark.symbol}, shown as a dashed line`
    : ''
  return `${props.symbol} adjusted performance indexed to 100${benchmark}. ${selected.value.toUpperCase()} return ${returnLabel(current.stock.endpoint_return_pct)}.`
})
</script>

<template>
  <section class="history" :aria-labelledby="`${chartId}-heading`">
    <div class="history-head">
      <div>
        <h2 :id="`${chartId}-heading`">Performance · {{ symbol }}</h2>
        <p class="history-basis">
          Adjusted closes · native-currency percentage returns
          <template v-if="logarithmicScale"> · logarithmic Y-scale</template>
        </p>
      </div>
      <div class="range-controls" aria-label="Performance range">
        <button
          v-for="range in ranges"
          :key="range"
          type="button"
          class="range-button"
          :class="{ active: selected === range }"
          :aria-pressed="selected === range"
          :disabled="loading"
          @click="load(range)"
        >
          {{ range === 'max' ? 'Max' : range.toUpperCase() }}
        </button>
      </div>
    </div>

    <p v-if="loading" class="history-state" role="status">Loading adjusted history…</p>
    <p v-else-if="error" class="history-state bad" role="alert">{{ error }}</p>
    <template v-else-if="history">
      <p v-if="history.warning" class="history-state warn" role="status">{{ history.warning }}</p>
      <p v-if="history.stock.stale || history.benchmark?.stale" class="history-state warn">
        Cached history is older than the weekly refresh window.
      </p>
      <p v-if="!history.stock.points.length" class="history-state mute">No chart data for this range.</p>
      <template v-else>
        <div class="legend" aria-hidden="true">
          <span><i class="legend-line stock-line" />{{ history.stock.symbol }} ({{ history.stock.native_currency }})</span>
          <span v-if="history.benchmark?.points.length">
            <i class="legend-line benchmark-line" />{{ history.benchmark.symbol }} · {{ history.benchmark.name }} ({{ history.benchmark.native_currency }})
          </span>
        </div>
        <svg
          class="chart"
          :viewBox="`0 0 ${width} ${height}`"
          role="img"
          :aria-labelledby="`${chartId}-title ${chartId}-desc`"
        >
          <title :id="`${chartId}-title`">{{ symbol }} indexed performance</title>
          <desc :id="`${chartId}-desc`">{{ description }}</desc>
          <g class="grid">
            <template v-for="value in gridValues" :key="value">
              <line :x1="padding.left" :x2="width - padding.right" :y1="gridY(value)" :y2="gridY(value)" />
              <text :x="padding.left - 8" :y="gridY(value) + 4">{{ axisLabel(value) }}</text>
            </template>
          </g>
          <path v-if="benchmarkPath" class="series benchmark" :d="benchmarkPath" />
          <path class="series stock" :d="stockPath" />
          <text class="date-label" :x="padding.left" :y="height - 10">{{ history.stock.points[0]?.date }}</text>
          <text class="date-label date-label-end" :x="width - padding.right" :y="height - 10">{{ history.stock.points.at(-1)?.date }}</text>
        </svg>
        <div class="chart-summary">
          <p><strong>{{ history.stock.symbol }}</strong> {{ returnLabel(history.stock.endpoint_return_pct) }}</p>
          <p v-if="history.benchmark?.points.length">
            <strong>{{ history.benchmark.symbol }} · {{ history.benchmark.name }}</strong>
            {{ returnLabel(history.benchmark.endpoint_return_pct) }}
          </p>
          <p>As of {{ history.stock.as_of }}</p>
          <p>
            {{ history.comparison_start ? 'Comparison starts' : 'Indexed from' }}
            {{ history.comparison_start || history.stock.normalization_date }}
          </p>
          <p v-if="history.stock.native_currency !== history.benchmark?.native_currency">
            Currencies: {{ history.stock.native_currency }} / {{ history.benchmark?.native_currency }}; returns are not FX-converted.
          </p>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.history {
  padding: 1rem 1.1rem 1.2rem;
  background: color-mix(in srgb, var(--surface) 84%, var(--accent-soft));
  border-block: 1px solid var(--line);
}
.history-head, .legend, .chart-summary, .range-controls { display: flex; align-items: center; }
.history-head { justify-content: space-between; gap: 1rem; }
.history h2 { margin: 0; font-size: 1rem; }
.history-basis { margin: 0.2rem 0 0; color: var(--mute); font-size: 0.82rem; }
.range-controls { gap: 0.25rem; }
.range-button { padding: 0.35rem 0.55rem; min-width: 2.8rem; }
.range-button.active { background: var(--accent); color: var(--accent-contrast, white); }
.history-state { margin: 1rem 0 0; }
.legend { gap: 1rem; margin: 0.85rem 0 0.15rem; font-size: 0.82rem; flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 0.4rem; }
.legend-line { width: 1.8rem; border-top: 3px solid var(--accent); }
.benchmark-line { border-top-style: dashed; border-top-color: var(--text); }
.chart { width: 100%; min-width: 36rem; height: auto; overflow: visible; }
.grid line { stroke: var(--line); stroke-width: 1; }
.grid text, .date-label { fill: var(--mute); font-size: 11px; }
.grid text { text-anchor: end; }
.date-label-end { text-anchor: end; }
.series { fill: none; vector-effect: non-scaling-stroke; stroke-width: 2.5; }
.series.stock { stroke: var(--accent); }
.series.benchmark { stroke: var(--text); stroke-dasharray: 8 6; }
.chart-summary { gap: 0.6rem 1.2rem; flex-wrap: wrap; font-size: 0.82rem; }
.chart-summary p { margin: 0; }
@media (max-width: 700px) {
  .history-head { align-items: flex-start; flex-direction: column; }
  .history { overflow-x: auto; }
}
</style>
