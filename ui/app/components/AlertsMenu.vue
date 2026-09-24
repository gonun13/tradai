<script setup lang="ts">
import type { Alert } from '~/composables/useHoldingsApi'

const api = useHoldingsApi()
const open = ref(false)
const tab = ref<'unread' | 'read'>('unread')
const ackError = ref('')
const root = ref<HTMLElement | null>(null)

const { data: alertsData, refresh } = await useAsyncData('alerts', () =>
  api.listAlerts().catch(() => ({ ok: true, unread_count: 0, alerts: [] as Alert[] })),
)

const alerts = computed(() => {
  const list = [...(alertsData.value?.alerts ?? [])]
  return list.sort((a, b) => {
    const byDate = String(b.raised_at).localeCompare(String(a.raised_at))
    return byDate !== 0 ? byDate : b.id - a.id
  })
})

const unreadAlerts = computed(() => alerts.value.filter((a) => a.unread))
const readAlerts = computed(() => alerts.value.filter((a) => !a.unread))
const unreadCount = computed(() => alertsData.value?.unread_count ?? unreadAlerts.value.length)
const activeAlerts = computed(() => (tab.value === 'unread' ? unreadAlerts.value : readAlerts.value))

function toggle() {
  open.value = !open.value
  if (open.value) {
    tab.value = 'unread'
    ackError.value = ''
    void refresh()
  }
}

function close() {
  open.value = false
}

function alertPath(a: Alert) {
  return `/${a.book || 'portfolio'}/log/${encodeURIComponent(a.symbol)}`
}

async function ackAlert(a: Alert) {
  ackError.value = ''
  try {
    await api.ackAlert(a.id)
    await refresh()
  } catch (e: unknown) {
    ackError.value = e instanceof Error ? e.message : 'Ack failed'
  }
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

defineExpose({ refresh })
</script>

<template>
  <div ref="root" class="alerts-menu">
    <button
      type="button"
      class="bell"
      :aria-expanded="open"
      aria-haspopup="true"
      aria-label="Alerts"
      @click.stop="toggle"
    >
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path
          fill="currentColor"
          d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm8-6V11a8 8 0 1 0-16 0v5l-2 2v1h20v-1l-2-2Z"
        />
      </svg>
      <span v-if="unreadCount" class="count">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
    </button>

    <div v-if="open" class="panel-pop" role="dialog" aria-label="Alerts">
      <div class="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          :aria-selected="tab === 'unread'"
          :class="{ active: tab === 'unread' }"
          @click="tab = 'unread'"
        >
          Unread
          <span v-if="unreadCount" class="tab-count">{{ unreadCount }}</span>
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="tab === 'read'"
          :class="{ active: tab === 'read' }"
          @click="tab = 'read'"
        >
          Read
        </button>
      </div>

      <p v-if="ackError" class="bad pop-msg">{{ ackError }}</p>

      <p v-if="!activeAlerts.length" class="mute pop-msg">
        <template v-if="tab === 'unread'">No unread alerts.</template>
        <template v-else>No read alerts yet.</template>
      </p>

      <ul v-else class="alerts">
        <li v-for="a in activeAlerts" :key="a.id" :class="{ unread: a.unread }">
          <div>
            <NuxtLink class="alert-symbol" :to="alertPath(a)" @click="close">
              <strong>{{ a.symbol }}</strong>
            </NuxtLink>
            <span class="book-chip" :data-book="a.book || 'portfolio'">
              {{ a.book || 'portfolio' }}
            </span>
            <span class="pill" :data-action="a.action">{{ a.horizon }} {{ a.action }}</span>
            <span class="sub">
              {{ a.raised_at }}
              <template v-if="a.acked_at"> · acked {{ a.acked_at }}</template>
            </span>
          </div>
          <button v-if="a.unread" type="button" class="ghost" @click="ackAlert(a)">Ack</button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.alerts-menu {
  position: relative;
  flex-shrink: 0;
}

.bell {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.5rem;
  height: 2.5rem;
  padding: 0;
  border-radius: 0;
  background: transparent;
  color: var(--accent);
  border-color: var(--line);
}

.bell:hover {
  border-color: var(--accent);
}

.count {
  position: absolute;
  top: -0.35rem;
  right: -0.35rem;
  min-width: 1.15rem;
  height: 1.15rem;
  padding: 0 0.25rem;
  border-radius: 999px;
  background: var(--bad);
  color: #fff;
  font-size: 0.7rem;
  line-height: 1.15rem;
  text-align: center;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.panel-pop {
  position: absolute;
  top: calc(100% + 0.5rem);
  right: 0;
  z-index: 40;
  width: min(24rem, calc(100vw - 2rem));
  max-height: min(28rem, 70vh);
  overflow: auto;
  border: 1px solid var(--line);
  background: #fff;
  box-shadow: 0 8px 24px rgba(26, 31, 28, 0.12);
}

.tabs {
  display: flex;
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  background: #fff;
  z-index: 1;
}

.tabs button {
  flex: 1;
  background: transparent;
  color: var(--mute);
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  padding: 0.65rem 0.75rem;
}

.tabs button.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.tab-count {
  margin-left: 0.35rem;
  font-size: 0.85em;
  color: var(--bad);
}

.pop-msg {
  margin: 0.75rem 1rem;
}

.alerts {
  list-style: none;
  margin: 0;
  padding: 0 0.75rem;
}

.alerts li {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: flex-start;
  padding: 0.75rem 0.25rem;
  border-bottom: 1px solid var(--line);
}

.alerts li:last-child {
  border-bottom: 0;
}

.alerts li.unread {
  border-left: 3px solid var(--bad);
  padding-left: 0.65rem;
}

.rationale {
  margin: 0.35rem 0 0;
  font-size: 0.9rem;
}

.alert-symbol {
  color: var(--ink);
  text-decoration: none;
}

.alert-symbol:hover { color: var(--accent); }

.book-chip {
  display: inline-block;
  margin: 0 0.3rem;
  padding: 0.1rem 0.35rem;
  border: 1px solid #b9c0bb;
  color: #59615b;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.65rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.book-chip[data-book="tracker"] {
  border-color: #9bafd0;
  color: #31568e;
}
</style>
