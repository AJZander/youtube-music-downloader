import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, LinearProgress, Paper, Stack, Tab, Tabs, Tooltip,
  Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'

import { api, subscribeEvents } from '../api.js'
import AlbumDupes from './library/AlbumDupes.jsx'
import Browser from './library/Browser.jsx'
import Ignored from './library/Ignored.jsx'
import SplitArtists from './library/SplitArtists.jsx'
import Trash from './library/Trash.jsx'
import TrackDupes from './library/TrackDupes.jsx'
import { bytes } from './library/shared.jsx'

/** A first scan of a NAS-backed library runs for about an hour — a bare
 *  percentage with no time attached just looks stuck. */
function eta(seconds) {
  if (seconds == null) return 'unknown'
  if (seconds < 90) return `${Math.round(seconds)}s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} hours`
}

function Stat({ label, value, hint }) {
  const chip = <Chip size="small" variant="outlined"
    label={<span><strong>{value}</strong> {label}</span>} />
  return hint ? <Tooltip title={hint}>{chip}</Tooltip> : chip
}

export default function Library() {
  const [tab, setTab] = useState(0)
  const [overview, setOverview] = useState(null)
  const [scan, setScan] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.libraryOverview().then((d) => { setOverview(d); setScan(d.scan) }).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])
  useEffect(() => subscribeEvents({ 'library.scan': setScan }), [])

  // SSE carries the scan, but a page opened mid-scan has to catch up somehow.
  useEffect(() => {
    if (!scan?.running) return undefined
    const id = setInterval(() => {
      api.libraryScanStatus().then((s) => {
        setScan(s)
        if (!s.running) refresh()
      }).catch(() => {})
    }, 3000)
    return () => clearInterval(id)
  }, [scan?.running, refresh])

  const startScan = async (full) => {
    setError(null)
    try { await api.libraryScan(full); setScan({ ...scan, running: true, phase: 'walking' }) }
    catch (e) { setError(e.message) }
  }

  const running = scan?.running
  const unenriched = overview?.unenriched || 0
  const losslessPct = overview?.files
    ? Math.round(100 * overview.lossless_files / overview.files) : 0

  return (
    <Box>
      <Paper sx={{ p: 1.5, mb: 2 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap',
          gap: 1 }}>
          <Stat label="files" value={overview?.files?.toLocaleString() ?? '—'} />
          <Stat label="artists" value={overview?.artists?.toLocaleString() ?? '—'} />
          <Stat label="albums" value={overview?.albums?.toLocaleString() ?? '—'} />
          <Stat label="on disk" value={bytes(overview?.bytes)} />
          <Stat label="lossless" value={`${losslessPct}%`}
            hint={`${overview?.lossless_files?.toLocaleString() || 0} FLAC/WAV files`} />
          {overview?.trash_entries > 0 && (
            <Stat label="in trash" value={bytes(overview.trash_bytes)}
              hint={`${overview.trash_entries} files waiting to be purged or restored`} />
          )}
          {overview?.unreadable > 0 && (
            <Stat label="unreadable" value={overview.unreadable.toLocaleString()}
              hint="Files whose tags will not parse. They are skipped by the
                duplicate report and not retried on every scan." />
          )}
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" startIcon={<RefreshIcon />} disabled={running}
            onClick={() => startScan(false)}>
            {running ? 'Scanning…' : 'Rescan'}
          </Button>
          <Tooltip title="Re-read tags on every file, ignoring the modified-time
            shortcut. Slow — only needed if tags were changed outside this app.">
            <span>
              <Button size="small" disabled={running} onClick={() => startScan(true)}>
                Full rescan
              </Button>
            </span>
          </Tooltip>
        </Stack>

        {running && (
          <Box sx={{ mt: 1 }}>
            <LinearProgress
              variant={scan.total ? 'determinate' : 'indeterminate'}
              value={scan.pct || 0} />
            <Typography variant="caption" color="text.secondary">
              {scan.phase === 'walking' && 'Listing files…'}
              {scan.phase === 'reading' && (
                <>
                  {`Reading tags: ${scan.done.toLocaleString()} of ${
                    scan.total.toLocaleString()} (${scan.pct}%)`}
                  {scan.rate_per_sec != null
                    && ` · ${scan.rate_per_sec}/sec · ${eta(scan.eta_sec)} left`}
                </>
              )}
              {scan.phase === 'pruning' && 'Removing entries for deleted files…'}
            </Typography>
          </Box>
        )}
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {!running && unenriched > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}
          action={<Button size="small" onClick={() => startScan(false)}>Scan now</Button>}>
          {unenriched.toLocaleString()} files have not been read yet. Duplicate
          detection compares formats, bitrates and tags, so it will miss these
          until the scan finishes. Reading tags off a network mount is slow —
          the first scan of a library this size takes about an hour, and later
          ones take about a minute.
        </Alert>
      )}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}
        variant="scrollable" allowScrollButtonsMobile>
        <Tab label="Duplicate albums" />
        <Tab label="Duplicate tracks" />
        <Tab label="Split artists" />
        <Tab label="Browse" />
        <Tab label={overview?.trash_entries ? `Trash (${overview.trash_entries})` : 'Trash'} />
        <Tab label="Ignored" />
      </Tabs>

      {running && tab < 3 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          The duplicate report waits for the scan to finish — it needs a
          consistent picture of the library.
        </Alert>
      )}

      {!running && tab === 0 && <AlbumDupes onChanged={refresh} />}
      {!running && tab === 1 && <TrackDupes onChanged={refresh} />}
      {!running && tab === 2 && <SplitArtists onChanged={refresh} />}
      {tab === 3 && <Browser />}
      {tab === 4 && <Trash onChanged={refresh} />}
      {tab === 5 && <Ignored onChanged={refresh} />}
    </Box>
  )
}
