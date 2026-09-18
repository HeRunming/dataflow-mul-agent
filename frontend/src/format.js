/* Display helpers. Backend timestamps are epoch seconds. */
export function toDate(value) {
  if (value === null || value === undefined || value === '') return null
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function relativeTime(value) {
  const date = toDate(value)
  if (!date) return ''
  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  if (seconds < 45) return '刚刚'
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`
  if (seconds < 86400 * 7) return `${Math.round(seconds / 86400)} 天前`
  return date.toLocaleDateString()
}

export function clockTime(value) {
  const date = toDate(value)
  return date ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''
}

export function cellText(value) {
  if (value === null || value === undefined) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

export function shortId(id) {
  return String(id || '').replace(/^run-/, '').slice(0, 10)
}

export function countLabel(count, unit) {
  return `${count} ${unit}`
}
