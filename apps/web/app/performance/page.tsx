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
  config_id: string | null
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
        ticker: string | null
        side: string | null
        signal_id: string | null
        signals?: SignalRelation | SignalRelation[] | null
        strategy_runs?: RunRelation | RunRelation[] | null
      }
    | {
        run_id: string | null
        ticker: string | null
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

type PaperAccountRow = {
  id: string
  config_id: string | null
  starting_cash: number | string | null
  cash_balance: number | string | null
  reserved_cash: number | string | null
  status: string | null
}

type RealizedLot = {
  qty: number
  cost: number
  fees: number
}

type RealizedRunPnl = {
  realizedFees: number
  realizedPnl: number
}

const MAX_PERFORMANCE_FILLS = 2000
const MAX_PERFORMANCE_SIGNALS = 2000
const MAX_PERFORMANCE_POSITIONS = 1000

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

function oppositeSide(side: string | null | undefined) {
  if (side === 'yes') {
    return 'no'
  }
  if (side === 'no') {
    return 'yes'
  }
  return null
}

function sortedFills(fills: FillWithOrder[]) {
  return fills
    .slice()
    .sort(
      (a, b) =>
        new Date(a.created_at ?? 0).getTime() - new Date(b.created_at ?? 0).getTime(),
    )
}

function positionKey(runId: string, ticker: string | null | undefined, side: string) {
  return `${runId}:${ticker ?? 'unknown'}:${side}`
}

function buildRealizedPnl(fills: FillWithOrder[]) {
  const lots = new Map<string, RealizedLot[]>()
  const byRun = new Map<string, RealizedRunPnl>()
  const events: PerformanceFillEvent[] = []

  for (const fill of sortedFills(fills)) {
    if (!fill.created_at) {
      continue
    }
    const order = parentOrder(fill)
    const run = firstRelation(order?.strategy_runs)
    const runId = order?.run_id ?? run?.id ?? 'unknown'
    const label = runLabel(run, runId)
    if (label.startsWith('spread_capture')) {
      continue
    }

    const side = order?.side
    const qty = Number(fill.fill_qty ?? 0)
    if (qty <= 0 || (side !== 'yes' && side !== 'no')) {
      continue
    }

    const notional = fillNotional(fill)
    const fee = Number(fill.fee ?? 0)
    if (!signalPayload(fill)?.is_closing_order) {
      const key = positionKey(runId, order?.ticker, side)
      const runLots = lots.get(key) ?? []
      runLots.push({ qty, cost: notional, fees: fee })
      lots.set(key, runLots)
      continue
    }

    const openedSide = oppositeSide(side)
    if (!openedSide) {
      continue
    }
    const key = positionKey(runId, order?.ticker, openedSide)
    const runLots = lots.get(key) ?? []
    let remainingQty = qty
    let realizedPnl = 0
    let realizedFees = 0
    const closeAvg = notional / qty

    while (remainingQty > 0 && runLots.length > 0) {
      const lot = runLots[0]
      const matchedQty = Math.min(remainingQty, lot.qty)
      const entryAvg = lot.cost / lot.qty
      const entryFeeShare = (lot.fees * matchedQty) / lot.qty
      const closeFeeShare = (fee * matchedQty) / qty
      realizedPnl += matchedQty * (1 - entryAvg - closeAvg) - entryFeeShare - closeFeeShare
      realizedFees += entryFeeShare + closeFeeShare

      lot.qty -= matchedQty
      lot.cost -= entryAvg * matchedQty
      lot.fees -= entryFeeShare
      remainingQty -= matchedQty
      if (lot.qty <= 0) {
        runLots.shift()
      }
    }

    lots.set(key, runLots)
    if (realizedFees > 0 || realizedPnl !== 0) {
      const row = byRun.get(runId) ?? { realizedFees: 0, realizedPnl: 0 }
      row.realizedFees += realizedFees
      row.realizedPnl += realizedPnl
      byRun.set(runId, row)
      events.push({
        runId,
        label,
        timestamp: fill.created_at,
        signedValue: realizedPnl,
      })
    }
  }

  return { byRun, events }
}

function buildSpreadArbEvents(fills: FillWithOrder[]) {
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
  for (const fill of sortedFills(fills)) {
    if (!fill.created_at) {
      continue
    }
    const order = parentOrder(fill)
    const run = firstRelation(order?.strategy_runs)
    const runId = order?.run_id ?? run?.id ?? 'unknown'
    const label = runLabel(run, runId)
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
  return arbEvents.sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  )
}

export default async function PerformancePage() {
  const [
    fillsResponse,
    positionsResponse,
    signalsResponse,
    accountsResponse,
  ] = await Promise.all([
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
            ticker,
            side,
            signal_id,
            signals (
              signal_payload
            ),
            strategy_runs (
              id,
              config_id,
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
      MAX_PERFORMANCE_FILLS,
    ),
    fetchAllRows<PositionRow>((from, to) =>
      supabase
        .from('positions')
        .select('run_id,ticker,side,qty,avg_entry,unrealized_pnl')
        .neq('qty', 0)
        .range(from, to),
      500,
      MAX_PERFORMANCE_POSITIONS,
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
              config_id,
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
      MAX_PERFORMANCE_SIGNALS,
    ),
    fetchAllRows<PaperAccountRow>((from, to) =>
      supabase
        .from('paper_accounts')
        .select('id,config_id,starting_cash,cash_balance,reserved_cash,status')
        .eq('status', 'active')
        .range(from, to),
    ),
  ])

  const firstError =
    fillsResponse.error ??
    positionsResponse.error ??
    signalsResponse.error ??
    accountsResponse.error
  if (firstError) {
    return <div className="card red">Error loading performance: {firstError.message}</div>
  }

  const fills = fillsResponse.data
  const positions = positionsResponse.data
  const signals = signalsResponse.data
  const accounts = accountsResponse.data
  const accountByConfigId = new Map(
    accounts
      .filter((account) => account.config_id)
      .map((account) => [account.config_id as string, account]),
  )
  const byRun = new Map<
    string,
    {
      label: string
      configId: string | null
      mode: string
      fills: number
      fees: number
      lockedArbFees: number
      lockedArbPnl: number
      notional: number
      latest: string | null
      openMarkPnl: number
      realizedFees: number
      realizedPnl: number
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
        configId: run?.config_id ?? null,
        mode: run?.mode ?? 'paper',
        fills: 0,
        fees: 0,
        lockedArbFees: 0,
        lockedArbPnl: 0,
        notional: 0,
        latest: null,
        openMarkPnl: 0,
        realizedFees: 0,
        realizedPnl: 0,
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
      row.openMarkPnl += Number(position.unrealized_pnl ?? 0)
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
        configId: run?.config_id ?? null,
        mode: run?.mode ?? 'paper',
        fills: 0,
        fees: 0,
        lockedArbFees: 0,
        lockedArbPnl: 0,
        notional: 0,
        latest: null,
        openMarkPnl: 0,
        realizedFees: 0,
        realizedPnl: 0,
        signals: 0,
        edgeTotal: 0,
        probabilityTotal: 0,
      }
    row.signals += 1
    row.edgeTotal += Number(signal.edge ?? 0)
    row.probabilityTotal += Number(signal.prob_estimate ?? 0)
    byRun.set(signal.run_id, row)
  }

  const realized = buildRealizedPnl(fills)
  for (const [runId, realizedRow] of realized.byRun.entries()) {
    const row = byRun.get(runId)
    if (row) {
      row.realizedFees += realizedRow.realizedFees
      row.realizedPnl += realizedRow.realizedPnl
    }
  }

  const rows = [...byRun.entries()]
    .map(([runId, row]) => {
      const account = row.configId ? accountByConfigId.get(row.configId) ?? null : null
      const startingCash = Number(account?.starting_cash ?? 0)
      const cashBalance = Number(account?.cash_balance ?? 0)
      const reservedCash = Number(account?.reserved_cash ?? 0)
      const paperPnl = account
        ? cashBalance + reservedCash - startingCash
        : row.openMarkPnl +
          row.lockedArbPnl +
          row.realizedPnl -
          (row.fees - row.lockedArbFees - row.realizedFees)
      return {
        runId,
        ...row,
        paperPnl,
        account,
        cashBalance,
        reservedCash,
        buyingPower: Math.max(cashBalance - reservedCash, 0),
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
  const totalBuyingPower = rows.reduce((total, row) => total + row.buyingPower, 0)
  const avgFillRate =
    rows.length === 0
      ? null
      : rows.reduce((total, row) => total + Number(row.fillRate ?? 0), 0) / rows.length
  const fillEvents = [...buildSpreadArbEvents(fills), ...realized.events].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  )

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
          <div className="cardLabel">Buying Power</div>
          <div className="cardValue green">{formatCurrency(totalBuyingPower)}</div>
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
              <th>Realized</th>
              <th>Locked Arb</th>
              <th>Open Mark</th>
              <th>Buying Power</th>
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
                <td>{formatCurrency(row.realizedPnl)}</td>
                <td>{formatCurrency(row.lockedArbPnl)}</td>
                <td>{formatCurrency(row.openMarkPnl)}</td>
                <td>{row.account ? formatCurrency(row.buyingPower) : '-'}</td>
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
