<script setup lang="ts">
import type { AgentContextPreviewItem, TechnicalFeatures } from '~/types/api'

const props = defineProps<{
  book: 'portfolio' | 'tracker'
  items: AgentContextPreviewItem[]
  pending?: boolean
  error?: string | null
}>()

const { moneyOrDash, pctOrDash, fmtNum } = useFormat()

const subjectLabel = computed(() => (props.book === 'portfolio' ? 'holding' : 'tracked name'))

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    return new Intl.NumberFormat('en-IE', { maximumFractionDigits: 4 }).format(value)
  }
  if (Array.isArray(value)) return value.length ? value.map(text).join(', ') : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function label(key: string): string {
  return key.replaceAll('_', ' ').replace(/^./, (first) => first.toUpperCase())
}

function timestamp(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').replace(/\.\d+(?=Z|[+-]\d\d:\d\d$)/, '').replace('Z', ' UTC')
}

function technicalRows(features: TechnicalFeatures | null | undefined) {
  return [
    ['RSI 14', fmtNum(features?.rsi_14, 1)],
    ['SMA 20', fmtNum(features?.sma_20)],
    ['SMA 50', fmtNum(features?.sma_50)],
    ['Return 1m', pctOrDash(features?.return_1m_pct)],
    ['Return 3m', pctOrDash(features?.return_3m_pct)],
    ['Return 6m', pctOrDash(features?.return_6m_pct)],
    ['vs SMA 20', pctOrDash(features?.vs_sma20_pct)],
    ['vs SMA 50', pctOrDash(features?.vs_sma50_pct)],
  ]
}

function fundamentalsScore(score: number): string {
  return `${Math.round(score * 100)}% coverage`
}
</script>

<template>
  <section class="panel context-preview" aria-labelledby="context-preview-title">
    <div class="list-head">
      <h1 id="context-preview-title">Agent context preview</h1>
      <div class="context-head-actions">
        <span v-if="items.length" class="context-count">
          {{ items.length }} {{ subjectLabel }}{{ items.length === 1 ? '' : 's' }}
        </span>
        <OperationHistoryModal kind="ingest" />
      </div>
    </div>
    <p class="mute">
      Current cached market data that will be assembled for the next Claude + Jev run.
      Open a ticker to inspect it.
    </p>

    <p v-if="pending" class="mute">Loading context…</p>
    <p v-else-if="error" class="bad">Could not load agent context: {{ error }}</p>
    <p v-else-if="!items.length" class="mute">
      No {{ book === 'portfolio' ? 'holdings' : 'tracked names' }} to include yet.
    </p>

    <details v-for="item in items" :key="`${item.book}-${item.symbol}`" class="context-ticker">
      <summary>
        <span class="context-identity">
          <strong>{{ item.symbol }}</strong>
          <span class="context-name">{{ item.name || 'Unnamed instrument' }}</span>
        </span>
        <span class="context-status" aria-label="Cached data coverage">
          <span>{{ item.historical.quote || item.historical.bars ? 'Historical ready' : 'No historical' }}</span>
          <span>{{ item.fundamentals ? 'Fundamentals ready' : 'No fundamentals' }}</span>
          <span>{{ item.technicals ? 'Technicals ready' : 'No technicals' }}</span>
          <span>{{ item.news.length }} news</span>
        </span>
      </summary>

      <div class="context-grid">
        <section class="context-layer" aria-label="Historical data">
          <h2>Historical</h2>
          <template v-if="item.historical.quote || item.historical.bars">
            <dl class="context-values">
              <template v-if="item.historical.quote">
                <dt>Quote</dt>
                <dd>{{ moneyOrDash(item.historical.quote.price, item.historical.quote.currency) }}</dd>
                <dt>Quote source</dt>
                <dd>{{ item.historical.quote.source || '—' }}</dd>
                <dt>Quote as of</dt>
                <dd>{{ timestamp(item.historical.quote.as_of) }}</dd>
              </template>
              <template v-if="item.historical.bars">
                <dt>Daily bars</dt>
                <dd>{{ item.historical.bars.count ?? '—' }}</dd>
                <dt>Latest close</dt>
                <dd>{{ fmtNum(item.historical.bars.last_close) }}</dd>
                <dt>Latest bar</dt>
                <dd>{{ timestamp(item.historical.bars.as_of) }}</dd>
              </template>
            </dl>
          </template>
          <p v-else class="layer-empty">No accepted quote or bar snapshot.</p>
        </section>

        <section class="context-layer" aria-label="Fundamentals data">
          <h2>Fundamentals</h2>
          <template v-if="item.fundamentals">
            <p class="layer-meta">
              {{ item.fundamentals.source }} · {{ item.fundamentals.completeness_state }} ·
              {{ fundamentalsScore(item.fundamentals.coverage_score) }}
            </p>
            <p class="layer-meta">As of {{ timestamp(item.fundamentals.as_of) }}</p>
            <dl v-if="Object.keys(item.fundamentals.payload).length" class="context-values">
              <template v-for="(value, key) in item.fundamentals.payload" :key="key">
                <dt>{{ label(String(key)) }}</dt>
                <dd>{{ text(value) }}</dd>
              </template>
            </dl>
            <p v-else class="layer-empty">The accepted snapshot contains no values.</p>
            <p v-if="item.fundamentals.missing_fields.length" class="missing-fields">
              Missing: {{ item.fundamentals.missing_fields.map(label).join(', ') }}
            </p>
          </template>
          <p v-else class="layer-empty">No accepted fundamentals snapshot.</p>
        </section>

        <section class="context-layer" aria-label="Technicals data">
          <h2>Technicals</h2>
          <template v-if="item.technicals">
            <p class="layer-meta">
              {{ item.technicals.source }} · as of {{ timestamp(item.technicals.as_of) }}
            </p>
            <dl class="context-values">
              <template v-for="([name, value], index) in technicalRows(item.technicals.features)" :key="index">
                <dt>{{ name }}</dt>
                <dd>{{ value }}</dd>
              </template>
            </dl>
          </template>
          <p v-else class="layer-empty">No calculated technical snapshot.</p>
        </section>

        <section class="context-layer context-news" aria-label="News data">
          <h2>News</h2>
          <ol v-if="item.news.length" class="context-news-list">
            <li v-for="news in item.news" :key="news.id">
              <a v-if="news.url" :href="news.url" target="_blank" rel="noopener noreferrer">
                {{ news.title }}
              </a>
              <strong v-else>{{ news.title }}</strong>
              <p v-if="news.snippet">{{ news.snippet }}</p>
              <span class="layer-meta">
                {{ news.source_name || 'Unknown publisher' }} ·
                {{ timestamp(news.published_at || news.fetched_at) }}
                <template v-if="news.adapter_source"> · via {{ news.adapter_source }}</template>
              </span>
            </li>
          </ol>
          <p v-else class="layer-empty">No linked news in the recent-news window.</p>
        </section>
      </div>
    </details>
  </section>
</template>

<style scoped>
.context-preview h1 { margin-bottom: 0; }

.context-count {
  color: var(--mute);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.context-head-actions { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; justify-content: flex-end; }

.context-ticker {
  border-top: 1px solid var(--line);
}

.context-ticker:last-child {
  border-bottom: 1px solid var(--line);
}

.context-ticker summary {
  position: relative;
  display: grid;
  grid-template-columns: minmax(10rem, 1fr) auto;
  gap: 1rem;
  align-items: center;
  padding: 0.9rem 0.15rem 0.9rem 1.45rem;
  color: var(--accent-strong);
  cursor: pointer;
  list-style: none;
}

.context-ticker summary::-webkit-details-marker { display: none; }

.context-ticker summary::before {
  position: absolute;
  left: 0.3rem;
  content: '›';
  color: var(--accent);
  font-size: 1.35rem;
  line-height: 1;
  transition: transform 120ms ease;
}

.context-ticker[open] summary::before { transform: rotate(90deg); }

.context-ticker summary:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--accent) 38%, transparent);
  outline-offset: 2px;
}

.context-identity {
  display: inline-flex;
  gap: 0.6rem;
  align-items: baseline;
}

.context-name {
  color: var(--mute);
  font-weight: 400;
}

.context-status {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.context-status span {
  padding: 0.12rem 0.35rem;
  border: 1px solid var(--line);
  color: var(--mute);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.68rem;
  white-space: nowrap;
}

.context-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border-top: 1px solid var(--line);
  border-left: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.32);
}

.context-layer {
  min-width: 0;
  padding: 1rem;
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.context-layer h2 {
  margin: 0 0 0.65rem;
  color: var(--accent-strong);
  font-size: 0.98rem;
}

.context-news {
  grid-column: 1 / -1;
}

.layer-meta,
.layer-empty,
.missing-fields {
  margin: 0.3rem 0;
  color: var(--mute);
  font-size: 0.82rem;
}

.missing-fields { color: #8a5a20; }

.context-values {
  display: grid;
  grid-template-columns: minmax(6rem, auto) minmax(0, 1fr);
  gap: 0.3rem 0.75rem;
  margin: 0.75rem 0 0;
  font-size: 0.84rem;
}

.context-values dt { color: var(--mute); }
.context-values dd { margin: 0; overflow-wrap: anywhere; }

.context-news-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: 1rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.context-news-list li {
  padding-left: 0.75rem;
  border-left: 2px solid var(--line);
}

.context-news-list a { color: var(--accent); font-weight: 600; }
.context-news-list p { margin: 0.35rem 0; font-size: 0.88rem; line-height: 1.4; }

@media (max-width: 900px) {
  .context-ticker summary { grid-template-columns: 1fr; }
  .context-status { justify-content: flex-start; }
  .context-grid { grid-template-columns: 1fr; }
  .context-news { grid-column: auto; }
}
</style>
