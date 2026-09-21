<script setup lang="ts">
/**
 * The one header (0019). Before the tracker existed each page hand-rolled its own hero, which
 * the tabs made untenable — they have to agree on where they are.
 *
 * Ingest / Run / Force stay **inline buttons, not a dropdown**: they are the operator's whole
 * daily interaction with this thing, and burying a one-click action behind a menu to save a
 * row of header is a bad trade. They are global rather than per-tab because one combined run
 * covers both books.
 */
const route = useRoute()
const { refreshing, advising, busy, canRun, ingestNow, runNow } = useGlobalOps()

const tabs = [
  { to: '/', label: 'Portfolio', hint: 'What you own' },
  { to: '/tracker', label: 'Tracker', hint: 'What you are considering' },
]

const isActive = (to: string) => (to === '/' ? route.path === '/' : route.path.startsWith(to))
</script>

<template>
  <main class="shell">
    <header class="hero">
      <div class="hero-top">
        <div>
          <p class="brand">Tradai</p>
          <nav class="tabs" aria-label="Books">
            <NuxtLink
              v-for="tab in tabs"
              :key="tab.to"
              :to="tab.to"
              class="tab"
              :class="{ active: isActive(tab.to) }"
              :aria-current="isActive(tab.to) ? 'page' : undefined"
            >
              {{ tab.label }}
              <span class="tab-hint">{{ tab.hint }}</span>
            </NuxtLink>
          </nav>
        </div>
        <div class="header-actions">
          <button type="button" :disabled="busy" @click="ingestNow()">
            {{ refreshing ? 'Ingesting…' : 'Ingest now' }}
          </button>
          <button type="button" class="ghost" :disabled="!canRun" @click="runNow(false)">
            {{ advising ? 'Running…' : 'Run now' }}
          </button>
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
      </div>
    </header>

    <slot />
  </main>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 0.5rem;
  margin: 1rem 0 0;
  border-bottom: 1px solid var(--line);
}

.tab {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  padding: 0.5rem 0.9rem;
  color: var(--mute);
  text-decoration: none;
  border: 1px solid transparent;
  border-bottom: none;
  margin-bottom: -1px;
}

.tab:hover {
  color: var(--accent);
}

.tab.active {
  color: var(--accent);
  font-weight: 600;
  background: var(--panel);
  border-color: var(--line);
}

.tab-hint {
  font-size: 0.8rem;
  font-weight: 400;
  color: var(--mute);
}
</style>
