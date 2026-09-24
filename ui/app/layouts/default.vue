<script setup lang="ts">
/**
 * Portfolio and Tracker are separate product modules. The chrome above them is deliberately
 * neutral: ingest and advisory still operate on both books in one run, while the module
 * switcher changes the operator's working context.
 */
const route = useRoute()
const { refreshing, advising, busy, canRun, ingestNow, runNow } = useGlobalOps()

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
        <span class="utility-label">Shared workspace</span>
      </div>

      <nav class="module-switcher" aria-label="Modules">
        <span class="switcher-label">Module</span>
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

.utility-label,
.tools-label,
.switcher-label {
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

.switcher-label {
  align-self: center;
  margin-right: 0.25rem;
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
  align-items: center;
  justify-content: flex-end;
  gap: 0.45rem;
}

.shared-tools button,
.shared-tools a.ghost {
  white-space: nowrap;
}

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

  .switcher-label,
  .tools-label {
    display: none;
  }

  .module-link {
    flex: 1;
    min-width: 0;
  }
}
</style>
