'use client'

import { useMemo, useState } from 'react'
import { formatDateTime, formatNumber } from '@/components/Format'
import { StatusBadge } from '@/components/StatusBadge'

export type OrderTableRow = {
  id: string
  ticker: string | null
  run_id?: string | null
  strategy_name?: string | null
  strategy_version?: number | null
  mode?: string | null
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
  const [strategy, setStrategy] = useState('all')
  const strategies = useMemo(
    () =>
      Array.from(new Set(orders.map((order) => order.strategy_name).filter(Boolean))).sort(),
    [orders],
  )
  const filtered = useMemo(() => {
    return orders.filter((order) => {
      const decisionMatches = decision === 'all' || order.risk_decision === decision
      const strategyMatches = strategy === 'all' || order.strategy_name === strategy
      return decisionMatches && strategyMatches
    })
  }, [decision, orders, strategy])

  return (
    <>
      <div className="toolbar">
        <p className="pageKicker">{filtered.length} orders shown</p>
        <div className="toolbarControls">
          <select
            className="select"
            onChange={(event) => setStrategy(event.target.value)}
            value={strategy}
          >
            <option value="all">all strategies</option>
            {strategies.map((item) => (
              <option key={item} value={item ?? ''}>
                {item}
              </option>
            ))}
          </select>
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
      </div>
      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th>Strategy</th>
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
                <td>
                  <div className="blue">{order.strategy_name ?? '-'}</div>
                  <div className="muted">
                    {order.mode ?? '-'} / {order.run_id?.slice(0, 8) ?? '-'}
                  </div>
                </td>
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
