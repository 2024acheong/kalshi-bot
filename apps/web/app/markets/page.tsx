import { MarketsRealtime, type MarketRow, type SnapshotRow } from '@/components/MarketsRealtime'
import { supabase } from '@/lib/supabase'

export const dynamic = 'force-dynamic'

type CatalogRow = {
  ticker: string
  title: string | null
  category: string | null
  close_time: string | null
  status: string | null
  synced_at: string | null
}

export default async function MarketsPage() {
  const supabaseUrl = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL
  const supabasePublishableKey =
    process.env.SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
  if (!supabaseUrl || !supabasePublishableKey) {
    return (
      <div className="card red">
        SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required for live market updates.
      </div>
    )
  }

  const catalogResult = await supabase
    .from('market_catalog')
    .select('ticker, title, category, close_time, status, synced_at')
    .order('synced_at', { ascending: false })

  if (catalogResult.error) {
    return <div className="card red">Error loading markets: {catalogResult.error.message}</div>
  }

  const catalog = (catalogResult.data ?? []) as CatalogRow[]
  const catalogTickers = new Set(catalog.map((market) => market.ticker))
  let snapshots: SnapshotRow[] = []
  let snapshotWarning: string | null = null

  if (catalog.length > 0) {
    const snapshotResult = await supabase
      .from('market_snapshots')
      .select(`
        ticker,
        timestamp,
        yes_bid,
        yes_ask,
        yes_bid_size,
        yes_ask_size,
        last_price,
        volume_24h,
        open_interest,
        source,
        raw_sequence,
        created_at
      `)
      .order('created_at', { ascending: false })
      .limit(1000)

    if (snapshotResult.error) {
      console.error('Error loading market snapshots', snapshotResult.error)
      snapshotWarning = `Snapshots unavailable: ${snapshotResult.error.message}`
    } else {
      snapshots = ((snapshotResult.data ?? []) as SnapshotRow[]).filter((snapshot) =>
        catalogTickers.has(snapshot.ticker),
      )
    }
  }

  const latestByTicker = new Map<string, SnapshotRow>()
  for (const snapshot of snapshots) {
    if (!latestByTicker.has(snapshot.ticker)) {
      latestByTicker.set(snapshot.ticker, snapshot)
    }
  }

  const markets: MarketRow[] = catalog.map((market) => ({
    ...market,
    snapshot: latestByTicker.get(market.ticker) ?? null,
  }))

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Market Universe</h1>
          <p className="pageKicker">{markets.length} watched markets with live snapshot inserts.</p>
        </div>
        {snapshotWarning ? <span className="badge badgeYellow">{snapshotWarning}</span> : null}
      </section>
      <MarketsRealtime
        markets={markets}
        supabaseUrl={supabaseUrl}
        supabasePublishableKey={supabasePublishableKey}
      />
    </>
  )
}
