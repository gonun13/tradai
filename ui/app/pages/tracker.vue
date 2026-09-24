<script setup lang="ts">
import type {
  Recommendation,
  SymbolHit,
  TrackerEntry,
  TrackerInput,
} from '~/composables/useHoldingsApi'

/**
 * The tracker (0019): names of interest the operator does not own. Ingested and researched
 * on the same daily cadence as the portfolio, so what you see here is what the agents saw.
 */
const api = useHoldingsApi()
const { moneyOrDash, pctOrDash, fmtNum } = useFormat()
const { message: opsMessage, error: opsError, advising } = useGlobalOps()

const message = ref('')
const error = ref('')
const busy = ref(false)
const advisingSingle = ref<string | null>(null)

const { data, pending, refresh, error: loadError } = await useAsyncData('tracker', () =>
  api.listTracker(),
)
const entries = computed<TrackerEntry[]>(() => data.value?.tracker ?? [])

const { data: advisoryData, refresh: refreshAdvisory } = await useAsyncData('tracker-advisory', () =>
  api.latestRun().catch(() => ({ ok: true, run: null, recommendations: [] as Recommendation[] })),
)

// --- Search ---------------------------------------------------------------------------
// Local instruments rank first; Yahoo and Finnhub fill in behind them. A vendor being down
// returns a warning, never an error — manual symbol entry below always stays open.
const query = ref('')
const results = ref<SymbolHit[]>([])
const searchWarnings = ref<string[]>([])
const searching = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(query, (q) => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  if (q.trim().length < 2) {
    results.value = []
    searchWarnings.value = []
    return
  }
  searchTimer = setTimeout(() => runSearch(q), 300)
})

async function runSearch(q: string) {
  searching.value = true
  try {
    const res = await api.searchInstruments(q)
    results.value = res.results
    searchWarnings.value = res.warnings
  } catch (e) {
    searchWarnings.value = [e instanceof Error ? e.message : String(e)]
  } finally {
    searching.value = false
  }
}

// --- Add form -------------------------------------------------------------------------
const emptyForm = (): TrackerInput => ({
  symbol: '',
  name: '',
  note: '',
  isin: '',
  mic: '',
  currency: '',
  kind: 'equity',
  region: '',
})
const form = reactive<TrackerInput>(emptyForm())
const formOpen = ref(false)

function pick(hit: SymbolHit) {
  Object.assign(form, {
    symbol: hit.symbol,
    name: hit.name ?? '',
    note: '',
    isin: hit.isin ?? '',
    mic: hit.mic ?? '',
    // Neither search source returns currency; the API resolves it from the region unless
    // you say otherwise, and the first ingest confirms it from the quote.
    currency: hit.currency ?? '',
    kind: hit.kind ?? 'equity',
    region: hit.region ?? '',
  })
  formOpen.value = true
  message.value = ''
  error.value = ''
}

function startManual() {
  Object.assign(form, emptyForm())
  if (query.value.trim()) {
    form.symbol = query.value.trim().toUpperCase()
  }
  formOpen.value = true
  message.value = ''
  error.value = ''
}

function cancelAdd() {
  formOpen.value = false
  Object.assign(form, emptyForm())
}

async function submit() {
  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const payload: TrackerInput = {
      symbol: form.symbol.trim().toUpperCase(),
      name: form.name?.trim() || null,
      note: form.note?.trim() || null,
      isin: form.isin?.trim() || null,
      mic: form.mic?.trim() || null,
      currency: form.currency?.trim().toUpperCase() || null,
      kind: form.kind || 'equity',
      region: form.region || null,
    }
    await api.addTracker(payload)
    message.value = `${payload.symbol} tracked — it ingests on the next "Ingest now" and is researched on the next run.`
    cancelAdd()
    query.value = ''
    results.value = []
    await refresh()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

// --- Row actions ----------------------------------------------------------------------
const editingId = ref<number | null>(null)
const editNote = ref('')

function startEditNote(entry: TrackerEntry) {
  editingId.value = entry.id
  editNote.value = entry.note ?? ''
}

async function saveNote(entry: TrackerEntry) {
  busy.value = true
  error.value = ''
  try {
    await api.updateTracker(entry.id, { note: editNote.value.trim() || null })
    editingId.value = null
    await refresh()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function removeEntry(entry: TrackerEntry) {
  if (!confirm(`Stop tracking ${entry.symbol}? Its note is deleted with it.`)) {
    return
  }
  busy.value = true
  error.value = ''
  try {
    await api.removeTracker(entry.id)
    message.value = `${entry.symbol} removed from the tracker.`
    await refresh()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = false
  }
}

async function runSingle(entry: TrackerEntry) {
  advisingSingle.value = entry.symbol
  error.value = ''
  try {
    const result = await api.runAdvisorySingle(entry.symbol, { force: false })
    const runId = result.run_id
    if (!runId) {
      throw new Error((result.worker as { error?: string })?.error || 'Failed to start')
    }
    if (result.from_cache) {
      message.value = `Used cached run #${runId} for ${entry.symbol}`
      await refreshAdvisory()
      return
    }
    message.value = `Run #${runId} started for ${entry.symbol}...`
    // Poll for completion
    await pollSingleRun(runId)
    await Promise.all([refresh(), refreshAdvisory()])
  } catch (e) {
    error.value = errText(e)
  } finally {
    advisingSingle.value = null
  }
}

async function pollSingleRun(runId: number) {
  const terminal = new Set(['succeeded', 'failed', 'partial'])
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 2000))
    try {
      const snap = await api.getRun(runId)
      if (terminal.has(snap.run.status)) {
        message.value = `Run #${runId} ${snap.run.status} — ${snap.recommendations.length} recommendation(s)`
        return
      }
    } catch {
      // Run might not be ready yet, continue polling
    }
  }
  message.value = `Run #${runId} still in progress — reload later`
}

function errText(e: unknown) {
  const data = (e as { data?: { error?: string } })?.data
  return data?.error ?? (e instanceof Error ? e.message : String(e))
}

const noQuoteYet = computed(() => entries.value.filter((e) => e.quote === null))
</script>

<template>
  <div class="module-page tracker-page">
    <section class="module-hero" aria-labelledby="tracker-title">
      <div>
        <p class="module-kicker">Research pipeline · Investor profile</p>
        <h1 id="tracker-title" class="module-title">Tracker</h1>
        <p class="module-description">
          The workspace for names you are considering: explore the entry case, monitor momentum
          and decide what deserves capital next.
        </p>
      </div>
      <div class="module-metrics" aria-label="Tracker summary">
        <div class="module-metric">
          <span class="module-metric-label">Tracked names</span>
          <strong>{{ entries.length }}</strong>
        </div>
        <div class="module-metric">
          <span class="module-metric-label">Awaiting ingest</span>
          <strong>{{ noQuoteYet.length }}</strong>
        </div>
        <div class="module-metric">
          <span class="module-metric-label">Decision horizon</span>
          <strong>1 · 3 · 6m</strong>
        </div>
      </div>
    </section>

    <p class="module-guide">
      Tracked names share quote, bars and technical ingestion with holdings, then join the same
      daily Claude/Jev run under your <NuxtLink to="/setup">investor profile</NuxtLink>. News is
      intentionally holdings-only. Recording a purchase hands the name to Portfolio.
    </p>

    <p v-if="opsMessage || message" class="ok">{{ opsMessage || message }}</p>
    <p v-if="opsError || error || loadError" class="bad">{{ opsError || error || loadError }}</p>

    <section class="panel">
      <div class="list-head">
        <h1>Find a name</h1>
        <button type="button" class="ghost" @click="startManual">Enter a ticker manually</button>
      </div>
      <label class="wide">
        Search by company name or ticker
        <input
          v-model="query"
          type="search"
          autocomplete="off"
          placeholder="e.g. Airbus, vanguard ftse, NVDA"
        >
      </label>
      <p v-if="searching" class="mute">Searching…</p>
      <p v-for="(w, i) in searchWarnings" :key="i" class="mute warn">{{ w }}</p>

      <ul v-if="results.length" class="hits">
        <li v-for="hit in results" :key="`${hit.source}-${hit.symbol}`">
          <button type="button" class="ghost hit-btn" @click="pick(hit)">
            <strong>{{ hit.symbol }}</strong>
            <span class="sub">
              {{ hit.name || '—' }}
              <template v-if="hit.exchange"> · {{ hit.exchange }}</template>
              · {{ hit.kind }}
              <template v-if="hit.known"> · already in your book</template>
            </span>
          </button>
        </li>
      </ul>
      <p v-else-if="query.trim().length >= 2 && !searching" class="mute">
        Nothing found — the ticker may need a venue suffix (<code>.PA</code>, <code>.DE</code>).
        You can add it by hand.
      </p>
    </section>

    <section v-if="formOpen" class="panel">
      <h1>Track {{ form.symbol || 'a name' }}</h1>
      <p class="mute">Correct anything search got wrong — these fields decide how it is ingested.</p>
      <form class="form" @submit.prevent="submit">
        <label>
          Symbol
          <input v-model="form.symbol" required autocomplete="off" placeholder="AIR.PA">
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
            <option value="">infer from symbol</option>
            <option value="eu">eu</option>
            <option value="us">us</option>
          </select>
        </label>
        <label>
          Currency
          <input v-model="form.currency" maxlength="3" placeholder="auto">
        </label>
        <label>
          MIC / exchange
          <input v-model="form.mic" placeholder="XPAR / XNAS" autocomplete="off">
        </label>
        <label>
          ISIN
          <input v-model="form.isin" autocomplete="off">
        </label>
        <label class="wide">
          Why you are watching it
          <textarea
            v-model="form.note"
            rows="2"
            placeholder="e.g. Want the defence exposure but not at 30x. Revisit after H1."
          ></textarea>
        </label>
        <div class="wide actions">
          <button type="submit" :disabled="busy">Track it</button>
          <button type="button" class="ghost" @click="cancelAdd">Cancel</button>
        </div>
      </form>
    </section>

    <section v-if="noQuoteYet.length" class="panel warn">
      <h1>Not ingested yet</h1>
      <p class="mute">
        No quote on file for these — run <strong>Ingest now</strong>, and check the ticker if it
        stays empty.
      </p>
      <ul class="missing">
        <li v-for="e in noQuoteYet" :key="e.id"><strong>{{ e.symbol }}</strong></li>
      </ul>
    </section>

    <section class="panel">
      <div class="list-head">
        <h1>Tracked names</h1>
        <button type="button" class="ghost" :disabled="pending" @click="refresh()">Reload</button>
      </div>
      <p v-if="pending" class="mute">Loading…</p>
      <p v-else-if="!entries.length" class="mute">
        Nothing tracked yet. Search above to add the first name.
      </p>
      <table v-else>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Name</th>
            <th>Price</th>
            <th>Price (EUR)</th>
            <th>1m</th>
            <th>3m</th>
            <th>6m</th>
            <th>RSI14</th>
            <th>Note</th>
            <th>Added</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in entries" :key="e.id">
            <td><strong>{{ e.symbol }}</strong></td>
            <td class="mute">{{ e.name || '—' }}</td>
            <td>{{ e.quote ? moneyOrDash(e.quote.price, e.quote.currency) : '—' }}</td>
            <td>{{ moneyOrDash(e.price_eur) }}</td>
            <td>{{ pctOrDash(e.technicals?.return_1m_pct) }}</td>
            <td>{{ pctOrDash(e.technicals?.return_3m_pct) }}</td>
            <td>{{ pctOrDash(e.technicals?.return_6m_pct) }}</td>
            <td>{{ fmtNum(e.technicals?.rsi_14, 1) }}</td>
            <td>
              <template v-if="editingId === e.id">
                <input v-model="editNote" class="note-input" autocomplete="off">
                <button type="button" class="ghost icon-btn" :disabled="busy" @click="saveNote(e)">
                  Save
                </button>
                <button type="button" class="ghost icon-btn" @click="editingId = null">Cancel</button>
              </template>
              <span v-else class="mute">{{ e.note || '—' }}</span>
            </td>
            <td class="mute">{{ (e.added_at || '').slice(0, 10) }}</td>
            <td class="row-actions">
              <button
                v-if="editingId !== e.id"
                type="button"
                class="ghost icon-btn"
                @click="startEditNote(e)"
              >
                Note
              </button>
              <NuxtLink
                class="ghost icon-btn"
                title="Record an acquisition — this moves it to the Portfolio module"
                :to="`/portfolio?buy=${encodeURIComponent(e.symbol)}`"
              >
                Bought
              </NuxtLink>
              <button
                type="button"
                class="ghost icon-btn"
                :disabled="advisingSingle === e.symbol || busy"
                @click="runSingle(e)"
                title="Run agent on this symbol only"
              >
                Run
              </button>
              <button type="button" class="ghost icon-btn" :disabled="busy" @click="removeEntry(e)">
                Remove
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <RecommendationsPanel
      book="tracker"
      :run="advisoryData?.run ?? null"
      :recommendations="advisoryData?.recommendations ?? []"
      :busy="advising"
      @reload="refreshAdvisory()"
    />
  </div>
</template>

<style scoped>
textarea {
  font: inherit;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--ink);
  resize: vertical;
}

.hits {
  list-style: none;
  margin: 0.75rem 0 0;
  padding: 0;
}
.hits li { margin: 0.3rem 0; }
.hit-btn {
  width: 100%;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.note-input { width: 100%; min-width: 8rem; }

.row-actions {
  display: flex;
  gap: 0.3rem;
  flex-wrap: wrap;
}

.actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}
</style>
