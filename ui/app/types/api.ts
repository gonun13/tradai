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

export type HistoryPoint = {
  date: string
  adjusted_close: number
  resolution: 'daily' | 'weekly' | 'monthly'
  normalized_value: number
}

export type HistorySeries = {
  instrument_id?: number
  region?: 'eu' | 'us'
  symbol: string
  name?: string | null
  native_currency: string
  source: string | null
  as_of: string | null
  earliest_date: string | null
  coverage_start: string | null
  normalization_date: string | null
  stale: boolean | null
  endpoint_return_pct: number | null
  points: HistoryPoint[]
}

export type HistoryResponse = {
  ok: boolean
  range: '1y' | '2y' | '5y' | 'max'
  basis: 'adjusted_performance'
  normalization_base: 100
  comparison_start: string | null
  warning: string | null
  stock: HistorySeries
  benchmark: HistorySeries | null
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
  cost_display: number | null
  market_value_native: number | null
  market_value_display: number | null
  pnl_native: number | null
  pnl_display: number | null
  pnl_pct: number | null
  daily_change_pct: number | null
  fx_to_display: number | null
  display_currency: DisplayCurrency
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

export type OperationRunSummary = {
  id: number
  trigger: 'manual' | 'forced' | 'targeted' | 'schedule'
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'partial'
  started_at: string
  finished_at: string | null
}

export type IngestRunDetail = OperationRunSummary & {
  report: Record<string, unknown> | null
  error: string | null
}

export type AdvisoryRunLog = OperationRunSummary & {
  log: string | null
  error: string | null
}

export type SetupKeys = {
  finnhub: boolean
  marketaux: boolean
  alpha_vantage: boolean
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
  cost_display: number | null
  market_value_display: number | null
  pnl_display: number | null
  /** Historical pre-0025 snapshots. */
  cost_eur?: number | null
  market_value_eur?: number | null
  pnl_eur?: number | null
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
  display_currency: DisplayCurrency
  portfolio_market_value_display: number | null
  portfolio_cost_display: number | null
  cash_display: number | null
  portfolio_total_display: number | null
  realized_gains_ytd_display: number | null
  /** Historical pre-0025 snapshots remain readable and are never rewritten. */
  portfolio_market_value_eur?: number | null
  portfolio_cost_eur?: number | null
  cash_eur?: number | null
  portfolio_total_eur?: number | null
  realized_gains_ytd_eur?: number | null
  calendar_year: number
  tracker: Array<{ symbol: string; name: string | null }>
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
  added_at: string
  kind: string | null
  region: string | null
  currency: string
  quote: { price: number | null; currency: string | null; as_of: string | null } | null
  price_display: number | null
  /** Historical pre-0025 snapshots. */
  price_eur?: number | null
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
  // 0028: set on an `explanation` turn.
  tension?: string | null
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

export type TrackerEntry = {
  id: number
  symbol: string
  instrument_id: number
  name: string | null
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
  // No cost basis exists for an unowned name, so this is the converted quote.
  price_display: number | null
  display_currency: DisplayCurrency
  daily_change_pct: number | null
  technicals: TechnicalsSnapshot | null
  fundamentals: FundamentalsSnapshot | null
  news: NewsItem[]
}

export type TrackerInput = {
  symbol: string
  name?: string | null
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
  display_currency: DisplayCurrency
  display_currency_options: DisplayCurrency[]
  cash: MoneySetting
  realized_gains_ytd_from_disposals_display: number | null
  realized_gains_ytd_override: MoneySetting
  realized_gains_ytd_display: number | null
  calendar_year: number
}

export type DisplayCurrency = 'EUR' | 'USD' | 'GBP' | 'CHF'

export type MoneySetting = {
  amount: number | null
  currency: DisplayCurrency | null
  display_amount: number | null
}

export type MoneySettingInput = {
  amount: number
  currency: DisplayCurrency
}

export type RecommendationExplanation = {
  text: string
  tension: string | null
  market_read: string | null
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
  // 0027: set when an unchanged subject was carried forward from the run that decided it.
  carried_from_run_id: number | null
  carried_from_at: string | null
  // 0028: Claude's plain-language why for the combined decision (same on every horizon row).
  // Meant to be read — unlike `rationale`, it is not stripped from page state.
  explanation: RecommendationExplanation | null
  created_at: string
  updated_at: string
}
