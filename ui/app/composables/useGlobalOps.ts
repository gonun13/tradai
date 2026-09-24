export function useGlobalOps() {
  const api = useHoldingsApi()

  const refreshing = useState('ops-refreshing', () => false)
  const advising = useState('ops-advising', () => false)
  const message = useState('ops-message', () => '')
  const error = useState('ops-error', () => '')

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

  async function ingestNow() {
    error.value = ''
    message.value = ''
    refreshing.value = true
    try {
      const result = await api.refreshMarket()
      const errs = (result.refresh?.errors as string[] | undefined) ?? []
      const missing =
        (result.refresh?.symbol_not_found as
          | Array<{ instrument_id: number; symbol: string; vendor: string; detail: string | null }>
          | undefined) ?? []
      const news = result.refresh?.news as
        | { fetched?: number; skipped?: boolean; reason?: string }
        | undefined
      const techCount = Array.isArray(result.refresh?.technicals)
        ? (result.refresh.technicals as unknown[]).length
        : 0
      const statistics = result.refresh?.statistics as
        | {
            layers?: Record<
              string,
              { ingested?: number; from_cache?: number; missing?: number }
            >
          }
        | undefined
      const layerLabels: Record<string, string> = {
        historical: 'Historical (quotes + bars)',
        fundamentals: 'Fundamentals',
        technicals: 'Technicals',
        news: 'News',
      }
      const layerParts = Object.entries(layerLabels).flatMap(([key, label]) => {
        const counts = statistics?.layers?.[key]
        if (!counts) return []
        const missingPart = counts.missing ? ` / ${counts.missing} missing` : ''
        return [
          `${label}: ${counts.ingested ?? 0} ingested / ${counts.from_cache ?? 0} cached${missingPart}`,
        ]
      })
      const parts = layerParts.length
        ? layerParts
        : [`Quotes/FX + ${techCount} technical set(s)`]
      if (!layerParts.length && news?.skipped) {
        parts.push(`news cached (${news.reason || 'daily'})`)
      } else if (!layerParts.length && news) {
        parts.push(`${news.fetched ?? 0} news item(s)`)
      }
      if (missing.length) {
        parts.push(`${missing.length} symbol(s) not found`)
      }
      if (errs.length && !missing.length) {
        message.value = `${parts.join(' · ')} — ${errs.length} warning(s): ${errs.join(' · ')}`
      } else if (errs.length > missing.length) {
        message.value = `${parts.join(' · ')} — other warnings: ${errs.filter((e) => !e.includes('symbol not found')).join(' · ') || 'see report'}`
      } else {
        message.value = `Ingest complete: ${parts.join(' · ')}.`
      }
      await Promise.all([
        refreshNuxtData('holdings'),
        refreshNuxtData('tracker'),
        refreshNuxtData('ingest-report'),
      ])
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Ingest failed'
    } finally {
      refreshing.value = false
    }
  }

  async function runNow(force = false) {
    error.value = ''
    message.value = ''
    advising.value = true
    try {
      const started = await api.runAdvisory({ force })
      const runId = started.run_id
      if (!runId) {
        throw new Error((started.worker?.error as string) || 'Advisory did not start')
      }
      if (started.from_cache) {
        message.value =
          (started.worker?.message as string) ||
          `Using cached run #${runId} (daily). Force run to spend tokens again.`
        await Promise.all([refreshNuxtData('advisory-latest'), refreshNuxtData('alerts')])
        return
      }
      message.value = `Advisory run #${runId} started…`
      const done = await pollRun(runId)
      await Promise.all([refreshNuxtData('advisory-latest'), refreshNuxtData('alerts')])
      if (!done) {
        message.value = `Run #${runId} still in progress — reload later.`
      } else if (done.run.status === 'succeeded') {
        message.value = `Run #${runId} succeeded — ${done.recommendations.length} recommendation(s).`
      } else if (done.run.status === 'partial') {
        message.value = `Run #${runId} partial — ${done.recommendations.length} rec(s). ${done.run.error || ''}`
      } else {
        error.value = `Run #${runId} ${done.run.status}: ${done.run.error || 'see log'}`
      }
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : 'Advisory failed'
      await Promise.all([refreshNuxtData('advisory-latest'), refreshNuxtData('alerts')])
    } finally {
      advising.value = false
    }
  }

  return {
    refreshing,
    advising,
    busy,
    canRun,
    message,
    error,
    ingestNow,
    runNow,
  }
}
