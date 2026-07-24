import { formatCurrency, formatDateTime, formatNumber, formatPercent } from '@/components/Format'
import { supabase } from '@/lib/supabase'

export const dynamic = 'force-dynamic'

type ConfigRelation = {
  name: string | null
  version: number | null
}

type RunRelation = {
  id: string
  mode: string | null
  strategy_configs?: ConfigRelation | ConfigRelation[] | null
}

type FillRow = {
  fill_price: number | string | null
  fill_qty: number | null
  fee: number | string | null
  created_at: string | null
}

type OrderRow = {
  id: string
  run_id: string | null
  status: string | null
  risk_decision: string | null
  price: number | string | null
  qty: number | null
  created_at: string | null
  fills?: FillRow[] | null
  strategy_runs?: RunRelation | RunRelation[] | null
}

type PositionRow = {
  run_id: string | null
  qty: number | null
  unrealized_pnl: number | string | null
}

type SignalRow = {
  run_id: string | null
  edge: number | string | null
  prob_estimate: number | string | null
}

function firstRelation<T>(value: T | T[] | null | undefined) {
  return Array.isArray(value) ? value[0] ?? null : value ?? null
}

function runLabel(run: RunRelation | null, fallbackRunId: string | null) {
  const config = firstRelation(run?.strategy_configs)
  const name = config?.name ?? 'unknown'
  const version = config?.version ?? '-'
  return `${name} v${version} / ${(run?.id ?? fallbackRunId ?? '').slice(0, 8)}`
}

function fillNotional(fill: FillRow) {
  return Number(fill.fill_price ?? 0) * Number(fill.fill_qty ?? 0)
}

export default async function PerformancePage() {
  const [ordersResponse, positionsResponse, signalsResponse] = await Promise.all([
    supabase
      .from('orders')
      .select(`
        id,
        run_id,
        status,
        risk_decision,
        price,
        qty,
        created_at,
        fills (
          fill_price,
          fill_qty,
          fee,
          created_at
        ),
        strategy_runs (
          id,
          mode,
          strategy_configs (
            name,
            version
          )
        )
      `)
      .order('created_at', { ascending: false })
      .limit(1000),
    supabase.from('positions').select('run_id,qty,unrealized_pnl'),
    supabase.from('signals').select('run_id,edge,prob_estimate').limit(5000),
  ])

  const firstError = ordersResponse.error ?? positionsResponse.error ?? signalsResponse.error
  if (firstError) {
    return <div className="card red">Error loading performance: {firstError.message}</div>
  }

  const orders = (ordersResponse.data ?? []) as unknown as OrderRow[]
  const positions = (positionsResponse.data ?? []) as PositionRow[]
  const signals = (signalsResponse.data ?? []) as SignalRow[]
  const byRun = new Map<
    string,
    {
      label: string
      mode: string
      orders: number
      allowed: number
      fills: number
      fees: number
      notional: number
      latest: string | null
      unrealizedPnl: number
      signals: number
      edgeTotal: number
      probabilityTotal: number
    }
  >()

  for (const order of orders) {
    const run = firstRelation(order.strategy_runs)
    const runId = order.run_id ?? run?.id ?? 'unknown'
    const row =
      byRun.get(runId) ??
      {
        label: runLabel(run, order.run_id),
        mode: run?.mode ?? 'paper',
        orders: 0,
        allowed: 0,
        fills: 0,
        fees: 0,
        notional: 0,
        latest: null,
        unrealizedPnl: 0,
        signals: 0,
        edgeTotal: 0,
        probabilityTotal: 0,
      }
    row.orders += 1
    if (order.risk_decision === 'allow') {
      row.allowed += 1
    }
    for (const fill of order.fills ?? []) {
      row.fills += 1
      row.fees += Number(fill.fee ?? 0)
      row.notional += fillNotional(fill)
    }
    if (!row.latest || (order.created_at && order.created_at > row.latest)) {
      row.latest = order.created_at
    }
    byRun.set(runId, row)
  }

  for (const position of positions) {
    if (!position.run_id) {
      continue
    }
    const row = byRun.get(position.run_id)
    if (row) {
      row.unrealizedPnl += Number(position.unrealized_pnl ?? 0)
    }
  }

  for (const signal of signals) {
    if (!signal.run_id) {
      continue
    }
    const row = byRun.get(signal.run_id)
    if (row) {
      row.signals += 1
      row.edgeTotal += Number(signal.edge ?? 0)
      row.probabilityTotal += Number(signal.prob_estimate ?? 0)
    }
  }

  const rows = [...byRun.entries()]
    .map(([runId, row]) => {
      const paperPnl = row.unrealizedPnl - row.fees
      return {
        runId,
        ...row,
        paperPnl,
        hitRate: row.allowed === 0 ? null : row.fills / row.allowed,
        avgEdge: row.signals === 0 ? null : row.edgeTotal / row.signals,
        avgProbability: row.signals === 0 ? null : row.probabilityTotal / row.signals,
        drawdown: Math.min(0, paperPnl),
      }
    })
    .sort((a, b) => b.paperPnl - a.paperPnl)

  const totalPaperPnl = rows.reduce((total, row) => total + row.paperPnl, 0)
  const totalFees = rows.reduce((total, row) => total + row.fees, 0)
  const totalFills = rows.reduce((total, row) => total + row.fills, 0)
  const avgHitRate =
    rows.length === 0
      ? null
      : rows.reduce((total, row) => total + Number(row.hitRate ?? 0), 0) / rows.length

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Performance</h1>
          <p className="pageKicker">Paper PnL, fills, fees, hit rate, drawdown, and edge by strategy run.</p>
        </div>
      </section>

      <section className="grid summaryGrid">
        <div className="card">
          <div className="cardLabel">Paper PnL</div>
          <div className={totalPaperPnl >= 0 ? 'cardValue green' : 'cardValue red'}>
            {formatCurrency(totalPaperPnl)}
          </div>
        </div>
        <div className="card">
          <div className="cardLabel">Fills</div>
          <div className="cardValue">{totalFills}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Fees</div>
          <div className="cardValue">{formatCurrency(totalFees)}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Avg Hit Rate</div>
          <div className="cardValue">{formatPercent(avgHitRate)}</div>
        </div>
      </section>

      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th>Strategy Run</th>
              <th>Mode</th>
              <th>Paper PnL</th>
              <th>Fees</th>
              <th>Fills</th>
              <th>Hit Rate</th>
              <th>Avg Edge</th>
              <th>Avg Prob</th>
              <th>Drawdown</th>
              <th>Latest</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.runId}>
                <td className="blue">{row.label}</td>
                <td>{row.mode}</td>
                <td className={row.paperPnl >= 0 ? 'green mono' : 'red mono'}>
                  {formatCurrency(row.paperPnl)}
                </td>
                <td>{formatCurrency(row.fees)}</td>
                <td>{row.fills}</td>
                <td>{formatPercent(row.hitRate)}</td>
                <td>{formatNumber(row.avgEdge)}</td>
                <td>{formatPercent(row.avgProbability)}</td>
                <td className="red">{formatCurrency(row.drawdown)}</td>
                <td>{formatDateTime(row.latest)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
