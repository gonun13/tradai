<script setup lang="ts">
import type {
  AdvisoryRunLog,
  IngestRunDetail,
  OperationRunSummary,
} from '~/types/api'

const props = defineProps<{ kind: 'advisory' | 'ingest' }>()
const api = useHoldingsApi()
const open = ref(false)
const loading = ref(false)
const detailLoading = ref(false)
const loadError = ref('')
const detailError = ref('')
const runs = ref<OperationRunSummary[]>([])
const selectedId = ref<number | null>(null)
const detail = ref<AdvisoryRunLog | IngestRunDetail | null>(null)
const dialog = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const opener = ref<HTMLElement | null>(null)
const historyVersion = useState('ops-history-version', () => 0)

const title = computed(() => (props.kind === 'advisory' ? 'Advisory logs' : 'Ingest logs'))

function timestamp(value: string | null | undefined) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString()
}

async function fetchRuns() {
  loading.value = true
  loadError.value = ''
  runs.value = []
  detail.value = null
  try {
    const response = props.kind === 'advisory' ? await api.listAgentRuns() : await api.listIngestRuns()
    runs.value = response.runs
    selectedId.value = response.runs[0]?.id ?? null
    if (selectedId.value != null) await fetchDetail(selectedId.value)
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'Could not load operation history.'
  } finally {
    loading.value = false
  }
}

async function fetchDetail(id: number) {
  selectedId.value = id
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  try {
    const response = props.kind === 'advisory'
      ? await api.getAgentRunLog(id)
      : await api.getIngestRun(id)
    if (selectedId.value === id) detail.value = response.run
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : 'Could not load operation detail.'
  } finally {
    if (selectedId.value === id) detailLoading.value = false
  }
}

async function show(event: MouseEvent) {
  opener.value = event.currentTarget as HTMLElement
  open.value = true
  await nextTick()
  closeButton.value?.focus()
  await fetchRuns()
}

function close() {
  open.value = false
  nextTick(() => opener.value?.focus())
}

function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.key !== 'Tab' || !dialog.value) return
  const nodes = [...dialog.value.querySelectorAll<HTMLElement>(
    'button:not(:disabled), a[href], input:not(:disabled), [tabindex]:not([tabindex="-1"])',
  )]
  if (!nodes.length) return
  const first = nodes[0]!
  const last = nodes[nodes.length - 1]!
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

watch(historyVersion, () => {
  if (open.value) fetchRuns()
})

function lines(value: string | null | undefined) {
  return value?.split('\n').filter(Boolean) ?? []
}

function countLine(name: string, value: unknown) {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  return `${name}: ${row.ingested ?? 0} ingested · ${row.from_cache ?? 0} cached · ${row.missing ?? 0} missing`
}

const ingestLines = computed(() => {
  if (props.kind !== 'ingest' || !detail.value || !('report' in detail.value)) return []
  const report = detail.value.report
  if (!report) return []
  const output: string[] = []
  const stats = report.statistics as Record<string, unknown> | undefined
  const layers = stats?.layers as Record<string, unknown> | undefined
  if (report.updated_at) output.push(`Report updated: ${timestamp(String(report.updated_at))}`)
  if (Array.isArray(report.instruments)) {
    output.push(`Instruments: ${report.instruments.length} · unit: ${String(stats?.unit ?? 'instrument datasets')}`)
  }
  for (const [key, label] of Object.entries({
    historical: 'Historical', fundamentals: 'Fundamentals', technicals: 'Technicals', news: 'News',
  })) {
    const line = countLine(label, layers?.[key])
    if (line) output.push(line)
  }
  const historical = (layers?.historical as Record<string, unknown> | undefined)?.operations
    ?? (stats?.historical as Record<string, unknown> | undefined)
  if (historical && typeof historical === 'object') {
    for (const [key, value] of Object.entries(historical)) {
      const line = countLine(`Historical ${key.replaceAll('_', ' ')}`, value)
      if (line) output.push(line)
    }
  }
  const warnings = Array.isArray(report.errors) ? report.errors : []
  for (const warning of warnings) output.push(`Warning: ${String(warning)}`)
  const missing = Array.isArray(report.symbol_not_found) ? report.symbol_not_found : []
  for (const item of missing) {
    const row = item as Record<string, unknown>
    const source = [row.book, row.vendor].filter(Boolean).join(' · ')
    output.push(`Missing symbol: ${row.symbol ?? 'unknown'}${source ? ` · ${source}` : ''}${row.detail ? ` — ${row.detail}` : ''}`)
  }
  return output
})
</script>

<template>
  <button type="button" class="ghost" @click="show">{{ title }}</button>
  <Teleport to="body">
    <div v-if="open" class="operation-backdrop" @mousedown.self="close">
      <section
        ref="dialog"
        class="operation-dialog"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="`${kind}-logs-title`"
        @keydown="keydown"
      >
        <header class="operation-dialog-head">
          <div>
            <p class="operation-kicker">Latest 20</p>
            <h1 :id="`${kind}-logs-title`">{{ title }}</h1>
          </div>
          <button ref="closeButton" type="button" class="ghost" aria-label="Close logs" @click="close">Close</button>
        </header>

        <p v-if="loading" class="mute" role="status">Loading history…</p>
        <div v-else-if="loadError" class="operation-error" role="alert">
          <p>{{ loadError }}</p>
          <button type="button" class="ghost" @click="fetchRuns">Try again</button>
        </div>
        <p v-else-if="!runs.length" class="mute">No {{ kind }} operations have been recorded yet.</p>
        <div v-else class="operation-layout">
          <ol class="operation-list" aria-label="Operation history">
            <li v-for="run in runs" :key="run.id">
              <button
                type="button"
                :class="{ selected: selectedId === run.id }"
                :aria-current="selectedId === run.id ? 'true' : undefined"
                @click="fetchDetail(run.id)"
              >
                <strong>#{{ run.id }} · {{ run.status }}</strong>
                <span>{{ run.trigger }} · {{ timestamp(run.started_at) }}</span>
              </button>
            </li>
          </ol>

          <article class="operation-detail" aria-live="polite">
            <p v-if="detailLoading" class="mute">Loading detail…</p>
            <p v-else-if="detailError" class="bad" role="alert">{{ detailError }}</p>
            <template v-else-if="detail">
              <h2>
                {{ kind === 'advisory' ? 'Run' : 'Ingest' }} #{{ detail.id }} · {{ detail.status }} ·
                {{ detail.trigger }} · started {{ timestamp(detail.started_at) }} · finished {{ timestamp(detail.finished_at) }}
              </h2>
              <template v-if="kind === 'advisory' && 'log' in detail">
                <p v-for="(line, index) in lines(detail.log)" :key="index" class="operation-line">{{ line }}</p>
              </template>
              <template v-else>
                <p v-for="(line, index) in ingestLines" :key="index" class="operation-line">{{ line }}</p>
                <p v-if="!ingestLines.length && detail.status === 'succeeded'" class="mute">No report detail was stored.</p>
              </template>
              <p v-if="detail.error" class="bad">Error: {{ detail.error }}</p>
            </template>
          </article>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.operation-backdrop {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1rem;
  background: rgba(20, 25, 22, 0.58);
}
.operation-dialog {
  width: min(72rem, 100%);
  max-height: min(48rem, calc(100vh - 2rem));
  overflow: auto;
  padding: 1.25rem;
  border: 1px solid var(--line);
  background: var(--paper);
  color: var(--ink);
  box-shadow: 0 1.5rem 5rem rgba(0, 0, 0, 0.28);
}
.operation-dialog-head { display: flex; justify-content: space-between; gap: 1rem; align-items: start; }
.operation-dialog-head h1 { margin: 0; }
.operation-kicker { margin: 0 0 0.25rem; color: var(--mute); font: 0.7rem ui-monospace, monospace; text-transform: uppercase; letter-spacing: 0.1em; }
.operation-layout { display: grid; grid-template-columns: minmax(15rem, 19rem) minmax(0, 1fr); gap: 1rem; margin-top: 1rem; }
.operation-list { max-height: 34rem; overflow: auto; margin: 0; padding: 0; list-style: none; border: 1px solid var(--line); }
.operation-list li + li { border-top: 1px solid var(--line); }
.operation-list button { width: 100%; display: flex; flex-direction: column; gap: 0.25rem; text-align: left; border: 0; background: transparent; color: var(--ink); }
.operation-list button.selected { background: var(--accent-soft); box-shadow: inset 3px 0 var(--accent); }
.operation-list span { color: var(--mute); font-size: 0.8rem; }
.operation-detail { min-width: 0; padding: 1rem; border: 1px solid var(--line); background: rgba(255, 255, 255, 0.5); }
.operation-detail h2 { margin: 0 0 1rem; font-size: 0.95rem; line-height: 1.5; }
.operation-line { margin: 0.35rem 0; overflow-wrap: anywhere; font: 0.82rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
.operation-error { color: var(--bad); }
@media (max-width: 44rem) {
  .operation-layout { grid-template-columns: 1fr; }
  .operation-list { max-height: 12rem; }
  .operation-dialog { max-height: calc(100vh - 1rem); padding: 0.9rem; }
}
</style>
