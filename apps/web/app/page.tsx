import { formatAge, formatCurrency, formatDateTime, formatNumber } from '@/components/Format'
import { StatusBadge } from '@/components/StatusBadge'
import { supabase } from '@/lib/supabase'

export const dynamic = 'force-dynamic'

type FillRow = {
  id: string
  fill_price: number | string | null
  fill_qty: number | null
  fee: number | string | null
  fill_type: string | null
  created_at: string | null
}

type OrderRow = {
  id: string
  ticker: string | null
  intent: string | null
  side: string | null
  price: number | string | null
  qty: number | null
  risk_decision: string | null
  status: string | null
  created_at: string | null
  fills?: FillRow[] | null
}

function startOfTodayIso() {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  return start.toISOString()
}

function fillGross(fill: FillRow) {
  return Number(fill.fill_qty ?? 0) * Number(fill.fill_price ?? 0)
}

export default async function OverviewPage() {
  const startIso = startOfTodayIso()

  const [
    ordersToday,
    fillsToday,
    latestSnapshot,
    latestHeartbeat,
    recentOrders,
  ] = await Promise.all([
    supabase
      .from('orders')
      .select('id', { count: 'exact', head: true })
      .gte('created_at', startIso),
    supabase
      .from('fills')
      .select('fill_qty, fill_price, fee, created_at')
      .gte('created_at', startIso),
    supabase
      .from('market_snapshots')
      .select('created_at')
      .order('created_at', { ascending: false })
      .limit(1),
    supabase
      .from('system_events')
      .select('created_at,payload_json')
      .eq('event_type', 'worker_heartbeat')
      .order('created_at', { ascending: false })
      .limit(1),
    supabase
      .from('orders')
      .select(`
        id,
        ticker,
        intent,
        side,
        price,
        qty,
        risk_decision,
        status,
        created_at,
        fills (
          id,
          fill_price,
          fill_qty,
          fee,
          fill_type,
          created_at
        )
      `)
      .order('created_at', { ascending: false })
      .limit(15),
  ])

  const fills = (fillsToday.data ?? []) as FillRow[]
  const grossExposure = fills.reduce((total, fill) => total + fillGross(fill), 0)
  const feesPaid = fills.reduce((total, fill) => total + Number(fill.fee ?? 0), 0)
  const latestSnapshotAt = latestSnapshot.data?.[0]?.created_at ?? null
  const latestHeartbeatAt = latestHeartbeat.data?.[0]?.created_at ?? null
  const latestActivity = latestHeartbeatAt ?? latestSnapshotAt
  const workerIsStale =
    !latestActivity || Date.now() - new Date(latestActivity).getTime() > 2 * 60 * 1000
  const recent = (recentOrders.data ?? []) as OrderRow[]
  const allowedOrders = recent.filter((order) => order.risk_decision === 'allow').length
  const blockedOrders = recent.filter((order) => order.risk_decision === 'block').length
  const filledOrders = recent.filter((order) => (order.fills ?? []).length > 0).length
  const activityBars = recent.length > 0
    ? recent
        .slice()
        .reverse()
        .map((order, index) => {
          const qty = Number(order.qty ?? 0)
          const base = 18 + ((index + 1) / Math.max(recent.length, 1)) * 54
          return Math.min(100, Math.max(18, base + qty))
        })
    : [24, 30, 28, 42, 38, 55, 51, 66, 63, 74, 70, 82, 78, 88, 84]

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Overview</h1>
          <p className="pageKicker">Worker health, trading volume, and recent decisions.</p>
        </div>
        {workerIsStale ? (
          <span className="badge badgeYellow">Worker may be offline</span>
        ) : (
          <span className="badge badgeGreen">Worker active</span>
        )}
      </section>

      <section className="grid summaryGrid">
        <div className="card">
          <div className="cardLabel">Orders Today</div>
          <div className="cardValue">{ordersToday.count ?? 0}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Fills Today</div>
          <div className="cardValue">{fills.length}</div>
          <div className="cardMeta">{formatCurrency(grossExposure)} gross exposure</div>
        </div>
        <div className="card">
          <div className="cardLabel">Fees Today</div>
          <div className="cardValue">{formatCurrency(feesPaid)}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Worker Heartbeat</div>
          <div className="cardValue">{formatAge(latestActivity)}</div>
          <div className="cardMeta">{formatDateTime(latestActivity)}</div>
        </div>
      </section>

      <section className="cockpitGrid">
        <div className="chartPanel">
          <div className="pageHeader">
            <div>
              <h2 className="pageTitle">Execution Curve</h2>
              <p className="pageKicker">Recent order activity by submission sequence.</p>
            </div>
            <span className="badge badgeGray">last {recent.length || 15}</span>
          </div>
          <div className="equityBars" aria-label="Recent order activity chart">
            {activityBars.map((height, index) => (
              <div
                aria-hidden="true"
                className="equityBar"
                key={`${height}-${index}`}
                style={{ height: `${height}%` }}
              />
            ))}
          </div>
        </div>

        <div className="metricsPanel">
          <div className="cardLabel">Strategy Metrics</div>
          <div className="metricLine">
            <span className="muted">Allowed Orders</span>
            <span className="mono green">{allowedOrders}</span>
          </div>
          <div className="metricLine">
            <span className="muted">Blocked Orders</span>
            <span className="mono red">{blockedOrders}</span>
          </div>
          <div className="metricLine">
            <span className="muted">Filled Orders</span>
            <span className="mono blue">{filledOrders}</span>
          </div>
          <div className="metricLine">
            <span className="muted">Net After Fees</span>
            <span className="mono">{formatCurrency(grossExposure - feesPaid)}</span>
          </div>
          <div className="metricLine">
            <span className="muted">Worker Activity</span>
            <span className={workerIsStale ? 'mono yellow' : 'mono green'}>
              {workerIsStale ? 'stale' : 'live'}
            </span>
          </div>
        </div>
      </section>

      <section>
        <div className="pageHeader">
          <div>
            <h2 className="pageTitle">Recent Activity</h2>
            <p className="pageKicker">Last 15 orders with fill outcomes.</p>
          </div>
        </div>
        <div className="activityList">
          {recent.map((order) => {
            const fillsForOrder = order.fills ?? []
            const filledQty = fillsForOrder.reduce((total, fill) => total + Number(fill.fill_qty ?? 0), 0)
            const fillText =
              fillsForOrder.length > 0
                ? `${filledQty} filled via ${fillsForOrder[0]?.fill_type ?? 'fill'}`
                : 'no fill'
            return (
              <div className="activityItem" key={order.id}>
                <div>
                  <div className="blue">{order.ticker ?? '-'}</div>
                  <div className="muted">
                    {order.intent ?? '-'} / {order.side ?? '-'} / {formatDateTime(order.created_at)}
                  </div>
                </div>
                <div>
                  <div>{formatNumber(order.qty)} @ {formatNumber(order.price)}</div>
                  <div className="muted">status {order.status ?? '-'}</div>
                </div>
                <div>
                  <StatusBadge value={order.risk_decision} />
                  <div className="cardMeta">{fillText}</div>
                </div>
              </div>
            )
          })}
        </div>
      </section>
    </>
  )
}
