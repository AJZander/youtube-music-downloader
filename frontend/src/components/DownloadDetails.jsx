import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Box, Chip, Divider, IconButton, LinearProgress, Link, Table, TableBody,
  TableCell, TableHead, TableRow, Tab, Tabs, Tooltip, Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import ReplayIcon from '@mui/icons-material/Replay'
import VerifiedIcon from '@mui/icons-material/Verified'

import { api } from '../api.js'
import { transferLine } from '../format.js'
import BottomPane, { STATS_BAR_HEIGHT } from './BottomPane.jsx'

export { STATS_BAR_HEIGHT }

const STATUS_COLOR = {
  queued: 'default', paused: 'warning', resolving: 'info', downloading: 'info',
  importing: 'info', completed: 'success', partial: 'warning', failed: 'error',
  cancelled: 'default', pending: 'default', validating: 'info',
  skipped_duplicate: 'secondary', import_failed: 'warning',
  needs_review: 'warning', downloaded: 'info',
}
const ACTIVE_TRACK = new Set(['downloading', 'validating', 'importing', 'downloaded'])
const ACTIVE_JOB = new Set(['queued', 'paused', 'resolving', 'downloading', 'importing'])
const RETRYABLE = new Set(['failed', 'import_failed', 'needs_review', 'cancelled'])

const FILTERS = {
  all: () => true,
  active: (t) => ACTIVE_TRACK.has(t.status) || t.status === 'pending',
  done: (t) => t.status === 'completed',
  skipped: (t) => t.status === 'skipped_duplicate',
  problems: (t) => RETRYABLE.has(t.status),
}

function when(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

function Field({ label, children }) {
  return (
    <Box sx={{ display: 'flex', gap: 1, py: 0.25, minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary"
        sx={{ width: 118, flexShrink: 0 }}>
        {label}
      </Typography>
      <Typography variant="caption" sx={{ wordBreak: 'break-all', minWidth: 0 }}
        component="div">
        {children ?? '—'}
      </Typography>
    </Box>
  )
}

function TrackRow({ track, progress, onRetry }) {
  const active = ACTIVE_TRACK.has(track.status)
  // Live stats win over the row's slower persisted percentage.
  const pct = (active && progress?.progress_pct != null)
    ? progress.progress_pct : track.progress_pct
  const transfer = active ? transferLine(progress) : null
  return (
    <TableRow hover sx={{ '& td': { py: 0.4 } }}>
      <TableCell sx={{ width: 42, color: 'text.secondary' }}>
        {track.position ?? track.track_number ?? '–'}
      </TableCell>
      <TableCell sx={{ maxWidth: 320 }}>
        <Typography variant="body2" noWrap
          title={[track.artist, track.title].filter(Boolean).join(' — ')}>
          {track.title || track.source_url}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap display="block">
          {[track.artist, track.album].filter(Boolean).join(' · ') || ' '}
        </Typography>
      </TableCell>
      <TableCell sx={{ width: 190 }}>
        <Chip size="small" variant="outlined" label={track.status.replace(/_/g, ' ')}
          color={STATUS_COLOR[track.status] || 'default'} />
        {active && (
          <>
            <LinearProgress
              variant={pct != null ? 'determinate' : 'indeterminate'}
              value={pct || 0}
              sx={{ mt: 0.5, height: 4, borderRadius: 2 }} />
            <Typography variant="caption" color="text.secondary" display="block">
              {track.stage || 'working'}
              {pct != null && ` · ${Math.round(pct)}%`}
            </Typography>
            {transfer && (
              <Typography variant="caption" color="text.secondary" display="block">
                {transfer}
              </Typography>
            )}
          </>
        )}
      </TableCell>
      <TableCell sx={{ width: 120, whiteSpace: 'nowrap' }}>
        {track.format && (
          <Typography variant="caption">
            {track.format}{track.bitrate_kbps ? ` ${track.bitrate_kbps}k` : ''}
          </Typography>
        )}
        {track.below_target && (
          <Tooltip title="Downloaded below the target bitrate">
            <Chip size="small" color="warning" variant="outlined" label="low" sx={{ ml: 0.5 }} />
          </Tooltip>
        )}
        {track.plex_verified && (
          <Tooltip title="Confirmed visible in Plex">
            <VerifiedIcon color="success" fontSize="small"
              sx={{ ml: 0.5, verticalAlign: 'middle' }} />
          </Tooltip>
        )}
      </TableCell>
      <TableCell sx={{ maxWidth: 300 }}>
        {track.error_message && (
          <Typography variant="caption" color="error" sx={{ wordBreak: 'break-word' }}>
            {track.error_code}: {track.error_message}
          </Typography>
        )}
        {!track.error_message && track.library_path && (
          <Typography variant="caption" color="text.secondary" noWrap display="block"
            title={track.library_path}>
            {track.library_path}
          </Typography>
        )}
      </TableCell>
      <TableCell sx={{ width: 44 }}>
        {RETRYABLE.has(track.status) && (
          <Tooltip title="Retry this track">
            <IconButton size="small" onClick={() => onRetry(track.id)}>
              <ReplayIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </TableCell>
    </TableRow>
  )
}

/**
 * Bottom-docked detail pane for one download, the way a torrent client shows
 * the selected torrent: what it is, and every file inside it with live
 * progress. Mounted at app level so it stays put while you switch tabs.
 */
export default function DownloadDetails({
  jobId, trackEvents, trackProgress, height, onHeight, onClose, onChanged,
}) {
  const [job, setJob] = useState(null)
  const [tracks, setTracks] = useState(null)
  const [tab, setTab] = useState(0)
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    if (!jobId) return
    api.getJob(jobId)
      .then((j) => { setJob(j); setTracks(j.tracks || []); setError(null) })
      .catch((e) => setError(e.message))
  }, [jobId])

  useEffect(() => { setJob(null); setTracks(null); setFilter('all'); load() }, [jobId, load])

  // SSE carries per-track progress; the poll is a backstop that also picks up
  // tracks added when a job expands.
  useEffect(() => {
    if (!job || !ACTIVE_JOB.has(job.status)) return undefined
    const id = setInterval(load, 5000)
    return () => clearInterval(id)
  }, [job, load])

  useEffect(() => {
    if (!tracks) return
    let changed = false
    const next = tracks.map((t) => {
      const event = trackEvents[t.id]
      if (event && event !== t) { changed = true; return { ...t, ...event } }
      return t
    })
    if (changed) setTracks(next)
  }, [trackEvents]) // eslint-disable-line react-hooks/exhaustive-deps

  const counts = useMemo(() => {
    const out = {}
    ;(tracks || []).forEach((t) => { out[t.status] = (out[t.status] || 0) + 1 })
    return out
  }, [tracks])

  const shown = useMemo(
    () => (tracks || []).filter(FILTERS[filter] || FILTERS.all), [tracks, filter])

  const retry = async (trackId) => {
    try { await api.resolveTrack(trackId, 'retry'); load(); onChanged?.() }
    catch (e) { setError(e.message) }
  }

  const done = job ? job.completed_tracks + job.skipped_tracks + job.failed_tracks : 0
  const progress = job?.total_tracks ? Math.round(100 * done / job.total_tracks) : 0
  const current = (tracks || []).find((t) => ACTIVE_TRACK.has(t.status))

  return (
    <BottomPane height={height} onHeight={onHeight}>
      <Box sx={{ px: 2, pb: 0.5, display: 'flex', alignItems: 'center', gap: 1,
        flexWrap: 'wrap' }}>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="subtitle2" noWrap>
            {job
              ? ([job.artist, job.title].filter(Boolean).join(' — ') || job.source_url)
              : 'Loading…'}
          </Typography>
          {job && (
            <Typography variant="caption" color="text.secondary" noWrap display="block">
              {job.provider} {job.source_type} · {done}/{job.total_tracks || '?'} handled
              {current && ` · now: ${current.title || 'working'}${
                current.stage ? ` (${current.stage})` : ''}`}
              {current && transferLine(trackProgress?.[current.id])
                && ` · ${transferLine(trackProgress[current.id])}`}
            </Typography>
          )}
        </Box>
        {job && (
          <Chip size="small" variant="outlined" label={job.status}
            color={STATUS_COLOR[job.status] || 'default'} />
        )}
        <Tooltip title="Close">
          <IconButton size="small" onClick={onClose}><CloseIcon fontSize="small" /></IconButton>
        </Tooltip>
      </Box>

      {job && ACTIVE_JOB.has(job.status) && job.total_tracks > 0 && (
        <LinearProgress variant="determinate" value={progress} sx={{ height: 3 }} />
      )}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 34, px: 1 }}>
        <Tab label="Tracks" sx={{ minHeight: 34, py: 0 }} />
        <Tab label="General" sx={{ minHeight: 34, py: 0 }} />
      </Tabs>
      <Divider />

      {error && (
        <Typography variant="caption" color="error" sx={{ px: 2, py: 0.5 }}>{error}</Typography>
      )}

      <Box sx={{ flexGrow: 1, overflow: 'auto', minHeight: 0 }}>
        {tab === 0 && (
          <>
            <Box sx={{ px: 2, py: 0.75, display: 'flex', gap: 0.5, flexWrap: 'wrap',
              position: 'sticky', top: 0, zIndex: 1, bgcolor: 'background.paper' }}>
              {Object.keys(FILTERS).map((key) => {
                const n = key === 'all'
                  ? (tracks || []).length
                  : (tracks || []).filter(FILTERS[key]).length
                if (key !== 'all' && !n) return null
                return (
                  <Chip key={key} size="small" label={`${key} (${n})`}
                    color={filter === key ? 'primary' : 'default'}
                    variant={filter === key ? 'filled' : 'outlined'}
                    onClick={() => setFilter(key)} />
                )
              })}
            </Box>
            {tracks === null && <LinearProgress />}
            {tracks && !tracks.length && (
              <Typography variant="body2" color="text.secondary"
                sx={{ p: 2, textAlign: 'center' }}>
                No tracks yet — this job has not been expanded into tracks.
              </Typography>
            )}
            {tracks?.length > 0 && (
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    <TableCell>#</TableCell>
                    <TableCell>Track</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Audio</TableCell>
                    <TableCell>Result</TableCell>
                    <TableCell />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {shown.map((t) => (
                    <TrackRow key={t.id} track={t} onRetry={retry}
                      progress={trackProgress?.[t.id]} />
                  ))}
                </TableBody>
              </Table>
            )}
          </>
        )}

        {tab === 1 && job && (
          <Box sx={{ p: 2, display: 'grid', gap: 0.5,
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
            <Field label="Source">
              <Link href={job.source_url} target="_blank" rel="noreferrer"
                sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                {job.source_url}<OpenInNewIcon sx={{ fontSize: 12 }} />
              </Link>
            </Field>
            <Field label="Provider">{job.provider} · {job.source_type}</Field>
            <Field label="Status">{job.status}</Field>
            <Field label="Queue position">{job.queue_position ?? '—'}</Field>
            <Field label="Tracks">
              {job.total_tracks} total · {job.completed_tracks} completed
              · {job.skipped_tracks} skipped · {job.failed_tracks} failed
            </Field>
            <Field label="By track status">
              {Object.entries(counts).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`)
                .join(' · ') || '—'}
            </Field>
            <Field label="Queued">{when(job.created_at)}</Field>
            <Field label="Started">{when(job.started_at)}</Field>
            <Field label="Finished">{when(job.finished_at)}</Field>
            <Field label="Job id">{job.id}</Field>
            {job.force && <Field label="Force">dedup and guards bypassed</Field>}
            {job.error_message && (
              <Field label="Error">
                <Typography variant="caption" color="error">
                  {job.error_code}: {job.error_message}
                </Typography>
              </Field>
            )}
          </Box>
        )}
      </Box>
    </BottomPane>
  )
}
