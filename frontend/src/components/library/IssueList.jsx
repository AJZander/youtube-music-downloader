import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, IconButton, InputAdornment, LinearProgress, Stack, TextField, ToggleButton,
  ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import DoneIcon from '@mui/icons-material/Done'
import SearchIcon from '@mui/icons-material/Search'
import UndoIcon from '@mui/icons-material/Undo'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'

import { api } from '../../api.js'
import { keepTextSelection, useShiftRange } from '../rangeSelect.js'
import { BULK, KINDS, asText, kindLabel, severityColor, when } from './issues.js'

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
    if (detail.files != null) {
      lines.push(`${detail.matched} of ${detail.files} files matched`
        + (detail.artist === 0 ? ' · a different artist' : detail.artist >= 1 ? ' · your artist' : ''))
    }
  } else if (kind === 'album_copies') {
    lines.push(`keeps ${detail.keep}`)
    for (const k of (detail.copies || []).slice(0, 4)) lines.push(`merges ${k}`)
  } else if (kind === 'bad_file') {
    for (const p of (detail.paths || []).slice(0, 4)) lines.push(base(p))
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
export function IssueRow({
  issue, onNavigate, onChanged, notify, showRef = false, checked, onCheck,
}) {
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
      bgcolor: checked ? 'action.selected' : undefined,
      '&:hover': { bgcolor: 'action.hover' },
    }}>
      {onCheck && (
        <Checkbox size="small" checked={!!checked} onChange={onCheck}
          onMouseDown={keepTextSelection} sx={{ p: 0.25, mt: -0.25 }}
          inputProps={{ 'aria-label': `Select ${kindLabel(issue.kind)}: ${issue.summary}` }} />
      )}
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
        {open && (
          <Tooltip title={issue.state === 'failed'
            ? "Clear it — don't report it again"
            : "Not a problem — don't report it again"}>
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

/**
 * Every open issue of one kind, paged — the sidebar's "Review" — with
 * search, the kind's filters, and bulk actions on what is ticked: a page,
 * a shift-click range, or every issue that matches (less any unticked).
 */
export function IssueListDialog({ kind, artistDir, onClose, onNavigate, onChanged, notify }) {
  const bulk = BULK[kind] || {}
  const filters = [...(bulk.filters || []),
    { value: 'failed', label: 'Failed', help: 'Fixes that were tried and failed' }]
  const applyable = !!KINDS[kind]?.applyable
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState(null)
  const [picked, setPicked] = useState(() => new Set())
  const [all, setAll] = useState(false)          // every match, less `skipped`
  const [skipped, setSkipped] = useState(() => new Set())
  const [busy, setBusy] = useState(false)
  const range = useShiftRange()

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 300)
    return () => clearTimeout(t)
  }, [q])

  const clearPicks = () => { setPicked(new Set()); setAll(false); setSkipped(new Set()) }

  const load = useCallback((offset = 0) => {
    setLoading(true); setError(null)
    api.libraryIssues({ kind, artist_dir: artistDir, state: 'open', q: query, filter,
      limit: PAGE, offset })
      .then((d) => {
        setTotal(d.total)
        setItems((prev) => (offset ? [...prev, ...d.items] : d.items))
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [kind, artistDir, query, filter])
  useEffect(() => { clearPicks(); load(0) }, [load])

  const isPicked = (id) => (all ? !skipped.has(id) : picked.has(id))
  const count = all ? Math.max(0, (total || 0) - skipped.size) : picked.size
  const pageOn = items.length > 0 && items.every((i) => isPicked(i.id))
  const selection = () => (all
    ? { kind, artist_dir: artistDir, q: query || undefined, filter: filter || undefined,
      exclude: [...skipped] }
    : { ids: [...picked] })

  const setMany = (ids, on) => {
    const update = (prev) => {
      const next = new Set(prev)
      // in "all" mode the set holds the exceptions
      ids.forEach((id) => ((on !== all) ? next.add(id) : next.delete(id)))
      return next
    }
    if (all) setSkipped(update)
    else setPicked(update)
  }
  const toggle = (id, e) => setMany(range(items.map((i) => i.id), id, e), !isPicked(id))
  const togglePage = () => {
    if (all && pageOn) { clearPicks(); return }
    setMany(items.map((i) => i.id), !pageOn)
  }

  const act = async (fn, message, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(true)
    try {
      const r = await fn(selection())
      notify?.(message(r))
      clearPicks()
      onChanged?.()
      load(0)
    } catch (e) { notify?.(e.message, 'error') } finally { setBusy(false) }
  }

  const scope = artistDir ? ` for ${artistDir}` : ''
  const actionLabel = items[0]?.action_label || 'Apply'
  const many = (n) => `${n.toLocaleString()} ${kindLabel(kind).toLowerCase()} issue${n === 1 ? '' : 's'}`
  const apply = () => act(
    (sel) => api.libraryApplyIssues({ ...sel, only_auto: false }),
    (r) => `Queued ${r.queued} fix${r.queued === 1 ? '' : 'es'}`,
    bulk.confirmApply || count > 50
      ? `${actionLabel}: ${many(count)}${scope}?\n\n${KINDS[kind]?.help || ''}` : null)
  const decide = (d) => act(
    (sel) => api.libraryDecideIssues({ ...sel, decision: d.decision }),
    (r) => `${d.label}: ${r.queued} queued`,
    count > 20 ? `${d.label}: ${many(count)}${scope}?\n\n${d.help || ''}` : null)
  const dismiss = () => act(
    (sel) => api.libraryDismissIssues(sel),
    (r) => `Dismissed ${r.dismissed}`,
    count > 20 ? `${bulk.dismissLabel || 'Dismiss'} ${many(count)}${scope}? `
      + 'They will not be reported again.' : null)
  const triage = () => act(
    () => api.libraryTriageMatches({ artist_dir: artistDir }),
    (r) => `Accepting ${r.accepted} matches; ${r.no_release} marked not on MusicBrainz`,
    'Accept every match the files settle (all files matched, your artist), and mark every '
      + 'album whose best match is another artist\'s song as not on MusicBrainz'
      + `${scope}? The rest stay for you.`)

  const dismissFailed = async () => {
    if (!window.confirm(`Dismiss every failed "${kindLabel(kind)}" fix`
      + `${artistDir ? ` for ${artistDir}` : ''}? They will not be reported again.`)) return
    setLoading(true)
    try {
      const r = await api.libraryDismissFailed({ kind, artist_dir: artistDir })
      notify?.(`Dismissed ${r.dismissed} failed fix${r.dismissed === 1 ? '' : 'es'}`)
      onChanged?.()
      load(0)
    } catch (e) { notify?.(e.message, 'error'); setLoading(false) }
  }

  return (
    <Dialog open fullWidth maxWidth="md" onClose={onClose}>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
        <Box sx={{ flexGrow: 1 }}>
          {kindLabel(kind)}
          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
            {total != null && `${total.toLocaleString()} open`}
            {artistDir ? ` · ${artistDir}` : ' · whole library'}
          </Typography>
        </Box>
        {items.some((i) => i.state === 'failed') && (
          <Tooltip title="Dismiss every failed fix of this kind, not just the ones shown">
            <span>
              <Button size="small" color="error" disabled={loading} onClick={dismissFailed}>
                Dismiss failed
              </Button>
            </span>
          </Tooltip>
        )}
        <IconButton onClick={onClose} aria-label="Close"><CloseIcon /></IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        {KINDS[kind]?.help && (
          <Typography variant="body2" color="text.secondary" sx={{ px: 2, pt: 1 }}>
            {KINDS[kind].help}
          </Typography>
        )}
        {bulk.triage && !artistDir && (
          <Alert severity="info" sx={{ mx: 2, mt: 1 }}
            action={<Button size="small" disabled={busy} onClick={triage}>Settle them</Button>}>
            Settle what the files settle: accept matches where every file matched and the
            artist is yours, and mark another artist&apos;s song of the same name as not on
            MusicBrainz.
          </Alert>
        )}
        <Stack direction="row" sx={{ px: 2, py: 1, gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField size="small" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by artist, album or folder" sx={{ flexGrow: 1, minWidth: 200 }}
            InputProps={{ startAdornment: (
              <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
            ) }} />
          {filters.length > 0 && (
            <ToggleButtonGroup size="small" exclusive value={filter}
              onChange={(_, v) => setFilter(v)}>
              {filters.map((f) => (
                <ToggleButton key={f.value} value={f.value} sx={{ py: 0.25, fontSize: '0.72rem' }}>
                  <Tooltip title={f.help || ''}><span>{f.label}</span></Tooltip>
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          )}
        </Stack>
        <Box sx={{
          display: 'flex', alignItems: 'center', gap: 0.75, px: 1, py: 0.5, flexWrap: 'wrap',
          position: 'sticky', top: 0, zIndex: 1, bgcolor: 'background.paper',
          borderTop: 1, borderBottom: 1, borderColor: 'divider',
        }}>
          <Checkbox size="small" checked={pageOn} indeterminate={count > 0 && !pageOn}
            onChange={togglePage} disabled={!items.length} sx={{ p: 0.25 }}
            inputProps={{ 'aria-label': 'Select the issues shown' }} />
          <Typography variant="caption" sx={{ minWidth: 70 }}>
            {count ? `${count.toLocaleString()} selected` : 'Select'}
          </Typography>
          {pageOn && !all && total > items.length && (
            <Button size="small" sx={{ py: 0 }}
              onClick={() => { setAll(true); setSkipped(new Set()) }}>
              Select all {total.toLocaleString()} that match
            </Button>
          )}
          {all && (
            <Button size="small" sx={{ py: 0 }} onClick={clearPicks}>Clear selection</Button>
          )}
          <Box sx={{ flexGrow: 1 }} />
          {busy && <CircularProgress size={16} />}
          {applyable && (
            <Button size="small" variant="contained" disabled={!count || busy} onClick={apply}
              startIcon={<DoneIcon fontSize="small" />}>
              {actionLabel}
            </Button>
          )}
          {(bulk.decisions || []).map((d) => (
            <Tooltip key={d.decision} title={d.help || ''}>
              <span>
                <Button size="small" variant="outlined" disabled={!count || busy}
                  onClick={() => decide(d)}>
                  {d.label}
                </Button>
              </span>
            </Tooltip>
          ))}
          <Tooltip title="Not a problem — don't report these again">
            <span>
              <Button size="small" disabled={!count || busy} onClick={dismiss}
                startIcon={<VisibilityOffIcon fontSize="small" />}>
                {bulk.dismissLabel || 'Dismiss'}
              </Button>
            </span>
          </Tooltip>
        </Box>
        {loading && <LinearProgress />}
        {error && <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>}
        {total === 0 && (
          <Typography color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
            {query || filter ? 'Nothing open matches.' : 'Nothing open.'}
          </Typography>
        )}
        {items.map((issue) => (
          <IssueRow key={issue.id} issue={issue} showRef notify={notify}
            checked={isPicked(issue.id)} onCheck={(e) => toggle(issue.id, e)}
            onNavigate={(i) => { onNavigate(i); onClose() }}
            onChanged={(i) => {
              setItems((prev) => prev.filter((x) => x.id !== i.id))
              setTotal((n) => (n ? n - 1 : n))
              setPicked((prev) => { const next = new Set(prev); next.delete(i.id); return next })
              onChanged?.()
            }} />
        ))}
        {total != null && items.length < total && (
          <Button fullWidth disabled={loading} onClick={() => load(items.length)}>
            Load more ({(total - items.length).toLocaleString()} remaining)
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

/** "Apply all" for one kind, after saying what that means. `onlyAuto`:
 *  just the fixes confident enough to apply automatically; otherwise every
 *  open one (missing tracks, merges, damaged files — never automatic), or
 *  those `filter` picks (missing tracks: the ones never tried). */
export function ConfirmApplyDialog({
  kind, count, artistDir, onClose, onDone, notify, onlyAuto = true, filter,
}) {
  const [busy, setBusy] = useState(false)
  const narrowed = !onlyAuto && filter
    ? BULK[kind]?.filters?.find((f) => f.value === filter)?.label || filter : null
  const apply = async () => {
    setBusy(true)
    try {
      const r = await api.libraryApplyIssues({
        kind, artist_dir: artistDir, only_auto: onlyAuto,
        filter: onlyAuto ? undefined : filter || undefined })
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
          {onlyAuto ? 'Only fixes confident enough to apply automatically are included'
            : narrowed ? `Only the open ones ${narrowed.toLowerCase()} are included`
              : 'Every open one is included'}
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
