'use client'

import { useMemo, useState } from 'react'

export type PerformanceFillEvent = {
  runId: string
  label: string
  timestamp: string
  signedValue: number
}

type Curve = {
  runId: string
  label: string
  color: string
  value: number
  path: string
}

type RangeKey = '1W' | '1M' | '3M' | 'YTD' | 'ALL'

const CHART_COLORS = ['#22d3ee', '#4ade80', '#c084fc', '#fbbf24', '#f87171', '#60a5fa']
const RANGES: RangeKey[] = ['1W', '1M', '3M', 'YTD', 'ALL']

function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    currency: 'USD',
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
    style: 'currency',
  }).format(value)
}

function addMonths(date: Date, months: number) {
  const next = new Date(date)
  next.setMonth(next.getMonth() + months)
  return next
}

function rangeStart(range: RangeKey, now: Date, minEventTime: number | null) {
  if (range === '1W') {
    return now.getTime() - 7 * 24 * 60 * 60 * 1000
  }
  if (range === '1M') {
    return addMonths(now, -1).getTime()
  }
  if (range === '3M') {
    return addMonths(now, -3).getTime()
  }
  if (range === 'YTD') {
    return new Date(now.getFullYear(), 0, 1).getTime()
  }
  return minEventTime ?? now.getTime() - 24 * 60 * 60 * 1000
}

function formatAxisDate(timestamp: number, range: RangeKey) {
  const date = new Date(timestamp)
  if (range === '1W') {
    return new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(date)
  }
  if (range === 'ALL') {
    return new Intl.DateTimeFormat('en-US', { month: 'short', year: '2-digit' }).format(date)
  }
  return new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short' }).format(date)
}

function xFor(timestamp: number, minTime: number, maxTime: number) {
  return 72 + ((timestamp - minTime) / Math.max(maxTime - minTime, 1)) * 856
}

function yFor(value: number, minValue: number, maxValue: number) {
  return 276 - ((value - minValue) / Math.max(maxValue - minValue, 0.01)) * 218
}

function stepPath(points: Array<{ time: number; value: number }>, minTime: number, maxTime: number, minValue: number, maxValue: number) {
  if (points.length === 0) {
    return ''
  }

  const [first, ...rest] = points
  const segments = [`M ${xFor(first.time, minTime, maxTime).toFixed(2)} ${yFor(first.value, minValue, maxValue).toFixed(2)}`]
  let previous = first
  for (const point of rest) {
    segments.push(`H ${xFor(point.time, minTime, maxTime).toFixed(2)}`)
    segments.push(`V ${yFor(point.value, minValue, maxValue).toFixed(2)}`)
    previous = point
  }
  segments.push(`H ${xFor(maxTime, minTime, maxTime).toFixed(2)}`)
  return segments.join(' ')
}

export function PerformanceChart({ events }: { events: PerformanceFillEvent[] }) {
  const [range, setRange] = useState<RangeKey>('1M')

  const chart = useMemo(() => {
    const sortedEvents = events
      .map((event) => ({ ...event, time: new Date(event.timestamp).getTime() }))
      .filter((event) => Number.isFinite(event.time))
      .sort((a, b) => a.time - b.time)
    const now = new Date()
    const minEventTime = sortedEvents.length > 0 ? sortedEvents[0].time : null
    const endTime = Math.max(now.getTime(), sortedEvents.at(-1)?.time ?? now.getTime())
    const startTime = Math.min(rangeStart(range, now, minEventTime), endTime - 1)
    const byRun = new Map<string, { label: string; events: typeof sortedEvents }>()

    for (const event of sortedEvents) {
      const run = byRun.get(event.runId) ?? { label: event.label, events: [] }
      run.events.push(event)
      byRun.set(event.runId, run)
    }

    const runCurves = [...byRun.entries()].map(([runId, run], index) => {
      let value = 0
      const points = [{ time: startTime, value }]
      for (const event of run.events) {
        if (event.time < startTime || event.time > endTime) {
          continue
        }
        value += event.signedValue
        points.push({ time: event.time, value })
      }
      points.push({ time: endTime, value })
      return {
        runId,
        label: run.label,
        color: CHART_COLORS[index % CHART_COLORS.length],
        points,
        value,
      }
    })

    const allValues = runCurves.flatMap((curve) => curve.points.map((point) => point.value)).concat(0)
    const minRaw = Math.min(...allValues)
    const maxRaw = Math.max(...allValues)
    const padding = Math.max((maxRaw - minRaw) * 0.12, 0.01)
    const minValue = minRaw - padding
    const maxValue = maxRaw + padding
    const axisTicks = [0, 0.25, 0.5, 0.75, 1].map((offset) => startTime + (endTime - startTime) * offset)
    const curves: Curve[] = runCurves
      .map((curve) => ({
        runId: curve.runId,
        label: curve.label,
        color: curve.color,
        value: curve.value,
        path: stepPath(curve.points, startTime, endTime, minValue, maxValue),
      }))
      .sort((a, b) => b.value - a.value)

    return { axisTicks, curves, endTime, maxValue, minValue, startTime }
  }, [events, range])

  if (events.length === 0) {
    return (
      <div className="chartEmpty">
        No fills yet. The performance curve will populate as paper trades execute.
      </div>
    )
  }

  const baselineY = yFor(0, chart.minValue, chart.maxValue)

  return (
    <div className="performanceChart">
      <div className="rangeSelector" aria-label="Performance range">
        {RANGES.map((option) => (
          <button
            className={option === range ? 'rangeButton active' : 'rangeButton'}
            key={option}
            onClick={() => setRange(option)}
            type="button"
          >
            {option}
          </button>
        ))}
      </div>
      <svg className="lineChart" viewBox="0 0 1000 340" role="img" aria-label="Strategy performance over time">
        <defs>
          <linearGradient id="chartFade" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgba(34, 211, 238, 0.14)" />
            <stop offset="100%" stopColor="rgba(34, 211, 238, 0)" />
          </linearGradient>
        </defs>
        <rect className="chartPlotFill" x="72" y="42" width="856" height="234" />
        <line className="chartGridLine" x1="72" x2="928" y1="58" y2="58" />
        <line className="chartGridLine chartBaseline" x1="72" x2="928" y1={baselineY} y2={baselineY} />
        <line className="chartGridLine" x1="72" x2="928" y1="276" y2="276" />
        <text className="chartAxisLabel" x="72" y="28">{formatCurrency(chart.maxValue)}</text>
        <text className="chartAxisLabel" x="72" y={Math.min(306, baselineY + 18)}>0</text>
        <text className="chartAxisLabel" x="72" y="306">{formatCurrency(chart.minValue)}</text>
        {chart.axisTicks.map((tick) => (
          <g key={tick}>
            <line className="chartTick" x1={xFor(tick, chart.startTime, chart.endTime)} x2={xFor(tick, chart.startTime, chart.endTime)} y1="276" y2="283" />
            <text className="chartTimeLabel" x={xFor(tick, chart.startTime, chart.endTime)} y="326">
              {formatAxisDate(tick, range)}
            </text>
          </g>
        ))}
        {chart.curves.map((curve) => (
          <path
            className="chartLine"
            d={curve.path}
            fill="none"
            key={curve.runId}
            stroke={curve.color}
          />
        ))}
      </svg>
      <div className="chartLegend">
        {chart.curves.map((curve) => (
          <div className="legendItem" key={curve.runId}>
            <span className="legendSwatch" style={{ background: curve.color }} />
            <span>{curve.label}</span>
            <span className={curve.value >= 0 ? 'mono green' : 'mono red'}>{formatCurrency(curve.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
