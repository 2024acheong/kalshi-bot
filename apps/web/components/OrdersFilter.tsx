'use client'

import { useMemo, useState } from 'react'
import { formatDateTime, formatNumber } from '@/components/Format'
import { StatusBadge } from '@/components/StatusBadge'

export type OrderTableRow = {
  id: string
  ticker: string | null
  intent: string | null
  side: string | null
  price: number | string | null
  qty: number | null
  risk_decision: string | null
  status: string | null
  created_at: string | null
}

type OrdersFilterProps = {
  orders: OrderTableRow[]
}

const decisions = ['all', 'allow', 'block', 'reduce_only']

export function OrdersFilter({ orders }: OrdersFilterProps) {
  const [decision, setDecision] = useState('all')
  const filtered = useMemo(() => {
    if (decision === 'all') {
      return orders
    }
    return orders.filter((order) => order.risk_decision === decision)
  }, [decision, orders])

  return (
    <>
      <div className="toolbar">
        <p className="pageKicker">{filtered.length} orders shown</p>
        <div className="segments" aria-label="Filter risk decisions">
          {decisions.map((item) => (
            <button
              className={decision === item ? 'segment segmentActive' : 'segment'}
              key={item}
              onClick={() => setDecision(item)}
              type="button"
            >
              {item}
            </button>
          ))}
        </div>
      </div>
      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Intent</th>
              <th>Side</th>
              <th>Price</th>
              <th>Qty</th>
              <th>Risk</th>
              <th>Status</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((order) => (
              <tr key={order.id}>
                <td className="blue">{order.ticker ?? '-'}</td>
                <td>{order.intent ?? '-'}</td>
                <td>{order.side ?? '-'}</td>
                <td>{formatNumber(order.price)}</td>
                <td>{formatNumber(order.qty)}</td>
                <td><StatusBadge value={order.risk_decision} /></td>
                <td>{order.status ?? '-'}</td>
                <td>{formatDateTime(order.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
