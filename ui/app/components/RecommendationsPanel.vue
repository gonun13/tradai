<script setup lang="ts">
import type { AgentRun, Book, Recommendation } from '~/composables/useHoldingsApi'

/**
 * One combined daily run decides on both books (0019), so it produces one recommendation
 * list. Each module renders this filtered to its own `book` — you never have to read past the
 * other book's rows to find yours. A cross-book `sell A to fund B` still shows: the sell
 * appears here with its `pair_symbol` linking across to the Tracker module.
 */
const props = defineProps<{
  book: Book
  run: AgentRun | null
  recommendations: Recommendation[]
  busy?: boolean
}>()

const emit = defineEmits<{ reload: [] }>()

const mine = computed(() =>
  // Rows written before 0019 carry no book and were all decided about holdings.
  props.recommendations.filter((r) => (r.book ?? 'portfolio') === props.book),
)

const bySymbol = computed(() => {
  const map = new Map<string, Recommendation[]>()
  for (const r of mine.value) {
    const list = map.get(r.symbol) ?? []
    list.push(r)
    map.set(r.symbol, list)
  }
  return map
})

const infoNeeds = computed(() => props.run?.info_needs ?? [])

// 0028: every explained row carries the run-wide market read it was written against. Prefer a
// fresh one; on a day where everything carried, show the deciding run's read and say so.
const marketRead = computed(() => {
  const explained = mine.value.filter((r) => r.explanation?.market_read)
  const fresh = explained.find((r) => !r.carried_from_run_id)
  const pick = fresh ?? explained[0]
  if (!pick?.explanation?.market_read) {
    return null
  }
  return {
    text: pick.explanation.market_read,
    from: fresh ? null : pick.carried_from_run_id,
  }
})

const emptyLabel = computed(() =>
  props.book === 'tracker'
    ? 'No tracker recommendations in this run.'
    : 'No recommendations stored.',
)

function jevConfidence(r: Recommendation) {
  // 0013 promoted confidence to a column; fall back to the Jev blob for pre-0013 rows.
  const c = r.confidence ?? (r.jev?.confidence as number | undefined)
  return typeof c === 'number' ? c.toFixed(2) : null
}

function lensLine(recs: Recommendation[]) {
  const lenses = recs[0]?.jev_lenses
  if (!lenses) {
    return null
  }
  // Internal signal names only — the thesis reasoning behind them stays off-screen.
  // 0027: lenses follow the ingestion layers. Runs before it stored `thesis` where
  // `fundamentals` now sits.
  const fundamentalsLabel = props.book === 'tracker' ? 'case' : 'fundamentals'
  const parts: string[] = []
  for (const key of ['historical', 'fundamentals', 'technicals', 'news'] as const) {
    const a = (lenses[key] ?? (key === 'fundamentals' ? lenses.thesis : undefined))?.action
    if (a) {
      parts.push(`${key === 'fundamentals' ? fundamentalsLabel : key}:${a}`)
    }
  }
  return parts.length ? parts.join(' · ') : null
}

function carriedLine(recs: Recommendation[]) {
  const r = recs[0]
  if (!r?.carried_from_run_id) {
    return null
  }
  const on = r.carried_from_at ? ` (${r.carried_from_at.slice(0, 10)})` : ''
  return `unchanged — carried from run #${r.carried_from_run_id}${on}`
}

function logPath(symbol: string) {
  return `/${props.book}/log/${encodeURIComponent(symbol)}`
}

function onLogClick(event: Event, rec: Recommendation | undefined) {
  if (!rec?.conversation?.length) {
    event.preventDefault()
  }
}
</script>

<template>
  <section class="panel">
    <div class="list-head">
      <div>
        <p class="section-kicker">{{ book === 'portfolio' ? 'Owned-book advisory' : 'Entry advisory' }}</p>
        <h1>{{ book === 'portfolio' ? 'Portfolio decisions' : 'Tracker signals' }}</h1>
      </div>
      <button type="button" class="ghost" :disabled="busy" @click="emit('reload')">Reload run</button>
    </div>

    <p v-if="run" class="mute">
      Run #{{ run.id }} ·
      <strong
        :class="{
          ok: run.status === 'succeeded',
          bad: run.status === 'failed',
          warn: run.status === 'partial' || run.status === 'running' || run.status === 'pending',
        }"
      >{{ run.status }}</strong>
      · {{ run.trigger }}
      · started {{ run.started_at }}
      <template v-if="run.finished_at"> · finished {{ run.finished_at }}</template>
    </p>
    <p v-else class="mute">
      No agent run yet — set <code>CLAUDE_CODE_OAUTH_TOKEN</code> + <code>TYPESAFE_API_KEY</code>, then Run now.
    </p>
    <p v-if="run?.error" class="bad">{{ run.error }}</p>
    <pre v-if="run?.log" class="log">{{ run.log }}</pre>

    <div v-if="marketRead" class="market-read">
      <h2>Market read</h2>
      <p class="rationale">{{ marketRead.text }}</p>
      <p v-if="marketRead.from" class="sub">From run #{{ marketRead.from }} — nothing was re-decided this run.</p>
    </div>

    <!-- Info-needs are run-wide (they plan the next ingest), so both modules show them. -->
    <div v-if="infoNeeds.length" class="info-needs">
      <h2>Info-needs (ingest planning)</h2>
      <ul>
        <li v-for="(need, idx) in infoNeeds" :key="idx">
          <strong>{{ need.priority || 'medium' }}</strong>
          — {{ need.description }}
          <span v-if="need.symbols?.length" class="sub">{{ need.symbols.join(', ') }}</span>
        </li>
      </ul>
    </div>

    <p v-if="!mine.length && run?.status === 'succeeded'" class="mute">{{ emptyLabel }}</p>

    <div v-for="[symbol, recs] in bySymbol" :key="symbol" class="ctx">
      <div class="rec-head">
        <h2>{{ symbol }} <span class="mute">{{ recs[0]?.instrument_name }}</span></h2>
        <NuxtLink
          class="ghost icon-btn"
          title="Claude ↔ Jev conversation log"
          :to="logPath(symbol)"
          :aria-disabled="!recs[0]?.conversation?.length"
          @click="onLogClick($event, recs[0])"
        >
          ⌗ log
        </NuxtLink>
      </div>
      <p class="tech">
        <span v-for="r in recs" :key="r.id" class="pill" :data-action="r.action">
          {{ r.horizon }} {{ r.action }}<template v-if="jevConfidence(r)"> · conf {{ jevConfidence(r) }}</template>
        </span>
      </p>
      <p v-if="lensLine(recs)" class="sub lenses">supporting {{ lensLine(recs) }}</p>
      <p v-if="carriedLine(recs)" class="sub">{{ carriedLine(recs) }}</p>
      <p v-if="recs[0]?.explanation" class="rationale">{{ recs[0].explanation.text }}</p>
      <p v-if="recs[0]?.explanation?.tension" class="sub">Tension: {{ recs[0].explanation.tension }}</p>
      <!-- 0013's `better_use` finally has somewhere to point: the named alternative lives
           in the other module, so link straight to it. -->
      <p v-for="r in recs.filter((x) => x.pair_symbol)" :key="`pair-${r.id}`" class="sub">
        {{ r.horizon }}: proceeds would fund
        <NuxtLink :to="book === 'portfolio' ? '/tracker' : '/portfolio'">{{ r.pair_symbol }}</NuxtLink>
      </p>
    </div>
  </section>
</template>

<style scoped>
.section-kicker {
  margin: 0 0 0.2rem;
  color: var(--accent);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.section-kicker + h1 { margin-bottom: 0; }
</style>
