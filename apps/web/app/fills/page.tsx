import { FillsFilter, type FillTableRow } from '@/components/FillsFilter'
import { formatCurrency } from '@/components/Format'
import { supabase } from '@/lib/supabase'

export const dynamic = 'force-dynamic'

type FillWithOrder = {
  id: string
  fill_price: number | string | null
  fill_qty: number | null
  fee: number | string | null
  fill_type: string | null
  created_at: string | null
  orders?:
    | {
        ticker: string | null
        side: string | null
        run_id: string | null
        strategy_runs?: RunRelation | RunRelation[] | null
      }
    | {
        ticker: string | null
        side: string | null
        run_id: string | null
        strategy_runs?: RunRelation | RunRelation[] | null
      }[]
    | null
}

type ConfigRelation = {
  name: string | null
  version: number | null
}

type RunRelation = {
  id: string
  mode: string | null
  strategy_configs?: ConfigRelation | ConfigRelation[] | null
}

function parentOrder(fill: FillWithOrder) {
  if (Array.isArray(fill.orders)) {
    return fill.orders[0] ?? null
  }
  return fill.orders ?? null
}

function firstRelation<T>(value: T | T[] | null | undefined) {
  return Array.isArray(value) ? value[0] ?? null : value ?? null
}

export default async function FillsPage() {
  const { data, error } = await supabase
    .from('fills')
    .select(`
      id,
      fill_price,
      fill_qty,
      fee,
      fill_type,
      created_at,
      orders (
        ticker,
        side,
        run_id,
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

  if (error) {
    return <div className="card red">Error loading fills: {error.message}</div>
  }

  const fills = (data ?? []) as unknown as FillWithOrder[]
  const grossNotional = fills.reduce(
    (total, fill) => total + Number(fill.fill_qty ?? 0) * Number(fill.fill_price ?? 0),
    0,
  )
  const feesPaid = fills.reduce((total, fill) => total + Number(fill.fee ?? 0), 0)
  const tableRows: FillTableRow[] = fills.map((fill) => {
    const order = parentOrder(fill)
    const run = firstRelation(order?.strategy_runs)
    const config = firstRelation(run?.strategy_configs)
    return {
      id: fill.id,
      ticker: order?.ticker ?? null,
      side: order?.side ?? null,
      strategy_name: config?.name ?? null,
      run_id: order?.run_id ?? run?.id ?? null,
      mode: run?.mode ?? null,
      fill_price: fill.fill_price,
      fill_qty: fill.fill_qty,
      fee: fill.fee,
      fill_type: fill.fill_type,
      created_at: fill.created_at,
    }
  })

  return (
    <>
      <section className="pageHeader">
        <div>
          <h1 className="pageTitle">Execution Fills</h1>
          <p className="pageKicker">Detailed history of matched paper trades.</p>
        </div>
      </section>

      <section className="grid summaryGrid">
        <div className="card">
          <div className="cardLabel">Notional Traded</div>
          <div className="cardValue">{formatCurrency(grossNotional)}</div>
          <div className="cardMeta">fill_qty * fill_price</div>
        </div>
        <div className="card">
          <div className="cardLabel">Fees Paid</div>
          <div className="cardValue">{formatCurrency(feesPaid)}</div>
        </div>
        <div className="card">
          <div className="cardLabel">Net After Fees</div>
          <div className="cardValue">{formatCurrency(grossNotional - feesPaid)}</div>
          <div className="cardMeta">not profit/loss</div>
        </div>
        <div className="card">
          <div className="cardLabel">Fill Count</div>
          <div className="cardValue">{fills.length}</div>
        </div>
      </section>

      <FillsFilter fills={tableRows} />
    </>
  )
}
