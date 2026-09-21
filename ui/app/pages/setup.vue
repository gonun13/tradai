<script setup lang="ts">
import type { PortfolioSettings, SetupKeys, SetupSchedule } from '~/composables/useHoldingsApi'

const api = useHoldingsApi()
const { message: opsMessage, error: opsError } = useGlobalOps()

const { data, pending, refresh, error } = await useAsyncData('setup-status', () => api.setupStatus())

// --- 0019: two free-text profiles Claude and Jev read verbatim on every run. The investor
// profile judges names you don't own (the Tracker) and travels as context everywhere; the
// portfolio profile carries the rules for what you already hold.
const settings = ref<PortfolioSettings | null>(null)
const investorText = ref('')
const portfolioText = ref('')
const cashInput = ref('')
const realizedInput = ref('')
const profileMessage = ref('')
const profileError = ref('')
const profileBusy = ref(false)

async function loadProfile() {
  try {
    const res = await api.getSettings()
    settings.value = res.settings
    investorText.value = res.settings.investor_profile_text ?? ''
    portfolioText.value = res.settings.portfolio_profile_text ?? ''
    cashInput.value = res.settings.cash_eur === null ? '' : String(res.settings.cash_eur)
    realizedInput.value =
      res.settings.realized_gains_ytd_override_eur === null
        ? ''
        : String(res.settings.realized_gains_ytd_override_eur)
  } catch (e) {
    profileError.value = e instanceof Error ? e.message : String(e)
  }
}

async function saveProfile() {
  profileBusy.value = true
  profileMessage.value = ''
  profileError.value = ''
  try {
    const res = await api.saveSettings({
      investor_profile_text: investorText.value,
      portfolio_profile_text: portfolioText.value,
      cash_eur: cashInput.value === '' ? null : Number(cashInput.value),
      realized_gains_ytd_override_eur: realizedInput.value === '' ? null : Number(realizedInput.value),
    })
    settings.value = res.settings
    profileMessage.value = 'Saved — Claude and Jev use this on the next run.'
  } catch (e) {
    profileError.value = e instanceof Error ? e.message : String(e)
  } finally {
    profileBusy.value = false
  }
}

onMounted(loadProfile)

const keys = computed<SetupKeys | null>(() => data.value?.keys ?? null)
const schedule = computed<SetupSchedule | null>(() => data.value?.schedule ?? null)
const docs = computed(() => data.value?.docs ?? {})
const warnings = computed(() => data.value?.warnings ?? [])

const rows: Array<{ key: keyof SetupKeys; label: string; required: boolean; docKey: string }> = [
  { key: 'finnhub', label: 'FINNHUB_API_KEY', required: false, docKey: 'finnhub' },
  { key: 'marketaux', label: 'MARKETAUX_API_TOKEN', required: false, docKey: 'marketaux' },
  { key: 'typesafe', label: 'TYPESAFE_API_KEY', required: true, docKey: 'typesafe' },
  { key: 'claude_oauth', label: 'CLAUDE_CODE_OAUTH_TOKEN', required: true, docKey: 'claude' },
  { key: 'fmp', label: 'FMP_API_KEY (optional)', required: false, docKey: 'fmp' },
]

function statusLabel(ok: boolean | undefined, required: boolean) {
  if (ok) {
    return 'set'
  }
  return required ? 'missing' : 'optional — unset'
}
</script>

<template>
  <div>
    <p class="tag">Setup — profiles &amp; keys</p>

    <p class="note">
      Keys live in the host <code>.env</code> (never typed into the browser). Edit, then
      <code>bin/up -d</code>. {{ docs.env_file }}
    </p>

    <p v-if="opsMessage" class="ok">{{ opsMessage }}</p>
    <p v-if="opsError || error" class="bad">{{ opsError || error }}</p>
    <p v-for="(w, i) in warnings" :key="i" class="bad">{{ w }}</p>

    <section class="panel">
      <h1>Profiles</h1>
      <p class="mute">
        Two fields, written once in your own words. Claude and Jev read them verbatim on every
        run and reason with them; nothing in Tradai validates or overrides their calls.
      </p>
      <p v-if="profileMessage" class="ok">{{ profileMessage }}</p>
      <p v-if="profileError" class="bad">{{ profileError }}</p>

      <p v-if="!investorText" class="mute warn">
        Your investor profile is empty, so tracked names are judged by a generic default.
        Write it before trusting the first Tracker recommendations.
      </p>

      <div class="form">
        <label class="wide">
          Investor profile — who you are, and what makes a name worth <em>buying</em>
          <span class="sub">
            Judges the <NuxtLink to="/tracker">Tracker</NuxtLink>, and travels as context on
            every call. Objective, time horizon, risk tolerance, what you want to own and why.
          </span>
          <textarea
            v-model="investorText"
            rows="4"
            placeholder="e.g. Compounding over 5+ years, moderate risk. I want businesses I can explain in a sentence, bought at a price that doesn't already assume the good outcome. No turnarounds I can't check."
          ></textarea>
        </label>

        <label class="wide">
          Portfolio profile — the rules for what you already <em>own</em>
          <span class="sub">
            Judges the <NuxtLink to="/">Portfolio</NuxtLink>. Position sizing, trimming, when a
            sell is warranted, tax situation.
          </span>
          <textarea
            v-model="portfolioText"
            rows="4"
            placeholder="e.g. Portugal tax resident — prefer not to realise a loss unless it offsets a gain this year or the position looks unlikely to recover within a couple of years. A buy doesn't require a sell; the cash reserve is there to be used."
          ></textarea>
        </label>

        <label>
          Cash reserve (EUR)
          <input v-model="cashInput" type="number" step="0.01" placeholder="e.g. 3500" >
        </label>
        <label>
          Gains realised elsewhere this year (EUR)
          <input v-model="realizedInput" type="number" step="0.01" placeholder="optional" >
        </label>
        <div class="wide actions">
          <button :disabled="profileBusy" @click="saveProfile">Save</button>
          <span v-if="settings" class="mute">
            Realised this year: {{ settings.realized_gains_ytd_eur }} € ({{ settings.calendar_year }})
          </span>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="list-head">
        <h1>API keys &amp; tokens</h1>
        <button type="button" class="ghost" :disabled="pending" @click="refresh()">Reload</button>
      </div>
      <p v-if="pending" class="mute">Loading…</p>
      <table v-else-if="keys">
        <thead>
          <tr>
            <th>Variable</th>
            <th>Status</th>
            <th>Where</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.key">
            <td><code>{{ row.label }}</code></td>
            <td :class="{ ok: keys[row.key], bad: row.required && !keys[row.key] }">
              {{ statusLabel(keys[row.key], row.required) }}
            </td>
            <td class="mute">{{ docs[row.docKey] || '—' }}</td>
          </tr>
          <tr>
            <td><code>ANTHROPIC_API_KEY</code></td>
            <td :class="{ bad: keys.anthropic_api_key_set, ok: !keys.anthropic_api_key_set }">
              {{ keys.anthropic_api_key_set ? 'SET — unset it' : 'unset (good)' }}
            </td>
            <td class="mute">{{ docs.claude }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="schedule" class="panel">
      <h1>Daily advisory schedule</h1>
      <p class="mute">
        After US regular close (<code>{{ schedule.us_close }}</code>
        {{ schedule.timezone }}), wait
        <strong>{{ schedule.after_us_close_minutes }}</strong> minutes → fire at
        <strong>{{ schedule.fire_at_et }} ET</strong>. Weekends and listed NYSE holidays are skipped.
      </p>
      <p class="mute">
        Cache window: {{ schedule.interval_seconds }}s · scenario rounds:
        {{ schedule.max_scenario_rounds }} · override via
        <code>ADVISORY_AFTER_US_CLOSE_MINUTES</code> in <code>.env</code>.
      </p>
    </section>

    <section class="panel">
      <h1>Alert policy</h1>
      <p class="mute">
        Unread in-app alerts are raised when a recommendation action is
        <strong>buy</strong> or <strong>sell</strong> (any horizon), or when Claude reports a
        holding's thesis as broken. That covers a tracked name Jev says to buy now. Ack clears
        unread. Hold / watch / drop stay on the recommendations panel only.
      </p>
    </section>
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
</style>
