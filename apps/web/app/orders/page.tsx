import { OrdersFilter, type OrderTableRow } from '@/components/OrdersFilter'
import { supabase } from '@/lib/supabase'

export const dynamic = 'force-dynamic'

export default async function OrdersPage() {
  const { data, error } = await supabase
    .from('orders')
    .select('id, ticker, intent, side, price, qty, risk_decision, status, created_at')
    .order('created_at', { ascending: false })
    .limit(100)

  if (error) {
    return <div className="card red">Error loading orders: {error.message}</div>
  }

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Order Blotter</h1>
          <p className="pageKicker">Live view of strategy order decisions.</p>
        </div>
      </section>
      <OrdersFilter orders={(data ?? []) as OrderTableRow[]} />
    </>
  )
}
