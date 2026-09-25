<script setup lang="ts">
import type { ConversationTurn, Recommendation } from '~/composables/useHoldingsApi'

definePageMeta({
  alias: ['/portfolio/log/:symbol', '/tracker/log/:symbol'],
})

const route = useRoute()
const api = useHoldingsApi()
const { moneyOrDash } = useFormat()
const { message: opsMessage, error: opsError } = useGlobalOps()

const symbol = computed(() => {
  const raw = route.params.symbol
  const value = Array.isArray(raw) ? raw[0] : raw
  return decodeURIComponent(String(value || ''))
})

const { data, pending, error } = await useAsyncData(
  () => `advisory-log-${symbol.value}`,
  () => api.latestRun(),
  { watch: [symbol] },
)

const recs = computed(() => {
  const all = data.value?.recommendations ?? []
  return all.filter((r) => r.symbol === symbol.value)
})

const primary = computed(() => recs.value[0] ?? null)

const conversation = computed((): ConversationTurn[] => {
  return (primary.value?.conversation as ConversationTurn[] | null) ?? []
})

function jevConfidence(r: Recommendation) {
  const c = r.confidence ?? r.jev?.confidence
  return typeof c === 'number' ? c.toFixed(2) : null
}

function lensLine(recsList: Recommendation[]) {
  const lenses = recsList[0]?.jev_lenses
  if (!lenses) {
    return null
  }
  // Internal signal names only — the thesis reasoning behind them stays off-screen.
  // 0027: runs before it stored `thesis` where `fundamentals` now sits.
  const parts: string[] = []
  for (const key of ['historical', 'fundamentals', 'technicals', 'news'] as const) {
    const a = (lenses[key] ?? (key === 'fundamentals' ? lenses.thesis : undefined))?.action
    if (a) {
      parts.push(`${key}:${a}`)
    }
  }
  return parts.length ? parts.join(' · ') : null
}

function turnAnswerDetails(turn: ConversationTurn) {
  if (!turn.answers) {
    return []
  }
  return Object.entries(turn.answers).map(([horizon, v]) => ({
    horizon,
    action: v.action || '?',
    extras: Object.entries(v.payload || {}).filter(
      ([k]) => !['type', 'choice', 'answer', 'value'].includes(k),
    ),
  }))
}

const contextHolding = computed(() => {
  const holdings = data.value?.run?.context?.holdings ?? []
  return holdings.find((h) => h.symbol === symbol.value) ?? null
})

// 0019: the same transcript serves both books, so a tracked name resolves here instead.
const contextTracked = computed(() => {
  const tracked = data.value?.run?.context?.tracked ?? []
  return tracked.find((t) => t.symbol === symbol.value) ?? null
})

const runCurrency = computed(() => data.value?.run?.context?.display_currency ?? 'EUR')
const trackedDisplayPrice = computed(() =>
  contextTracked.value?.price_display ?? contextTracked.value?.price_eur ?? null,
)
const holdingDisplayValue = computed(() =>
  contextHolding.value?.market_value_display ?? contextHolding.value?.market_value_eur ?? null,
)

const resolvedBook = computed<'portfolio' | 'tracker'>(() => {
  const recommendationBook = primary.value?.book
  if (recommendationBook === 'tracker' || recommendationBook === 'portfolio') {
    return recommendationBook
  }
  return contextTracked.value && !contextHolding.value ? 'tracker' : 'portfolio'
})

const routeBook = computed<'portfolio' | 'tracker' | null>(() => {
  if (route.path.startsWith('/tracker/')) return 'tracker'
  if (route.path.startsWith('/portfolio/')) return 'portfolio'
  return null
})

const book = computed(() => routeBook.value ?? resolvedBook.value)

// Which of the two profiles (0019) was actually applied to this symbol. `context.mandates`
// carries the resolved text per book — defaults already substituted, so this shows what the
// models were really given rather than going blank when the operator hasn't written a half.
// Runs from before 0019 only have the single `mandate` string.
const mandate = computed(() => {
  const ctx = data.value?.run?.context
  if (!ctx) {
    return null
  }
  return ctx.mandates?.[book.value] ?? ctx.mandate ?? null
})

const models = computed(() => {
  const m = data.value?.run?.models
  return (m ?? null) as {
    claude?: { enabled?: boolean; binary?: string }
    jev?: { enabled?: boolean; model?: string }
  } | null
})

watchEffect(() => {
  useHead({ title: symbol.value ? `Log — ${symbol.value}` : 'Log' })
})

watch(
  [pending, resolvedBook, symbol],
  ([isPending, correctBook, currentSymbol]) => {
    if (isPending || !currentSymbol || routeBook.value === correctBook) return
    void navigateTo(`/${correctBook}/log/${encodeURIComponent(currentSymbol)}`, { replace: true })
  },
  { immediate: true },
)
</script>

<template>
  <div class="module-page log-page">
    <section class="module-hero log-hero" :aria-labelledby="`log-title-${symbol}`">
      <div>
        <p class="module-kicker">{{ book === 'tracker' ? 'Tracker signal' : 'Portfolio decision' }}</p>
        <h1 :id="`log-title-${symbol}`" class="module-title log-title">{{ symbol || 'Decision log' }}</h1>
        <p class="module-description">Claude ↔ Jev research and decision trace from the latest run.</p>
      </div>
      <NuxtLink class="ghost module-back" :to="book === 'tracker' ? '/tracker' : '/portfolio'">
        Back to {{ book === 'tracker' ? 'Tracker' : 'Portfolio' }}
      </NuxtLink>
    </section>

    <p v-if="opsMessage" class="ok">{{ opsMessage }}</p>
    <p v-if="opsError" class="bad">{{ opsError }}</p>
    <p v-if="pending" class="mute">Loading transcript…</p>
    <p v-else-if="error" class="bad">{{ error }}</p>
    <p v-else-if="!primary" class="mute">No recommendation found for {{ symbol }} on the latest run.</p>

    <template v-else>
      <section class="panel">
        <div class="list-head">
          <h1>
            {{ symbol }}
            <span class="mute">{{ primary.instrument_name }}</span>
          </h1>
          <p v-if="data?.run" class="mute">
            Run #{{ data.run.id }} · {{ data.run.status }} · {{ data.run.started_at }}
          </p>
          <p v-if="models" class="sub">
            Claude {{ models.claude?.enabled ? 'enabled' : 'disabled' }}<template v-if="models.claude?.binary"> ({{ models.claude.binary }})</template>
            · Jev {{ models.jev?.model || '—' }} ({{ models.jev?.enabled ? 'enabled' : 'disabled' }})
          </p>
        </div>
        <p class="tech">
          <span v-for="r in recs" :key="r.id" class="pill" :data-action="r.action">
            {{ r.horizon }} {{ r.action }}<template v-if="jevConfidence(r)"> · conf {{ jevConfidence(r) }}</template>
          </span>
        </p>
        <p v-if="lensLine(recs)" class="sub lenses">supporting {{ lensLine(recs) }}</p>
        <p v-if="primary.carried_from_run_id" class="sub">
          Unchanged since run #{{ primary.carried_from_run_id }}<template v-if="primary.carried_from_at">
          ({{ primary.carried_from_at.slice(0, 10) }})</template> — carried forward without a new
          Claude/Jev pass; the conversation below is from that run.
        </p>
      </section>

      <section v-if="primary.explanation" class="panel" aria-label="Why this recommendation">
        <h1>Why</h1>
        <p class="mute">
          Claude's explanation of the combined decision<template v-if="primary.carried_from_run_id">,
          written when run #{{ primary.carried_from_run_id }} decided it</template>. It explains the
          action; it never changes it.
        </p>
        <p class="rationale">{{ primary.explanation.text }}</p>
        <p v-if="primary.explanation.tension" class="rationale">
          <strong>Tension:</strong> {{ primary.explanation.tension }}
        </p>
        <p v-if="primary.explanation.market_read" class="sub">
          Market read: {{ primary.explanation.market_read }}
        </p>
      </section>

      <section v-if="mandate" class="panel">
        <h1>Mandate used for this run</h1>
        <p class="mute">
          The
          <strong>{{ book === 'tracker' ? 'investor profile' : 'portfolio mandate' }}</strong>
          actually sent to Claude and Jev for run #{{ data?.run?.id }} — the Setup page value
          may have changed since.
        </p>
        <p class="rationale">{{ mandate }}</p>
      </section>

      <section v-if="contextTracked && !contextHolding" class="panel">
        <h1>Ingested for {{ symbol }}</h1>
        <p class="mute">
          Tracked, not owned — no position fields. Fundamentals, news and technical evidence
          shown here are the snapshot used for this run.
        </p>
        <div class="ctx">
          <p class="tech">
            tracked since {{ (contextTracked.added_at || '').slice(0, 10) }}
            <template v-if="trackedDisplayPrice != null"> · {{ moneyOrDash(trackedDisplayPrice, runCurrency) }}</template>
          </p>
          <p v-if="contextTracked.quote" class="sub">
            quote {{ contextTracked.quote.price }} {{ contextTracked.quote.currency }}
            as of {{ contextTracked.quote.as_of }}
          </p>
          <p v-if="contextTracked.technicals" class="tech">
            <span v-for="(v, k) in contextTracked.technicals" :key="k">{{ k }}: {{ v }} </span>
          </p>
          <ul v-if="contextTracked.news?.length" class="news">
            <li v-for="(n, i) in contextTracked.news" :key="i">
              <a v-if="n.url" :href="n.url" target="_blank" rel="noopener noreferrer">{{ n.title }}</a>
              <span v-else>{{ n.title }}</span>
              <span class="sub"> · {{ n.source || 'news' }} · {{ n.published_at }}</span>
            </li>
          </ul>
        </div>
      </section>

      <section v-if="contextHolding" class="panel">
        <h1>Ingested for {{ symbol }}</h1>
        <p class="mute">What Claude and Jev actually saw for this run, not live data.</p>
        <div class="ctx">
          <p class="tech">
            qty {{ contextHolding.quantity }} · avg cost {{ contextHolding.avg_cost }}
            <template v-if="holdingDisplayValue != null"> · mv {{ moneyOrDash(holdingDisplayValue, runCurrency) }}</template>
            <template v-if="contextHolding.pnl_pct != null"> · pnl {{ contextHolding.pnl_pct }}%</template>
            <template v-if="contextHolding.weight_pct != null"> · wgt {{ contextHolding.weight_pct }}%</template>
            <template v-if="contextHolding.weight_cost_pct != null"> (cost wgt {{ contextHolding.weight_cost_pct }}%)</template>
            <template v-if="contextHolding.held_days != null"> · held {{ contextHolding.held_days }}d</template>
          </p>
          <p v-if="contextHolding.quote" class="sub">
            quote {{ contextHolding.quote.price }} {{ contextHolding.quote.currency }}
            as of {{ contextHolding.quote.as_of }} ({{ contextHolding.quote.source }})
          </p>
          <p v-if="contextHolding.technicals" class="tech">
            <span v-for="(v, k) in contextHolding.technicals" :key="k">{{ k }}: {{ v }} </span>
          </p>
          <ul v-if="contextHolding.news?.length" class="news">
            <li v-for="(n, i) in contextHolding.news" :key="i">
              <a v-if="n.url" :href="n.url" target="_blank" rel="noopener noreferrer">{{ n.title }}</a>
              <span v-else>{{ n.title }}</span>
              <span class="sub"> · {{ n.source || 'news' }} · {{ n.published_at }}</span>
            </li>
          </ul>
          <p v-if="contextHolding.thesis" class="rationale">
            Thesis (v{{ contextHolding.thesis.version }}): {{ contextHolding.thesis.text }}
          </p>
        </div>
      </section>

      <section class="panel log-panel" aria-label="Conversation log">
        <h1>Conversation</h1>
        <p v-if="!conversation.length" class="mute">No transcript for this ticker.</p>
        <ol v-else class="turns">
          <li v-for="(turn, idx) in conversation" :key="idx" class="turn" :data-role="turn.role">
            <p class="turn-meta">
              <strong>{{ turn.role }}</strong>
              · {{ turn.kind }}
              <template v-if="turn.lens"> · lens {{ turn.lens }}</template>
              <template v-if="turn.round"> · round {{ turn.round }}</template>
              <span v-if="turn.at" class="sub">{{ turn.at }}</span>
            </p>
            <p v-if="turn.summary">{{ turn.summary }}</p>
            <p v-if="turn.hypothesis" class="mute">Hypothesis: {{ turn.hypothesis }}</p>
            <p v-if="turn.question_hint" class="mute">Hint: {{ turn.question_hint }}</p>
            <p v-if="turn.research_excerpt" class="rationale">{{ turn.research_excerpt }}</p>
            <p v-if="turn.tension" class="mute">Tension: {{ turn.tension }}</p>
            <ul v-if="turnAnswerDetails(turn).length" class="turns turn-answers">
              <li v-for="d in turnAnswerDetails(turn)" :key="d.horizon">
                <strong>{{ d.horizon }}</strong> {{ d.action }}
                <span v-for="[k, v] in d.extras" :key="k" class="sub"> · {{ k }}: {{ v }}</span>
              </li>
            </ul>
          </li>
        </ol>
      </section>
    </template>
  </div>
</template>

<style scoped>
.log-hero {
  grid-template-columns: minmax(0, 1fr) auto;
}

.log-title {
  font-size: clamp(2.4rem, 7vw, 4.8rem);
}

.module-back {
  align-self: end;
}

@media (max-width: 720px) {
  .log-hero { grid-template-columns: 1fr; }
  .module-back { justify-self: start; }
}
</style>
