import { formatCurrency, formatDateTime, formatNumber, formatPercent } from '@/components/Format'
import { PerformanceChart, type PerformanceFillEvent } from '@/components/PerformanceChart'
import { fetchAllRows } from '@/lib/fetchAllRows'
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

type FillWithOrder = FillRow & {
  id: string
  orders?:
    | {
        run_id: string | null
        side: string | null
        signal_id: string | null
        signals?: SignalRelation | SignalRelation[] | null
        strategy_runs?: RunRelation | RunRelation[] | null
      }
    | {
        run_id: string | null
        side: string | null
        signal_id: string | null
        signals?: SignalRelation | SignalRelation[] | null
        strategy_runs?: RunRelation | RunRelation[] | null
      }[]
    | null
}

type SignalRelation = {
  signal_payload?: {
    is_closing_order?: boolean
  } | null
}

type PositionRow = {
  run_id: string | null
  ticker: string | null
  side: string | null
  qty: number | null
  avg_entry: number | string | null
  unrealized_pnl: number | string | null
}

type SignalRow = {
  run_id: string | null
  edge: number | string | null
  prob_estimate: number | string | null
  strategy_runs?: RunRelation | RunRelation[] | null
}

type MarketSnapshotRow = {
  ticker: string | null
  yes_bid: number | string | null
  no_bid: number | string | null
  timestamp: string | null
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

function parentOrder(fill: FillWithOrder) {
  return Array.isArray(fill.orders) ? fill.orders[0] ?? null : fill.orders ?? null
}

function signalPayload(fill: FillWithOrder) {
  return firstRelation(parentOrder(fill)?.signals)?.signal_payload ?? null
}

function buildFillEvents(fills: FillWithOrder[]) {
  const nonArbEvents: PerformanceFillEvent[] = []
  const arbPairs = new Map<
    string,
    {
      runId: string
      label: string
      timestamp: string
      yesQty: number
      yesCost: number
      yesFees: number
      noQty: number
      noCost: number
      noFees: number
    }
  >()
  for (const fill of fills) {
    if (!fill.created_at) {
      continue
    }
    const order = parentOrder(fill)
    const run = firstRelation(order?.strategy_runs)
    const runId = order?.run_id ?? run?.id ?? 'unknown'
    const label = runLabel(run, runId)
    const isClosing = Boolean(signalPayload(fill)?.is_closing_order)
    const notional = fillNotional(fill)
    const fee = Number(fill.fee ?? 0)
    const signalId = order?.signal_id
    const side = order?.side
    const qty = Number(fill.fill_qty ?? 0)
    if (signalId && qty > 0 && (side === 'yes' || side === 'no') && label.startsWith('spread_capture')) {
      const pairKey = `${runId}:${signalId}`
      const pair =
        arbPairs.get(pairKey) ??
        {
          runId,
          label,
          timestamp: fill.created_at,
          yesQty: 0,
          yesCost: 0,
          yesFees: 0,
          noQty: 0,
          noCost: 0,
          noFees: 0,
        }
      pair.timestamp =
        new Date(fill.created_at).getTime() > new Date(pair.timestamp).getTime()
          ? fill.created_at
          : pair.timestamp
      if (side === 'yes') {
        pair.yesQty += qty
        pair.yesCost += notional
        pair.yesFees += fee
      } else {
        pair.noQty += qty
        pair.noCost += notional
        pair.noFees += fee
      }
      arbPairs.set(pairKey, pair)
      continue
    }
    if (!isClosing) {
      continue
    }
    nonArbEvents.push({
      runId,
      label,
      timestamp: fill.created_at,
      signedValue: notional - fee,
    })
  }
  const arbEvents = [...arbPairs.values()].flatMap((pair) => {
    const matchedQty = Math.min(pair.yesQty, pair.noQty)
    if (matchedQty <= 0) {
      return []
    }
    const yesAvg = pair.yesCost / pair.yesQty
    const noAvg = pair.noCost / pair.noQty
    const feeShare =
      (pair.yesFees * matchedQty) / pair.yesQty +
      (pair.noFees * matchedQty) / pair.noQty
    return [
      {
        runId: pair.runId,
        label: pair.label,
        timestamp: pair.timestamp,
        signedValue: matchedQty * (1 - yesAvg - noAvg) - feeShare,
      },
    ]
  })
  return [...arbEvents, ...nonArbEvents].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  )
}

export default async function PerformancePage() {
  const [fillsResponse, positionsResponse, signalsResponse, snapshotsResponse] = await Promise.all([
    fetchAllRows<FillWithOrder>(
      (from, to) => supabase
        .from('fills')
        .select(`
          id,
          fill_price,
          fill_qty,
          fee,
          created_at,
          orders (
            run_id,
            side,
            signal_id,
            signals (
              signal_payload
            ),
            strategy_runs (
              id,
              mode,
              strategy_configs (
                name,
                version
              )
            )
          )
        `)
        .order('created_at', { ascending: false })
        .range(from, to),
      500,
      3000,
    ),
    fetchAllRows<PositionRow>((from, to) =>
      supabase
        .from('positions')
        .select('run_id,ticker,side,qty,avg_entry,unrealized_pnl')
        .neq('qty', 0)
        .range(from, to),
    ),
    fetchAllRows<SignalRow>(
      (from, to) => supabase
        .from('signals')
        .select(`
          run_id,
          edge,
          prob_estimate,
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
        .range(from, to),
      500,
      3000,
    ),
    fetchAllRows<MarketSnapshotRow>(
      (from, to) => supabase
        .from('market_snapshots')
        .select('ticker,yes_bid,no_bid,timestamp')
        .order('timestamp', { ascending: false })
        .range(from, to),
      1000,
      5000,
    ),
  ])

  const firstError =
    fillsResponse.error ?? positionsResponse.error ?? signalsResponse.error ?? snapshotsResponse.error
  if (firstError) {
    return <div className="card red">Error loading performance: {firstError.message}</div>
  }

  const fills = fillsResponse.data
  const positions = positionsResponse.data
  const signals = signalsResponse.data
  const snapshots = snapshotsResponse.data
  const latestSnapshotByTicker = new Map<string, MarketSnapshotRow>()
  for (const snapshot of snapshots) {
    if (snapshot.ticker && !latestSnapshotByTicker.has(snapshot.ticker)) {
      latestSnapshotByTicker.set(snapshot.ticker, snapshot)
    }
  }
  const byRun = new Map<
    string,
    {
      label: string
      mode: string
      fills: number
      fees: number
      lockedArbFees: number
      lockedArbPnl: number
      notional: number
      latest: string | null
      openMarkPnl: number
      signals: number
      edgeTotal: number
      probabilityTotal: number
    }
  >()
  const arbPairs = new Map<
    string,
    {
      runId: string
      label: string
      yesQty: number
      yesCost: number
      yesFees: number
      noQty: number
      noCost: number
      noFees: number
    }
  >()

  for (const fill of fills) {
    const order = parentOrder(fill)
    const run = firstRelation(order?.strategy_runs)
    const runId = order?.run_id ?? run?.id ?? 'unknown'
    const row =
      byRun.get(runId) ??
      {
        label: runLabel(run, order?.run_id ?? runId),
        mode: run?.mode ?? 'paper',
        fills: 0,
        fees: 0,
        lockedArbFees: 0,
        lockedArbPnl: 0,
        notional: 0,
        latest: null,
        openMarkPnl: 0,
        signals: 0,
        edgeTotal: 0,
        probabilityTotal: 0,
      }
    row.fills += 1
    row.fees += Number(fill.fee ?? 0)
    row.notional += fillNotional(fill)
    if (!row.latest || (fill.created_at && fill.created_at > row.latest)) {
      row.latest = fill.created_at
    }
    byRun.set(runId, row)

    const signalId = order?.signal_id
    const side = order?.side
    const qty = Number(fill.fill_qty ?? 0)
    if (signalId && qty > 0 && (side === 'yes' || side === 'no') && row.label.startsWith('spread_capture')) {
      const pairKey = `${runId}:${signalId}`
      const pair =
        arbPairs.get(pairKey) ??
        {
          runId,
          label: row.label,
          yesQty: 0,
          yesCost: 0,
          yesFees: 0,
          noQty: 0,
          noCost: 0,
          noFees: 0,
        }
      if (side === 'yes') {
        pair.yesQty += qty
        pair.yesCost += fillNotional(fill)
        pair.yesFees += Number(fill.fee ?? 0)
      } else {
        pair.noQty += qty
        pair.noCost += fillNotional(fill)
        pair.noFees += Number(fill.fee ?? 0)
      }
      arbPairs.set(pairKey, pair)
    }
  }

  for (const pair of arbPairs.values()) {
    const matchedQty = Math.min(pair.yesQty, pair.noQty)
    if (matchedQty <= 0) {
      continue
    }
    const yesAvg = pair.yesCost / pair.yesQty
    const noAvg = pair.noCost / pair.noQty
    const feeShare =
      (pair.yesFees * matchedQty) / pair.yesQty +
      (pair.noFees * matchedQty) / pair.noQty
    const lockedPnl = matchedQty * (1 - yesAvg - noAvg) - feeShare
    const row = byRun.get(pair.runId)
    if (row) {
      row.lockedArbPnl += lockedPnl
      row.lockedArbFees += feeShare
    }
  }

  for (const position of positions) {
    if (!position.run_id) {
      continue
    }
    const row = byRun.get(position.run_id)
    if (row) {
      const latestSnapshot = position.ticker ? latestSnapshotByTicker.get(position.ticker) : null
      const side = position.side
      const mark =
        side === 'yes'
          ? Number(latestSnapshot?.yes_bid ?? NaN)
          : side === 'no'
            ? Number(latestSnapshot?.no_bid ?? NaN)
            : NaN
      const qty = Number(position.qty ?? 0)
      const avgEntry = Number(position.avg_entry ?? NaN)
      row.openMarkPnl +=
        Number.isFinite(mark) && Number.isFinite(avgEntry)
          ? (mark - avgEntry) * qty
          : Number(position.unrealized_pnl ?? 0)
    }
  }

  for (const signal of signals) {
    if (!signal.run_id) {
      continue
    }
    const run = firstRelation(signal.strategy_runs)
    const row =
      byRun.get(signal.run_id) ??
      {
        label: runLabel(run, signal.run_id),
        mode: run?.mode ?? 'paper',
        fills: 0,
        fees: 0,
        lockedArbFees: 0,
        lockedArbPnl: 0,
        notional: 0,
        latest: null,
        openMarkPnl: 0,
        signals: 0,
        edgeTotal: 0,
        probabilityTotal: 0,
      }
    row.signals += 1
    row.edgeTotal += Number(signal.edge ?? 0)
    row.probabilityTotal += Number(signal.prob_estimate ?? 0)
    byRun.set(signal.run_id, row)
  }

  const rows = [...byRun.entries()]
    .map(([runId, row]) => {
      const nonArbFees = row.fees - row.lockedArbFees
      const paperPnl = row.openMarkPnl + row.lockedArbPnl - nonArbFees
      return {
        runId,
        ...row,
        paperPnl,
        nonArbFees,
        fillRate: row.signals === 0 ? null : row.fills / row.signals,
        avgEdge: row.signals === 0 ? null : row.edgeTotal / row.signals,
        avgProbability: row.signals === 0 ? null : row.probabilityTotal / row.signals,
        drawdown: Math.min(0, paperPnl),
      }
    })
    .sort((a, b) => b.paperPnl - a.paperPnl)

  const totalPaperPnl = rows.reduce((total, row) => total + row.paperPnl, 0)
  const totalFees = rows.reduce((total, row) => total + row.fees, 0)
  const totalFills = rows.reduce((total, row) => total + row.fills, 0)
  const avgFillRate =
    rows.length === 0
      ? null
      : rows.reduce((total, row) => total + Number(row.fillRate ?? 0), 0) / rows.length
  const fillEvents = buildFillEvents(fills)

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Performance</h1>
          <p className="pageKicker">Paper PnL, fills, fees, fill rate, drawdown, and edge by strategy run.</p>
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
          <div className="cardLabel">Avg Fill Rate</div>
          <div className="cardValue">{formatPercent(avgFillRate)}</div>
        </div>
      </section>

      <section className="chartPanel performanceChartPanel">
        <div className="pageHeader">
          <div>
            <h2 className="pageTitle">Strategy Performance Curve</h2>
            <p className="pageKicker">Cumulative locked arbitrage and realized paper value by strategy run.</p>
          </div>
          <span className="badge badgeGray">{rows.length} runs</span>
        </div>
        <PerformanceChart events={fillEvents} />
      </section>

      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th>Strategy Run</th>
              <th>Mode</th>
              <th>Paper PnL</th>
              <th>Locked Arb</th>
              <th>Open Mark</th>
              <th>Fees</th>
              <th>Fills</th>
              <th>Fill Rate</th>
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
                <td>{formatCurrency(row.lockedArbPnl)}</td>
                <td>{formatCurrency(row.openMarkPnl)}</td>
                <td>{formatCurrency(row.fees)}</td>
                <td>{row.fills}</td>
                <td>{formatPercent(row.fillRate)}</td>
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
