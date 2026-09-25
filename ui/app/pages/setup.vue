<script setup lang="ts">
import type { PortfolioSettings, SetupKeys } from '~/composables/useHoldingsApi'

const api = useHoldingsApi()

const { data, pending, refresh, error } = await useAsyncData('setup-status', () => api.setupStatus())

const settings = ref<PortfolioSettings | null>(null)
const investorText = ref('')
const portfolioText = ref('')
const cashInput = ref('')
const realizedInput = ref('')
const profilesLoading = ref(true)

const portfolioMessage = ref('')
const portfolioError = ref('')
const portfolioBusy = ref(false)
const trackerMessage = ref('')
const trackerError = ref('')
const trackerBusy = ref(false)

async function loadProfiles() {
  profilesLoading.value = true
  portfolioError.value = ''
  trackerError.value = ''

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
    const message = e instanceof Error ? e.message : String(e)
    portfolioError.value = message
    trackerError.value = message
  } finally {
    profilesLoading.value = false
  }
}

function optionalNumber(value: string) {
  return value === '' ? null : Number(value)
}

async function savePortfolio() {
  portfolioBusy.value = true
  portfolioMessage.value = ''
  portfolioError.value = ''

  try {
    const res = await api.saveSettings({
      portfolio_profile_text: portfolioText.value,
      cash_eur: optionalNumber(cashInput.value),
      realized_gains_ytd_override_eur: optionalNumber(realizedInput.value),
    })
    settings.value = res.settings
    portfolioMessage.value = 'Portfolio saved for the next advisory run.'
  } catch (e) {
    portfolioError.value = e instanceof Error ? e.message : String(e)
  } finally {
    portfolioBusy.value = false
  }
}

async function saveTracker() {
  trackerBusy.value = true
  trackerMessage.value = ''
  trackerError.value = ''

  try {
    const res = await api.saveSettings({
      investor_profile_text: investorText.value,
    })
    settings.value = res.settings
    trackerMessage.value = 'Investor profile saved for the next advisory run.'
  } catch (e) {
    trackerError.value = e instanceof Error ? e.message : String(e)
  } finally {
    trackerBusy.value = false
  }
}

onMounted(loadProfiles)

const keys = computed<SetupKeys | null>(() => data.value?.keys ?? null)

type ServiceStatus = 'Ready' | 'Needs attention' | 'Connected' | 'Not connected — optional'

const services = computed<
  Array<{
    key: 'claude' | 'jev' | 'finnhub' | 'marketaux' | 'alpha_vantage'
    name: string
    status: ServiceStatus
  }>
>(() => {
  if (!keys.value) return []

  return [
    {
      key: 'claude',
      name: 'Claude research',
      status:
        keys.value.claude_oauth && !keys.value.anthropic_api_key_set ? 'Ready' : 'Needs attention',
    },
    {
      key: 'jev',
      name: 'Jev decisions',
      status: keys.value.typesafe ? 'Ready' : 'Needs attention',
    },
    {
      key: 'finnhub',
      name: 'Finnhub',
      status: keys.value.finnhub ? 'Connected' : 'Not connected — optional',
    },
    {
      key: 'marketaux',
      name: 'Marketaux',
      status: keys.value.marketaux ? 'Connected' : 'Not connected — optional',
    },
    {
      key: 'alpha_vantage',
      name: 'Alpha Vantage',
      status: keys.value.alpha_vantage ? 'Connected' : 'Not connected — optional',
    },
  ]
})

const billingConflict = computed(() => keys.value?.anthropic_api_key_set === true)

function serviceTone(status: ServiceStatus) {
  if (status === 'Ready' || status === 'Connected') return 'ready'
  if (status === 'Needs attention') return 'attention'
  return 'optional'
}
</script>

<template>
  <div class="setup-page">
    <header class="setup-heading">
      <p class="setup-kicker">Setup</p>
      <h1>Shape how each book is advised.</h1>
      <p>
        Keep the two mandates distinct. Save either book without changing the other.
      </p>
    </header>

    <div class="book-grid">
      <section class="book-card portfolio-card" aria-labelledby="portfolio-setup-title">
        <div class="book-card-heading">
          <p class="book-label">Owned capital</p>
          <h2 id="portfolio-setup-title">Portfolio</h2>
          <p>
            Adds sizing, trimming, selling, and tax rules for positions you already own.
          </p>
        </div>

        <form class="book-form" @submit.prevent="savePortfolio">
          <p v-if="profilesLoading" class="form-state" role="status">Loading portfolio…</p>
          <p v-if="portfolioMessage" class="form-state success" role="status">
            {{ portfolioMessage }}
          </p>
          <p v-if="portfolioError" class="form-state failure" role="alert">
            {{ portfolioError }}
          </p>

          <label>
            Portfolio profile
            <span class="field-hint">
              Your rules for position size, trimming, selling, and tax decisions.
            </span>
            <textarea
              v-model="portfolioText"
              rows="6"
              :disabled="profilesLoading || portfolioBusy"
              placeholder="Describe how you manage positions once you own them."
            ></textarea>
          </label>

          <div class="money-fields">
            <label>
              Cash reserve (EUR)
              <input
                v-model="cashInput"
                type="number"
                step="0.01"
                :disabled="profilesLoading || portfolioBusy"
                placeholder="0.00"
              >
            </label>
            <label>
              Realised gains elsewhere (EUR)
              <input
                v-model="realizedInput"
                type="number"
                step="0.01"
                :disabled="profilesLoading || portfolioBusy"
                placeholder="0.00"
              >
            </label>
          </div>

          <div class="realised-total" aria-live="polite">
            <span>Calculated realised total</span>
            <strong v-if="settings">
              {{ settings.realized_gains_ytd_eur }} €
              <small>{{ settings.calendar_year }}</small>
            </strong>
            <strong v-else>—</strong>
          </div>

          <button type="submit" :disabled="profilesLoading || portfolioBusy">
            {{ portfolioBusy ? 'Saving portfolio…' : 'Save portfolio' }}
          </button>
        </form>
      </section>

      <section class="book-card tracker-card" aria-labelledby="tracker-setup-title">
        <div class="book-card-heading">
          <p class="book-label">Research pipeline</p>
          <h2 id="tracker-setup-title">Tracker</h2>
          <p>
            The investor profile sets the Tracker buying lens and accompanies all advice.
          </p>
        </div>

        <form class="book-form" @submit.prevent="saveTracker">
          <p v-if="profilesLoading" class="form-state" role="status">Loading investor profile…</p>
          <p v-if="trackerMessage" class="form-state success" role="status">
            {{ trackerMessage }}
          </p>
          <p v-if="trackerError" class="form-state failure" role="alert">
            {{ trackerError }}
          </p>
          <p v-if="!profilesLoading && !investorText" class="empty-warning">
            Your investor profile is empty. Tracker names use a generic buying lens until you
            add one.
          </p>

          <label>
            Investor profile
            <span class="field-hint">
              Who you are, what you want to own, and what makes a name worth buying.
            </span>
            <textarea
              v-model="investorText"
              rows="6"
              :disabled="profilesLoading || trackerBusy"
              placeholder="Describe your goals, horizon, risk tolerance, and buying criteria."
            ></textarea>
          </label>

          <button type="submit" :disabled="profilesLoading || trackerBusy">
            {{ trackerBusy ? 'Saving investor profile…' : 'Save investor profile' }}
          </button>
        </form>
      </section>
    </div>

    <section class="service-panel" aria-labelledby="service-status-title">
      <div class="service-heading">
        <div>
          <p class="setup-kicker">Connections</p>
          <h2 id="service-status-title">Service status</h2>
        </div>
        <button type="button" class="ghost" :disabled="pending" @click="refresh()">
          {{ pending ? 'Checking…' : 'Check again' }}
        </button>
      </div>

      <p v-if="error" class="form-state failure" role="alert">
        Service status could not be checked. {{ error }}
      </p>
      <p v-else-if="pending && !keys" class="form-state" role="status">Checking services…</p>

      <ul v-else class="service-list">
        <li v-for="service in services" :key="service.key">
          <span>{{ service.name }}</span>
          <strong class="service-state" :data-tone="serviceTone(service.status)">
            {{ service.status }}
          </strong>
        </li>
      </ul>

      <p v-if="billingConflict" class="billing-warning" role="alert">
        Anthropic API billing is configured. Claude research should use subscription access.
      </p>
    </section>
  </div>
</template>

<style scoped>
.setup-page {
  max-width: 86rem;
  margin: 0 auto;
}

.setup-heading {
  max-width: 48rem;
  margin-bottom: 2rem;
}

.setup-kicker,
.book-label {
  margin: 0 0 0.5rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.setup-kicker {
  color: var(--mute);
}

.setup-heading h1 {
  margin: 0;
  font-size: clamp(2rem, 5vw, 3.8rem);
  letter-spacing: -0.045em;
  line-height: 1;
}

.setup-heading > p:last-child {
  margin: 0.9rem 0 0;
  color: var(--mute);
  font-size: 1.05rem;
  line-height: 1.5;
}

.book-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: clamp(1rem, 2.5vw, 2rem);
  align-items: stretch;
}

.book-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: clamp(1.25rem, 3vw, 2rem);
  border: 1px solid var(--card-line);
  background: var(--card-background);
  color: var(--card-ink);
  box-shadow: 0 18px 45px rgba(24, 35, 30, 0.06);
}

.portfolio-card {
  --card-ink: #17231e;
  --card-muted: #58675f;
  --card-accent: #2b6751;
  --card-accent-strong: #174634;
  --card-line: #bfcdbf;
  --card-background: rgba(248, 248, 239, 0.9);
  border-left: 0.45rem solid var(--card-accent);
}

.tracker-card {
  --card-ink: #17233a;
  --card-muted: #59667b;
  --card-accent: #4167a6;
  --card-accent-strong: #25487f;
  --card-line: #c0cee2;
  --card-background: rgba(245, 248, 253, 0.92);
  border-top: 0.45rem solid var(--card-accent);
  border-radius: 0 0 1.25rem 1.25rem;
}

.book-label {
  color: var(--card-accent);
}

.book-card h2 {
  margin: 0;
  color: var(--card-accent-strong);
  font-size: clamp(2rem, 4vw, 3.25rem);
  letter-spacing: -0.045em;
  line-height: 1;
}

.book-card-heading > p:last-child {
  min-height: 3rem;
  margin: 0.8rem 0 0;
  color: var(--card-muted);
  line-height: 1.5;
}

.book-form {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 1rem;
  margin-top: 1.5rem;
}

.book-form label {
  color: var(--card-ink);
  font-weight: 600;
}

.field-hint {
  min-height: 2.2rem;
  color: var(--card-muted);
  font-size: 0.84rem;
  font-weight: 400;
  line-height: 1.35;
}

.book-form textarea,
.book-form input {
  width: 100%;
  border-color: var(--card-line);
}

.book-form textarea {
  min-height: 9.5rem;
  padding: 0.65rem 0.75rem;
  color: var(--card-ink);
  font: inherit;
  line-height: 1.45;
  resize: vertical;
}

.book-form textarea:focus-visible,
.book-form input:focus-visible,
.book-form button:focus-visible {
  outline-color: color-mix(in srgb, var(--card-accent) 42%, transparent);
}

.book-form button {
  align-self: flex-start;
  margin-top: auto;
  border-color: var(--card-accent);
  background: var(--card-accent);
}

.money-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.85rem;
}

.realised-total {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.8rem 0;
  border-block: 1px solid var(--card-line);
  color: var(--card-muted);
}

.realised-total strong {
  color: var(--card-accent-strong);
  white-space: nowrap;
}

.realised-total small {
  margin-left: 0.25rem;
  color: var(--card-muted);
  font-weight: 400;
}

.form-state,
.empty-warning,
.billing-warning {
  margin: 0;
  padding: 0.7rem 0.8rem;
  border: 1px solid var(--line);
  line-height: 1.4;
}

.form-state {
  color: var(--mute);
}

.success {
  border-color: #86ae98;
  background: #edf6f0;
  color: #245c3e;
}

.failure,
.billing-warning {
  border-color: #c99595;
  background: #faeeee;
  color: #762929;
}

.empty-warning {
  border-color: #cfad75;
  background: #fff7e8;
  color: #704915;
}

.service-panel {
  max-width: 60rem;
  margin: 2rem auto 0;
  padding: clamp(1.1rem, 2.5vw, 1.6rem);
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.55);
}

.service-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.service-heading h2 {
  margin: 0;
  font-size: 1.3rem;
}

.service-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin: 1rem 0 0;
  padding: 0;
  list-style: none;
}

.service-list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  min-width: 0;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--line);
}

.service-list li:nth-child(odd) {
  padding-right: 1.25rem;
}

.service-list li:nth-child(even) {
  padding-left: 1.25rem;
  border-left: 1px solid var(--line);
}

.service-state {
  font-size: 0.86rem;
  font-weight: 600;
  text-align: right;
}

.service-state[data-tone='ready'] {
  color: var(--ok);
}

.service-state[data-tone='attention'] {
  color: var(--bad);
}

.service-state[data-tone='optional'] {
  color: var(--mute);
  font-weight: 400;
}

.billing-warning {
  margin-top: 1rem;
}

@media (max-width: 800px) {
  .book-grid,
  .service-list {
    grid-template-columns: 1fr;
  }

  .book-card-heading > p:last-child,
  .field-hint {
    min-height: 0;
  }

  .service-list li:nth-child(odd),
  .service-list li:nth-child(even) {
    padding-inline: 0;
    border-left: 0;
  }
}

@media (max-width: 480px) {
  .money-fields {
    grid-template-columns: 1fr;
  }

  .service-heading,
  .service-list li,
  .realised-total {
    align-items: stretch;
    flex-direction: column;
  }

  .service-state {
    text-align: left;
  }
}
</style>
