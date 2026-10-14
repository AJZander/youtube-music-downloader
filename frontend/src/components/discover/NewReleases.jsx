import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Box, Button, Chip, IconButton, InputAdornment, LinearProgress, MenuItem, Pagination,
  Paper, Select, Snackbar, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import FilterListIcon from '@mui/icons-material/FilterList'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import VisibilityIcon from '@mui/icons-material/Visibility'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'

import { api, subscribeEvents } from '../../api.js'
import ReleaseSection from '../artist/ReleaseSection.jsx'
import SelectionBar from '../artist/SelectionBar.jsx'
import { describeQueue, useReleaseSelection } from '../artist/selection.js'
import { CATEGORIES } from '../artist/shared.js'
import ArtistSidebar from './ArtistSidebar.jsx'

const PAGE_SIZES = [100, 250, 500]
// The app bar (a dense toolbar) stays at the top; the sidebar sticks below it.
const STICKY_TOP = 48 + 8

function loadPageSize() {
  try {
    const n = Number(localStorage.getItem('mdl-discover-page-size'))
    return PAGE_SIZES.includes(n) ? n : 500
  } catch { return 500 }
}

/** Releases by the library's artists and the artists recommended from them —
 *  the same rows, selection and queueing as the artist browser. Filtered and
 *  paged on the server, so a page of singles is a full page of singles. */
export default function NewReleases({ status, onQueued, onBrowseArtist, stats, bottomOffset }) {
  const [releases, setReleases] = useState(null)
  const [meta, setMeta] = useState(null)        // total, counts, owned
  const [loading, setLoading] = useState(false)
  const [years, setYears] = useState(2)
  const [source, setSource] = useState('all')
  const [showOwned, setShowOwned] = useState(false)
  const [cats, setCats] = useState([])          // none picked: every category
  const [artist, setArtist] = useState(null)    // {channel_id, name}
  const [query, setQuery] = useState('')
  const [q, setQ] = useState('')
  const [pageSize, setPageSize] = useState(loadPageSize)
  const [paging, setPaging] = useState({ key: '', page: 0 })
  const [sidebarVersion, setSidebarVersion] = useState(0)
  const [expanded, setExpanded] = useState({})
  const [fetching, setFetching] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [undo, setUndo] = useState(null)        // the last releases hidden: {ids, text, key}
  const top = useRef(null)

  useEffect(() => {
    const id = setTimeout(() => setQ(query.trim()), 300)
    return () => clearTimeout(id)
  }, [query])
  useEffect(() => {
    try { localStorage.setItem('mdl-discover-page-size', String(pageSize)) } catch { /* private */ }
  }, [pageSize])

  // What the sidebar counts by, and the list on top of that.
  const filters = useMemo(() => ({
    years, include_owned: showOwned,
    ...(source !== 'all' && { source }),
    ...(cats.length && { categories: cats.join(',') }),
  }), [years, showOwned, source, cats])
  const filterKey = JSON.stringify([filters, artist?.channel_id, q, pageSize])
  // A new filter starts at the first page.
  const page = paging.key === filterKey ? paging.page : 0
  const goTo = (p) => {
    setPaging({ key: filterKey, page: p })
    top.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const params = useMemo(() => ({
    ...filters,
    ...(artist && { artist: artist.channel_id }),
    ...(q && { q }),
    offset: page * pageSize,
    limit: pageSize,
  }), [filters, artist, q, page, pageSize])

  const seq = useRef(0)
  const load = useCallback(async () => {
    const mine = ++seq.current
    setLoading(true)
    try {
      const d = await api.discoverReleases(params)
      if (mine !== seq.current) return
      // Keep tracklists already opened; the list itself carries none.
      setReleases((prev) => {
        const opened = Object.fromEntries((prev || []).filter((r) => r.tracks)
          .map((r) => [r.browse_id, r]))
        return d.releases.map((r) => (opened[r.browse_id]
          ? { ...r, ...pickAlbum(opened[r.browse_id]) } : r))
      })
      setMeta({ total: d.total, counts: d.counts, owned: d.owned })
    } catch (e) {
      if (mine === seq.current) setError(e.message)
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [params])

  useEffect(() => { load() }, [load])
  const refresh = useCallback(() => {
    load()
    setSidebarVersion((v) => v + 1)
  }, [load])

  // The scan finds more as it goes: refresh when it has checked more artists.
  const checked = (status?.checked || 0) + (status?.suggested_checked || 0)
  const lastChecked = useRef(checked)
  useEffect(() => {
    if (checked - lastChecked.current >= 5) {
      lastChecked.current = checked
      refresh()
    }
  }, [checked, refresh])

  const expandedRef = useRef(expanded)
  const fetchingRef = useRef(fetching)
  const releasesRef = useRef(releases)
  expandedRef.current = expanded
  fetchingRef.current = fetching
  releasesRef.current = releases

  // A release download finished: re-read the page and the counts, so it
  // drops out as owned, and any tracklist open on it, so its tracks say so.
  const reloadTimer = useRef(null)
  useEffect(() => subscribeEvents({
    'job.updated': (job) => {
      if (!FINISHED.has(job.status) || !job.source_url?.startsWith(RELEASE_URL)) return
      const r = (releasesRef.current || []).find((x) => x.url === job.source_url)
      if (r) {
        const update = (patch) => setReleases((list) => list.map(
          (x) => (x.browse_id === r.browse_id ? { ...x, ...patch } : x)))
        // What the server will say too; this way it goes now.
        if (job.status === 'completed') update({ downloaded: true, queued: false })
        if (r.tracks) {
          api.catalogAlbum(r.browse_id).then((album) => update(pickAlbum(album))).catch(() => {})
        }
      }
      // Batch a run of finishing downloads into one re-read.
      clearTimeout(reloadTimer.current)
      reloadTimer.current = setTimeout(refresh, 1500)
    },
  }), [refresh])
  useEffect(() => () => clearTimeout(reloadTimer.current), [])

  const expand = useCallback(async (r) => {
    const opening = !expandedRef.current[r.browse_id]
    setExpanded((e) => ({ ...e, [r.browse_id]: opening }))
    if (!opening || r.tracks || fetchingRef.current[r.browse_id]) return
    setFetching((f) => ({ ...f, [r.browse_id]: true }))
    try {
      const album = await api.catalogAlbum(r.browse_id)
      setReleases((list) => list.map((x) => (x.browse_id === r.browse_id
        ? { ...x, ...pickAlbum(album) } : x)))
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
  }, [])

  // A download that just finished leaves at once, before the re-read.
  const list = useMemo(() => (releases || []).filter((r) => showOwned || !alreadyHave(r)),
    [releases, showOwned])

  // Releases picked on other pages stay picked, and queue with this page's.
  const known = useRef({})
  useEffect(() => {
    for (const r of releases || []) known.current[r.browse_id] = r
  }, [releases])
  const selectable = useMemo(() => {
    const here = new Set(list.map((r) => r.browse_id))
    return [...list, ...Object.values(known.current).filter((r) => !here.has(r.browse_id))]
  }, [list])
  const titles = useMemo(() => Object.fromEntries(selectable.map((r) => [r.browse_id, r.title])),
    [selectable])

  // What appeared since the last look first, then by year.
  const sections = useMemo(() => {
    const out = []
    const fresh = list.filter((r) => r.is_new)
    if (fresh.length) out.push({ key: 'new', label: 'Just appeared', releases: fresh })
    const byYear = {}
    for (const r of list.filter((x) => !x.is_new)) (byYear[r.year || 'Unknown year'] ||= []).push(r)
    for (const year of Object.keys(byYear).sort().reverse()) {
      out.push({ key: year, label: year, releases: byYear[year] })
    }
    return out
  }, [list])

  const {
    selection, setSelection, toggle, toggleTrack, selectMany, summary, picks, clearSettled,
  } = useReleaseSelection(selectable, false)

  // Not interested: hidden from Discover, until undone.
  const hide = useCallback(async (ids, text) => {
    setBusy(true); setError(null)
    try {
      await api.discoverDismissReleases(ids)
      const gone = new Set(ids)
      setReleases((list) => list.filter((x) => !gone.has(x.browse_id)))
      setSelection((s) => Object.fromEntries(Object.entries(s).filter(([id]) => !gone.has(id))))
      setUndo({ ids, text, key: Date.now() })
      refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }, [refresh, setSelection])
  const dismiss = useCallback((r) => hide([r.browse_id], `“${r.title}” hidden`), [hide])
  const selectedIds = Object.keys(selection)
  const dismissSelected = () => hide(selectedIds,
    `${selectedIds.length} release${selectedIds.length === 1 ? '' : 's'} hidden`)
  const restore = async () => {
    const { ids } = undo
    setUndo(null)
    try {
      await api.discoverRestoreReleases(ids)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const queue = async () => {
    setBusy(true); setError(null); setNotice(null)
    try {
      const result = await api.catalogQueue(picks(selectable), false)
      clearSettled(result)
      const { notice: text, error: problem } = describeQueue(result, titles)
      setNotice(text)
      if (problem) setError(problem)
      onQueued?.()
      load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleCategory = (key) => setCats((c) => (c.includes(key)
    ? c.filter((x) => x !== key) : [...c, key]))
  const counts = meta?.counts || {}
  const everything = Object.values(counts).reduce((n, c) => n + c, 0)
  const total = meta?.total || 0
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const noun = cats.length === 1
    ? CATEGORIES.find((c) => c.key === cats[0])?.label.toLowerCase() : 'releases'
  const filtered = cats.length > 0 || !!artist || !!q
  const scanning = status && status.searched < status.library_artists

  // Downloads and dismissals shrink the list: past its end, step back.
  useEffect(() => {
    if (meta && page > 0 && page >= pages) setPaging({ key: filterKey, page: pages - 1 })
  }, [meta, page, pages, filterKey])

  return (
    <Box sx={{
      display: 'grid', gap: 2, alignItems: 'start',
      gridTemplateColumns: { xs: 'minmax(0, 1fr)', md: '270px minmax(0, 1fr)' },
    }}>
      <Paper variant="outlined" sx={{
        display: { xs: 'none', md: 'flex' }, flexDirection: 'column', overflow: 'hidden',
        position: 'sticky', top: STICKY_TOP,
        height: `calc(100vh - ${STICKY_TOP + bottomOffset + 16}px)`,
      }}>
        <ArtistSidebar params={filters} version={sidebarVersion}
          selected={artist?.channel_id}
          onSelect={(a) => setArtist(a && { channel_id: a.channel_id, name: a.name })} />
      </Paper>

      <Box ref={top} sx={{ minWidth: 0, scrollMarginTop: STICKY_TOP }}>
        <Paper sx={{ p: 1.5, mb: 2 }}>
          <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
            <ToggleButtonGroup size="small" exclusive value={source}
              onChange={(_, v) => v && setSource(v)}>
              <ToggleButton value="all">All</ToggleButton>
              <ToggleButton value="library">Your artists</ToggleButton>
              <ToggleButton value="suggested">Artists like yours</ToggleButton>
            </ToggleButtonGroup>
            <Select size="small" value={years} onChange={(e) => setYears(e.target.value)}>
              <MenuItem value={1}>This year</MenuItem>
              <MenuItem value={2}>This year and last</MenuItem>
              <MenuItem value={3}>Last three years</MenuItem>
              <MenuItem value={5}>Last five years</MenuItem>
              <MenuItem value={0}>All years</MenuItem>
            </Select>
            <Box sx={{ flexGrow: 1 }} />
            {(meta?.owned > 0 || showOwned) && (
              <Button size="small" variant={showOwned ? 'contained' : 'outlined'}
                startIcon={showOwned ? <VisibilityOffIcon /> : <VisibilityIcon />}
                onClick={() => setShowOwned((v) => !v)}>
                {showOwned ? 'Hide already downloaded'
                  : `Show ${meta.owned.toLocaleString()} already downloaded`}
              </Button>
            )}
          </Stack>
          <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.75, alignItems: 'center', mt: 1.25 }}>
            <Chip size="small" clickable label={`All ${everything.toLocaleString()}`}
              color={cats.length ? 'default' : 'primary'}
              variant={cats.length ? 'outlined' : 'filled'}
              onClick={() => setCats([])} />
            {CATEGORIES.filter((c) => counts[c.key] || cats.includes(c.key)).map((c) => (
              <Chip key={c.key} size="small" clickable
                color={cats.includes(c.key) ? 'primary' : 'default'}
                variant={cats.includes(c.key) ? 'filled' : 'outlined'}
                label={`${c.label} ${(counts[c.key] || 0).toLocaleString()}`}
                onClick={() => toggleCategory(c.key)} />
            ))}
            <TextField size="small" placeholder="Filter by title or artist" value={query}
              onChange={(e) => setQuery(e.target.value)} sx={{ flexGrow: 1, minWidth: 200, ml: 1 }}
              InputProps={{ startAdornment: (
                <InputAdornment position="start"><FilterListIcon fontSize="small" /></InputAdornment>
              ) }} />
          </Stack>
          {artist && (
            <Stack direction="row" sx={{ gap: 0.75, alignItems: 'center', mt: 1.25 }}>
              <Chip size="small" color="secondary" label={`Artist: ${artist.name}`}
                onDelete={() => setArtist(null)} />
              <Button size="small" startIcon={<OpenInNewIcon />}
                onClick={() => onBrowseArtist?.(artist.channel_id)}>
                Whole discography
              </Button>
            </Stack>
          )}
        </Paper>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {notice && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice(null)}>{notice}</Alert>}

        <Box sx={{ height: 4, mb: 1 }}>{loading && <LinearProgress />}</Box>
        {releases && !sections.length && !loading && (
          <Paper variant="outlined" sx={{ p: 3, textAlign: 'center', borderStyle: 'dashed' }}>
            <Typography variant="body2" color="text.secondary">
              {scanning && !filtered
                ? 'Nothing yet — Discover is still reading your artists. New releases appear here as it finds them.'
                : filtered ? 'Nothing matches the filters.'
                  : meta?.owned ? 'You already have everything new from your artists in this period.'
                    : 'No new releases from your artists in this period.'}
            </Typography>
          </Paper>
        )}

        {sections.map((s) => (
          <ReleaseSection key={s.key} section={s} selection={selection} expanded={expanded}
            fetching={fetching} titles={titles} query="" force={false}
            onToggle={toggle} onToggleTrack={toggleTrack} onExpand={expand}
            onSelectMany={selectMany} onOpenArtist={onBrowseArtist} onDismiss={dismiss} />
        ))}

        {total > 0 && (
          <Paper variant="outlined" sx={{
            p: 1, pl: 1.5, mb: 2, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap',
          }}>
            <Typography variant="body2" color="text.secondary">
              {(page * pageSize + 1).toLocaleString()}–
              {Math.min(total, (page + 1) * pageSize).toLocaleString()} of{' '}
              <Box component="span" sx={{ color: 'text.primary', fontWeight: 600 }}>
                {total.toLocaleString()}
              </Box>{' '}{noun}{artist && ` by ${artist.name}`}
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
            {pages > 1 && (
              <Pagination size="small" count={pages} page={page + 1} siblingCount={1}
                showFirstButton showLastButton onChange={(_, p) => goTo(p - 1)} />
            )}
            <Select size="small" value={pageSize} onChange={(e) => setPageSize(e.target.value)}
              sx={{ fontSize: '0.85rem' }}>
              {PAGE_SIZES.map((n) => <MenuItem key={n} value={n}>{n} per page</MenuItem>)}
            </Select>
          </Paper>
        )}

        <SelectionBar summary={summary} caps={stats?.caps} force={false} busy={busy}
          onClear={() => setSelection({})} onQueue={queue} bottomOffset={bottomOffset}
          selected={selectedIds.length} onDismiss={dismissSelected} />
      </Box>

      <Snackbar open={!!undo} key={undo?.key} autoHideDuration={10000}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
        onClose={(_, reason) => { if (reason !== 'clickaway') setUndo(null) }}>
        {undo ? (
          <Alert severity="info" variant="filled" sx={{ width: '100%', alignItems: 'center' }}
            action={(
              <>
                <Button color="inherit" size="small" onClick={restore}>Undo</Button>
                <IconButton color="inherit" size="small" aria-label="Close"
                  onClick={() => setUndo(null)}>
                  <CloseIcon fontSize="small" />
                </IconButton>
              </>
            )}>
            {undo.text}
          </Alert>
        ) : <span />}
      </Snackbar>
    </Box>
  )
}

const RELEASE_URL = 'https://music.youtube.com/browse/'

// Job statuses that mean a download is over, one way or the other.
const FINISHED = new Set(['completed', 'partial', 'failed', 'cancelled'])

/** In the library already, or downloaded here: hidden unless asked for. */
function alreadyHave(r) {
  return r.in_library || r.downloaded
}

/** The parts of a fetched album a release row needs. */
function pickAlbum(album) {
  return {
    tracks: album.tracks, track_count: album.track_count, duration_sec: album.duration_sec,
    artists: album.artists, library: album.library,
  }
}
