import { supabase } from '@/lib/supabase'

export default async function Home() {
  const { data: markets, error } = await supabase
    .from('market_catalog')
    .select(`
      ticker,
      title,
      category,
      status,
      close_time,
      market_snapshots (
        yes_bid,
        yes_ask,
        last_price,
        timestamp
      )
    `)
    .order('synced_at', { ascending: false })
    .limit(10)

  if (error) {
    console.error(error)
    return <div>Error loading markets: {error.message}</div>
  }

  return (
    <main style={{
      padding: '2rem',
      fontFamily: 'monospace',
      backgroundColor: '#0f0f0f',
      minHeight: '100vh',
      color: '#e2e8f0'
    }}>
      <h1 style={{ color: '#60a5fa', marginBottom: '0.5rem' }}>
        Kalshi Bot
      </h1>
      <p style={{ color: '#64748b', marginBottom: '2rem', fontSize: '0.85rem' }}>
        {markets?.length ?? 0} markets tracked
      </p>

      {markets?.map(m => {
        const snap = m.market_snapshots?.[0]
        return (
          <div key={m.ticker} style={{
            marginBottom: '1rem',
            padding: '1rem',
            border: '1px solid #1e293b',
            borderRadius: '6px',
            backgroundColor: '#1a1a2e'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <span style={{ color: '#60a5fa', fontWeight: 'bold', fontSize: '0.85rem' }}>
                  {m.ticker}
                </span>
                <p style={{ margin: '0.25rem 0', fontSize: '0.9rem' }}>{m.title}</p>
                <span style={{ fontSize: '0.75rem', color: '#64748b' }}>
                  {m.category} · closes {m.close_time ? new Date(m.close_time).toLocaleDateString() : 'N/A'}
                </span>
              </div>
              {snap && (
                <div style={{ textAlign: 'right', fontSize: '0.85rem' }}>
                  <div style={{ color: '#4ade80' }}>Bid: {snap.yes_bid ?? '—'}</div>
                  <div style={{ color: '#f87171' }}>Ask: {snap.yes_ask ?? '—'}</div>
                  <div style={{ color: '#94a3b8', fontSize: '0.75rem' }}>
                    Last: {snap.last_price ?? '—'}
                  </div>
                </div>
              )}
            </div>
          </div>
        )
      })}
    </main>
  )
}