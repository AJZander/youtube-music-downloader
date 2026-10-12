import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, LinearProgress, Paper, Tab, Tabs, Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'

import { api, subscribeEvents } from '../api.js'
import Charts from './discover/Charts.jsx'
import NewReleases from './discover/NewReleases.jsx'
import SuggestedArtists from './discover/SuggestedArtists.jsx'

function ago(epochSeconds) {
  if (!epochSeconds) return 'not yet'
  const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60)
  if (minutes < 2) return 'just now'
  if (minutes < 90) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return hours < 36 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`
}

/** Where the background scan is: a first pass over a big library takes hours,
 *  and a page that just says "nothing yet" for that long looks broken. */
function ScanStatus({ status, onScan }) {
  if (!status) return null
  if (!status.enabled) {
    return (
      <Alert severity="info" sx={{ mb: 2 }}>
        Discover's background scan is off (DISCOVER_ENABLED=false), so new releases are not
        being looked for. Charts still work.
      </Alert>
    )
  }
  const total = status.library_artists || 0
  const firstPass = status.searched < total
  const perHour = Math.round(3600 / (status.interval_sec || 12))
  const backoff = status.pacer?.backoff_sec > 0
  return (
    <Paper sx={{ p: 1.5, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Box sx={{ flexGrow: 1, minWidth: 240 }}>
          <Typography variant="body2">
            {firstPass
              ? `Reading your artists: ${status.searched.toLocaleString()} of ${total.toLocaleString()}`
              : `Watching ${status.matched.toLocaleString()} of your artists`
                + (status.suggested ? ` and ${status.suggested} recommended ones` : '')
                + ' for new releases'}
          </Typography>
          <Typography variant="caption" color={backoff ? 'warning.main' : 'text.secondary'}>
            {backoff
              ? `Paused — YouTube asked us to slow down; resuming in ${Math.ceil(status.pacer.backoff_sec / 60)} min.`
              : firstPass
                ? `About ${perHour} requests an hour, your biggest artists first — results appear as it goes.`
                : `Last checked ${ago(status.last_checked)} · ${status.due} due for a re-check`
                  + (status.current ? ` · now: ${status.current}` : '')}
          </Typography>
        </Box>
        <Button size="small" startIcon={<RefreshIcon />} onClick={onScan}>Check now</Button>
      </Box>
      {firstPass && total > 0 && (
        <LinearProgress variant="determinate" value={100 * status.searched / total}
          sx={{ mt: 1, height: 4, borderRadius: 2 }} />
      )}
    </Paper>
  )
}

/**
 * New music: recent releases by the artists in the library and by the
 * artists recommended from them, the recommendations themselves, and the
 * charts. The scan behind the first two runs on the server, slowly.
 */
export default function Discover({ onQueued, onBrowseArtist, stats, bottomOffset }) {
  const [tab, setTab] = useState(0)
  const [status, setStatus] = useState(null)

  const refresh = useCallback(() => {
    api.discoverStatus().then(setStatus).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => subscribeEvents({ 'discover.progress': setStatus }), [])

  const scan = () => api.discoverScan().then(setStatus).catch(() => {})

  return (
    <Box>
      <ScanStatus status={status} onScan={scan} />
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}
        variant="scrollable" allowScrollButtonsMobile>
        <Tab label="New releases" />
        <Tab label="Artists you might like" />
        <Tab label="Charts" />
      </Tabs>
      {/* Kept mounted, like the artist browser: a selection survives a tab switch. */}
      <Box sx={{ display: tab === 0 ? 'block' : 'none' }}>
        <NewReleases status={status} onQueued={onQueued} onBrowseArtist={onBrowseArtist}
          stats={stats} bottomOffset={bottomOffset} />
      </Box>
      {tab === 1 && <SuggestedArtists status={status} onBrowseArtist={onBrowseArtist} />}
      <Box sx={{ display: tab === 2 ? 'block' : 'none' }}>
        <Charts onQueued={onQueued} onBrowseArtist={onBrowseArtist} />
      </Box>
    </Box>
  )
}
