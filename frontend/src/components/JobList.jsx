import { useEffect, useState } from 'react'
import {
  Box, Chip, Collapse, IconButton, LinearProgress, Paper, Table, TableBody,
  TableCell, TableHead, TableRow, Tooltip, Typography,
} from '@mui/material'
import CancelIcon from '@mui/icons-material/Cancel'
import DeleteIcon from '@mui/icons-material/Delete'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ReplayIcon from '@mui/icons-material/Replay'
import VerifiedIcon from '@mui/icons-material/Verified'

import { api } from '../api.js'

const STATUS_COLOR = {
  queued: 'default', resolving: 'info', downloading: 'info', importing: 'info',
  completed: 'success', partial: 'warning', failed: 'error', cancelled: 'default',
  pending: 'default', validating: 'info', skipped_duplicate: 'secondary',
  import_failed: 'warning', needs_review: 'warning', downloaded: 'info',
}

function StatusChip({ status }) {
  return <Chip size="small" label={status.replace(/_/g, ' ')}
    color={STATUS_COLOR[status] || 'default'} variant="outlined" />
}

function TrackRow({ track }) {
  const active = ['downloading', 'validating', 'importing'].includes(track.status)
  return (
    <TableRow hover>
      <TableCell sx={{ width: 40 }}>{track.position}</TableCell>
      <TableCell>
        <Typography variant="body2">
          {[track.artist, track.title].filter(Boolean).join(' — ') || track.source_url}
        </Typography>
        {track.album && (
          <Typography variant="caption" color="text.secondary">{track.album}</Typography>
        )}
        {active && (
          <LinearProgress variant={track.progress_pct ? 'determinate' : 'indeterminate'}
            value={track.progress_pct || 0} sx={{ mt: 0.5, height: 4, borderRadius: 2 }} />
        )}
        {track.error_message && (
          <Typography variant="caption" color="error" display="block">
            {track.error_code}: {track.error_message}
          </Typography>
        )}
      </TableCell>
      <TableCell sx={{ whiteSpace: 'nowrap' }}>
        <StatusChip status={track.status} />
        {track.stage && active && (
          <Typography variant="caption" display="block" color="text.secondary">
            {track.stage}
          </Typography>
        )}
      </TableCell>
      <TableCell sx={{ whiteSpace: 'nowrap' }}>
        {track.format && (
          <Typography variant="caption">
            {track.format}{track.bitrate_kbps ? ` ${track.bitrate_kbps}k` : ''}
            {track.below_target ? ' (below target)' : ''}
          </Typography>
        )}
        {track.plex_verified && (
          <Tooltip title="Confirmed visible in Plex">
            <VerifiedIcon color="success" fontSize="small" sx={{ ml: 0.5, verticalAlign: 'middle' }} />
          </Tooltip>
        )}
      </TableCell>
    </TableRow>
  )
}

function JobCard({ job, trackEvents, onChanged }) {
  const [open, setOpen] = useState(false)
  const [tracks, setTracks] = useState(null)

  useEffect(() => {
    if (open) api.getJob(job.id).then((j) => setTracks(j.tracks || [])).catch(() => {})
  }, [open, job.id, job.status, job.completed_tracks, job.failed_tracks])

  // merge live SSE track updates
  useEffect(() => {
    if (!tracks) return
    let changed = false
    const next = tracks.map((t) => {
      const ev = trackEvents[t.id]
      if (ev && ev !== t) { changed = true; return { ...t, ...ev } }
      return t
    })
    if (changed) setTracks(next)
  }, [trackEvents]) // eslint-disable-line react-hooks/exhaustive-deps

  const running = ['queued', 'resolving', 'downloading', 'importing'].includes(job.status)
  const retryable = ['partial', 'failed', 'cancelled'].includes(job.status)
  const progress = job.total_tracks
    ? Math.round(100 * (job.completed_tracks + job.skipped_tracks + job.failed_tracks) / job.total_tracks)
    : 0

  return (
    <Paper sx={{ mb: 1.5, p: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <IconButton size="small" onClick={() => setOpen((o) => !o)}>
          {open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        </IconButton>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="body1" noWrap>
            {[job.artist, job.title].filter(Boolean).join(' — ') || job.source_url}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {job.provider} {job.source_type} · {job.completed_tracks}/{job.total_tracks || '?'} done
            {job.skipped_tracks ? ` · ${job.skipped_tracks} skipped` : ''}
            {job.failed_tracks ? ` · ${job.failed_tracks} failed` : ''}
            {' · '}{new Date(job.created_at).toLocaleString()}
          </Typography>
          {running && job.total_tracks > 0 && (
            <LinearProgress variant="determinate" value={progress}
              sx={{ mt: 0.5, height: 4, borderRadius: 2 }} />
          )}
        </Box>
        <StatusChip status={job.status} />
        {running && (
          <Tooltip title="Cancel">
            <IconButton size="small" onClick={() => api.cancelJob(job.id).then(onChanged)}>
              <CancelIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        {retryable && (
          <Tooltip title="Retry failed tracks">
            <IconButton size="small" onClick={() => api.retryJob(job.id).then(onChanged)}>
              <ReplayIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        {!running && (
          <Tooltip title="Remove from history (files stay)">
            <IconButton size="small" onClick={() => api.deleteJob(job.id).then(onChanged)}>
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </Box>
      {job.error_message && (
        <Typography variant="caption" color="error">
          {job.error_code}: {job.error_message}
        </Typography>
      )}
      <Collapse in={open} unmountOnExit>
        {tracks === null ? (
          <LinearProgress sx={{ my: 1 }} />
        ) : (
          <Table size="small" sx={{ mt: 1 }}>
            <TableHead>
              <TableRow>
                <TableCell>#</TableCell><TableCell>Track</TableCell>
                <TableCell>Status</TableCell><TableCell>Result</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {tracks.map((t) => <TrackRow key={t.id} track={t} />)}
            </TableBody>
          </Table>
        )}
      </Collapse>
    </Paper>
  )
}

export default function JobList({ jobs, trackEvents, onChanged }) {
  if (!jobs.length) {
    return <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
      Nothing queued yet — paste a link above.
    </Typography>
  }
  return (
    <Box>
      {jobs.map((j) => (
        <JobCard key={j.id} job={j} trackEvents={trackEvents} onChanged={onChanged} />
      ))}
    </Box>
  )
}
