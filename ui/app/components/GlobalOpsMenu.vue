<script setup lang="ts">
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const { refreshing, advising, busy, canRun, ingestNow, runNow } = useGlobalOps()

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

async function onIngest() {
  close()
  await ingestNow()
}

async function onRun(force: boolean) {
  close()
  await runNow(force)
}

function onDocClick(event: MouseEvent) {
  if (!open.value || !root.value) {
    return
  }
  if (!root.value.contains(event.target as Node)) {
    close()
  }
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    close()
  }
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="ops-menu">
    <button
      type="button"
      class="ops-trigger"
      :aria-expanded="open"
      aria-haspopup="true"
      aria-label="Global operations"
      @click.stop="toggle"
    >
      Ops
      <span class="caret" aria-hidden="true">▾</span>
    </button>

    <div v-if="open" class="panel-pop" role="menu" aria-label="Global operations">
      <button type="button" role="menuitem" :disabled="busy" @click="onIngest">
        {{ refreshing ? 'Ingesting…' : 'Ingest now' }}
      </button>
      <button type="button" role="menuitem" :disabled="!canRun" @click="onRun(false)">
        {{ advising ? 'Running…' : 'Run now' }}
      </button>
      <button
        type="button"
        role="menuitem"
        title="Bypass daily cache — spends Claude/Jev tokens"
        :disabled="!canRun"
        @click="onRun(true)"
      >
        Force run
      </button>
      <NuxtLink role="menuitem" to="/setup" @click="close">Setup</NuxtLink>
    </div>
  </div>
</template>

<style scoped>
.ops-menu {
  position: relative;
  flex-shrink: 0;
}

.ops-trigger {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  height: 2.5rem;
  padding: 0 0.75rem;
  border-radius: 0;
  background: transparent;
  color: var(--accent);
  border-color: var(--line);
}

.ops-trigger:hover {
  border-color: var(--accent);
}

.caret {
  font-size: 0.75rem;
  line-height: 1;
}

.panel-pop {
  position: absolute;
  top: calc(100% + 0.5rem);
  right: 0;
  z-index: 40;
  min-width: 11rem;
  border: 1px solid var(--line);
  background: #fff;
  box-shadow: 0 8px 24px rgba(26, 31, 28, 0.12);
  display: flex;
  flex-direction: column;
  padding: 0.35rem 0;
}

.panel-pop button,
.panel-pop a {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.55rem 0.9rem;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  text-decoration: none;
  cursor: pointer;
}

.panel-pop button:hover:not(:disabled),
.panel-pop a:hover {
  background: rgba(47, 93, 80, 0.08);
  color: var(--accent);
}

.panel-pop button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
