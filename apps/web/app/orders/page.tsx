import { OrdersFilter, type OrderTableRow } from '@/components/OrdersFilter'
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

type OrderWithRun = Omit<OrderTableRow, 'strategy_name' | 'strategy_version' | 'mode'> & {
  strategy_runs?: RunRelation | RunRelation[] | null
}

function firstRelation<T>(value: T | T[] | null | undefined) {
  return Array.isArray(value) ? value[0] ?? null : value ?? null
}

export default async function OrdersPage() {
  const { data, error } = await supabase
    .from('orders')
    .select(`
      id,
      run_id,
      ticker,
      intent,
      side,
      price,
      qty,
      risk_decision,
      status,
      created_at,
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
    .limit(100)

  if (error) {
    return <div className="card red">Error loading orders: {error.message}</div>
  }

  const orders = ((data ?? []) as unknown as OrderWithRun[]).map((order) => {
    const run = firstRelation(order.strategy_runs)
    const config = firstRelation(run?.strategy_configs)
    return {
      ...order,
      mode: run?.mode ?? null,
      strategy_name: config?.name ?? null,
      strategy_version: config?.version ?? null,
    }
  })

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Order Blotter</h1>
          <p className="pageKicker">Live view of strategy order decisions.</p>
        </div>
      </section>
      <OrdersFilter orders={orders} />
    </>
  )
}
