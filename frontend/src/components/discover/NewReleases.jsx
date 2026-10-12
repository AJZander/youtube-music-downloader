import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Box, Chip, FormControlLabel, InputAdornment, LinearProgress, MenuItem, Paper,
  Select, Stack, Switch, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import FilterListIcon from '@mui/icons-material/FilterList'

import { api } from '../../api.js'
import ReleaseSection from '../artist/ReleaseSection.jsx'
import SelectionBar from '../artist/SelectionBar.jsx'
import { describeQueue, useReleaseSelection } from '../artist/selection.js'
import { CATEGORIES } from '../artist/shared.js'

/** Recent releases by the library's artists and the artists recommended
 *  from them — the same rows, selection and queueing as the artist browser. */
export default function NewReleases({ status, onQueued, onBrowseArtist, stats, bottomOffset }) {
  const [releases, setReleases] = useState(null)
  const [years, setYears] = useState(2)
  const [source, setSource] = useState('all')
  const [includeOwned, setIncludeOwned] = useState(false)
  const [hidden, setHidden] = useState({})
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState({})
  const [fetching, setFetching] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const load = useCallback(async () => {
    try {
      const d = await api.discoverReleases({
        years, include_owned: includeOwned, ...(source !== 'all' && { source }),
      })
      // Keep tracklists already opened; the list itself carries none.
      setReleases((prev) => {
        const opened = Object.fromEntries((prev || []).filter((r) => r.tracks)
          .map((r) => [r.browse_id, r]))
        return d.releases.map((r) => (opened[r.browse_id]
          ? { ...r, ...pickAlbum(opened[r.browse_id]) } : r))
      })
    } catch (e) {
      setError(e.message)
    }
  }, [years, source, includeOwned])

  useEffect(() => { load() }, [load])
  // The scan finds more as it goes: refresh when it has checked more artists.
  const checked = (status?.checked || 0) + (status?.suggested_checked || 0)
  const lastChecked = useRef(checked)
  useEffect(() => {
    if (checked - lastChecked.current >= 5) {
      lastChecked.current = checked
      load()
    }
  }, [checked, load])

  const expandedRef = useRef(expanded)
  const fetchingRef = useRef(fetching)
  expandedRef.current = expanded
  fetchingRef.current = fetching

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

  const dismiss = useCallback(async (r) => {
    try {
      await api.discoverDismissRelease(r.browse_id)
      setReleases((list) => list.filter((x) => x.browse_id !== r.browse_id))
    } catch (e) {
      setError(e.message)
    }
  }, [])

  const list = useMemo(() => releases || [], [releases])
  const titles = useMemo(() => Object.fromEntries(list.map((r) => [r.browse_id, r.title])), [list])
  const counts = useMemo(() => {
    const c = {}
    for (const r of list) c[r.category] = (c[r.category] || 0) + 1
    return c
  }, [list])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return list.filter((r) => !hidden[r.category] && (!needle
      || r.title.toLowerCase().includes(needle)
      || (r.artist_name || '').toLowerCase().includes(needle)))
  }, [list, hidden, query])

  // What appeared since the last look first, then by year.
  const sections = useMemo(() => {
    const out = []
    const fresh = visible.filter((r) => r.is_new)
    if (fresh.length) out.push({ key: 'new', label: 'Just appeared', releases: fresh })
    const byYear = {}
    for (const r of visible.filter((x) => !x.is_new)) (byYear[r.year] ||= []).push(r)
    for (const year of Object.keys(byYear).sort().reverse()) {
      out.push({ key: year, label: year, releases: byYear[year] })
    }
    return out
  }, [visible])

  const {
    selection, setSelection, toggle, toggleTrack, selectMany, summary, picks, clearSettled,
  } = useReleaseSelection(list, false)

  const queue = async () => {
    setBusy(true); setError(null); setNotice(null)
    try {
      const shown = sections.flatMap((s) => s.releases)
      const result = await api.catalogQueue(picks(shown), false)
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

  const scanning = status && status.searched < status.library_artists

  return (
    <Box>
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
          </Select>
          <Box sx={{ flexGrow: 1 }} />
          <FormControlLabel sx={{ mr: 0 }}
            control={<Switch size="small" checked={includeOwned}
              onChange={(e) => setIncludeOwned(e.target.checked)} />}
            label={<Typography variant="body2">Show ones I have</Typography>} />
        </Stack>
        <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.75, alignItems: 'center', mt: 1.25 }}>
          {CATEGORIES.filter((c) => counts[c.key]).map((c) => (
            <Chip key={c.key} size="small" clickable
              color={hidden[c.key] ? 'default' : 'primary'}
              variant={hidden[c.key] ? 'outlined' : 'filled'}
              label={`${c.label} ${counts[c.key]}`}
              onClick={() => setHidden((h) => ({ ...h, [c.key]: !h[c.key] }))} />
          ))}
          <TextField size="small" placeholder="Filter by title or artist" value={query}
            onChange={(e) => setQuery(e.target.value)} sx={{ flexGrow: 1, minWidth: 200, ml: 1 }}
            InputProps={{ startAdornment: (
              <InputAdornment position="start"><FilterListIcon fontSize="small" /></InputAdornment>
            ) }} />
        </Stack>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice(null)}>{notice}</Alert>}

      {!releases && <LinearProgress />}
      {releases && !sections.length && (
        <Paper variant="outlined" sx={{ p: 3, textAlign: 'center', borderStyle: 'dashed' }}>
          <Typography variant="body2" color="text.secondary">
            {scanning
              ? 'Nothing yet — Discover is still reading your artists. New releases appear here as it finds them.'
              : list.length ? 'Nothing matches the filters.'
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

      <SelectionBar summary={summary} caps={stats?.caps} force={false} busy={busy}
        onClear={() => setSelection({})} onQueue={queue} bottomOffset={bottomOffset} />
    </Box>
  )
}

/** The parts of a fetched album a release row needs. */
function pickAlbum(album) {
  return {
    tracks: album.tracks, track_count: album.track_count, duration_sec: album.duration_sec,
    artists: album.artists, library: album.library,
  }
}
