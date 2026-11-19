/** One file's standard audit time in this company (median to 90th percentile seconds), or null when too few were measured. */
export type PerFileSeconds = {low: number; high: number} | null

const minutes = (seconds: number) => seconds / 60

function halfHours(seconds: number) {
  const hours = Math.round(seconds / 1800) / 2
  return Number.isInteger(hours) ? String(hours) : hours.toFixed(1)
}

/** 「约 3～6 分钟」 for N files; null when there is no estimate to show. */
export function durationRange(perFile: PerFileSeconds | undefined, count: number): string | null {
  if (!perFile || !Number.isInteger(count) || count < 1) return null
  const {low, high} = perFile
  if (!Number.isFinite(low) || !Number.isFinite(high) || low < 0 || high < low) return null
  const from = low * count, to = high * count
  if (to < 60) return '不到 1 分钟'
  if (to >= 90 * 60) {
    const a = halfHours(Math.max(from, 1800)), b = halfHours(to)
    return a === b ? `约 ${b} 小时` : `约 ${a}～${b} 小时`
  }
  const a = Math.max(1, Math.round(minutes(from))), b = Math.max(a, Math.ceil(minutes(to)))
  return a === b ? `约 ${b} 分钟` : `约 ${a}～${b} 分钟`
}
