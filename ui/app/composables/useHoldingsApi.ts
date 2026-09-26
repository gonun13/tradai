import type {
  HistoryResponse,
  AgentContextPreviewItem,
  Holding,
  HoldingInput,
  OperationRunSummary,
  IngestRunDetail,
  AdvisoryRunLog,
  SetupKeys,
  SetupSchedule,
  Alert,
  AgentRun,
  Book,
  TrackerEntry,
  TrackerInput,
  SymbolHit,
  PortfolioSettings,
  DisplayCurrency,
  MoneySettingInput,
  Recommendation,
} from '~/types/api'

export function useHoldingsApi() {
  const list = () =>
    $fetch<{
      ok: boolean
      holdings: Holding[]
      display_currency: DisplayCurrency
      portfolio_market_value_display: number | null
    }>('/api/holdings')

  const create = (body: HoldingInput) =>
    $fetch<{ ok: boolean; holding: Holding }>('/api/holdings', { method: 'POST', body })

  const update = (id: number, body: Partial<HoldingInput>) =>
    $fetch<{ ok: boolean; holding: Holding }>(`/api/holdings/${id}`, { method: 'PUT', body })

  const remove = (id: number) =>
    $fetch<{ ok: boolean }>(`/api/holdings/${id}`, { method: 'DELETE' })

  const refreshMarket = () =>
    $fetch<{ ok: boolean; run_id: number; refresh: Record<string, unknown> }>('/api/refresh/market', {
      method: 'POST',
    })

  const listIngestRuns = () =>
    $fetch<{ ok: boolean; runs: OperationRunSummary[] }>('/api/ingest/runs')

  const getIngestRun = (id: number) =>
    $fetch<{ ok: boolean; run: IngestRunDetail }>(`/api/ingest/runs/${id}`)

  const ingestReport = () =>
    $fetch<{
      ok: boolean
      updated_at?: string
      symbol_not_found: Array<{
        instrument_id: number
        symbol: string
        vendor: string
        detail: string | null
      }>
      errors: string[]
      report: Record<string, unknown> | null
      message?: string
    }>('/api/ingest/report')

  // `research` (synthesis / per-symbol notes / thesis findings) is Claude's internal
  // reasoning — the operator wants it used, not seen. Strip it before the run object ever
  // reaches page state, since Nuxt would otherwise embed it in the SSR payload even if no
  // template renders it. `info_needs` is a separate field and is unaffected.
  const dropResearch = (run: AgentRun | null): AgentRun | null =>
    run ? { ...run, research: null } : run

  // `Recommendation.rationale` carries the same per-ticker thesis prose, independently of
  // `run.research` — strip it here too, or it still ships in the SSR payload unrendered
  // (Nuxt embeds fetched data even for fields no template renders, e.g. on the dashboard,
  // which never shows `conversation` but still fetches it for the log-icon link state).
  // `conversation[].research_excerpt` carries the same clipped research text and gets the
  // same treatment; the rest of each turn (role, lens, summary, timestamps) stays — that's
  // procedural, not thesis prose.
  const dropRationale = (recs: Recommendation[]): Recommendation[] =>
    recs.map((r) => ({
      ...r,
      rationale: null,
      conversation: r.conversation
        ? r.conversation.map((turn) => ({ ...turn, research_excerpt: undefined }))
        : r.conversation,
    }))

  const runAdvisory = (opts?: { force?: boolean }) => {
    const q = opts?.force ? '?force=1' : ''
    return $fetch<{
      ok: boolean
      from_cache?: boolean
      run_id: number | null
      run: AgentRun | null
      recommendations?: Recommendation[]
      worker: Record<string, unknown>
    }>(`/api/agent/run${q}`, { method: 'POST' }).then((res) => ({
      ...res,
      run: dropResearch(res.run),
      recommendations: res.recommendations ? dropRationale(res.recommendations) : res.recommendations,
    }))
  }
  const runAdvisorySingle = (symbol: string, opts?: { force?: boolean }) => {
    const params = new URLSearchParams({ symbol })
    if (opts?.force) params.set('force', '1')
    return $fetch<{
      ok: boolean
      from_cache?: boolean
      run_id: number | null
      run: AgentRun | null
      recommendations?: Recommendation[]
      worker: Record<string, unknown>
    }>(`/api/agent/run/symbol?${params.toString()}`, { method: 'POST' })
  }
  const latestRun = () =>
    $fetch<{
      ok: boolean
      run: AgentRun | null
      recommendations: Recommendation[]
    }>('/api/agent/runs/latest').then((res) => ({
      ...res,
      run: dropResearch(res.run),
      recommendations: dropRationale(res.recommendations),
    }))

  const getRun = (id: number) =>
    $fetch<{
      ok: boolean
      run: AgentRun
      recommendations: Recommendation[]
    }>(`/api/agent/runs/${id}`).then((res) => ({
      ...res,
      run: dropResearch(res.run) as AgentRun,
      recommendations: dropRationale(res.recommendations),
    }))

  const listAgentRuns = () =>
    $fetch<{ ok: boolean; runs: OperationRunSummary[] }>('/api/agent/runs')

  const getAgentRunLog = (id: number) =>
    $fetch<{ ok: boolean; run: AdvisoryRunLog }>(`/api/agent/runs/${id}/log`)

  const listAlerts = (opts?: { unread?: boolean }) => {
    const q =
      opts?.unread === true ? '?unread=1' : opts?.unread === false ? '?unread=0' : ''
    return $fetch<{
      ok: boolean
      unread_count: number
      alerts: Alert[]
    }>(`/api/alerts${q}`).then((res) => ({
      ...res,
      // Same reasoning as dropResearch: rationale is Claude's per-ticker thesis prose.
      alerts: res.alerts.map((a) => ({ ...a, rationale: null })),
    }))
  }

  const ackAlert = (id: number) =>
    $fetch<{ ok: boolean; alert: Alert; unread_count: number }>(`/api/alerts/${id}/ack`, {
      method: 'POST',
    })

  const setupStatus = () =>
    $fetch<{
      ok: boolean
      stage: string
      keys: SetupKeys
      schedule: SetupSchedule
      docs: Record<string, string>
      warnings: string[]
    }>('/api/setup')

  const getSettings = () =>
    $fetch<{ ok: boolean; settings: PortfolioSettings }>('/api/portfolio/settings')

  const saveSettings = (body: {
    investor_profile_text?: string | null
    portfolio_profile_text?: string | null
    display_currency?: DisplayCurrency
    cash?: MoneySettingInput | null
    realized_gains_ytd_override?: MoneySettingInput | null
  }) =>
    $fetch<{ ok: boolean; settings: PortfolioSettings }>('/api/portfolio/settings', {
      method: 'PUT',
      body,
    })

  // 0019: the tracker — a second book, not a note. Same ingest and same agent research as
  // the portfolio, so entries carry a quote and technicals like a holding does.
  const listTracker = () =>
    $fetch<{ ok: boolean; tracker: TrackerEntry[]; display_currency: DisplayCurrency }>('/api/tracker')

  const contextPreview = (book: Book) =>
    $fetch<{
      ok: boolean
      book: Book
      display_currency: DisplayCurrency
      instruments: AgentContextPreviewItem[]
    }>('/api/context/preview', { query: { book } })

  const addTracker = (body: TrackerInput) =>
    $fetch<{ ok: boolean; entry: TrackerEntry }>('/api/tracker', { method: 'POST', body })

  const removeTracker = (id: number) =>
    $fetch<{ ok: boolean }>(`/api/tracker/${id}`, { method: 'DELETE' })

  const searchInstruments = (q: string) =>
    $fetch<{ ok: boolean; query: string; results: SymbolHit[]; warnings: string[] }>(
      '/api/instruments/search',
      { query: { q } },
    )

  const instrumentHistory = (id: number, range: '1y' | '2y' | '5y' | 'max') =>
    $fetch<HistoryResponse>(`/api/instruments/${id}/history`, { query: { range } })

  return {
    list,
    create,
    update,
    remove,
    getSettings,
    saveSettings,
    listTracker,
    contextPreview,
    addTracker,
    removeTracker,
    searchInstruments,
    instrumentHistory,
    refreshMarket,
    listIngestRuns,
    getIngestRun,
    ingestReport,
    runAdvisory,
    runAdvisorySingle,
    latestRun,
    getRun,
    listAgentRuns,
    getAgentRunLog,
    listAlerts,
    ackAlert,
    setupStatus,
  }
}
