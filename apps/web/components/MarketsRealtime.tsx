'use client'

import { useEffect, useMemo, useState } from 'react'
import { createClient } from '@supabase/supabase-js'
import { formatAge, formatDateTime, formatNumber } from '@/components/Format'

export type SnapshotRow = {
  ticker: string
  timestamp: string | null
  yes_bid: number | string | null
  yes_ask: number | string | null
  yes_bid_size: number | null
  yes_ask_size: number | null
  last_price: number | string | null
  volume_24h: number | string | null
  open_interest: number | string | null
  source: string | null
  raw_sequence: number | null
  created_at: string | null
}

export type MarketRow = {
  ticker: string
  title: string | null
  category: string | null
  close_time: string | null
  status: string | null
  synced_at: string | null
  snapshot: SnapshotRow | null
}

type MarketsRealtimeProps = {
  markets: MarketRow[]
  supabaseUrl: string
  supabasePublishableKey: string
}

function spread(snapshot: SnapshotRow | null) {
  if (!snapshot?.yes_bid || !snapshot.yes_ask) {
    return null
  }
  return Number(snapshot.yes_ask) - Number(snapshot.yes_bid)
}

export function MarketsRealtime({
  markets,
  supabaseUrl,
  supabasePublishableKey,
}: MarketsRealtimeProps) {
  const [rows, setRows] = useState(markets)
  const watched = useMemo(() => new Set(markets.map((market) => market.ticker)), [markets])
  const browserSupabase = useMemo(
    () => createClient(supabaseUrl, supabasePublishableKey),
    [supabasePublishableKey, supabaseUrl],
  )

  useEffect(() => {
    const channel = browserSupabase
      .channel('market_snapshots_changes')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'market_snapshots' },
        (payload) => {
          const snapshot = payload.new as SnapshotRow
          if (!watched.has(snapshot.ticker)) {
            return
          }
          setRows((current) =>
            current.map((market) =>
              market.ticker === snapshot.ticker
                ? { ...market, snapshot }
                : market,
            ),
          )
        },
      )
      .subscribe()

    return () => {
      browserSupabase.removeChannel(channel)
    }
  }, [browserSupabase, watched])

  return (
    <section className="grid marketGrid">
      {rows.map((market) => {
        const currentSpread = spread(market.snapshot)
        return (
          <article className="marketCard" key={market.ticker}>
            <div className="marketTop">
              <div>
                <div className="blue">{market.ticker}</div>
                <p style={{ margin: '0.35rem 0 0', fontSize: '0.85rem' }}>
                  {market.title ?? 'Untitled market'}
                </p>
                <div className="cardMeta">
                  {market.category ?? '-'} / {market.status ?? '-'} / closes {formatDateTime(market.close_time)}
                </div>
              </div>
              <span className="badge badgeGray">
                {formatAge(market.snapshot?.created_at)}
              </span>
            </div>

            <div className="metricRow">
              <div>
                <div className="metricLabel">Bid</div>
                <div className="metricValue green">{formatNumber(market.snapshot?.yes_bid)}</div>
                <div className="cardMeta">size {formatNumber(market.snapshot?.yes_bid_size)}</div>
              </div>
              <div>
                <div className="metricLabel">Ask</div>
                <div className="metricValue red">{formatNumber(market.snapshot?.yes_ask)}</div>
                <div className="cardMeta">size {formatNumber(market.snapshot?.yes_ask_size)}</div>
              </div>
              <div>
                <div className="metricLabel">Spread</div>
                <div className="metricValue yellow">{formatNumber(currentSpread)}</div>
                <div className="cardMeta">last {formatNumber(market.snapshot?.last_price)}</div>
              </div>
            </div>

            <div className="cardMeta">
              snapshot {formatDateTime(market.snapshot?.timestamp)} / seq {formatNumber(market.snapshot?.raw_sequence)}
            </div>
          </article>
        )
      })}
    </section>
  )
}
