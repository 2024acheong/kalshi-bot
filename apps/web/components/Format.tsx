export function formatCurrency(value: number) {
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
    style: 'currency',
    currency: 'USD',
  }).format(value)
}

export function formatNumber(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    return String(value)
  }
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 4,
  }).format(parsed)
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'short',
    timeStyle: 'medium',
  }).format(new Date(value))
}

export function formatAge(value: string | null | undefined) {
  if (!value) {
    return 'never'
  }
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) {
    return `${seconds}s ago`
  }
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) {
    return `${minutes}m ago`
  }
  return `${Math.round(minutes / 60)}h ago`
}
