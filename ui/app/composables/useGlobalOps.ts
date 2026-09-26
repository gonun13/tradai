export function useGlobalOps() {
  const api = useHoldingsApi()

  const refreshing = useState('ops-refreshing', () => false)
  const advising = useState('ops-advising', () => false)
  const flash = useState<OperationFlash | null>('ops-flash', () => null)
  const historyVersion = useState('ops-history-version', () => 0)

  const { data: holdingsPayload } = useNuxtData<{ holdings?: unknown[] }>('holdings')
  const { data: trackerPayload } = useNuxtData<{ tracker?: unknown[] }>('tracker')

  const holdingsCount = computed(() => holdingsPayload.value?.holdings?.length ?? 0)
  const trackerCount = computed(() => trackerPayload.value?.tracker?.length ?? 0)
  const busy = computed(() => refreshing.value || advising.value)
  // One run covers both books. If either payload has not been loaded in this browser session,
  // let the API resolve whether the combined book is empty.
  const canRun = computed(() => {
    if (busy.value) {
      return false
    }
    if (holdingsPayload.value == null || trackerPayload.value == null) {
      return true
    }
    return holdingsCount.value + trackerCount.value > 0
  })

  async function pollRun(runId: number) {
    const terminal = new Set(['succeeded', 'failed', 'partial'])
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      const snap = await api.getRun(runId)
      if (terminal.has(snap.run.status)) {
        return snap
      }
    }
    return null
  }

  function showFlash(text: string, status: OperationFlash['status']) {
    flash.value = { text, status, nonce: Date.now() }
    const nonce = flash.value.nonce
    setTimeout(() => {
      if (flash.value?.nonce === nonce) flash.value = null
    }, status === 'success' ? 4000 : 8000)
  }

  function dismissFlash() {
    flash.value = null
  }

  async function refreshOperationViews() {
    await Promise.all([
      refreshNuxtData('holdings'),
      refreshNuxtData('tracker'),
      refreshNuxtData('portfolio-context-preview'),
      refreshNuxtData('tracker-context-preview'),
      refreshNuxtData('advisory-latest'),
      refreshNuxtData('tracker-advisory'),
      refreshNuxtData('ops-advisory-status'),
      refreshNuxtData('alerts'),
    ])
  }

  async function ingestNow() {
    refreshing.value = true
    try {
      await api.refreshMarket()
      showFlash('Ingest finished.', 'success')
      historyVersion.value++
      await Promise.all([
        refreshNuxtData('ingest-report'),
        refreshNuxtData('ops-ingest-status'),
        refreshOperationViews(),
      ])
    } catch {
      showFlash('Ingest failed.', 'error')
      historyVersion.value++
    } finally {
      refreshing.value = false
    }
  }

  async function runNow(force = false) {
    advising.value = true
    try {
      const started = await api.runAdvisory({ force })
      const runId = started.run_id
      if (!runId) {
        throw new Error((started.worker?.error as string) || 'Advisory did not start')
      }
      if (started.from_cache) {
        showFlash(`Run #${runId} already current.`, 'success')
        await refreshOperationViews()
        return
      }
      const done = await pollRun(runId)
      historyVersion.value++
      await refreshOperationViews()
      if (!done) {
        showFlash(`Run #${runId} is still running.`, 'warning')
      } else if (done.run.status === 'succeeded') {
        showFlash(`Run #${runId} succeeded.`, 'success')
      } else if (done.run.status === 'partial') {
        showFlash(`Run #${runId} finished with warnings.`, 'warning')
      } else {
        showFlash(`Run #${runId} failed.`, 'error')
      }
    } catch {
      showFlash('Advisory failed.', 'error')
      await refreshOperationViews()
    } finally {
      advising.value = false
    }
  }

  async function runSymbol(symbol: string) {
    advising.value = true
    try {
      const started = await api.runAdvisorySingle(symbol)
      const runId = started.run_id
      if (!runId) throw new Error((started.worker?.error as string) || 'Advisory did not start')
      if (started.from_cache) {
        showFlash(`Run #${runId} already current.`, 'success')
        await refreshOperationViews()
        return
      }
      const done = await pollRun(runId)
      historyVersion.value++
      await refreshOperationViews()
      if (!done) showFlash(`Run #${runId} is still running.`, 'warning')
      else if (done.run.status === 'succeeded') showFlash(`Run #${runId} succeeded.`, 'success')
      else if (done.run.status === 'partial') showFlash(`Run #${runId} finished with warnings.`, 'warning')
      else showFlash(`Run #${runId} failed.`, 'error')
    } catch {
      showFlash('Advisory failed.', 'error')
      await refreshOperationViews()
    } finally {
      advising.value = false
    }
  }

  return {
    refreshing,
    advising,
    busy,
    canRun,
    flash,
    dismissFlash,
    ingestNow,
    runNow,
    runSymbol,
  }
}

type OperationFlash = {
  text: string
  status: 'success' | 'warning' | 'error'
  nonce: number
}
