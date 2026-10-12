import {
  Box, Chip, IconButton, LinearProgress, Paper, Tooltip, Typography,
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import ReplayIcon from '@mui/icons-material/Replay'

import { api } from '../api.js'

const STATUS_COLOR = {
  queued: 'default', paused: 'warning', resolving: 'info', downloading: 'info',
  importing: 'info', completed: 'success', partial: 'warning', failed: 'error',
  cancelled: 'default',
}

const ACTIVE = new Set(['queued', 'paused', 'resolving', 'downloading', 'importing'])
const RETRYABLE = new Set(['partial', 'failed', 'cancelled'])

function JobCard({ job, selected, onSelect, onChanged }) {
  const handled = job.completed_tracks + job.skipped_tracks + job.failed_tracks
  const progress = job.total_tracks ? Math.round(100 * handled / job.total_tracks) : 0

  return (
    <Paper
      onClick={() => onSelect(selected ? null : job.id)}
      sx={{
        mb: 1, p: 1.25, cursor: 'pointer',
        ...(selected && {
          outline: 2, outlineStyle: 'solid', outlineColor: 'primary.main',
          bgcolor: 'action.selected',
        }),
      }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="body2" noWrap>
            {[job.artist, job.title].filter(Boolean).join(' — ') || job.source_url}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap display="block">
            {job.provider} {job.source_type} · {job.completed_tracks}/{job.total_tracks || '?'} done
            {job.skipped_tracks ? ` · ${job.skipped_tracks} skipped` : ''}
            {job.failed_tracks ? ` · ${job.failed_tracks} failed` : ''}
            {' · '}{new Date(job.created_at).toLocaleString()}
          </Typography>
          {job.total_tracks > 0 && job.status === 'partial' && (
            <LinearProgress variant="determinate" value={progress}
              color="warning" sx={{ mt: 0.5, height: 3, borderRadius: 2 }} />
          )}
        </Box>
        <Chip size="small" variant="outlined" label={job.status}
          color={STATUS_COLOR[job.status] || 'default'} />
        <Box sx={{ display: 'flex' }} onClick={(e) => e.stopPropagation()}>
          {RETRYABLE.has(job.status) && (
            <Tooltip title="Retry failed tracks">
              <IconButton size="small" onClick={() => api.retryJob(job.id).then(onChanged)}>
                <ReplayIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title="Remove from history (files stay)">
            <IconButton size="small" onClick={() => api.deleteJob(job.id).then(onChanged)}>
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>
      {job.error_message && (
        <Typography variant="caption" color="error" sx={{ wordBreak: 'break-word' }}>
          {job.error_code}: {job.error_message}
        </Typography>
      )}
    </Paper>
  )
}

/**
 * History only — anything still in the queue is drawn by <Queue>, which owns
 * ordering. Track listings live in the detail pane at the bottom of the
 * screen, so a job is a single row here whether or not it is selected.
 */
export default function JobList({ jobs, selectedJobId, onSelect, onChanged }) {
  const history = jobs.filter((j) => !ACTIVE.has(j.status))
  if (!history.length) {
    return <Typography color="text.secondary" sx={{ mt: 2, textAlign: 'center' }}>
      No finished jobs yet.
    </Typography>
  }
  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
        <Typography variant="subtitle2">History</Typography>
        <Typography variant="caption" color="text.secondary">
          {history.length} finished · click one for its tracks
        </Typography>
      </Box>
      {history.map((job) => (
        <JobCard key={job.id} job={job} onChanged={onChanged}
          selected={job.id === selectedJobId} onSelect={onSelect} />
      ))}
    </Box>
  )
}
