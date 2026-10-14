import { useEffect, useState } from 'react'
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, CircularProgress, Dialog,
  DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Tab, Tabs,
  TextField, Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'

import { api } from '../../api.js'
import AlbumDupes from './AlbumDupes.jsx'
import Ignored from './Ignored.jsx'
import SplitArtists, { SplitArtistGroup } from './SplitArtists.jsx'
import TrackDupes from './TrackDupes.jsx'
import Trash from './Trash.jsx'

/** A large modal with a title bar and a close button. */
export function ManagerDialog({ title, subtitle, onClose, children, maxWidth = 'lg' }) {
  return (
    <Dialog open fullWidth maxWidth={maxWidth} onClose={onClose}
      PaperProps={{ sx: { height: maxWidth === 'lg' ? '85vh' : undefined } }}>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
        <Box sx={{ flexGrow: 1 }}>
          {title}
          {subtitle && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        <IconButton onClick={onClose}><CloseIcon /></IconButton>
      </DialogTitle>
      <DialogContent dividers>{children}</DialogContent>
    </Dialog>
  )
}

const DUPE_TABS = ['albums', 'tracks', 'artists']

/** Every duplicate group in the library, with the bulk actions the old
 *  Library tab had. */
export function DupesManagerDialog({ initial = 'albums', scanRunning, onClose, onChanged }) {
  const [tab, setTab] = useState(Math.max(0, DUPE_TABS.indexOf(initial)))
  return (
    <ManagerDialog title="Duplicates" onClose={onClose}>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, mt: -1 }}>
        <Tab label="Duplicate albums" />
        <Tab label="Duplicate tracks" />
        <Tab label="Split artists" />
      </Tabs>
      {scanRunning
        ? (
          <Alert severity="info">
            The duplicate report waits for the library scan to finish — it needs
            a consistent picture of the library.
          </Alert>
        )
        : (
          <>
            {tab === 0 && <AlbumDupes onChanged={onChanged} />}
            {tab === 1 && <TrackDupes onChanged={onChanged} />}
            {tab === 2 && <SplitArtists onChanged={onChanged} />}
          </>
        )}
    </ManagerDialog>
  )
}

export function TrashDialog({ onClose, onChanged }) {
  return (
    <ManagerDialog title="Library trash" maxWidth="md" onClose={onClose}>
      <Trash onChanged={onChanged} />
    </ManagerDialog>
  )
}

export function IgnoredDialog({ onClose, onChanged }) {
  return (
    <ManagerDialog title="Ignored" subtitle="decisions that duplicate scans respect"
      maxWidth="md" onClose={onClose}>
      <Ignored onChanged={onChanged} />
    </ManagerDialog>
  )
}

const strictKey = (s) => (s || '').normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g, '')

/** The split-artist groups one artist belongs to. */
export function MergeArtistDialog({ artistDir, onClose, onChanged }) {
  const [groups, setGroups] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    api.libraryDuplicates('artists', { limit: 500 })
      .then((d) => setGroups(d.items.filter((g) => g.variants.some(
        (v) => v.artist_dir === artistDir || strictKey(v.artist_dir) === strictKey(artistDir)))))
      .catch((e) => setError(e.message))
  }, [artistDir])
  return (
    <ManagerDialog title={`Merge ${artistDir}`} maxWidth="md" onClose={onClose}>
      {error && <Alert severity="error">{error}</Alert>}
      {!groups && !error && <Box sx={{ textAlign: 'center', p: 3 }}><CircularProgress /></Box>}
      {groups && !groups.length && (
        <Typography color="text.secondary" sx={{ p: 2, textAlign: 'center' }}>
          {artistDir} is not split across folders any more.
        </Typography>
      )}
      {groups?.map((g) => <SplitArtistGroup key={g.id} group={g} onMerged={onChanged} />)}
    </ManagerDialog>
  )
}

const LIST_FIELDS = new Set(['genres', 'moods'])

/**
 * Pin a value so it wins over MusicBrainz and Plex: genres and moods are
 * picked from the vocabularies, plain fields are typed.
 */
export function OverrideDialog({ entity, refKey, field, label, value, overridden,
                                 onClose, onSaved, notify }) {
  const isList = LIST_FIELDS.has(field)
  const [current, setCurrent] = useState(() => (isList
    ? (Array.isArray(value) ? value : (value ? [value] : []))
    : (Array.isArray(value) ? value.join(', ') : (value ?? ''))))
  const [input, setInput] = useState('')
  const [options, setOptions] = useState([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!isList) return undefined
    const id = setTimeout(() => {
      const fetcher = field === 'moods' ? api.libraryMoods : api.libraryGenres
      fetcher(input).then((d) => setOptions(d.items || [])).catch(() => {})
    }, 200)
    return () => clearTimeout(id)
  }, [input, field, isList])

  const save = async () => {
    setBusy(true)
    try {
      const v = isList ? current : (field === 'year' ? String(current).trim() : current)
      onSaved(await api.librarySetOverride(entity, refKey, field, v))
      notify?.(`${label} pinned`)
      onClose()
    } catch (e) { notify?.(e.message, 'error') } finally { setBusy(false) }
  }
  const clear = async () => {
    setBusy(true)
    try {
      onSaved(await api.libraryClearOverride(entity, refKey, field))
      notify?.(`${label} unpinned`)
      onClose()
    } catch (e) { notify?.(e.message, 'error') } finally { setBusy(false) }
  }

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Edit {label.toLowerCase()}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          A pinned value wins over MusicBrainz and Plex on every future check,
          and is written to the files and to Plex like any other fix.
        </Typography>
        {isList
          ? (
            <Autocomplete multiple freeSolo options={options} value={current}
              filterOptions={(x) => x}
              onChange={(_, v) => setCurrent(v)}
              inputValue={input} onInputChange={(_, v) => setInput(v)}
              renderTags={(vals, getTagProps) => vals.map((opt, i) => (
                <Chip size="small" label={opt} {...getTagProps({ index: i })} key={opt} />
              ))}
              renderInput={(params) => (
                <TextField {...params} autoFocus size="small" label={label}
                  placeholder={field === 'moods' ? 'Add a mood' : 'Add a genre'} />
              )} />
          )
          : (
            <TextField autoFocus fullWidth size="small" label={label} value={current}
              onChange={(e) => setCurrent(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') save() }} />
          )}
      </DialogContent>
      <DialogActions>
        {overridden && <Button color="warning" disabled={busy} onClick={clear}>Unpin</Button>}
        <Box sx={{ flexGrow: 1 }} />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={busy} onClick={save}>Pin</Button>
      </DialogActions>
    </Dialog>
  )
}

/** "Full run" — every album, one by one. Report-only by default. */
export function FullRunDialog({ status, onClose, onStarted, notify }) {
  const [reportOnly, setReportOnly] = useState(true)
  const [busy, setBusy] = useState(false)
  const start = async () => {
    setBusy(true)
    try {
      onStarted(await api.metadataRun(true, reportOnly))
      notify?.(reportOnly ? 'Full check started — report only' : 'Full run started')
      onClose()
    } catch (e) { notify?.(e.message, 'error') } finally { setBusy(false) }
  }
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Full run</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1.5 }}>
          Checks every artist, album and track one by one: the files and their
          tags, MusicBrainz, and what Plex shows. The first pass over a large
          library takes hours — MusicBrainz allows one request a second — and
          runs in the background.
        </Typography>
        <FormControlLabel
          control={<Checkbox checked={reportOnly} onChange={(e) => setReportOnly(e.target.checked)} />}
          label={<Typography variant="body2">Report only — change nothing</Typography>} />
        <Typography variant="caption" color="text.secondary" display="block">
          {reportOnly
            ? 'Every problem is listed under Needs attention for you to review.'
            : status?.auto_apply
              ? 'Confident fixes are applied as albums are checked; the rest wait for review.'
              : 'Auto-apply is off, so this still only reports. Turn auto-apply on to have fixes applied.'}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={busy} onClick={start}>Start</Button>
      </DialogActions>
    </Dialog>
  )
}
