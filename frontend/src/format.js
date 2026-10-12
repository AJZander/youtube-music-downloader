/** Shared human-readable formatters for transfer stats. */

export function bytes(n) {
  if (n == null) return '—'
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`
  if (n >= 1e3) return `${Math.round(n / 1e3)} kB`
  return `${n} B`
}

export function speed(bps) {
  return bps == null ? null : `${(bps / 1e6).toFixed(2)} MB/s`
}

export function eta(seconds) {
  if (seconds == null) return null
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

/** "3.1 MB/s · 12 MB of 48 MB · 28s left" — whichever parts are known. */
export function transferLine(p) {
  if (!p) return null
  return [
    speed(p.speed_bps),
    p.total_bytes ? `${bytes(p.downloaded_bytes)} of ${bytes(p.total_bytes)}`
      : (p.downloaded_bytes ? bytes(p.downloaded_bytes) : null),
    p.eta_sec != null ? `${eta(p.eta_sec)} left` : null,
  ].filter(Boolean).join(' · ') || null
}
