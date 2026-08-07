import { Alert, Button } from '@mui/material'

export default function HealthBanner({ health, stats, onResume }) {
  const banners = []
  if (health && !health.checks?.library_online) {
    banners.push(
      <Alert key="lib" severity="error" sx={{ mb: 1 }}>
        Music library mount is offline — imports are paused (downloads keep staging).
      </Alert>)
  }
  if (health && !health.checks?.plex_reachable) {
    banners.push(
      <Alert key="plex" severity="warning" sx={{ mb: 1 }}>
        Plex is unreachable — new files will import but won't be scanned until it's back.
      </Alert>)
  }
  if (health && !health.checks?.youtube_authenticated) {
    banners.push(
      <Alert key="cookies" severity="warning" sx={{ mb: 1 }}>
        No authenticated YouTube cookies — downloads run anonymously (no Premium
        256k formats, higher bot-check risk). Export cookies to data/cookies/youtube-cookies.txt.
      </Alert>)
  }
  const gov = stats?.governor
  if (gov?.paused_until) {
    banners.push(
      <Alert key="gov" severity="warning" sx={{ mb: 1 }}
        action={<Button size="small" color="inherit" onClick={onResume}>Resume now</Button>}>
        Downloads paused: {gov.pause_reason} (until {new Date(gov.paused_until * 1000).toLocaleTimeString()})
      </Alert>)
  }
  if (stats && stats.staging_used_gb > 0.8 * stats.staging_cap_gb) {
    banners.push(
      <Alert key="staging" severity="warning" sx={{ mb: 1 }}>
        Staging is at {stats.staging_used_gb}GB of {stats.staging_cap_gb}GB cap.
      </Alert>)
  }
  return banners
}
