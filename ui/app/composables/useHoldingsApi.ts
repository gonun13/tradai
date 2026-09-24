export type Instrument = {
  id: number
  isin: string | null
  symbol: string
  mic: string | null
  currency: string
  name: string | null
  kind: 'equity' | 'etf'
  region: 'eu' | 'us' | null
}

export type Transaction = {
  id: number
  side: 'buy' | 'sell'
  trade_date: string
  quantity: number
  unit_price: number
  commission: number
  lot_cost: number
  notes: string | null
}

export type Quote = {
  price: number
  currency: string
  as_of: string
  source: string
  updated_at: string
}

export type TechnicalFeatures = {
  as_of_bar?: string | null
  bar_count?: number | null
  last_close?: number | null
  sma_20?: number | null
  sma_50?: number | null
  rsi_14?: number | null
  return_1m_pct?: number | null
  return_3m_pct?: number | null
  return_6m_pct?: number | null
  vs_sma20_pct?: number | null
  vs_sma50_pct?: number | null
}

export type TechnicalsSnapshot = {
  as_of: string
  source: string
  updated_at: string
  features: TechnicalFeatures
}

export type NewsItem = {
  id: number
  title: string
  snippet: string | null
  url: string | null
  source_name: string | null
  adapter_source: string | null
  published_at: string | null
  fetched_at: string
}

export type FundamentalsSnapshot = {
  payload: Record<string, unknown>
  source: string
  as_of: string
  completeness_state: 'complete' | 'partial'
  coverage_score: number
  missing_fields: string[]
  updated_at?: string
}

export type AgentContextPreviewItem = {
  book: Book
  symbol: string
  name: string | null
  historical: {
    quote: Quote | null
    bars: {
      count: number | null
      last_close: number | null
      as_of: string | null
    } | null
  }
  fundamentals: FundamentalsSnapshot | null
  technicals: TechnicalsSnapshot | null
  news: NewsItem[]
}

export type Holding = {
  id: number
  quantity: number
  avg_cost: number
  total_cost: number
  notes: string | null
  lot_count: number
  trade_date: string | null
  unit_price: number | null
  commission: number | null
  cost_native: number
  cost_eur: number | null
  market_value_native: number | null
  market_value_eur: number | null
  pnl_native: number | null
  pnl_eur: number | null
  pnl_pct: number | null
  fx_to_eur: number | null
  display_currency: 'EUR'
  quote: Quote | null
  technicals: TechnicalsSnapshot | null
  fundamentals: FundamentalsSnapshot | null
  news: NewsItem[]
  created_at: string
  updated_at: string
  instrument: Instrument
  transactions: Transaction[]
}

export type HoldingInput = {
  symbol: string
  isin?: string
  mic?: string
  currency: string
  name?: string
  kind: 'equity' | 'etf'
  region?: 'eu' | 'us' | ''
  trade_date: string
  quantity: number
  unit_price: number
  commission: number
  notes?: string
  transaction_id?: number
}

export function useHoldingsApi() {
  const list = () =>
    $fetch<{
      ok: boolean
      holdings: Holding[]
      display_currency: string
      portfolio_market_value_eur: number | null
    }>('/api/holdings')

  const create = (body: HoldingInput) =>
    $fetch<{ ok: boolean; holding: Holding }>('/api/holdings', { method: 'POST', body })

  const update = (id: number, body: Partial<HoldingInput>) =>
    $fetch<{ ok: boolean; holding: Holding }>(`/api/holdings/${id}`, { method: 'PUT', body })

  const remove = (id: number) =>
    $fetch<{ ok: boolean }>(`/api/holdings/${id}`, { method: 'DELETE' })

  const refreshMarket = () =>
    $fetch<{ ok: boolean; refresh: Record<string, unknown> }>('/api/refresh/market', {
      method: 'POST',
    })

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
    cash_eur?: number | null
    realized_gains_ytd_override_eur?: number | null
  }) =>
    $fetch<{ ok: boolean; settings: PortfolioSettings }>('/api/portfolio/settings', {
      method: 'PUT',
      body,
    })

  const listTheses = (status?: 'draft' | 'approved' | 'superseded') =>
    $fetch<{ ok: boolean; theses: Thesis[]; coverage: ThesisCoverage[] }>(
      `/api/theses${status ? `?status=${status}` : ''}`,
    )

  const saveThesis = (
    id: number,
    body: { thesis?: string; falsifiers?: string[]; status?: string },
  ) => $fetch<{ ok: boolean; thesis: Thesis }>(`/api/theses/${id}`, { method: 'PUT', body })

  const createThesis = (body: {
    instrument_id: number
    thesis: string
    falsifiers?: string[]
    status?: string
  }) => $fetch<{ ok: boolean; thesis: Thesis }>('/api/theses', { method: 'POST', body })

  const removeThesis = (id: number) =>
    $fetch<{ ok: boolean }>(`/api/theses/${id}`, { method: 'DELETE' })

  // 0019: the tracker — a second book, not a note. Same ingest and same agent research as
  // the portfolio, so entries carry a quote and technicals like a holding does.
  const listTracker = () => $fetch<{ ok: boolean; tracker: TrackerEntry[] }>('/api/tracker')

  const contextPreview = (book: Book) =>
    $fetch<{
      ok: boolean
      book: Book
      display_currency: 'EUR'
      instruments: AgentContextPreviewItem[]
    }>('/api/context/preview', { query: { book } })

  const addTracker = (body: TrackerInput) =>
    $fetch<{ ok: boolean; entry: TrackerEntry }>('/api/tracker', { method: 'POST', body })

  const updateTracker = (id: number, body: { name?: string | null; note?: string | null }) =>
    $fetch<{ ok: boolean; entry: TrackerEntry }>(`/api/tracker/${id}`, { method: 'PUT', body })

  const removeTracker = (id: number) =>
    $fetch<{ ok: boolean }>(`/api/tracker/${id}`, { method: 'DELETE' })

  const searchInstruments = (q: string) =>
    $fetch<{ ok: boolean; query: string; results: SymbolHit[]; warnings: string[] }>(
      '/api/instruments/search',
      { query: { q } },
    )

  return {
    list,
    create,
    update,
    remove,
    getSettings,
    saveSettings,
    listTheses,
    saveThesis,
    createThesis,
    removeThesis,
    listTracker,
    contextPreview,
    addTracker,
    updateTracker,
    removeTracker,
    searchInstruments,
    refreshMarket,
    ingestReport,
    runAdvisory,
    runAdvisorySingle,
    latestRun,
    getRun,
    listAlerts,
    ackAlert,
    setupStatus,
  }
}

export type SetupKeys = {
  finnhub: boolean
  marketaux: boolean
  fmp: boolean
  typesafe: boolean
  claude_oauth: boolean
  anthropic_api_key_set: boolean
}

export type SetupSchedule = {
  timezone: string
  us_close: string
  after_us_close_minutes: number
  fire_at_et: string
  interval_seconds: number
  max_scenario_rounds: number
}

export type Alert = {
  id: number
  recommendation_id: number
  agent_run_id: number
  unread: boolean
  severity: string
  raised_at: string
  acked_at: string | null
  action: 'buy' | 'sell' | 'hold' | 'watch'
  book: Book
  horizon: '1m' | '3m' | '6m' | '12m' | '24m'
  symbol: string
  instrument_name: string | null
  rationale: string | null
}

export type AgentRun = {
  id: number
  trigger: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'partial'
  started_at: string
  finished_at: string | null
  models: Record<string, unknown> | null
  log: string | null
  error: string | null
  research: {
    synthesis?: string
    by_symbol?: Record<string, string>
    thesis_by_symbol?: Record<
      string,
      {
        thesis_status?: 'intact' | 'weakening' | 'broken' | null
        evidence?: string | null
        better_use_target?: string | null
      }
    >
  } | null
  info_needs: Array<{
    description: string
    symbols?: string[]
    priority?: string
  }> | null
  context: AgentContext | null
}

export type AgentContextHolding = {
  holding_id: number
  instrument_id: number
  symbol: string
  isin: string | null
  mic: string | null
  name: string | null
  kind: string
  region: string | null
  currency: string
  quantity: number
  avg_cost: number
  total_cost: number
  cost_eur: number | null
  market_value_eur: number | null
  pnl_eur: number | null
  pnl_pct: number | null
  weight_pct: number | null
  weight_cost_pct: number | null
  quote: {
    price: number | null
    currency: string | null
    as_of: string | null
    source: string | null
  } | null
  technicals: Record<string, unknown> | null
  fundamentals: FundamentalsSnapshot | null
  news: Array<{
    title: string
    snippet: string | null
    url: string | null
    source: string | null
    adapter_source: string | null
    published_at: string | null
  }>
  notes: string | null
  first_trade_date: string | null
  held_days: number | null
  open_lot_count: number | null
  realized_pnl_native: number | null
  thesis: {
    id: number
    text: string
    falsifiers: string[]
    version: number
    approved_at: string | null
  } | null
}

export type AgentContext = {
  display_currency: string
  portfolio_market_value_eur: number | null
  portfolio_cost_eur: number | null
  cash_eur: number | null
  portfolio_total_eur: number | null
  realized_gains_ytd_eur: number
  calendar_year: number
  tracker: Array<{ symbol: string; name: string | null; note: string | null }>
  profiles: { investor: string | null; portfolio: string | null }
  /** Resolved mandate per book, defaults already substituted — what was really sent. */
  mandates: Record<Book, string>
  /** The composed text actually applied to holdings — kept for the log page. */
  mandate: string
  holdings: AgentContextHolding[]
  tracked: AgentContextTracked[]
  built_at: string
}

export type AgentContextTracked = {
  book: 'tracker'
  instrument_id: number
  symbol: string
  name: string | null
  note: string | null
  added_at: string
  kind: string | null
  region: string | null
  currency: string
  quote: { price: number | null; currency: string | null; as_of: string | null } | null
  price_eur: number | null
  technicals: TechnicalFeatures | null
  fundamentals: FundamentalsSnapshot | null
  news: Array<{
    title: string
    snippet: string | null
    url: string | null
    source: string | null
    adapter_source: string | null
    published_at: string | null
  }>
}

export type ConversationTurn = {
  role: string
  kind: string
  lens?: string
  round?: number
  at?: string
  summary?: string
  hypothesis?: string
  question_hint?: string
  research_excerpt?: string
  answers?: Record<string, { action?: string; payload?: Record<string, unknown> }>
}

export type Book = 'portfolio' | 'tracker'

/** `drop` is tracker-only (0019): stop spending attention on a name never owned. */
export type RecommendationAction = 'buy' | 'sell' | 'hold' | 'watch' | 'drop'

export type RecommendationReason =
  | 'thesis_broken'
  | 'better_use'
  | 'thesis_intact_underweight'
  | 'new_conviction'
  | 'thesis_intact'
  | 'insufficient_evidence'
  | 'legacy'
  // 0019 — tracker verbs.
  | 'entry_now'
  | 'await_better_entry'
  | 'lost_interest'

export type Thesis = {
  id: number
  instrument_id: number
  symbol: string | null
  instrument_name: string | null
  thesis: string
  falsifiers: string[]
  status: 'draft' | 'approved' | 'superseded'
  version: number
  source: 'claude' | 'operator'
  agent_run_id: number | null
  created_at: string
  approved_at: string | null
  superseded_at: string | null
}

export type ThesisCoverage = {
  instrument_id: number
  symbol: string
  name: string | null
  has_approved: boolean
  pending_drafts: number
}

export type TrackerEntry = {
  id: number
  symbol: string
  instrument_id: number
  name: string | null
  note: string | null
  added_at: string
  updated_at: string
  archived_at: string | null
  instrument: {
    currency: string | null
    kind: string | null
    region: string | null
    mic: string | null
    isin: string | null
  }
  quote: Quote | null
  // No cost basis exists for an unowned name, so the EUR figure is just the converted quote.
  price_eur: number | null
  technicals: TechnicalsSnapshot | null
  fundamentals: FundamentalsSnapshot | null
  news: NewsItem[]
}

export type TrackerInput = {
  symbol: string
  name?: string | null
  note?: string | null
  isin?: string | null
  mic?: string | null
  currency?: string | null
  kind?: string | null
  region?: string | null
}

/** A hit from GET /instruments/search — local instruments first, then Yahoo/Finnhub. */
export type SymbolHit = {
  symbol: string
  name: string | null
  mic: string | null
  currency: string | null
  kind: string | null
  region: string | null
  isin: string | null
  exchange?: string | null
  source: 'local' | 'yahoo' | 'finnhub' | 'vendor'
  known: boolean
}

export type PortfolioSettings = {
  // 0019: two profiles. Investor = who you are and what makes a name worth buying (drives
  // the tracker); portfolio = the rules for what you already own (drives holdings).
  investor_profile_text: string | null
  portfolio_profile_text: string | null
  cash_eur: number | null
  realized_gains_ytd_from_disposals_eur: number | null
  realized_gains_ytd_override_eur: number | null
  realized_gains_ytd_eur: number
  calendar_year: number
}

export type Recommendation = {
  id: number
  agent_run_id: number
  instrument_id: number
  book: Book
  symbol: string
  instrument_name: string | null
  kind: string
  region: string | null
  currency: string
  action: RecommendationAction
  horizon: '1m' | '3m' | '6m' | '12m' | '24m'
  // 0013 — why, and what the doctrine gate did about it.
  reason: RecommendationReason | null
  loss_gate: 'not_at_loss' | 'offset_same_year' | 'no_recovery_24m' | 'blocked' | null
  pair_symbol: string | null
  confidence: number | null
  suppressed: boolean
  suppressed_reason: string | null
  proposed_action: RecommendationAction | null
  price_at_rec: number | null
  rationale: string | null
  jev: Record<string, unknown> | null
  jev_lenses: Record<string, { action?: string; payload?: Record<string, unknown> }> | null
  conversation: ConversationTurn[] | null
  created_at: string
  updated_at: string
}
