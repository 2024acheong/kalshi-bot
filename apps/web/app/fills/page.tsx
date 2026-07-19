import { formatCurrency, formatDateTime, formatNumber } from '@/components/Format'
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
      }
    | {
        ticker: string | null
        side: string | null
      }[]
    | null
}

function parentOrder(fill: FillWithOrder) {
  if (Array.isArray(fill.orders)) {
    return fill.orders[0] ?? null
  }
  return fill.orders ?? null
}

type ParentOrder = {
    ticker: string | null
    side: string | null
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
        side
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

      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Side</th>
              <th>Fill Price</th>
              <th>Fill Qty</th>
              <th>Fee</th>
              <th>Fill Type</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {fills.map((fill) => (
              <FillRow key={fill.id} fill={fill} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function FillRow({ fill }: { fill: FillWithOrder }) {
  const order: ParentOrder | null = parentOrder(fill)
  return (
    <tr>
      <td className="blue">{order?.ticker ?? '-'}</td>
      <td>{order?.side ?? '-'}</td>
      <td>{formatNumber(fill.fill_price)}</td>
      <td>{formatNumber(fill.fill_qty)}</td>
      <td>{formatNumber(fill.fee)}</td>
      <td>{fill.fill_type ?? '-'}</td>
      <td>{formatDateTime(fill.created_at)}</td>
    </tr>
  )
}
