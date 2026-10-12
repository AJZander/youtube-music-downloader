import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert, Box, Chip, IconButton, LinearProgress, Paper, Tooltip, Typography,
} from '@mui/material'
import CancelIcon from '@mui/icons-material/Cancel'
import DragIndicatorIcon from '@mui/icons-material/DragIndicator'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp'
import KeyboardDoubleArrowDownIcon from '@mui/icons-material/KeyboardDoubleArrowDown'
import KeyboardDoubleArrowUpIcon from '@mui/icons-material/KeyboardDoubleArrowUp'
import PauseIcon from '@mui/icons-material/Pause'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'

import { api } from '../api.js'
import { transferLine } from '../format.js'

const STATUS_COLOR = {
  queued: 'default', paused: 'warning', resolving: 'info',
  downloading: 'info', importing: 'info',
}

function Row({ job, index, count, onAction, drag, selected, onSelect }) {
  const running = ['resolving', 'downloading', 'importing'].includes(job.status)
  const paused = job.status === 'paused'
  const progress = job.total_tracks
    ? Math.round(100 * (job.completed_tracks + job.skipped_tracks + job.failed_tracks)
      / job.total_tracks)
    : 0

  return (
    <Paper
      variant="outlined"
      draggable
      onDragStart={(e) => { drag.start(index); e.dataTransfer.effectAllowed = 'move' }}
      onDragOver={(e) => { e.preventDefault(); drag.over(index) }}
      onDragEnd={drag.end}
      onDrop={(e) => { e.preventDefault(); drag.drop() }}
      onClick={() => { if (!drag.justDragged()) onSelect(selected ? null : job.id) }}
      sx={{
        p: 1, mb: 0.75, display: 'flex', alignItems: 'center', gap: 1,
        opacity: drag.dragging === index ? 0.4 : paused ? 0.65 : 1,
        borderTop: drag.hover === index && drag.dragging > index ? 2 : undefined,
        borderBottom: drag.hover === index && drag.dragging < index ? 2 : undefined,
        borderColor: drag.hover === index ? 'primary.main' : undefined,
        cursor: 'grab',
        ...(selected && {
          outline: 2, outlineStyle: 'solid', outlineColor: 'primary.main',
          bgcolor: 'action.selected',
        }),
      }}
    >
      <DragIndicatorIcon fontSize="small" sx={{ color: 'text.disabled' }} />
      <Typography variant="caption" color="text.secondary"
        sx={{ width: 22, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {index + 1}
      </Typography>

      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap>
          {[job.artist, job.title].filter(Boolean).join(' — ') || job.source_url}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap>
          {job.provider} {job.source_type}
          {job.total_tracks > 0 && ` · ${job.completed_tracks}/${job.total_tracks} done`}
          {job.pending_tracks > 0 && ` · ${job.pending_tracks} waiting`}
        </Typography>
        {running && job.total_tracks > 0 && (
          <LinearProgress variant="determinate" value={progress}
            sx={{ mt: 0.5, height: 3, borderRadius: 2 }} />
        )}
      </Box>

      <Chip size="small" variant="outlined" label={job.status}
        color={STATUS_COLOR[job.status] || 'default'} />

      <Box sx={{ display: 'flex' }} onClick={(e) => e.stopPropagation()}>
        <Tooltip title="Move to top">
          <span><IconButton size="small" disabled={index === 0}
            onClick={() => onAction(job.id, 'move', 'top')}>
            <KeyboardDoubleArrowUpIcon fontSize="small" />
          </IconButton></span>
        </Tooltip>
        <Tooltip title="Move up">
          <span><IconButton size="small" disabled={index === 0}
            onClick={() => onAction(job.id, 'move', 'up')}>
            <KeyboardArrowUpIcon fontSize="small" />
          </IconButton></span>
        </Tooltip>
        <Tooltip title="Move down">
          <span><IconButton size="small" disabled={index === count - 1}
            onClick={() => onAction(job.id, 'move', 'down')}>
            <KeyboardArrowDownIcon fontSize="small" />
          </IconButton></span>
        </Tooltip>
        <Tooltip title="Move to bottom">
          <span><IconButton size="small" disabled={index === count - 1}
            onClick={() => onAction(job.id, 'move', 'bottom')}>
            <KeyboardDoubleArrowDownIcon fontSize="small" />
          </IconButton></span>
        </Tooltip>
        <Tooltip title={paused
          ? 'Resume'
          : 'Pause — keeps its place in the queue; the track already downloading finishes first'}>
          <IconButton size="small"
            onClick={() => onAction(job.id, paused ? 'resume' : 'pause')}>
            {paused ? <PlayArrowIcon fontSize="small" /> : <PauseIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        <Tooltip title="Cancel">
          <IconButton size="small" onClick={() => onAction(job.id, 'cancel')}>
            <CancelIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
    </Paper>
  )
}

export default function Queue({ onChanged, refreshToken, trackProgress,
                               selectedJobId, onSelect }) {
  const [queue, setQueue] = useState(null)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(-1)
  const [hover, setHover] = useState(-1)
  const items = queue?.items || []
  // A drag in flight must not be yanked out from under the pointer by a poll.
  const dragActive = useRef(false)
  const lastDragEnd = useRef(0)

  const refresh = useCallback(() => {
    if (dragActive.current) return
    api.queue().then(setQueue).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh, refreshToken])
  useEffect(() => {
    const id = setInterval(refresh, 4000)
    return () => clearInterval(id)
  }, [refresh])

  const act = async (id, action, arg) => {
    setError(null)
    try {
      if (action === 'move') await api.moveJob(id, arg)
      else if (action === 'pause') await api.pauseJob(id)
      else if (action === 'resume') await api.resumeJob(id)
      else if (action === 'cancel') await api.cancelJob(id)
      await api.queue().then(setQueue)
      onChanged?.()
    } catch (e) { setError(e.message) }
  }

  const drag = {
    dragging,
    hover,
    // A finished drag is followed by a click in some browsers, which would
    // toggle the detail pane every time a row is moved.
    justDragged: () => Date.now() - lastDragEnd.current < 250,
    start: (i) => { dragActive.current = true; setDragging(i) },
    over: (i) => setHover(i),
    end: () => {
      lastDragEnd.current = Date.now()
      dragActive.current = false
      setDragging(-1); setHover(-1)
    },
    drop: async () => {
      const from = dragging
      const to = hover
      lastDragEnd.current = Date.now()
      dragActive.current = false
      setDragging(-1); setHover(-1)
      if (from < 0 || to < 0 || from === to) return
      const next = [...items]
      next.splice(to, 0, next.splice(from, 1)[0])
      setQueue({ ...queue, items: next })          // optimistic
      try {
        await api.reorderQueue(next.map((j) => j.id))
        onChanged?.()
      } catch (e) { setError(e.message); refresh() }
    },
  }

  if (!queue) return null
  if (!items.length) {
    return (
      <Typography color="text.secondary" variant="body2"
        sx={{ mb: 2, textAlign: 'center' }}>
        Queue is empty.
      </Typography>
    )
  }

  const current = queue.current_track
  return (
    <Box sx={{ mb: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
        <Typography variant="subtitle2">Queue</Typography>
        <Typography variant="caption" color="text.secondary">
          {items.length} job{items.length === 1 ? '' : 's'}
          {queue.queued_tracks > 0 && ` · ${queue.queued_tracks} tracks waiting`}
          {' · drag to reorder, click for details, top of the list downloads next'}
        </Typography>
      </Box>

      {current && (() => {
        const live = trackProgress?.[current.id]
        const pct = live?.progress_pct ?? current.progress_pct
        const transfer = transferLine(live)
        return (
          <Alert severity="info" icon={<PlayArrowIcon fontSize="small" />}
            sx={{ mb: 1, py: 0.25 }}>
            <Typography variant="body2" component="div">
              Downloading now: <strong>
                {[current.artist, current.title].filter(Boolean).join(' — ')}
              </strong>
              {current.stage && ` · ${current.stage}`}
              {pct != null && ` · ${Math.round(pct)}%`}
            </Typography>
            {pct != null && (
              <LinearProgress variant="determinate" value={pct}
                sx={{ mt: 0.5, height: 4, borderRadius: 2 }} />
            )}
            {transfer && (
              <Typography variant="caption" color="text.secondary">{transfer}</Typography>
            )}
          </Alert>
        )
      })()}

      {error && <Alert severity="error" sx={{ mb: 1 }}
        onClose={() => setError(null)}>{error}</Alert>}

      {items.map((job, i) => (
        <Row key={job.id} job={job} index={i} count={items.length}
          onAction={act} drag={drag}
          selected={job.id === selectedJobId} onSelect={onSelect} />
      ))}
    </Box>
  )
}
