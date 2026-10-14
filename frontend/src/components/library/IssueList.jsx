import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, IconButton, LinearProgress, Stack, Tooltip, Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import DoneIcon from '@mui/icons-material/Done'
import UndoIcon from '@mui/icons-material/Undo'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'

import { api } from '../../api.js'
import { KINDS, asText, kindLabel, severityColor, when } from './issues.js'

const PAGE = 100

const FIELD_NAMES = {
  albumartist: 'album artist', tracknumber: 'track #', discnumber: 'disc #',
  mb_albumid: 'MusicBrainz ids', mb_releasegroupid: 'MusicBrainz ids',
  mb_albumartistid: 'MusicBrainz ids', mb_trackid: 'MusicBrainz ids',
  mb_releasetrackid: 'MusicBrainz ids', releasetype: 'release type', junk: 'junk tags',
}
const fieldName = (f) => FIELD_NAMES[f] || f.replace(/_/g, ' ')
const list = (v) => (Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : asText(v))
const base = (p) => (p || '').split('/').pop()

/** The concrete changes an issue proposes, read from its kind's detail. */
function IssueDetail({ kind, detail }) {
  if (!detail || typeof detail !== 'object') return null
  const lines = []
  if (kind === 'tags') {
    if (detail.fields && typeof detail.fields === 'object') {
      const merged = {}
      for (const [f, n] of Object.entries(detail.fields)) {
        const name = fieldName(f)
        merged[name] = Math.max(merged[name] || 0, typeof n === 'number' ? n : 1)
      }
      lines.push(Object.entries(merged).map(([f, n]) => `${f} (${n})`).join(' · '))
    }
    for (const ex of (detail.examples || []).slice(0, 4)) {
      if (ex.field === 'junk') lines.push(`${base(ex.path)}: remove ${list(ex.from)}`)
      else if (!ex.field.startsWith('mb_')) {
        lines.push(`${base(ex.path)}: ${fieldName(ex.field)} ${list(ex.from)} → ${list(ex.to)}`)
      }
    }
  } else if (kind === 'track_order') {
    for (const f of (detail.files || []).slice(0, 3)) lines.push(`${base(f.path)}: ${f.from} → ${f.to}`)
    for (const f of (detail.plex || []).slice(0, 3)) lines.push(`Plex ${base(f.path)}: ${f.from} → ${f.to}`)
  } else if (kind === 'missing_tracks') {
    for (const t of (detail.missing || []).slice(0, 5)) {
      lines.push(`${detail.multi_disc ? `${t.disc}-` : ''}${t.number}. ${t.title}`)
    }
    if (detail.download?.job_id) lines.push(`Download queued (${detail.download.tracks} tracks)`)
  } else if (kind === 'move' || kind === 'move_conflict') {
    if (detail.from || detail.to) lines.push(`${detail.from || ''} → ${detail.to || ''}`)
    if (detail.files?.length) lines.push(`${detail.files.length} files`)
  } else if (Array.isArray(detail.from)) {
    lines.push(`${list(detail.from)} → ${list(detail.to)}`)
  } else if (Array.isArray(detail.to)) {
    lines.push(list(detail.to))
  } else if (kind === 'review_match' && detail.candidates?.length) {
    const c = detail.candidates[0]
    lines.push(`Best: ${c.title} — ${c.artist}${c.date ? ` (${c.date.slice(0, 4)})` : ''}`
      + ` · ${Math.round((c.score || 0) * 100)}%`)
  } else if (kind === 'folder_artist') {
    lines.push(`folder "${detail.folder}" · tag "${detail.tag}"`)
  } else if (kind === 'not_in_plex' || kind === 'plex_ghost') {
    for (const p of (detail.paths || []).slice(0, 3)) lines.push(base(p))
  } else if (kind === 'extra_tracks') {
    for (const p of (detail.paths || []).slice(0, 4)) lines.push(base(p))
  } else if (kind === 'dup_album') {
    for (const d of (detail.dirpaths || []).slice(0, 4)) lines.push(d)
  } else if (kind === 'dup_track') {
    for (const p of (detail.paths || []).slice(0, 4)) lines.push(p)
  } else if (kind === 'split_artist') {
    lines.push((detail.folders || []).join(' · '))
  } else if (typeof detail.from === 'string' && typeof detail.to === 'string') {
    lines.push(`${detail.from} → ${detail.to}`)
  }
  const shown = lines.filter(Boolean)
  if (!shown.length) return null
  return (
    <Box sx={{ mt: 0.25 }}>
      {shown.slice(0, 6).map((l, i) => (
        <Typography key={`${i}-${l}`} variant="caption" color="text.secondary" display="block"
          sx={{ wordBreak: 'break-word' }}>
          {l}
        </Typography>
      ))}
      {shown.length > 6 && (
        <Typography variant="caption" color="text.secondary">
          …and {shown.length - 6} more
        </Typography>
      )}
    </Box>
  )
}

/**
 * One issue with its actions. `onNavigate` makes the text a link to the
 * artist/album the issue is about; `onChanged` hears about every action.
 */
export function IssueRow({ issue, onNavigate, onChanged, notify, showRef = false }) {
  const [busy, setBusy] = useState(false)
  const kind = KINDS[issue.kind]
  const applyable = kind ? kind.applyable : !!issue.action_label
  const open = issue.state === 'open' || issue.state === 'failed'

  const act = async (fn, message) => {
    setBusy(true)
    try {
      const r = await fn()
      notify?.(typeof message === 'function' ? message(r) : message)
      onChanged?.(issue)
    } catch (e) {
      notify?.(e.message, 'error')
    } finally { setBusy(false) }
  }

  return (
    <Box sx={{
      display: 'flex', gap: 1, alignItems: 'flex-start', py: 0.75, px: 1,
      borderLeft: 3, borderLeftColor: `${severityColor(issue.severity)}.main`,
      '&:hover': { bgcolor: 'action.hover' },
    }}>
      <Box sx={{ flexGrow: 1, minWidth: 0, cursor: onNavigate ? 'pointer' : 'default' }}
        onClick={() => onNavigate?.(issue)}>
        <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', flexWrap: 'wrap', gap: 0.5 }}>
          <Typography variant="body2" sx={{ fontWeight: 500 }}>{kindLabel(issue.kind)}</Typography>
          {issue.auto && open && (
            <Tooltip title="Confident enough to apply automatically">
              <Chip size="small" variant="outlined" color="success" label="auto"
                sx={{ height: 18, fontSize: '0.68rem' }} />
            </Tooltip>
          )}
          {issue.state !== 'open' && (
            <Chip size="small" label={issue.state}
              color={issue.state === 'failed' ? 'error'
                : issue.state === 'applied' ? 'success' : 'default'}
              sx={{ height: 18, fontSize: '0.68rem' }} />
          )}
        </Stack>
        <Typography variant="caption" color="text.secondary" display="block"
          sx={{ wordBreak: 'break-word' }}>
          {issue.summary}
        </Typography>
        {showRef && (
          <Typography variant="caption" color="text.secondary" display="block" noWrap
            title={issue.ref} sx={{ fontFamily: 'monospace', fontSize: '0.7rem' }}>
            {issue.album_key || issue.ref}
          </Typography>
        )}
        <IssueDetail kind={issue.kind} detail={issue.detail} />
        {issue.error && (
          <Typography variant="caption" color="error" display="block">{issue.error}</Typography>
        )}
      </Box>
      <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0, alignItems: 'center' }}>
        {busy && <CircularProgress size={16} />}
        {open && applyable && (
          <Tooltip title={kind?.help || ''}>
            <span>
              <Button size="small" variant="outlined" disabled={busy}
                startIcon={<DoneIcon fontSize="small" />}
                onClick={() => act(() => api.libraryApplyIssues({ ids: [issue.id] }),
                  (r) => (r?.queued ? 'Fix queued' : 'Nothing to apply'))}>
                {issue.action_label || 'Apply'}
              </Button>
            </span>
          </Tooltip>
        )}
        {issue.state === 'open' && (
          <Tooltip title="Not a problem — don't report it again">
            <span>
              <IconButton size="small" disabled={busy}
                onClick={() => act(() => api.libraryDismissIssues([issue.id]), 'Dismissed')}>
                <VisibilityOffIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        )}
        {issue.state === 'dismissed' && (
          <Tooltip title="Report this again">
            <span>
              <IconButton size="small" disabled={busy}
                onClick={() => act(() => api.libraryReopenIssues([issue.id]), 'Reopened')}>
                <UndoIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        )}
      </Stack>
    </Box>
  )
}

/** Every open issue of one kind, paged — the sidebar's "Review". */
export function IssueListDialog({ kind, artistDir, onClose, onNavigate, onChanged, notify }) {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback((offset = 0) => {
    setLoading(true); setError(null)
    api.libraryIssues({ kind, artist_dir: artistDir, state: 'open', limit: PAGE, offset })
      .then((d) => {
        setTotal(d.total)
        setItems((prev) => (offset ? [...prev, ...d.items] : d.items))
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [kind, artistDir])
  useEffect(() => { load(0) }, [load])

  return (
    <Dialog open fullWidth maxWidth="md" onClose={onClose}>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
        <Box sx={{ flexGrow: 1 }}>
          {kindLabel(kind)}
          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
            {total != null && `${total} open`}{artistDir ? ` · ${artistDir}` : ' · whole library'}
          </Typography>
        </Box>
        <IconButton onClick={onClose}><CloseIcon /></IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        {KINDS[kind]?.help && (
          <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 1 }}>
            {KINDS[kind].help}
          </Typography>
        )}
        {loading && <LinearProgress />}
        {error && <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>}
        {total === 0 && (
          <Typography color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
            Nothing open.
          </Typography>
        )}
        {items.map((issue) => (
          <IssueRow key={issue.id} issue={issue} showRef notify={notify}
            onNavigate={(i) => { onNavigate(i); onClose() }}
            onChanged={(i) => {
              setItems((prev) => prev.filter((x) => x.id !== i.id))
              setTotal((n) => (n ? n - 1 : n))
              onChanged?.()
            }} />
        ))}
        {total != null && items.length < total && (
          <Button fullWidth disabled={loading} onClick={() => load(items.length)}>
            Load more ({total - items.length} remaining)
          </Button>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** Change batches with Undo — the sidebar's Activity and the History tab. */
export function ChangeList({ batches, onChanged, notify, dense = false }) {
  const [busy, setBusy] = useState(null)

  const revert = async (batch) => {
    if (!window.confirm(`Undo "${batch.summary}"? `
      + 'Files and Plex go back to how they were before this change.')) return
    setBusy(batch.batch_id)
    try {
      const r = await api.libraryRevertChange(batch.batch_id)
      notify?.(`Undid ${r.reverted} change(s)`
        + (r.failures?.length ? ` — ${r.failures.length} could not be undone` : ''),
      r.failures?.length ? 'warning' : 'success')
      onChanged?.()
    } catch (e) { notify?.(e.message, 'error') } finally { setBusy(null) }
  }

  if (!batches) return <LinearProgress />
  if (!batches.length) {
    return (
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', p: 1 }}>
        No changes yet.
      </Typography>
    )
  }
  return batches.map((b) => (
    <Box key={b.batch_id} sx={{
      display: 'flex', alignItems: 'center', gap: 1, px: 1, py: dense ? 0.4 : 0.75,
      '&:hover': { bgcolor: 'action.hover' },
    }}>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant={dense ? 'caption' : 'body2'} display="block" noWrap
          title={b.summary} sx={{ textDecoration: b.reverted ? 'line-through' : 'none' }}>
          {b.summary}
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block" noWrap
          sx={{ fontSize: '0.68rem' }}>
          {when(b.applied_at)} · {b.target}{b.album_key ? ` · ${b.album_key}` : ''}
        </Typography>
      </Box>
      {b.reverted
        ? <Chip size="small" label="undone" sx={{ height: 18, fontSize: '0.68rem' }} />
        : (
          <Tooltip title="Undo this change">
            <span>
              <IconButton size="small" disabled={busy === b.batch_id} onClick={() => revert(b)}>
                {busy === b.batch_id ? <CircularProgress size={14} /> : <UndoIcon fontSize="small" />}
              </IconButton>
            </span>
          </Tooltip>
        )}
    </Box>
  ))
}

/** "Apply all" for one kind, after saying what that means. */
export function ConfirmApplyDialog({ kind, count, artistDir, onClose, onDone, notify }) {
  const [busy, setBusy] = useState(false)
  const apply = async () => {
    setBusy(true)
    try {
      const r = await api.libraryApplyIssues({ kind, artist_dir: artistDir, only_auto: true })
      notify?.(`Queued ${r.queued} fix${r.queued === 1 ? '' : 'es'}`)
      onDone?.(r)
      onClose()
    } catch (e) { notify?.(e.message, 'error') } finally { setBusy(false) }
  }
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Apply {count} × {kindLabel(kind)}?</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1 }}>
          {KINDS[kind]?.help}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Only fixes confident enough to apply automatically are included
          {artistDir ? <> for <strong>{artistDir}</strong></> : ' across the whole library'}.
          They run in the background, and every change is logged under
          Activity, where it can be undone.
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={busy} onClick={apply}>Apply</Button>
      </DialogActions>
    </Dialog>
  )
}
