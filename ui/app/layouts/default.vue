<script setup lang="ts">
/**
 * Portfolio and Tracker are separate product modules. The chrome above them is deliberately
 * neutral: ingest and advisory still operate on both books in one run, while the module
 * switcher changes the operator's working context.
 */
const route = useRoute()
const api = useHoldingsApi()
const { refreshing, advising, busy, canRun, flash, dismissFlash, ingestNow, runNow } = useGlobalOps()

const { data: ingestStatus } = await useAsyncData('ops-ingest-status', () =>
  api.ingestReport().catch(() => null),
)
const { data: advisoryStatus } = await useAsyncData('ops-advisory-status', () =>
  api.latestRun().catch(() => null),
)

const now = useState('ops-clock', () => Date.now())
let clock: ReturnType<typeof setInterval> | null = null

function elapsedHours(timestamp: string | null | undefined) {
  if (!timestamp) return 'Never run'

  const then = Date.parse(timestamp)
  if (!Number.isFinite(then)) return 'Time unavailable'

  const hours = Math.max(0, now.value - then) / 3_600_000
  return `${hours.toFixed(1)} hours ago`
}

const lastIngest = computed(() => elapsedHours(ingestStatus.value?.updated_at))
const lastAdvisory = computed(() => {
  const run = advisoryStatus.value?.run
  return elapsedHours(run?.started_at)
})

onMounted(() => {
  now.value = Date.now()
  clock = setInterval(() => {
    now.value = Date.now()
  }, 60_000)
})

onUnmounted(() => {
  if (clock) clearInterval(clock)
})

const moduleName = computed<'portfolio' | 'tracker' | 'neutral'>(() => {
  if (route.path.startsWith('/tracker')) return 'tracker'
  if (route.path.startsWith('/portfolio')) return 'portfolio'
  return 'neutral'
})

const modules = [
  { to: '/portfolio', label: 'Portfolio', hint: 'Owned capital' },
  { to: '/tracker', label: 'Tracker', hint: 'Research pipeline' },
] as const

const isActive = (to: string) => route.path === to || route.path.startsWith(`${to}/`)
</script>

<template>
  <div class="app-frame" :data-module="moduleName">
    <header class="utility-bar">
      <div class="utility-identity">
        <NuxtLink class="wordmark" to="/" aria-label="Tradai home">Tradai</NuxtLink>
      </div>

      <nav class="module-switcher" aria-label="Book navigation">
        <NuxtLink
          v-for="item in modules"
          :key="item.to"
          :to="item.to"
          class="module-link"
          :class="{ active: isActive(item.to) }"
          :aria-current="isActive(item.to) ? 'page' : undefined"
        >
          <span>{{ item.label }}</span>
          <small>{{ item.hint }}</small>
        </NuxtLink>
      </nav>

      <div class="shared-tools" aria-label="Shared tools">
        <span class="tools-label">Both books</span>
        <div class="operation-control">
          <button type="button" :disabled="busy" @click="ingestNow()">
            {{ refreshing ? 'Ingesting…' : 'Ingest now' }}
          </button>
          <small class="operation-age">{{ lastIngest }}</small>
        </div>
        <div class="operation-control">
          <button type="button" class="ghost" :disabled="!canRun" @click="runNow(false)">
            {{ advising ? 'Running…' : 'Run now' }}
          </button>
          <small class="operation-age">{{ lastAdvisory }}</small>
        </div>
        <button
          type="button"
          class="ghost"
          title="Bypass daily cache — spends Claude/Jev tokens"
          :disabled="!canRun"
          @click="runNow(true)"
        >
          Force run
        </button>
        <NuxtLink class="ghost" to="/setup">Setup</NuxtLink>
        <AlertsMenu />
      </div>
      <div
        v-if="flash"
        class="operation-flash"
        :data-status="flash.status"
        :role="flash.status === 'error' ? 'alert' : 'status'"
        aria-live="polite"
      >
        <span>{{ flash.text }}</span>
        <button type="button" aria-label="Dismiss operation message" @click="dismissFlash">×</button>
      </div>
    </header>

    <main class="module-canvas">
      <div class="shell">
        <slot />
      </div>
    </main>
  </div>
</template>

<style scoped>
.utility-bar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: grid;
  grid-template-columns: auto minmax(19rem, 1fr) auto;
  align-items: center;
  gap: 1.25rem;
  min-height: 5rem;
  padding: 0.75rem clamp(1rem, 3vw, 2.5rem);
  border-bottom: 1px solid #d8d7d2;
  background: rgba(250, 249, 246, 0.96);
  color: #242925;
  backdrop-filter: blur(14px);
}

.utility-identity {
  display: flex;
  flex-direction: column;
}

.wordmark {
  color: #242925;
  font-size: 1.45rem;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1;
  text-decoration: none;
}

.tools-label {
  color: #737871;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.module-switcher {
  display: flex;
  align-items: stretch;
  justify-self: start;
  gap: 0.35rem;
}

.module-link {
  display: flex;
  flex-direction: column;
  min-width: 8.8rem;
  padding: 0.45rem 0.75rem;
  border: 1px solid transparent;
  color: #545b55;
  line-height: 1.1;
  text-decoration: none;
}

.module-link small {
  margin-top: 0.2rem;
  color: #7a807a;
  font-size: 0.72rem;
}

.module-link:hover {
  border-color: #c7cac5;
}

.module-link.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-strong);
  box-shadow: inset 3px 0 0 var(--accent);
}

.shared-tools {
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  gap: 0.45rem;
}

.tools-label {
  align-self: center;
}

.shared-tools button,
.shared-tools a.ghost {
  white-space: nowrap;
}

.operation-control {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.2rem;
}

.operation-age {
  color: #737871;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.63rem;
  line-height: 1.2;
  text-align: center;
  white-space: nowrap;
}

.operation-flash {
  position: absolute;
  top: calc(100% + 0.5rem);
  right: clamp(1rem, 3vw, 2.5rem);
  display: flex;
  gap: 0.75rem;
  align-items: center;
  max-width: min(28rem, calc(100vw - 2rem));
  padding: 0.6rem 0.75rem;
  border: 1px solid #587360;
  background: #f3faf5;
  color: #19482c;
  box-shadow: 0 0.75rem 2rem rgba(20, 30, 24, 0.18);
  font-size: 0.9rem;
}

.operation-flash[data-status="warning"] { border-color: #9b702e; background: #fff8e9; color: #70460c; }
.operation-flash[data-status="error"] { border-color: #9b4545; background: #fff1f1; color: #712323; }
.operation-flash button { padding: 0 0.25rem; border: 0; background: transparent; color: currentColor; font-size: 1.15rem; line-height: 1; }

@media (max-width: 1080px) {
  .utility-bar {
    grid-template-columns: auto 1fr;
  }

  .shared-tools {
    grid-column: 1 / -1;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}

@media (max-width: 660px) {
  .utility-bar {
    position: static;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 0.75rem;
  }

  .utility-identity {
    flex-direction: row;
    align-items: baseline;
    gap: 0.6rem;
  }

  .module-switcher {
    width: 100%;
  }

  .tools-label {
    display: none;
  }

  .module-link {
    flex: 1;
    min-width: 0;
  }
}
</style>
