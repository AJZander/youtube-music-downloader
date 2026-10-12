import { Chip, Tooltip } from '@mui/material'

export const LOSSLESS = new Set(['flac', 'wav', 'alac', 'aiff', 'aif', 'ape', 'wv'])

export function bytes(n) {
  if (!n) return '0 B'
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`
  return `${Math.round(n / 1e3)} kB`
}

export function duration(seconds) {
  if (!seconds) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : `${m}:${String(s % 60).padStart(2, '0')}`
}

/** The one-glance verdict on a copy: what it is, and whether that is good. */
export function QualityChip({ format, bitrate, size = 'small' }) {
  const lossless = LOSSLESS.has((format || '').toLowerCase())
  const label = lossless ? (format || '').toUpperCase()
    : `${format || '?'}${bitrate ? ` ${bitrate}k` : ''}`
  return (
    <Chip size={size} label={label} variant={lossless ? 'filled' : 'outlined'}
      color={lossless ? 'success' : bitrate >= 256 ? 'primary' : 'default'} />
  )
}

/** Health issues read as jargon without the "so what" — spell it out. */
const HEALTH_HELP = {
  'no album tag': 'Plex cannot file these tracks under an album',
  'no album artist tag': 'Plex will split this off as a separate artist',
  'missing track numbers': 'Plex will order these tracks alphabetically',
  'junk titles': 'titles are raw YouTube video titles, not song titles',
  'mixed formats': 'this folder holds more than one audio format',
}

export function HealthChips({ issues }) {
  if (!issues?.length) return null
  return issues.map((issue) => (
    <Tooltip key={issue} title={HEALTH_HELP[issue] || ''}>
      <Chip size="small" color="warning" variant="outlined" label={issue}
        sx={{ mr: 0.5 }} />
    </Tooltip>
  ))
}
