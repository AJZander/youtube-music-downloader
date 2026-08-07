import { AppBar, Toolbar, Typography } from '@mui/material'

export default function StatsBar({ stats }) {
  if (!stats) return null
  const t = stats.tracks_by_status || {}
  const pending = t.pending || 0
  const dayCap = stats.caps?.per_day
  const parts = [
    `queue: ${pending}`,
    `today: ${stats.governor?.tracks_last_day ?? 0}${dayCap ? `/${dayCap}` : ''}`,
    `staging: ${stats.staging_used_gb}GB`,
    `disk free: ${stats.disk_free_gb}GB`,
  ]
  return (
    <AppBar position="fixed" color="default" sx={{ top: 'auto', bottom: 0 }} elevation={3}>
      <Toolbar variant="dense" sx={{ minHeight: 36 }}>
        <Typography variant="caption" color="text.secondary">
          {parts.join('  ·  ')}
        </Typography>
      </Toolbar>
    </AppBar>
  )
}
