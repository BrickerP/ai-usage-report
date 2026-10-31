export function fmtCompact(value: number): string {
  const n = Number(value) || 0
  const abs = Math.abs(n)
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return `${Math.round(n)}`
}

export function fmtUsd(value: number): string {
  const n = Number(value) || 0
  const digits = Math.abs(n) > 0 && Math.abs(n) < 0.01 ? 4 : 2
  return n.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtExactTokens(value: number | null): string {
  return value === null
    ? '—'
    : value.toLocaleString('en-US', { maximumFractionDigits: 0 })
}

export function fmtExactUsd(value: number | null): string {
  return value === null
    ? '—'
    : value.toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 8,
      })
}
