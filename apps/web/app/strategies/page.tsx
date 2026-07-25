import { formatAge, formatCurrency, formatDateTime, formatNumber } from '@/components/Format'
import { StatusBadge } from '@/components/StatusBadge'
import { fetchAllRows } from '@/lib/fetchAllRows'
import { supabase } from '@/lib/supabase'

export const dynamic = 'force-dynamic'

type StrategyConfigRow = {
  id: string
  name: string | null
  version: number | null
  params_json: Record<string, unknown> | null
  status: string | null
  updated_at: string | null
}

type RunRow = {
  id: string
  config_id: string | null
  mode: string | null
  started_at: string | null
  ended_at: string | null
}

type OrderRow = {
  id: string
  run_id: string | null
  ticker: string | null
  risk_decision: string | null
  status: string | null
  created_at: string | null
  fills?: FillRow[] | null
}

type FillRow = {
  fill_price: number | string | null
  fill_qty: number | null
  fee: number | string | null
  created_at: string | null
}

type SignalRow = {
  run_id: string | null
  ticker: string | null
  created_at: string | null
  edge: number | string | null
}

type PositionRow = {
  run_id: string | null
  qty: number | null
  unrealized_pnl: number | string | null
}

function latestDate(values: Array<string | null | undefined>) {
  const timestamps = values
    .filter(Boolean)
    .map((value) => new Date(value as string).getTime())
    .filter(Number.isFinite)
  if (timestamps.length === 0) {
    return null
  }
  return new Date(Math.max(...timestamps)).toISOString()
}

function paramsMode(params: Record<string, unknown> | null) {
  const value = params?.mode ?? params?.execution_mode
  return typeof value === 'string' ? value : 'paper'
}

export default async function StrategiesPage() {
  const [configsResponse, runsResponse, ordersResponse, signalsResponse, positionsResponse] =
    await Promise.all([
      fetchAllRows<StrategyConfigRow>((from, to) =>
        supabase
          .from('strategy_configs')
          .select('id,name,version,params_json,status,updated_at')
          .order('name', { ascending: true })
          .range(from, to),
      ),
      fetchAllRows<RunRow>((from, to) =>
        supabase
          .from('strategy_runs')
          .select('id,config_id,mode,started_at,ended_at')
          .order('started_at', { ascending: false })
          .range(from, to),
      ),
      fetchAllRows<OrderRow>((from, to) =>
        supabase
          .from('orders')
          .select(`
            id,
            run_id,
            ticker,
            risk_decision,
            status,
            created_at,
            fills (
              fill_price,
              fill_qty,
              fee,
              created_at
            )
          `)
          .order('created_at', { ascending: false })
          .range(from, to),
      ),
      fetchAllRows<SignalRow>((from, to) =>
        supabase
          .from('signals')
          .select('run_id,ticker,created_at,edge')
          .order('created_at', { ascending: false })
          .range(from, to),
      ),
      fetchAllRows<PositionRow>((from, to) =>
        supabase
          .from('positions')
          .select('run_id,qty,unrealized_pnl')
          .neq('qty', 0)
          .range(from, to),
      ),
    ])

  const firstError =
    configsResponse.error ??
    runsResponse.error ??
    ordersResponse.error ??
    signalsResponse.error ??
    positionsResponse.error
  if (firstError) {
    return <div className="card red">Error loading strategies: {firstError.message}</div>
  }

  const configs = configsResponse.data
  const runs = runsResponse.data
  const orders = ordersResponse.data
  const signals = signalsResponse.data
  const positions = positionsResponse.data
  const runById = new Map(runs.map((run) => [run.id, run]))

  const rows = configs.map((config) => {
    const configRuns = runs.filter((run) => run.config_id === config.id)
    const runIds = new Set(configRuns.map((run) => run.id))
    const configOrders = orders.filter((order) => order.run_id && runIds.has(order.run_id))
    const configSignals = signals.filter((signal) => signal.run_id && runIds.has(signal.run_id))
    const configPositions = positions.filter(
      (position) => position.run_id && runIds.has(position.run_id),
    )
    const tickers = new Set<string>()
    configOrders.forEach((order) => order.ticker && tickers.add(order.ticker))
    configSignals.forEach((signal) => signal.ticker && tickers.add(signal.ticker))
    const latestRun = configRuns[0] ?? null
    const latestActivity = latestDate([
      latestRun?.started_at,
      config.updated_at,
      ...configOrders.map((order) => order.created_at),
      ...configSignals.map((signal) => signal.created_at),
    ])
    const avgEdge =
      configSignals.length === 0
        ? null
        : configSignals.reduce((total, signal) => total + Number(signal.edge ?? 0), 0) /
          configSignals.length
    const openPnl = configPositions.reduce(
      (total, position) => total + Number(position.unrealized_pnl ?? 0),
      0,
    )
    const fills = configOrders.flatMap((order) => order.fills ?? [])
    const fees = fills.reduce((total, fill) => total + Number(fill.fee ?? 0), 0)
    const paperPnl = openPnl - fees
    return {
      config,
      latestRun,
      runs: configRuns.length,
      orders: configOrders.length,
      fills: fills.length,
      signals: configSignals.length,
      tickers: tickers.size,
      latestActivity,
      avgEdge,
      openPositions: configPositions.length,
      openPnl,
      fees,
      paperPnl,
    }
  })

  const enabled = configs.filter((config) => config.status === 'enabled').length
  const activeRuns = runs.filter((run) => !run.ended_at).length
  const paperRuns = runs.filter((run) => run.mode === 'paper').length
  const openPnl = positions.reduce(
    (total, position) => total + Number(position.unrealized_pnl ?? 0),
    0,
  )
  const totalFees = rows.reduce((total, row) => total + row.fees, 0)
  const paperPnl = openPnl - totalFees

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Strategies</h1>
          <p className="pageKicker">Running configs, modes, watched markets, and latest activity.</p>
        </div>
        <span className="badge badgeGreen">{activeRuns} active runs</span>
      </section>

      <section className="grid summaryGrid">
        <div className="card">
          <div className="cardLabel">Enabled</div>
          <div className="cardValue">{enabled}</div>
          <div className="cardMeta">{configs.length} total configs</div>
        </div>
        <div className="card">
          <div className="cardLabel">Paper Runs</div>
          <div className="cardValue">{paperRuns}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Open Positions</div>
          <div className="cardValue">{positions.length}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Paper PnL</div>
          <div className={paperPnl >= 0 ? 'cardValue green' : 'cardValue red'}>
            {formatCurrency(paperPnl)}
          </div>
          <div className="cardMeta">{formatCurrency(openPnl)} open PnL</div>
        </div>
      </section>

      <section className="grid strategyGrid">
        {rows.map((row) => (
          <article className="strategyCard" key={row.config.id}>
            <div className="marketTop">
              <div>
                <h2 className="cardTitle">{row.config.name ?? '-'}</h2>
                <div className="muted">
                  v{row.config.version ?? '-'} / {row.latestRun?.id.slice(0, 8) ?? 'no run'}
                </div>
              </div>
              <StatusBadge value={row.config.status} />
            </div>
            <div className="metricRow">
              <div>
                <div className="metricLabel">Mode</div>
                <div className="metricValue">{row.latestRun?.mode ?? paramsMode(row.config.params_json)}</div>
              </div>
              <div>
                <div className="metricLabel">Markets</div>
                <div className="metricValue">{row.tickers}</div>
              </div>
              <div>
                <div className="metricLabel">Signals</div>
                <div className="metricValue">{row.signals}</div>
              </div>
            </div>
            <div className="metricLine">
              <span className="muted">Orders</span>
              <span className="mono">{row.orders}</span>
            </div>
            <div className="metricLine">
              <span className="muted">Fills</span>
              <span className="mono">{row.fills}</span>
            </div>
            <div className="metricLine">
              <span className="muted">Avg Edge</span>
              <span className="mono blue">{formatNumber(row.avgEdge)}</span>
            </div>
            <div className="metricLine">
              <span className="muted">Paper PnL</span>
              <span className={row.paperPnl >= 0 ? 'mono green' : 'mono red'}>
                {formatCurrency(row.paperPnl)}
              </span>
            </div>
            <div className="metricLine">
              <span className="muted">Open PnL</span>
              <span className={row.openPnl >= 0 ? 'mono green' : 'mono red'}>
                {formatCurrency(row.openPnl)}
              </span>
            </div>
            <div className="metricLine">
              <span className="muted">Latest Activity</span>
              <span className="mono">{formatAge(row.latestActivity)}</span>
            </div>
            <div className="cardMeta">
              started {formatDateTime(row.latestRun?.started_at)} / runs {row.runs}
            </div>
          </article>
        ))}
      </section>
    </>
  )
}
