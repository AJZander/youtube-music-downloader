import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Box, Button, Chip, CircularProgress, FormControlLabel, InputAdornment, MenuItem,
  Paper, Select, Stack, Switch, TextField, Tooltip, Typography,
} from '@mui/material'
import FilterListIcon from '@mui/icons-material/FilterList'

import { api, subscribeEvents } from '../api.js'
import ArtistHeader from './artist/ArtistHeader.jsx'
import ArtistSearch from './artist/ArtistSearch.jsx'
import ReleaseSection from './artist/ReleaseSection.jsx'
import SelectionBar from './artist/SelectionBar.jsx'
import { describeQueue, useReleaseSelection } from './artist/selection.js'
import {
  CATEGORIES, loadRecent, ownedFully, pickNew, saveRecent, sortReleases,
} from './artist/shared.js'

/**
 * Browse an artist's whole YouTube Music discography and choose what to
 * download — whole releases, or single tracks out of one.
 *
 * The release list arrives at once; tracklists fill in behind it as the
 * server prefetches them (paced, and announced over SSE). Expanding a release
 * the prefetch has not reached yet loads it straight away.
 *
 * The selection (see useReleaseSelection) survives filters and refreshes,
 * and is cleared only by opening another artist.
 */
export default function ArtistBrowser({ request, onQueued, stats, bottomOffset = 0 }) {
  const [view, setView] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [recent, setRecent] = useState(loadRecent)

  const [expanded, setExpanded] = useState({})
  const [fetching, setFetching] = useState({})
  const [hidden, setHidden] = useState({})
  const [hideOwned, setHideOwned] = useState(false)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('newest')
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)

  // The SSE handler is registered once, so it reads these through refs.
  const refRef = useRef(null)
  const channelRef = useRef(null)
  const requestSeq = useRef(0)
  const refreshTimer = useRef(null)
  // Read by `expand`, which stays stable so memoized rows are not all redrawn.
  const expandedRef = useRef(expanded)
  const fetchingRef = useRef(fetching)
  expandedRef.current = expanded
  fetchingRef.current = fetching

  const remember = useCallback((artist) => {
    setRecent((prev) => {
      const next = [
        { ref: artist.channel_id, name: artist.name, thumbnail: artist.thumbnail },
        ...prev.filter((a) => a.ref !== artist.channel_id),
      ].slice(0, 10)
      saveRecent(next)
      return next
    })
  }, [])

  const forget = useCallback((ref) => {
    setRecent((prev) => {
      const next = prev.filter((a) => a.ref !== ref)
      saveRecent(next)
      return next
    })
  }, [])

  /** Re-read the view from the server's cache; out-of-order answers lose. */
  const reload = useCallback(async ({ refresh = false, quiet = true } = {}) => {
    const ref = refRef.current
    if (!ref) return
    const seq = ++requestSeq.current
    if (!quiet) setLoading(true)
    try {
      const v = await api.catalogArtist(ref, refresh)
      if (seq === requestSeq.current) setView(v)
    } catch (e) {
      if (!quiet) setError(e.message)
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [])

  const open = useCallback(async (ref) => {
    const seq = ++requestSeq.current
    refRef.current = ref
    setLoading(true); setError(null); setNotice(null)
    try {
      const v = await api.catalogArtist(ref)
      if (seq !== requestSeq.current) return
      channelRef.current = v.artist.channel_id
      setView(v)
      setSelection({}); setExpanded({}); setQuery(''); setHidden({})
      remember(v.artist)
    } catch (e) {
      if (seq === requestSeq.current) setError(e.message)
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }, [remember])

  // A link pasted into the main bar arrives here.
  useEffect(() => {
    if (request?.ref) open(request.ref)
  }, [request, open])

  useEffect(() => subscribeEvents({
    'catalog.progress': (p) => {
      if (p.channel_id !== channelRef.current) return
      // Batch a burst of progress into one re-read; the last one is immediate.
      clearTimeout(refreshTimer.current)
      refreshTimer.current = setTimeout(() => reload(), p.running ? 1500 : 0)
    },
  }), [reload])

  // SSE can drop (proxies, sleep); a slow poll keeps a long prefetch honest.
  const prefetching = !!view?.prefetch?.running
  useEffect(() => {
    if (!prefetching) return undefined
    const id = setInterval(() => reload(), 20000)
    return () => clearInterval(id)
  }, [prefetching, reload])

  // ---------------------------------------------------------------- selection
  const expand = useCallback(async (r) => {
    const opening = !expandedRef.current[r.browse_id]
    setExpanded((e) => ({ ...e, [r.browse_id]: opening }))
    if (!opening || r.tracks || fetchingRef.current[r.browse_id]) return
    setFetching((f) => ({ ...f, [r.browse_id]: true }))
    try {
      await api.catalogAlbum(r.browse_id)
      await reload()
    } catch (e) {
      setError(`${r.title}: ${e.message}`)
      setExpanded((x) => ({ ...x, [r.browse_id]: false }))
    } finally {
      setFetching((f) => {
        const next = { ...f }
        delete next[r.browse_id]
        return next
      })
    }
  }, [reload])

  // ---------------------------------------------------------------- derived
  const releases = useMemo(() => view?.releases || [], [view])
  const titles = useMemo(
    () => Object.fromEntries(releases.map((r) => [r.browse_id, r.title])), [releases])
  const counts = useMemo(() => {
    const c = {}
    for (const r of releases) c[r.category] = (c[r.category] || 0) + 1
    return c
  }, [releases])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return releases.filter((r) => !hidden[r.category]
      && !(hideOwned && ownedFully(r))
      && (!needle || r.title.toLowerCase().includes(needle)
        || (r.tracks || []).some((t) => t.title.toLowerCase().includes(needle))))
  }, [releases, hidden, hideOwned, query])

  const sections = useMemo(() => CATEGORIES
    .map((c) => ({ ...c, releases: sortReleases(visible.filter((r) => r.category === c.key), sort) }))
    .filter((s) => s.releases.length), [visible, sort])

  const {
    selection, setSelection, toggle, toggleTrack, selectMany, queueable, summary, picks,
    clearSettled,
  } = useReleaseSelection(releases, force)

  // ---------------------------------------------------------------- queueing
  const queue = async () => {
    // In the order shown — albums first — then any picks a filter hides.
    const shown = sections.flatMap((x) => x.releases)
    const rest = releases.filter((r) => !shown.includes(r))
    setBusy(true); setError(null); setNotice(null)
    try {
      const result = await api.catalogQueue(picks([...shown, ...rest], view.artist.name), force)
      clearSettled(result)
      const { notice: text, error: problem } = describeQueue(result, titles)
      setNotice(text)
      if (problem) setError(problem)
      onQueued?.()
      reload()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const selectedIds = Object.keys(selection)

  return (
    <Box>
      <ArtistSearch onOpen={open} busy={loading} recent={recent} onForget={forget} />

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice(null)}>{notice}</Alert>}

      {!view && loading && (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <CircularProgress size={28} />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Reading the discography…
          </Typography>
        </Paper>
      )}

      {!view && !loading && (
        <Paper variant="outlined" sx={{ p: 3, textAlign: 'center', borderStyle: 'dashed' }}>
          <Typography variant="body2" color="text.secondary">
            Find an artist to see every album, EP and single they have on YouTube Music,
            with tracklists and what is already in your library — then pick what to download.
          </Typography>
        </Paper>
      )}

      {view && (
        <>
          <ArtistHeader view={view} loading={loading}
            onRefresh={() => reload({ refresh: true, quiet: false })}
            onOpenArtist={open} />

          <Paper sx={{ p: 1.5, mb: 2 }}>
            <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.75, alignItems: 'center' }}>
              {CATEGORIES.filter((c) => counts[c.key]).map((c) => (
                <Chip key={c.key} size="small" clickable
                  color={hidden[c.key] ? 'default' : 'primary'}
                  variant={hidden[c.key] ? 'outlined' : 'filled'}
                  label={`${c.label} ${counts[c.key]}`}
                  onClick={() => setHidden((h) => ({ ...h, [c.key]: !h[c.key] }))} />
              ))}
              <Box sx={{ flexGrow: 1 }} />
              <FormControlLabel sx={{ mr: 0 }}
                control={<Switch size="small" checked={hideOwned}
                  onChange={(e) => setHideOwned(e.target.checked)} />}
                label={<Typography variant="body2">Hide owned</Typography>} />
              <Tooltip title="Tracks already in your library are normally left out. This
                downloads them again — and skips the duplicate check for what you queue.">
                <FormControlLabel sx={{ mr: 0 }}
                  control={<Switch size="small" color="warning" checked={force}
                    onChange={(e) => setForce(e.target.checked)} />}
                  label={<Typography variant="body2">Re-download owned</Typography>} />
              </Tooltip>
            </Stack>
            <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1, alignItems: 'center', mt: 1.25 }}>
              <TextField size="small" placeholder="Filter releases and tracks" value={query}
                onChange={(e) => setQuery(e.target.value)} sx={{ flexGrow: 1, minWidth: 200 }}
                InputProps={{ startAdornment: (
                  <InputAdornment position="start"><FilterListIcon fontSize="small" /></InputAdornment>
                ) }} />
              <Select size="small" value={sort} onChange={(e) => setSort(e.target.value)}
                sx={{ minWidth: 120 }}>
                <MenuItem value="newest">Newest</MenuItem>
                <MenuItem value="oldest">Oldest</MenuItem>
                <MenuItem value="title">A–Z</MenuItem>
              </Select>
              <Tooltip title="Everything shown that this library does not have yet — leaving
                out singles whose songs you already have or are taking on an album, and
                editions a bigger edition you are taking already holds. Tick any of those
                yourself and it downloads whole.">
                <Button size="small" variant="outlined"
                  onClick={() => selectMany(pickNew(visible, selectedIds), true)}>
                  Select new
                </Button>
              </Tooltip>
              <Button size="small"
                onClick={() => selectMany(visible.filter((r) => queueable(r)).map((r) => r.browse_id), true)}>
                Select all shown
              </Button>
              <Button size="small" disabled={!selectedIds.length}
                onClick={() => setSelection({})}>
                Clear
              </Button>
            </Stack>
          </Paper>

          {sections.map((s) => (
            <ReleaseSection key={s.key} section={s} selection={selection}
              expanded={expanded} fetching={fetching} titles={titles} query={query} force={force}
              onToggle={toggle} onToggleTrack={toggleTrack} onExpand={expand}
              onSelectMany={selectMany} />
          ))}
          {!sections.length && (
            <Typography color="text.secondary" sx={{ p: 2 }}>
              {releases.length ? 'Nothing matches the filters.' : 'No releases on YouTube Music.'}
            </Typography>
          )}

          <SelectionBar summary={summary} caps={stats?.caps} force={force}
            busy={busy} onClear={() => setSelection({})} onQueue={queue}
            bottomOffset={bottomOffset} />
        </>
      )}
    </Box>
  )
}
