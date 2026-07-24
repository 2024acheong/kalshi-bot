'use client'

import { useMemo, useState } from 'react'
import { formatDateTime, formatNumber } from '@/components/Format'

export type FillTableRow = {
  id: string
  ticker: string | null
  side: string | null
  strategy_name: string | null
  run_id: string | null
  mode: string | null
  fill_price: number | string | null
  fill_qty: number | null
  fee: number | string | null
  fill_type: string | null
  created_at: string | null
}

type FillsFilterProps = {
  fills: FillTableRow[]
}

export function FillsFilter({ fills }: FillsFilterProps) {
  const [strategy, setStrategy] = useState('all')
  const strategies = useMemo(
    () => Array.from(new Set(fills.map((fill) => fill.strategy_name).filter(Boolean))).sort(),
    [fills],
  )
  const filtered = useMemo(() => {
    if (strategy === 'all') {
      return fills
    }
    return fills.filter((fill) => fill.strategy_name === strategy)
  }, [fills, strategy])

  return (
    <>
      <div className="toolbar">
        <p className="pageKicker">{filtered.length} fills shown</p>
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
      </div>
      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th>Strategy</th>
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
            {filtered.map((fill) => (
              <tr key={fill.id}>
                <td>
                  <div className="blue">{fill.strategy_name ?? '-'}</div>
                  <div className="muted">
                    {fill.mode ?? '-'} / {fill.run_id?.slice(0, 8) ?? '-'}
                  </div>
                </td>
                <td className="blue">{fill.ticker ?? '-'}</td>
                <td>{fill.side ?? '-'}</td>
                <td>{formatNumber(fill.fill_price)}</td>
                <td>{formatNumber(fill.fill_qty)}</td>
                <td>{formatNumber(fill.fee)}</td>
                <td>{fill.fill_type ?? '-'}</td>
                <td>{formatDateTime(fill.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
