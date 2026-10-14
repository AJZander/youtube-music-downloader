import { useEffect, useRef, useState } from 'react'
import {
  Badge, Box, Button, Chip, Divider, IconButton, LinearProgress, Stack, ToggleButton,
  ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import BlockIcon from '@mui/icons-material/Block'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive'

import { api } from '../../api.js'
import { ChangeList } from './IssueList.jsx'
import { DUPE_KINDS, KINDS, PHASES, severityColor } from './issues.js'

const MIN_WIDTH = 260
const MAX_WIDTH = 560

const DUPE_TAB = { dup_album: 'albums', dup_track: 'tracks', split_artist: 'artists' }

function Group({ group, onApply, onReview, onResolve }) {
  const kind = KINDS[group.kind]
  const applyable = group.applyable ?? kind?.applyable
  return (
    <Box sx={{
      px: 1, py: 0.75, borderLeft: 3, borderLeftColor: `${severityColor(group.severity)}.main`,
      '&:hover': { bgcolor: 'action.hover' },
    }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
        <Tooltip title={group.description || kind?.help || ''} placement="left">
          <Typography variant="body2" sx={{ flexGrow: 1, minWidth: 0 }} noWrap>
            {group.label || kind?.label || group.kind}
          </Typography>
        </Tooltip>
        <Chip size="small" label={group.count.toLocaleString()}
          color={severityColor(group.severity)} variant="outlined"
          sx={{ height: 20, fontSize: '0.7rem' }} />
      </Box>
      <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
        {applyable && group.auto_count > 0 && (
          <Button size="small" variant="outlined" sx={{ py: 0, fontSize: '0.72rem' }}
            onClick={() => onApply(group)}>
            Apply all ({group.auto_count.toLocaleString()})
          </Button>
        )}
        <Button size="small" sx={{ py: 0, fontSize: '0.72rem' }} onClick={() => onReview(group)}>
          Review
        </Button>
        {DUPE_KINDS.has(group.kind) && (
          <Button size="small" sx={{ py: 0, fontSize: '0.72rem' }} onClick={() => onResolve(group)}>
            Resolve
          </Button>
        )}
      </Stack>
    </Box>
  )
}

/**
 * Right-hand rail: everything that needs a decision, grouped by kind, for the
 * whole library or just the artist on screen — plus what has been done
 * recently, with undo.
 */
export default function AttentionSidebar({
  open, onToggle, width, onWidth, artistDir, meta, overview, version,
  onApply, onReview, onResolveDupes, onTrash, onIgnored, onChanged, notify,
}) {
  const [scope, setScope] = useState('library')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [changes, setChanges] = useState(null)
  const resizing = useRef(false)

  const scopedArtist = scope === 'artist' ? artistDir : null
  useEffect(() => { if (!artistDir && scope === 'artist') setScope('library') }, [artistDir, scope])

  useEffect(() => {
    let live = true
    setLoading(true)
    api.libraryAttention(scopedArtist)
      .then((d) => { if (live) setData(d) })
      .catch(() => { if (live) setData((prev) => prev || { total: 0, groups: [] }) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [scopedArtist, version])

  useEffect(() => {
    let live = true
    api.libraryChanges({ limit: 15 })
      .then((d) => { if (live) setChanges(d.items || []) })
      .catch(() => { if (live) setChanges([]) })
    return () => { live = false }
  }, [version])

  useEffect(() => {
    const move = (e) => {
      if (!resizing.current) return
      onWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, window.innerWidth - e.clientX - 16)))
    }
    const stop = () => {
      if (!resizing.current) return
      resizing.current = false
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', stop)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', stop)
    }
  }, [onWidth])

  const total = data?.total ?? overview?.issues?.open ?? 0

  if (!open) {
    return (
      <Stack spacing={1} sx={{ alignItems: 'center', py: 1, height: '100%' }}>
        <Tooltip title="Show Needs attention" placement="left">
          <IconButton size="small" onClick={onToggle}><ChevronLeftIcon /></IconButton>
        </Tooltip>
        <Tooltip title={`${total} open issues`} placement="left">
          <IconButton size="small" onClick={onToggle}>
            <Badge badgeContent={total} color="warning" max={999}>
              <NotificationsActiveIcon fontSize="small" />
            </Badge>
          </IconButton>
        </Tooltip>
        <Tooltip title="Duplicates" placement="left">
          <IconButton size="small" onClick={() => onResolveDupes('albums')}>
            <ContentCopyIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Trash" placement="left">
          <IconButton size="small" onClick={onTrash}>
            <Badge badgeContent={overview?.trash_entries || 0} color="default" max={999}>
              <DeleteOutlineIcon fontSize="small" />
            </Badge>
          </IconButton>
        </Tooltip>
        <Tooltip title="Ignored" placement="left">
          <IconButton size="small" onClick={onIgnored}><BlockIcon fontSize="small" /></IconButton>
        </Tooltip>
      </Stack>
    )
  }

  const groups = data?.groups || []
  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 0, width }}>
      <Box onMouseDown={() => { resizing.current = true; document.body.style.userSelect = 'none' }}
        sx={{ width: 5, flexShrink: 0, cursor: 'ew-resize', '&:hover': { bgcolor: 'action.hover' } }} />
      <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', px: 1, pt: 1, gap: 0.5 }}>
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            Needs attention
            <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.75 }}>
              {total.toLocaleString()}
            </Typography>
          </Typography>
          <Tooltip title="Collapse">
            <IconButton size="small" onClick={onToggle}><ChevronRightIcon fontSize="small" /></IconButton>
          </Tooltip>
        </Box>
        <Box sx={{ px: 1, pb: 0.75 }}>
          <ToggleButtonGroup size="small" exclusive fullWidth value={scope}
            onChange={(_, v) => { if (v) setScope(v) }}>
            <ToggleButton value="library" sx={{ py: 0.25, fontSize: '0.72rem' }}>Library</ToggleButton>
            <ToggleButton value="artist" disabled={!artistDir} sx={{ py: 0.25, fontSize: '0.72rem' }}>
              <Typography noWrap variant="inherit" sx={{ maxWidth: 140 }}>
                {artistDir || 'This artist'}
              </Typography>
            </ToggleButton>
          </ToggleButtonGroup>
        </Box>
        <Box sx={{ height: 2 }}>{loading && <LinearProgress sx={{ height: 2 }} />}</Box>

        <Box sx={{ flexGrow: 1, overflowY: 'auto', minHeight: 0 }}>
          {data && !groups.length && (
            <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: 'center' }}>
              {meta?.last_run || meta?.run
                ? 'All clear — nothing needs attention.'
                : 'Nothing reported yet. Run a full check to find what needs fixing.'}
            </Typography>
          )}
          {groups.map((g) => (
            <Group key={g.kind} group={g}
              onApply={(grp) => onApply(grp, scopedArtist)}
              onReview={(grp) => onReview(grp, scopedArtist)}
              onResolve={(grp) => onResolveDupes(DUPE_TAB[grp.kind] || 'albums')} />
          ))}

          <Divider sx={{ mt: 1 }} />
          <Box sx={{ px: 1, pt: 1 }}>
            <Typography variant="overline" color="text.secondary">Activity</Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              {meta
                ? `${PHASES[meta.phase] || meta.phase || 'Idle'}${meta.paused ? ' (paused)' : ''}`
                  + `${meta.current ? ` — ${meta.current}` : ''}`
                  + `${meta.apply_queue ? ` · ${meta.apply_queue} fixes queued` : ''}`
                : '—'}
            </Typography>
            {meta?.next_run_at && !meta.running && (
              <Typography variant="caption" color="text.secondary" display="block">
                Next check {new Date(meta.next_run_at).toLocaleTimeString()}
              </Typography>
            )}
          </Box>
          <ChangeList batches={changes} dense onChanged={onChanged} notify={notify} />
        </Box>

        <Divider />
        <Stack direction="row" spacing={0.5} sx={{ p: 0.75, flexWrap: 'wrap', gap: 0.5 }}>
          <Button size="small" startIcon={<ContentCopyIcon fontSize="small" />}
            onClick={() => onResolveDupes('albums')}>
            Duplicates
          </Button>
          <Button size="small" startIcon={<DeleteOutlineIcon fontSize="small" />} onClick={onTrash}>
            Trash{overview?.trash_entries ? ` (${overview.trash_entries})` : ''}
          </Button>
          <Button size="small" startIcon={<BlockIcon fontSize="small" />} onClick={onIgnored}>
            Ignored
          </Button>
        </Stack>
      </Box>
    </Box>
  )
}
